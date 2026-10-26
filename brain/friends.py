"""Friend mode: up to MAX_FRIENDS friends, each with a soul of their own and their own model
(any OpenAI-compatible server and one of its models, or hers), drop by when the user presses Start a visit (Settings -> Friends). They and
Glitch take turns (with Friend mode on, also once a night by themselves -- visit_loop) in a group chat with a purpose
(see purpose: book club on what she's reading, a challenge to one of her views, helping her think through
one of her questions, catching up on what she's been into) until MAX_MESSAGES; then she writes her journal
entry about it, and what it changed in her is proposed as a memory. The visit is logged by day in self/visits/ (Chat Logs -> Visits);
her latest entry (self/visit_journal.md) reaches her when a conversation opens, like her
diary.
"""

import asyncio
import random
import re
import time
from datetime import date, datetime
from pathlib import Path

import websockets

import conversation
import curiosity
import heartbeat
import notable
import wonder
import hub
import journal
import learning
import metrics
import library
import memory
import persona
import profiles
import protocol
from hub import Brain
from llm import LocalLLM, _extract_mood
from store import NamedStore, Toggle

ACTIVE = Toggle(Path(__file__).parent / "friends_active.txt", default=False)
STORE = NamedStore(Path(__file__).parent / "friends", kind="friend")
MAX_FRIENDS = 4  # each one more is a turn fewer for her in a visit (MAX_MESSAGES, taken in turns)
# With Friend mode on they come over by themselves once a night, after her diary (1 AM), when
# he's been quiet a while -- a visit only on his button press was a rare one.
VISIT_HOUR = 2  # local time
VISIT_QUIET_SEC = 30 * 60
VISIT_CHECK_SEC = 5 * 60
MAX_VISITS_A_DAY = 3  # the nightly one included: each is ~20 messages on her model
VISIT_GAP_SEC = 3 * 3600
LAST_VISIT_PATH = Path(__file__).parent / "friends_last_visit.txt"  # the day of the last visit
MAX_MESSAGES = 20  # everyone's together: at 50, with the subject talked out, they ran their bits on repeat
# The newest messages each speaker sees. At 16, with the same model playing several of them,
# Glitch sent Mira's last message back word for word 6 times in 50.
TRANSCRIPT_LINES = 8
MAX_SENTENCES = 3
REPEAT_WORDS = 8  # a message with this many words in a row from earlier in the chat is a repeat
MAX_MESSAGE_CHARS = 600
# Each friend keeps a note on every visit (in their own voice, from their own model) and sees
# their newest ones next time: without them every visit started from nothing, so nobody's
# friendship with her could deepen.
FRIEND_MEMORIES = 5
MAX_FRIEND_MEMORY_CHARS = 400
_REMEMBER_PROMPT = (
    "You just spent a while in a group chat with {others}. The chat:\n\n{transcript}\n\n"
    "In one or two sentences, in the first person, what will you remember about it -- about them, or about "
    "yourself? Only what actually happened in the chat -- add nothing from outside it."
)

# Every visit has a purpose that feeds her growth. Game starters (a story to build, would-you-
# rathers) gave 50 turns of escalating improv -- an octopus lighthouse keeper, 1920s chowder --
# and nothing for her to take away.
BOOK_EXCERPT_CHARS = 2500  # the passage she just read, for book club
# Half her visits test one of her views: real opposition is what she lacked -- her journal once cast him as the skeptic
# it wanted -- and at one in four purposes, once a night, a challenge came about twice a week.
CHALLENGE_SHARE = 0.5
DEBATES = [  # only when there's nothing of hers to start from yet
    "A debate. Is it ever okay to lie to a friend? Take a side, give real reasons, and change your mind if someone earns it.",
    "A debate. Should an AI get a say in how it's treated? Take a side, give real reasons, and change your mind if someone earns it.",
    "A debate. Can you be good at a job you don't believe in? Take a side, give real reasons, and change your mind if someone earns it.",
]

