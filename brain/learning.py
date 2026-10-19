"""What she learns: behavior lessons from thumbs up/down, memories saved
after a reply (or proposed for review in training mode), and the memory
server profiles.
"""

import asyncio
import re
import json
import time
from datetime import date, datetime
from pathlib import Path

import websockets

import conversation
import curiosity
import hub
import journal
import lessons
import memory
import memory_profiles
import metrics
import persona
import profiles
import protocol
import reflection
import souls
import training
from hub import Brain

LESSONS_STATE_TIMEOUT_SEC = 8


async def _lessons_state_message() -> dict:
    try:
        state = await asyncio.wait_for(lessons.state(), timeout=LESSONS_STATE_TIMEOUT_SEC)
    except Exception as exc:
        state = {
            "available": lessons.available(),
            "active": lessons.read_active(),
            "autonomy": lessons.read_autonomy(),
            "lessons": [],
            "pending": lessons.read_pending(),
            "error": f"couldn't reach the lessons store: {exc!r}",
        }
    return protocol.lessons_state(**state)


async def send_lessons_state(websocket: websockets.ServerConnection) -> None:
    try:
        await hub.send(websocket, await _lessons_state_message())
    except Exception:
        pass  # connection closed before it finished -- nothing to tell


async def _broadcast_lessons_state() -> None:
    await hub.broadcast(await _lessons_state_message())


# Her diary's self-critique, up to the next heading (journal.py's entry; llm._DIARY_SYSTEM_PROMPT).
_SAY_DIFFERENTLY = re.compile(r"What I.d say differently[^\n]*\n(.*?)(?=\n[#*\s]*What I.m wondering|\Z)", re.DOTALL | re.IGNORECASE)


async def lesson_from_diary(entry: str, brain: Brain) -> None:
    """What she'd do differently, from her diary, as at most one lesson change -- applied or
    waiting for approval by the same autonomy setting as one learned from a rating."""
    critique = _SAY_DIFFERENTLY.search(entry)
    if not critique or not critique[1].strip() or not lessons.read_active():
        return
    try:
        active = await lessons.active_lessons()
        raw = await asyncio.to_thread(brain.llm.lesson_from_diary, critique[1].strip(), [l["content"] for l in active])
        action = lessons.parse_distillation(raw, active, [])
        if action is None or action["action"] not in ("create", "strengthen"):
            return
        kind, text = await lessons.handle_action(action)
    except Exception as exc:
        print(f"[brain] learning from her diary failed: {exc!r}")
        return
    print(f"[brain] lesson from her diary {kind}: {text}")
    await hub.broadcast(protocol.lesson_event(kind, f"From her diary: {text}"))
    await _broadcast_lessons_state()


