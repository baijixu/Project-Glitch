"""What she learns: behavior lessons from thumbs up/down, memories saved
after a reply (or proposed for review in training mode), and the memory
server profiles.
"""

import asyncio
import re
import time

import websockets

import hub
import lessons
import memory
import memory_profiles
import persona
import profiles
import protocol
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


def training_state_message(error: str = "") -> dict:
    available = memory.server_configured()
    return protocol.training_state(available, training.read_active(), training.read_pending(), error)


@hub.handles(protocol.SET_TRAINING_ACTIVE)
async def _set_training_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    training.set_active(bool(data.get("active")))
    await hub.broadcast(training_state_message())


async def _propose_memory(
    websocket: websockets.ServerConnection, user_text: str, reply_text: str, brain: Brain, asked: str = ""
) -> None:
    """Training mode's replacement for retain_exchange: asks the model for at most one fact
    worth keeping and queues it for the user to approve, edit or reject (brain/training.py).
    Nothing is sent to Hindsight from here. A failure or an empty answer is the normal
    outcome and never surfaces.
    """
    start = time.monotonic()
    # Only their name from user.md, so she can call them by it. The whole profile
    # went in before, and a 9B filed her own answers under its likes: her "I've got
    # a thing for the Bronze Age" came back as Josh's, 5 of 5, because it lists history.
    profile = persona.effective_user_info()
    name = persona.user_name()
    # ponytail: guessed from the profile's wording; an explicit pronouns line if this guesses wrong.
    if re.search(r"\b(female|woman)\b", profile, re.IGNORECASE):
        pronouns = ("her", "she")
    elif re.search(r"\b(male|man)\b", profile, re.IGNORECASE):
        pronouns = ("his", "he")
    else:
        pronouns = ("their", "they")
    known = "\n".join(
        part
        for part in (
            f"The human is called {name}." if name else "",
            brain.llm.memory_block,
            *(f"- {p['fact']}" for p in training.read_pending()),
        )
        if part
    )
    try:
        raw = await asyncio.to_thread(
            brain.llm.propose_memory, user_text, reply_text, known, asked, name, pronouns
        )
    except Exception as exc:
        await hub.debug_log(websocket, "training", f"memory proposal failed: {exc!r}", (time.monotonic() - start) * 1000)
        return
    fact = training.parse_fact(raw)
    if fact and await _compare_with_memories(fact, brain):
        await hub.debug_log(websocket, "training", "dropped a proposal she already remembers", (time.monotonic() - start) * 1000)
        return
    if fact and training.add_pending(fact, user_text):
        await hub.debug_log(websocket, "training", "memory proposed for review", (time.monotonic() - start) * 1000)
        await hub.broadcast(training_state_message())
    else:
        await hub.debug_log(websocket, "training", "nothing worth proposing", (time.monotonic() - start) * 1000)


COMPARE_WITH = 3  # her closest existing memories a new proposal is checked against


async def _compare_with_memories(fact: str, brain: Brain) -> str:
    """A memory that already says this, or "". One pair at a time, closest first; an
    OPPOSITE answer wins, so when unsure a proposal reaches the user rather than being
    dropped. Measured on his real exchanges: 14 of 23 repeats caught, 0 of 8 new ones
    dropped. OPPOSITE used to be shown as a contradiction too, but live it was 0 for 7:
    a 9B calls any two memories on one topic opposite. Best-effort: a failure means no check.
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
    memory server (Hindsight, Mem0) gets the raw exchange straight, and
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
    if not user_text.strip() and not reply_text.strip():
        return  # an image-only turn with nothing the user said -- nothing left worth keeping
    start = time.monotonic()
    if memory.server_backed() and training.read_active():
        await _propose_memory(websocket, user_text, reply_text, brain, asked)
        return
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