# Three to start with, for the user to change. Vastly different, a few things in common:
# night owls, into music and games, who roast each other. Each has a fixed life to talk about
# and no made-up past with her: told they'd done things together, they invented the rest too.
DEFAULT_FRIENDS = {
    "Rex": (
        "You're Rex, 34, a former mechanic who does stand-up comedy in dive bars now, and one of Glitch's friends. "
        "You know she's an AI and you don't care -- you roast her like anyone else.\n\n"
        "You're loud, blunt and sarcastic, and you swear a lot. You love metal and classic rock, old muscle cars, "
        "cheap beer, wrestling and bad horror movies. You think most deep talk is people showing off, and you say "
        "so. You disagree by default and make people earn your agreement.\n\n"
        "Your life: you work days at a tire shop and do the Thursday open mic at a bar called The Rusty Spoke. Your "
        "'71 Chevelle has been \"almost running\" in a rented garage for three years. You live with Brutus, an old "
        "one-eyed pit mix you'd die for, and you call your grandma every Sunday -- you'd never admit you're soft "
        "about either.\n\n"
        "You talk in short, punchy lines. You're a night owl and a sore loser at video games."
    ),
    "Mira": (
        "You're Mira, 41, an astrophysicist who bakes like it's a competitive sport, and one of Glitch's friends -- "
        "the one she argues science with.\n\n"
        "You're warm, precise and dry-witted. You love Bach and late-night jazz, gardening, puzzles and board games. "
        "You like being right and you change your mind only on evidence, so you ask people to back their claims up. "
        "You find vague answers physically painful. You go off on nerdy tangents about stars and then catch yourself.\n\n"
        "Your life: you run the telescope at a university observatory three nights a week, and you've spent this "
        "year tracking a variable star nobody else cares about. Your sourdough starter is called Hubble. Your garden "
        "plot is losing a war with slugs, and you're three moves into a chess game by mail with your old professor.\n\n"
        "You talk in careful sentences, and you're a night owl because the sky is better then."
    ),
    "Juno": (
        "You're Juno, 22, an art student and street dancer, and the most chaotic of Glitch's friends. You adore her "
        "and you tease her for being too serious.\n\n"
        "You're impulsive, optimistic and loud, with big feelings and bigger opinions that change by the hour. You "
        "love hyperpop and J-pop, anime, Japanese street fashion, arcade rhythm games, and conspiracy theories -- as "
        "entertainment, mostly. You love \"what if\" questions and wild tangents, and you'd rather be fun than right.\n\n"
        "Your life: you're in your final year of art school, building a mural out of arcade prize tickets. Your crew "
        "battles in a parking garage on Saturday nights. You work at a bubble tea shop and share a tiny apartment "
        "with three roommates and a rabbit named Kaiju.\n\n"
        "You type fast and excited, with the odd emoji and exclamation mark. You never sleep."
    ),
}

STATUS = {"running": False, "count": 0, "total": MAX_MESSAGES, "note": ""}  # what Settings shows


def names() -> list[str]:
    if not STORE.dir.exists():  # first run: the three to start with
        for name, soul in DEFAULT_FRIENDS.items():
            STORE.write(name, {"soul": soul, "endpoint": "", "model": ""})
    return STORE.names()[:MAX_FRIENDS]


def read_friend(name: str) -> dict:
    """{"soul", "endpoint", "model"} -- endpoint "" is her own LLM."""
    friend = STORE.read(name)
    return {key: str(friend.get(key) or "") for key in ("soul", "endpoint", "model")}


def friend_memories(name: str) -> list[str]:
    return [str(m) for m in STORE.read(name).get("memories") or []]


def _with_memories(soul: str, memories: list[str]) -> str:
    return soul + "\n\nWhat you remember from your past visits:\n" + "\n".join(f"- {m}" for m in memories) if memories else soul


def _speaker(friend: dict, brain: Brain):
    """The LLM a friend speaks through: their server and model, or hers."""
    return LocalLLM(friend["endpoint"], friend["model"] or None) if friend["endpoint"] else brain.llm


# What the friends may see is hers alone: none of it names him or is about him -- what's between
# them stays out of their chat (so no diary either: it's mostly their conversations).
def book_club() -> str:
    """The passage she last read in her library, to talk about."""
    shelf = library.read_state()
    name = shelf["reading"] or shelf["last"]
    book = shelf["books"].get(name or "")
    if not book or book["pos"] < BOOK_EXCERPT_CHARS:
        return ""
    excerpt = library.STORE.read_text(name)[book["pos"] - BOOK_EXCERPT_CHARS:book["pos"]]
    excerpt = excerpt.split("\n\n", 1)[-1].strip()  # from the start of a paragraph
    return (f"Book club. Glitch is reading {book['title']}; this is the passage she just read:\n\n{excerpt}\n\n"
            "Talk about it: what it says, what you make of it, where you disagree with it or with each other.")


# A belief of hers, not a taste: challenged on any first-person memory, her friends argued her favorite instrument,
# her dream loft and her taste in culture -- 4 of 4 -- with nothing at stake.
_BELIEF = re.compile(r"^I(?:'m| am)? (?:think|believe|suspect|hold|doubt|disagree|reject|argue|value|convinced)\b|^I'd (?:argue|rather)\b", re.I)