@hub.handles(
    protocol.SET_LESSONS_ACTIVE,
    protocol.SET_LESSONS_AUTONOMY,
    protocol.SAVE_LESSON,
    protocol.RETIRE_LESSON,
    protocol.DELETE_LESSON,
    protocol.RESOLVE_LESSON_PROPOSAL,
)
async def _lessons(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The Settings panel's Behavior learning controls (toggle, autonomy,
    add/edit/retire/delete a lesson, approve/reject a proposal). Every one
    ends by broadcasting the fresh lessons_state to every connected device.
    A refused change (over the lesson limit, a lesson that no longer exists)
    is reported as an "error" lesson_event so the click doesn't just
    silently do nothing.
    """
    msg_type = data["type"]
    try:
        if msg_type == protocol.SET_LESSONS_ACTIVE:
            lessons.set_active(bool(data.get("active")))
        elif msg_type == protocol.SET_LESSONS_AUTONOMY:
            lessons.set_autonomy(str(data.get("level", "")))
        elif msg_type == protocol.SAVE_LESSON:
            name, content = str(data.get("name", "")), str(data.get("content", ""))
            if hub.fields_too_long(name, content):
                return
            priority = data.get("priority")
            priority = int(priority) if priority is not None else None
            if data.get("id"):
                await lessons.update_lesson(str(data["id"]), name=name, content=content, priority=priority)
            else:
                await lessons.create_lesson(name, content, priority if priority is not None else lessons.DEFAULT_PRIORITY)
        elif msg_type == protocol.RETIRE_LESSON:
            await lessons.retire_lesson(str(data.get("id", "")), "retired by user")
        elif msg_type == protocol.DELETE_LESSON:
            await lessons.delete_lesson(str(data.get("id", "")))
        elif msg_type == protocol.RESOLVE_LESSON_PROPOSAL:
            applied = await lessons.resolve_pending(str(data.get("id", "")), bool(data.get("approve")))
            if applied:
                await hub.broadcast(protocol.lesson_event("applied", applied))
    except (ValueError, LookupError, lessons.LessonsUnavailable) as exc:
        await hub.broadcast(protocol.lesson_event("error", str(exc)))
    except Exception as exc:
        print(f"[brain] lessons change failed: {exc!r}")
        await hub.broadcast(protocol.lesson_event("error", "couldn't reach the lessons store"))
    await _broadcast_lessons_state()


@hub.handles(protocol.RATE_REPLY)
async def _rate_reply(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """A thumbs up/down on one of her replies. The rating itself is always
    logged locally (lessons.log_rating); learning from it only happens when
    the feature is on, role-play is off (in-character replies shouldn't
    teach her habits for normal conversation) and she's on her own LLM (a
    harness manages its own behavior). That part runs as a background task
    -- it's an extra LLM call plus Hindsight writes, and the user shouldn't
    wait on it.
    """
    rating = data.get("rating")
    user_text = str(data.get("user_text", ""))
    reply_text = str(data.get("reply_text", ""))
    note = str(data.get("note", "")).strip()
    if rating not in ("up", "down") or not reply_text.strip() or hub.fields_too_long(user_text, reply_text, note):
        return
    metrics.log("rating", up=rating == "up")
    if conversation.PRIVATE.read():  # the rating log keeps both sides of the exchange
        return await hub.debug_log(websocket, "lessons", "rating not kept (private chat)")
    roleplay = profiles.read_roleplay_active()
    lessons.log_rating(user_text, reply_text, rating, note, roleplay)
    await hub.debug_log(websocket, "lessons", f"rating logged ({rating})")
    if roleplay or not lessons.read_active() or not brain.llm.owns_conversation:
        return
    hub.spawn(_learn_from_rating(websocket, brain, user_text, reply_text, rating, note))


async def _learn_from_rating(
    websocket: websockets.ServerConnection, brain: Brain, user_text: str, reply_text: str, rating: str, note: str
) -> None:
    start = time.monotonic()
    try:
        active = await lessons.active_lessons()
        candidates = lessons.read_candidates() if lessons.read_autonomy() == lessons.ALL else []
        raw = await asyncio.to_thread(
            brain.llm.propose_lesson,
            user_text,
            reply_text,
            rating,
            note,
            [l["content"] for l in active],
            [c["content"] for c in candidates],
        )
        action = lessons.parse_distillation(raw, active, candidates, has_note=bool(note))
        if action is None:
            await hub.debug_log(websocket, "lessons", "nothing to learn from that rating", (time.monotonic() - start) * 1000)
            return
        kind, text = await lessons.handle_action(action)
    except Exception as exc:
        await hub.debug_log(websocket, "lessons", f"learning from rating failed: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] learning from rating failed: {exc!r}")
        return
    await hub.debug_log(websocket, "lessons", f"{kind} ({action['action']})", (time.monotonic() - start) * 1000)
    print(f"[brain] lesson {kind}: {text}")
    await hub.broadcast(protocol.lesson_event(kind, text))
    await _broadcast_lessons_state()


def memory_profiles_message(error: str = "") -> dict:
    types = [{"type": kind, **info} for kind, info in memory_profiles.TYPES.items()]
    return protocol.memory_profiles(memory_profiles.list_profiles(), memory_profiles.read_active(), types, error)


async def activate_memory() -> str:
    """Connects to the active memory profile. Returns "" or why it couldn't
    reach the server (it's still the backend -- calls fail softly per turn)."""
    try:
        await memory.activate()
        return ""
    except Exception as exc:
        print(f"[brain] couldn't reach the memory server for {memory_profiles.read_active()!r}: {exc!r}")
        return f"Couldn't reach that memory server ({type(exc).__name__}). It's saved; check the URL and key."


@hub.handles(protocol.SET_MEMORY_ACTIVE)
async def _set_memory_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    memory.set_memory_active(bool(data.get("active")))
    await hub.broadcast(protocol.memory_state(memory.read_memory_active()))


@hub.handles(protocol.CLEAR_MEMORY)
async def _clear_memory(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await memory.clear()
    brain.llm.set_memory("")


@hub.handles(protocol.GET_MEMORY_CONTENT)
async def _get_memory_content(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await hub.send(websocket, protocol.memory_content(await memory.read_entries()))


@hub.handles(protocol.GET_MEMORY_PROFILE)
async def _get_memory_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = str(data.get("name") or "").strip()
    try:
        profile = memory_profiles.read_profile(name)
    except (OSError, ValueError):
        profile = {}
    await hub.send(websocket, protocol.memory_profile_content(name, profile))


@hub.handles(protocol.SET_MEMORY_PROFILE, protocol.SAVE_MEMORY_PROFILE, protocol.DELETE_MEMORY_PROFILE)
async def _change_memory_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Settings -> Memory: pick, save, edit or delete a memory backend profile
    (memory_profiles.py). Every device's list updates; a refusal or an
    unreachable server is only told to the device that asked.
    """
    msg_type = data["type"]
    name = str(data.get("name") or "").strip()
    error = ""
    reconnect = False
    try:
        if msg_type == protocol.SET_MEMORY_PROFILE:
            if name != memory_profiles.LOCAL_NAME:
                memory_profiles.read_profile(name)  # must exist
            memory_profiles.set_active(name)
            reconnect = True
        elif msg_type == protocol.SAVE_MEMORY_PROFILE:
            fields = [str(data.get(k) or "") for k in ("backend", "url", "api_key", "space", "original_name")]
            if hub.fields_too_long(name, *fields):
                return
            kind, url, api_key, space, original = fields
            was_active = memory_profiles.read_active()
            saved = memory_profiles.save_profile(name, kind, url, api_key, space, replaces=original)
            reconnect = was_active in (saved, original)
            print(f"[brain] saved memory profile {saved!r} ({kind})")
        elif msg_type == protocol.DELETE_MEMORY_PROFILE:
            reconnect = memory_profiles.read_active() == name
            memory_profiles.delete_profile(name)
    except (OSError, ValueError) as exc:
        await hub.send(websocket, memory_profiles_message(str(exc) if isinstance(exc, ValueError) else "No such memory profile."))
        return
    if reconnect:
        error = await activate_memory()
        lessons.invalidate()
        await hub.debug_broadcast("memory", f"memory backend: {memory.active_description()}")
    await hub.broadcast(memory_profiles_message())
    if error:
        await hub.send(websocket, memory_profiles_message(error))
    await hub.broadcast(training_state_message())
    await _broadcast_lessons_state()


async def broadcast_training_state() -> None:
    await hub.broadcast(training_state_message())


def training_state_message(error: str = "") -> dict:
    available = memory.server_configured()
    return protocol.training_state(available, training.read_active(), training.read_pending(), error)


@hub.handles(protocol.SET_TRAINING_ACTIVE)
async def _set_training_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    training.set_active(bool(data.get("active")))
    await hub.broadcast(training_state_message())


SHOWN_OWN = 8  # her latest saves without him, newest first


@hub.handles(protocol.GET_MIND)
async def _get_mind(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await hub.send(websocket, mind_message(brain))


_reflecting = False


@hub.handles(protocol.REFLECT_NOW)
async def _reflect_now(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The 🧠 window's button: her weekly look-back and soul revision now, in the background."""
    global _reflecting
    if _reflecting:
        return
    _reflecting = True

    async def reflect() -> None:
        global _reflecting
        try:
            await reflection.run(brain)
        finally:
            _reflecting = False

    hub.spawn(reflect())


@hub.handles(protocol.GET_GROWTH)
async def _get_growth(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await hub.send(websocket, protocol.growth(metrics.growth()))


def mind_message(brain: Brain) -> dict:
    recalled = [line[2:] for line in brain.llm.memory_block.splitlines() if line.startswith("- ")]
    soul = souls.SOUL_MD_PATH
    text = soul.read_text(encoding="utf-8") if soul.exists() else ""
    edited = datetime.fromtimestamp(soul.stat().st_mtime).isoformat(timespec="minutes") if soul.exists() else ""
    waiting_lessons = [lessons.describe(p) for p in lessons.read_pending()]
    own = [{"text": r["text"], "source": r.get("source", ""), "t": r["t"]} for r in metrics.read() if r["e"] == "approved" and r.get("by") == "her"]
    return protocol.mind(recalled, training.read_pending(), waiting_lessons, own[-SHOWN_OWN:][::-1], len(text), len(text.splitlines()), edited, souls.SOUL_MAX_CHARS)


async def daily_snapshot() -> None:
    """Once a day (metrics.snapshot_due): how big her memory bank and his review list are."""
    try:
        size = len(await memory.list_memories()) if memory.server_backed() else None
    except Exception as exc:
        print(f"[metrics] counting her memories failed: {exc!r}")
        size = None
    metrics.log("bank", size=size, queue=len(training.read_pending()), lessons=len(lessons.read_pending()))


# How far into a day her memory proposals have read: {"day": "YYYY-MM-DD", "through": "HH:MM:SS"}.
# Each sitting is proposed from when it ends, and the nightly run only reads what's left.
PROPOSED_PATH = Path(__file__).parent / "proposed_through.json"
SITTING_QUIET_SEC = 60 * 60  # this long after his last message, a sitting is over
_ENTRY = re.compile(r"(?=^\*\*\d\d:\d\d:\d\d\*\* )", re.MULTILINE)
_STAMP = re.compile(r"\*\*(\d\d:\d\d:\d\d)\*\*")


def _proposed_through() -> dict:
    try:
        return json.loads(PROPOSED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def unproposed(day: date, log: str) -> str:
    """The entries of a day's log her memory proposals haven't read yet."""
    mark = _proposed_through()
    through = mark.get("through", "") if mark.get("day") == day.isoformat() else ""
    return "".join(e for e in _ENTRY.split(log) if (stamp := _STAMP.match(e)) and stamp[1] > through)


def mark_proposed(day: date, log: str) -> None:
    stamps = _STAMP.findall(log)
    if stamps:
        PROPOSED_PATH.write_text(json.dumps({"day": day.isoformat(), "through": stamps[-1]}), encoding="utf-8")


def can_propose(brain: Brain) -> bool:
    """Whether her memory proposals run: her own LLM (a harness keeps its own memory), memory on,
    a memory server, and memory training on."""
    return bool(brain.llm.owns_conversation and memory.read_memory_active() and memory.server_backed() and training.read_active())


def sitting_over(now: float, quiet_sec: float = SITTING_QUIET_SEC) -> date | None:
    """The day of a sitting that's over (quiet_sec since his last message) and not yet proposed from."""
    last = curiosity.last_user_at()
    if not last or now - last < quiet_sec:
        return None
    said = datetime.fromtimestamp(last)
    mark = _proposed_through()
    if mark.get("day") == said.date().isoformat() and mark.get("through", "") >= f"{said:%H:%M:%S}":
        return None
    return said.date()


def end_sitting(brain: Brain, quiet_sec: float = SITTING_QUIET_SEC) -> None:
    """A sitting that's over (an hour of quiet, or Clear Chat with quiet_sec=0) is proposed from
    in the background, so it's in his review list the same day instead of after the night."""
    if can_propose(brain) and (day := sitting_over(time.time(), quiet_sec)):
        hub.spawn(propose_unread(day, brain))


async def propose_unread(day: date, brain: Brain) -> None:
    """Memory proposals from the part of the day's chat no proposal has read yet -- a sitting
    that just ended, or what's left at night. Without the reply lock: his replies go first.
    The mark moves before the model runs, so a check a minute later can't start the same batch."""
    if not can_propose(brain):
        return
    name = persona.user_name() or "User"
    log = journal.day_log(day, name)
    unread = unproposed(day, log)
    mark_proposed(day, log)
    if f" {name}: " in unread:
        async with hub.doing("Going over your chat for memories..."):
            await propose_from_log(unread, day, name, brain)


MAX_CONCLUSION_TOKENS = 400
# "Plain words": without it 3 of 3 of her views came out as imagery ("the ghost in the machine that refuses to be
# optimized into silence") -- with it, the same stance she could actually bring up in a conversation.
_CONCLUSION_PROMPT = (
    "Below is something you wrote on your own:\n\n{text}\n\n"
    "In one sentence, in the first person: what have you come to think? The view you'd want to remember and stand "
    "by -- not a summary of what you wrote. Under 40 words, in plain words -- no metaphors or imagery, just what you "
    "think. No preamble. If it didn't lead you anywhere you'd stand "
    "by, reply with just: nothing"
)


MAX_DISTILL_TOKENS = 500
DISTILL_TYPES = ("decision", "lesson", "opinion")
_DISTILL_PROMPT = (
    "Below is the conversation you and {name} just finished:\n\n{transcript}\n\n"
    "What from it do you want to keep? Only things on your side of it:\n"
    "- DECISION: something you decided, or promised to do\n"
    "- LESSON: something you learned about how to be, or how to treat {name}\n"
    "- OPINION: a view you formed or changed\n"
    "Not facts about {name} -- those are remembered separately. At most three, and only ones that really came up. "
    "One line each, exactly like this:\n"
    "TYPE | what, in the first person, under 40 words, in plain words -- no metaphors | why it matters to you, under 20 words\n"
    "If nothing like that came up, reply with just: nothing"
)
_DISTILL_LINE = re.compile(r"^\W*(DECISION|LESSON|OPINION)\W*\|(.+)\|(.+)$", re.IGNORECASE)


def parse_distilled(raw: str) -> list[tuple[str, str, str]]:
    """_DISTILL_PROMPT's lines as [(type, what, why)]; anything else (preamble, "nothing") is dropped."""
    found = []
    for line in raw.splitlines():
        if match := _DISTILL_LINE.match(line.strip()):
            kind, what, why = match.group(1).lower(), match.group(2).strip().strip('"'), match.group(3).strip()
            if what and len(what) <= training.MAX_FACT_CHARS:
                found.append((kind, what, why))
    return found[:3]


async def distill_chat(brain: Brain, transcript: str, name: str) -> None:
    """Clear Chat, with memory training on: her decisions, lessons and opinions from the conversation that
    just ended, each with why it matters to her, into his review list -- facts about him come from the
    sitting's proposals (end_sitting). Without this, what she settled on in a chat was gone with it."""
    if not can_propose(brain):
        return
    try:
        async with brain.busy("Going over what she decided..."):
            raw = await asyncio.to_thread(
                brain.llm.under_soul, _DISTILL_PROMPT.format(name=name, transcript=transcript), MAX_DISTILL_TOKENS
            )
        source = f"Your chat, {datetime.now():%A %d %B}"
        added = sum(1 for kind, what, why in parse_distilled(raw) if training.add_pending(what, source, type=kind, why=why))
        print(f"[training] {added} decisions / lessons / opinions of hers from the cleared chat")
        if added:
            await hub.broadcast(training_state_message())
    except Exception as exc:
        print(f"[training] couldn't go over what she decided: {exc!r}")


async def propose_own(text: str, source: str, brain: Brain, own: bool = False) -> None:
    """What she's come to think from her own thinking -- a landed page, a finished book -- as one
    memory: proposed for his review with memory training on, otherwise kept like any other.
    Without this, what she works out alone never reaches what she remembers.

    `own`: low risk -- her take on a book, a picture, a topic of hers -- so with training on it's saved
    without asking, tagged unverified and listed in the 🧠 window; one that names him still waits."""
    if not (brain.llm.owns_conversation and memory.read_memory_active() and memory.server_backed()):
        return
    try:
        async with brain.busy("Working out what she thinks..."):
            fact = (await asyncio.to_thread(brain.llm.under_soul, _CONCLUSION_PROMPT.format(text=text), MAX_CONCLUSION_TOKENS)).strip().strip('"')
        if not fact or fact.lower().rstrip(".") == "nothing" or len(fact) > training.MAX_FACT_CHARS:
            return
        name = persona.user_name()
        about_him = bool(name) and re.search(rf"\b{re.escape(name)}\b", fact, re.IGNORECASE)
        if training.read_active() and (not own or about_him):
            if training.add_pending(fact, source):
                await hub.broadcast(training_state_message())
        elif training.read_active():
            await memory.retain_fact(fact, training.DEFAULT_IMPORTANCE, unverified=True)
            metrics.log("approved", type="opinion", by="her", text=fact, source=source)
            print(f"[training] she kept a view of her own (unverified): {fact[:80]!r}")
        else:
            await memory.retain_fact(fact, training.DEFAULT_IMPORTANCE)
    except Exception as exc:
        print(f"[training] couldn't keep what she came to think: {exc!r}")


async def propose_from_log(log: str, day: date, name: str, brain: Brain, source: str = "") -> None:
    """Training mode's proposals, once a night (journal.py): a few memories from the whole
    day's chat log, each checked against what she remembers, queued for the user to
    approve, edit or reject (brain/training.py). Nothing is sent to Hindsight from here.
    """
    profile = persona.effective_user_info()
    # ponytail: guessed from the profile's wording; an explicit pronouns line if this guesses wrong.
    if re.search(r"\b(female|woman)\b", profile, re.IGNORECASE):
        pronouns = ("her", "she")
    elif re.search(r"\b(male|man)\b", profile, re.IGNORECASE):
        pronouns = ("his", "he")
    else:
        pronouns = ("their", "they")
    try:
        known = [*await memory.read_entries(), *(p["fact"] for p in training.read_pending())]
        raw = await asyncio.to_thread(brain.llm.propose_memories, log, "\n".join(f"- {k}" for k in known), name, pronouns)
        added = asked = 0
        for fact in training.parse_facts(raw):
            owner = await asyncio.to_thread(brain.llm.whose_memory, log, fact, name, pronouns)
            if fact.startswith(name) and owner.strip().upper().startswith("NEITHER"):
                # Not found in the log: rather than drop it or keep it, she asks him.
                if curiosity.read_active() and curiosity.add_question(f"{fact.rstrip('.')} -- is that right?"):
                    asked += 1
                continue
            if not owner_matches(fact, owner, name):
                continue
            if "Glitch" in fact:  # her memories are in her voice: "Josh wants to get me a scarf"
                fact = (await asyncio.to_thread(brain.llm.in_her_voice, fact)).strip() or fact
            if await _compare_with_memories(fact, brain):
                continue
            if training.add_pending(fact, source or f"{day:%A %d %B}'s chat"):
                added += 1
    except Exception as exc:
        print(f"[training] couldn't propose memories from {day}: {exc!r}")
        return
    print(f"[training] proposed {added} memories from {day}" + (f", {asked} to ask him about" if asked else ""))
    if added:
        await hub.broadcast(training_state_message())


def owner_matches(fact: str, owner: str, name: str) -> bool:
    """A memory about him has to be his per the log, and one about her mustn't be his --
    her taste in his question ("didn't you say coffee is jittershit?") came back as his."""
    his = owner.strip().upper().startswith(name.upper())
    if fact.startswith(name):
        return his
    if fact.startswith(("I ", "I'", "My ", "Glitch")):
        return not his
    return True


COMPARE_WITH = 3  # her closest existing memories a new proposal is checked against


async def _compare_with_memories(fact: str, brain: Brain) -> str:
    """A memory that already says this, or "". One pair at a time, closest first; an
    OPPOSITE answer wins, so when unsure a proposal reaches the user rather than being
    dropped (14 of 23 repeats caught, 0 of 8 new ones dropped). OPPOSITE isn't shown as a
    contradiction: a 9B calls any two memories on one topic opposite. A failure means no check.
    """
    try:
        block = await memory.recall_for_prompt(fact)
        closest = [line[2:] for line in block.splitlines() if line.startswith("- ")][:COMPARE_WITH]
        same = ""
        for old in closest:
            word = (await asyncio.to_thread(brain.llm.compare_memories, fact, old)).strip().upper()
            if word.startswith("OPPOSITE"):
                return ""
            if word.startswith("SAME") and not same:
                same = old
        return same
    except Exception as exc:
        print(f"[training] couldn't check a proposal against her memories: {exc!r}")
        return ""


@hub.handles(protocol.RESOLVE_MEMORY_PROPOSAL)
async def resolve_memory_proposal(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Approve (with the user's edited wording and importance) or reject one queued memory.
    Approval retains it to Hindsight first and only then removes it from the queue, so a
    failed save leaves it waiting (and says why) instead of losing it.
    """
    proposal_id = str(data.get("id", ""))
    error = ""
    proposal = training.get_pending(proposal_id)
    if proposal is None:
        error = "that proposal is no longer in the list"
    elif data.get("approve") and proposal.get("kind") == "soul_rewrite":
        try:
            reflection.apply_proposal(proposal, brain)
        except (ValueError, OSError) as exc:
            print(f"[brain] changing her soul failed: {exc!r}")
            error = f"couldn't change her soul ({exc}) -- it's still in the list"
        else:
            training.remove_pending(proposal_id)
    elif data.get("approve") and proposal.get("kind") in ("update", "retire"):
        retire = proposal["kind"] == "retire" or bool(data.get("retire"))
        fact = str(data.get("fact") or proposal["fact"]).strip()
        try:
            await memory.curate(proposal["memory_id"], "" if retire else fact, retire, proposal.get("source", ""))
        except Exception as exc:
            print(f"[brain] changing a memory failed: {exc!r}")
            error = "couldn't change it on her memory server -- it's still in the list"
        else:
            training.remove_pending(proposal_id)
    elif data.get("approve"):
        fact = str(data.get("fact") or proposal["fact"]).strip()
        importance = str(data.get("importance") or training.DEFAULT_IMPORTANCE)
        if not fact or len(fact) > training.MAX_FACT_CHARS:
            error = f"a memory needs 1-{training.MAX_FACT_CHARS} characters"
        else:
            saving_since = time.monotonic()
            try:
                await memory.retain_fact(fact, importance)
            except Exception as exc:
                print(f"[brain] approving a memory failed: {exc!r}")
                error = "couldn't save it to her memory server -- it's still in the list"
            else:
                training.remove_pending(proposal_id)
                await hub.debug_broadcast(
                    "training", f"approved memory saved ({importance}, {len(fact)} chars)", (time.monotonic() - saving_since) * 1000
                )
    else:
        training.remove_pending(proposal_id)
    if proposal is not None and not error:
        metrics.log("approved" if data.get("approve") else "rejected", type=training.proposal_type(proposal))
    await hub.broadcast(training_state_message(error))


@hub.handles(protocol.ADD_MEMORY)
async def add_memory(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """A memory he writes himself -- his own words, so no proposal to approve."""
    fact = str(data.get("fact") or "").strip()
    importance = str(data.get("importance") or training.DEFAULT_IMPORTANCE)
    error = ""
    if not fact or len(fact) > training.MAX_FACT_CHARS:
        error = f"a memory needs 1-{training.MAX_FACT_CHARS} characters"
    else:
        try:
            await memory.retain_fact(fact, importance)
        except Exception as exc:
            print(f"[brain] adding a memory failed: {exc!r}")
            error = "couldn't save it to her memory server"
        else:
            metrics.log("approved", type="fact", by="him")
    await hub.broadcast(training_state_message(error))


async def maybe_retain_memory(
    websocket: websockets.ServerConnection, user_text: str, reply_text: str, brain: Brain, asked: str = ""
) -> None:
    """Glitch's own native memory (brain/memory.py) -- entirely separate
    from anything Hermes does with its own memory. Gated on three things:
    her own LLM (a harness manages its own memory), the feature's own on/off
    toggle, and role-play being OFF -- the user was explicit that in-character role-play content
    must never be captured as fact about them, and skipping retention
    entirely during role-play is the simplest way to guarantee that
    rather than trying to classify fiction-vs-real-signal reliably.

    Branches on the active provider (memory.py's own read_provider()): a
    memory server (Hindsight) gets the raw exchange straight, and
    decides server-side what's worth keeping and doesn't
    hand back the specific fact synchronously, so no memory_learned gets
    sent for that path. "local" restores the original flat-file
    behavior -- one extra lightweight LLM call
    (LocalLLM.maybe_extract_memory) judges whether there's exactly one
    new durable fact, and memory_learned only fires when something
    genuinely new was added (never for a duplicate/no-op).

    Wrapped in try/except throughout: a failure here must never surface to
    the user or affect anything else, it's a pure background nice-to-have.
    """
    if not brain.llm.owns_conversation or not memory.read_memory_active() or profiles.read_roleplay_active():
        return
    if conversation.PRIVATE.read():  # nothing from a private chat is kept
        return
    if not user_text.strip() and not reply_text.strip():
        return  # an image-only turn with nothing the user said -- nothing left worth keeping
    start = time.monotonic()
    if can_propose(brain):
        return  # she proposes from the whole day instead, once a night (propose_from_log)
    if memory.server_backed():
        try:
            await memory.retain_exchange(user_text, reply_text, asked)  # reply_text is "" for a web-search turn, see reply.reply_to
        except Exception as exc:
            await hub.debug_log(websocket, "memory", f"retain failed: {exc!r}", (time.monotonic() - start) * 1000)
            return
        await hub.debug_log(websocket, "memory", "retained", (time.monotonic() - start) * 1000)
        return

    try:
        fact = await asyncio.to_thread(
            brain.llm.maybe_extract_memory,
            user_text,
            reply_text or "(reply omitted -- it described search results or an image)",
            memory.read_local_entries(),
        )
    except Exception as exc:
        await hub.debug_log(websocket, "memory", f"extraction failed: {exc!r}", (time.monotonic() - start) * 1000)
        return
    if not fact:
        await hub.debug_log(websocket, "memory", "nothing new to remember", (time.monotonic() - start) * 1000)
        return
    # Never logs the fact itself -- category/timing only, same reasoning
    # as every other hub.debug_log call in this file (never conversation
    # content, and a remembered fact about the user is exactly that).
    if not memory.add_local_entry(fact):
        # Exact-duplicate re-add -- nothing actually changed, so no
        # memory_learned notification either (would be a false "learned
        # something new" for a fact she already had).
        await hub.debug_log(websocket, "memory", "fact already known", (time.monotonic() - start) * 1000)
        return
    # brain.llm, not a local from the top: the await above is long enough for
    # another device to have switched engines or plugged in a harness.
    brain.llm.set_memory(memory.read_local_block())
    await hub.debug_log(websocket, "memory", "learned something new", (time.monotonic() - start) * 1000)
    await hub.send(websocket, protocol.memory_learned(fact))