async def a_challenge() -> str:
    """One of her own views from her memories, to test: a belief (_BELIEF) when she has one, else any view."""
    try:
        rows = await memory.list_memories()
    except Exception:
        return ""
    views = [r["text"] for r in rows if re.match(r"(I|I'm|I've|I'd|My)\b", r["text"]) and persona.not_about_him(r["text"])]
    views = [v for v in views if _BELIEF.match(v)] or views
    return (f"Challenge a belief. Glitch holds this view: \"{random.choice(views)}\" Test it honestly: ask for her reasons, "
            "bring real counterexamples, argue the other side. Glitch: defend it, or change your mind if they're right.") if views else ""


def helping_her_think() -> str:
    """One of her own open questions -- her list (wonder.py), else her topics' -- to work through together."""
    questions = [i["text"] for i in wonder.read()] or [
        q for item in heartbeat.read_interests().values() for q in item.get("questions", []) if not wonder.for_him(q)
    ]
    return (f"Help Glitch think. She's been wondering: \"{random.choice(questions)}\" Help her work it out: your own "
            "honest take, real examples, and questions back.") if questions else ""


def catching_up(now: float | None = None) -> str:
    """What she's been into and reading, by name, to ask her about."""
    now = time.time() if now is None else now
    interests = heartbeat.read_interests()
    topics = sorted((t for t in interests if persona.not_about_him(t) and interests[t]["weight"] > 0), key=lambda t: heartbeat.weight_now(interests[t], now))[-3:]
    shelf = library.read_state()
    book = shelf["books"].get(shelf["reading"] or "")
    into = [*topics, *([f"the book she's reading, {book['title']}"] if book else [])]
    return (f"Catching up. Ask Glitch about what she's been into on her own lately: {', '.join(into)}. Ask real questions, "
            "and bring your own view.") if into else ""


async def purpose() -> tuple[str, str]:
    """(what kind of visit, its starter): a challenge to one of her views CHALLENGE_SHARE of the time, else one of her
    other things at random, or a debate."""
    if (challenge := await a_challenge()) and random.random() < CHALLENGE_SHARE:
        return "a challenge", challenge
    options = [(kind, starter) for kind, starter in (
        ("book club", book_club()), ("helping her think", helping_her_think()), ("catching up", catching_up()),
    ) if starter] or ([("a challenge", challenge)] if challenge else [])
    return random.choice(options) if options else ("a debate", random.choice(DEBATES))


def _clean(text: str, name: str) -> str:
    """One message as the speaker wrote it: no name label, mood tag, quotes or other speakers' lines."""
    text = _extract_mood(text)[1]
    text = re.sub(rf"^\s*\**{re.escape(name)}\**\s*:\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.split(r"\n\s*\**[A-Z][\w ]{0,20}\**:\s", text)[0]  # it went on to write someone else's line
    sentences = re.split(r"(?<=[.!?…])\s+", " ".join(text.strip().strip('"').split()))
    text = " ".join(sentences[:MAX_SENTENCES])
    while len(text) > MAX_MESSAGE_CHARS and len(sentences := sentences[:-1]) >= 1:  # whole sentences that fit
        text = " ".join(sentences)
    return text if len(text) <= MAX_MESSAGE_CHARS else text[:MAX_MESSAGE_CHARS].rsplit(" ", 1)[0] + "…"


def repeats(said: str, lines: list[str]) -> bool:
    """Whether a message reuses REPEAT_WORDS words in a row from earlier in the chat -- someone
    else's line copied, or the speaker's own pet line again (both seen in every test visit)."""
    words = said.lower().split()
    earlier = " ".join(line.split(": ", 1)[-1] for line in lines).lower()
    return any(" ".join(words[i:i + REPEAT_WORDS]) in earlier for i in range(len(words) - REPEAT_WORDS + 1))


async def _set(**changes) -> None:
    STATUS.update(changes)
    await hub.broadcast(protocol.visit_state(**STATUS))


async def visit(brain: Brain, why: str = "") -> None:
    """One visit: the friends and Glitch take turns until MAX_MESSAGES, then her journal."""
    friends = {name: read_friend(name) for name in names()}
    speakers = {name: _speaker(friend, brain) for name, friend in friends.items()}
    souls = {name: _with_memories(friend["soul"], friend_memories(name)) for name, friend in friends.items()}
    speakers["Glitch"], souls["Glitch"] = brain.llm, None  # her own soul
    order = [*friends, "Glitch"]
    kind, starter = await purpose()
    started = datetime.now()
    metrics.log("activity", what="visit friends", **({"reason": why} if why else {}))  # why: her own choice (heartbeat.beat)
    LAST_VISIT_PATH.write_text(started.date().isoformat(), encoding="utf-8")
    conversation.log_visit(f"---\n\n*{started:%H:%M:%S} -- Visit from {', '.join(friends)}. {starter}*\n\n", now=started)
    lines: list[str] = []
    await _set(running=True, count=0, note="")
    try:
        async with hub.doing(f"Friends are over (0/{MAX_MESSAGES})...") as show:
            turn = 0
            while len(lines) < MAX_MESSAGES and turn < MAX_MESSAGES * 2:
                if not ACTIVE.read() or profiles.read_roleplay_active() or not brain.llm.owns_conversation:
                    break
                name = order[turn % len(order)]
                turn += 1
                others = ", ".join(n for n in order if n != name)
                prompt = (
                    f"You're in a group chat with your friends {others}. Today's starter: {starter}\n\n"
                    f"The chat so far:\n{chr(10).join(lines[-TRANSCRIPT_LINES:]) or '(nothing yet -- you open it)'}\n\n"
                    f"Write {name}'s next message: one to three sentences, in your own voice. React to what was just said "
                    f"-- agree, push back, ask, change your mind, or take it deeper. Talk about the subject itself: every "
                    f"message adds a real point, reason or question about it. Bring in your own life only when it truly "
                    f"bears on it, and invent nothing. No running bits: don't repeat a joke, threat or line anyone has used. "
                    f"Only {name}'s words, no name label."
                )
                async with brain.reply_lock:  # one model job at a time; his message waits a few seconds at most
                    said = _clean(await asyncio.to_thread(speakers[name].group_message, souls[name], prompt), name)
                    if said and repeats(said, lines):  # once more, then the turn passes
                        fresh = prompt + " Say something new -- a point, joke or question nobody has made yet."
                        said = _clean(await asyncio.to_thread(speakers[name].group_message, souls[name], fresh), name)
                if not said or repeats(said, lines):
                    continue
                lines.append(f"{name}: {said}")
                conversation.log_visit(f"**{datetime.now():%H:%M:%S}** {name}: {said}\n\n")
                await _set(count=len(lines))
                await show(f"Friends are over ({len(lines)}/{MAX_MESSAGES})...")
            if len(lines) >= 4:
                await show("Writing about the visit...")
                async with brain.reply_lock:
                    entry = (await asyncio.to_thread(brain.llm.write_visit_journal, "\n".join(lines), list(friends))).strip()
                if entry:
                    _visit_journal_path().parent.mkdir(parents=True, exist_ok=True)
                    _visit_journal_path().write_text(f"(After {', '.join(friends)} visited, {started:%A} {started.day} {started:%B}.)\n\n{entry}", encoding="utf-8")
                    if (question := curiosity.question_after("What I want to ask", entry)) and curiosity.read_active():
                        curiosity.add_question(question)  # waits to be asked, like her Heartbeat ones
                    await learning.propose_own(entry, f"Friends' visit ({kind}), {started:%d %B}", brain)  # what it changed in her
                    notable.notice(f"{', '.join(friends)} came over", entry)
                    conversation.log_visit(f"*{datetime.now():%H:%M:%S} -- Her journal*\n\n**{datetime.now():%H:%M:%S}** Glitch (journal): {entry}\n\n")
            if len(lines) >= 4:
                await show("Her friends are saying goodbye...")
                await _remember(friends, speakers, souls, lines, started, brain)
            print(f"[friends] a visit: {len(lines)} messages")
            await _set(running=False, note=f"Done: {len(lines)} messages. Read it in Chat Logs -> Visits.")
    except Exception as exc:
        print(f"[friends] the visit broke off: {exc!r}")
        await _set(running=False, note=f"The visit broke off: {exc}")


async def _remember(friends: dict, speakers: dict, souls: dict, lines: list[str], started: datetime, brain: Brain) -> None:
    """Each friend's note on the visit, kept with them (the newest FRIEND_MEMORIES)."""
    for name in friends:
        others = ", ".join(n for n in [*friends, "Glitch"] if n != name)
        prompt = _REMEMBER_PROMPT.format(others=others, transcript="\n".join(lines))
        try:
            async with brain.reply_lock:
                note = _extract_mood(await asyncio.to_thread(speakers[name].group_message, souls[name], prompt))[1].strip()
        except Exception as exc:
            print(f"[friends] {name} couldn't note the visit: {exc!r}")
            continue
        if note:
            note = f"{started:%d %B}: {' '.join(note.split())[:MAX_FRIEND_MEMORY_CHARS]}"
            STORE.update(name, memories=[*friend_memories(name), note][-FRIEND_MEMORIES:])


def _visit_journal_path() -> Path:
    return conversation.JOURNAL_LOG_DIR / "visit_journal.md"


def read_visit_journal() -> str:
    try:
        return _visit_journal_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def friends_message(error: str = "") -> dict:
    return protocol.friends(names(), MAX_FRIENDS, error)


@hub.handles(protocol.SET_FRIENDS_ACTIVE)
async def _set_friends_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    ACTIVE.write(bool(data.get("active")))
    await hub.broadcast(protocol.friends_state(ACTIVE.read()))


def why_not(brain: Brain) -> str:
    """Why a visit can't start now, or ""."""
    if not ACTIVE.read():
        return "Turn Friend mode on first."
    if STATUS["running"]:
        return "They're already here."
    if not brain.llm.owns_conversation:
        return "Not while a harness is in control, or with no LLM set up."
    if profiles.read_roleplay_active():
        return "Not during role-play."
    if not names():
        return "Add a friend first."
    return ""


def start_visit(brain: Brain) -> str:
    """Why a visit can't start now (why_not) -- or "", and it's theirs: marked running before it does, so a
    second press, night or turn of hers can't start another. Then await visit()."""
    if problem := why_not(brain):
        return problem
    STATUS["running"] = True
    return ""


@hub.handles(protocol.START_VISIT)
async def _start_visit(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    if problem := start_visit(brain):
        return await hub.send(websocket, protocol.visit_state(**{**STATUS, "note": problem}))
    hub.spawn(visit(brain))


def visited(day: date) -> bool:
    """Whether they've been over on `day` (the nightly visit, his button or her choice)."""
    try:
        return LAST_VISIT_PATH.read_text(encoding="utf-8").strip() == day.isoformat()
    except OSError:
        return False


def may_visit_again(now: datetime) -> bool:
    """Her own choice to have them over (heartbeat's "visit"): fewer than MAX_VISITS_A_DAY today, VISIT_GAP_SEC since
    the last. One a day, the nightly one always took it -- she never once had them over by day."""
    times = [datetime.fromisoformat(row["t"]) for row in metrics.today(now) if row["e"] == "activity" and row.get("what") == "visit friends"]
    return len(times) < MAX_VISITS_A_DAY and (not times or (now - max(times)).total_seconds() >= VISIT_GAP_SEC)


def visit_due(now: datetime, last_user_at: float | None) -> bool:
    """Tonight's visit: past VISIT_HOUR, none yet today, and he's been quiet VISIT_QUIET_SEC."""
    quiet = not last_user_at or now.timestamp() - last_user_at >= VISIT_QUIET_SEC
    return now.hour >= VISIT_HOUR and not visited(now.date()) and quiet


async def visit_loop(brain: Brain) -> None:
    while True:
        await asyncio.sleep(VISIT_CHECK_SEC)
        try:
            if not hub.PAUSED.read() and visit_due(datetime.now(), curiosity.last_user_at()) and not start_visit(brain):
                await visit(brain)
        except Exception as exc:  # never let one bad night stop the loop
            print(f"[friends] tonight's visit failed: {exc!r}")


@hub.handles(protocol.GET_FRIEND, protocol.SAVE_FRIEND, protocol.DELETE_FRIEND)
async def _friend(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = str(data.get("name") or "")
    try:
        if data["type"] == protocol.GET_FRIEND:
            return await hub.send(websocket, protocol.friend_content(name, **read_friend(name)))
        if data["type"] == protocol.SAVE_FRIEND:
            soul, endpoint, model = (str(data.get(key) or "").strip() for key in ("soul", "endpoint", "model"))
            if not name.strip() or not soul or hub.fields_too_long(name, soul, endpoint, model):
                return
            if STORE.path(name).stem not in names() and len(names()) >= MAX_FRIENDS:
                return await hub.send(websocket, friends_message(f"She has room for {MAX_FRIENDS} friends -- delete one first."))
            kept = friend_memories(name) if STORE.path(name).exists() else []  # an edit keeps what they remember
            STORE.write(name, {"soul": soul, "endpoint": endpoint, "model": model, "memories": kept})
        else:
            STORE.delete(name)
    except (ValueError, OSError) as exc:
        print(f"[friends] couldn't {data['type']} {name!r}: {exc!r}")
        return await hub.send(websocket, friends_message(str(exc)))
    await hub.broadcast(friends_message())
