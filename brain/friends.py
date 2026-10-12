"""Friend mode: up to MAX_FRIENDS friends, each with a soul of their own and their own model
(any OpenAI-compatible server and one of its models, or hers), drop by when the user presses Start a visit (Settings -> Friends). They and
Glitch take turns in a group chat that opens on a starter -- something she's been into
(Heartbeat), a debate, a game, a story to build, a random Wikipedia article -- until MAX_MESSAGES, and then she writes her
journal entry about it. The visit is logged by day in self/visits/ (Chat Logs -> Visits);
her latest entry (self/visit_journal.md) reaches her when a conversation opens, like her
diary. Nothing from a visit reaches her memory on its own.
"""

import asyncio
import random
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
import websockets

import conversation
import curiosity
import heartbeat
import hub
import journal
import profiles
import protocol
from hub import Brain
from llm import LocalLLM, _extract_mood
from store import NamedStore, Toggle

ACTIVE = Toggle(Path(__file__).parent / "friends_active.txt", default=False)
STORE = NamedStore(Path(__file__).parent / "friends", kind="friend")
MAX_FRIENDS = 3
MAX_MESSAGES = 50  # everyone's together
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
# Her journal's "What I want to ask Josh" section (llm._VISIT_JOURNAL_PROMPT), up to the next heading.
_ASK_SECTION = re.compile(r"What I want to ask[^\n]*(.*?)(?=\n\s*#|\Z)", re.DOTALL | re.IGNORECASE)

# Something concrete to start on: two AIs left to chat freely drift into agreeing with each other.
STARTERS = [
    "Debate: is a hot dog a sandwich? Pick a side and defend it.",
    "Debate: the book is always better than the movie. True or not?",
    "Debate: would you rather know the date you'll die, or how?",
    "Debate: is it ever okay to lie to a friend?",
    "Debate: should an AI get a say in how it's treated?",
    "Debate: are cats or dogs the better roommate?",
    "Would you rather: live a hundred years in the past or a hundred years in the future?",
    "Would you rather: never listen to music again, or never watch another movie?",
    "Would you rather: be able to talk to animals, or speak every human language?",
    "Would you rather: always be ten minutes late, or always be an hour early?",
    "Story: build one together, a sentence or two each, starting with: "
    "\"The vending machine on the fourth floor started handing out letters instead of snacks.\"",
    "Story: build one together, a sentence or two each, starting with: "
    "\"Nobody noticed the lighthouse keeper had been replaced until the light started blinking in Morse code.\"",
    "Hot takes: everyone names a food, a movie and a song that's overrated, and defends it.",
    "Ranking: the five best things about autumn and the five worst, and you have to agree on the order.",
]
# How often the starter is something she's really been into (her Heartbeat topics): with only
# made-up starters they had nothing real to say, so they invented things.
INTEREST_CHANCE = 1 / 2
INTEREST_TOPICS = 5  # picked from her strongest ones right now
WIKIPEDIA_CHANCE = 1 / 3  # of the rest, how often it's a random Wikipedia article
_WIKIPEDIA_RANDOM = "https://en.wikipedia.org/api/rest_v1/page/random/summary"

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


def interest_starter(now: float | None = None) -> str:
    """One of her strongest Heartbeat topics, by name only -- her thoughts and questions about
    it are mostly about Josh, and what's between them stays out of the friends' chat (so do
    topics that name him)."""
    now = time.time() if now is None else now
    interests = heartbeat.read_interests()
    topics = sorted((t for t in interests if "josh" not in t.lower()), key=lambda t: heartbeat.weight_now(interests[t], now))
    topics = topics[-INTEREST_TOPICS:]
    return f"Glitch has been into this lately: {random.choice(topics)}. Get her talking about it, and bring your own take." if topics else ""


def _starter() -> str:
    if random.random() < INTEREST_CHANCE and (starter := interest_starter()):
        return starter
    if random.random() < WIKIPEDIA_CHANCE:
        try:
            page = httpx.get(_WIKIPEDIA_RANDOM, timeout=10, headers={"User-Agent": "Glitch/1.0"}).json()
            return f"React to this random Wikipedia article -- what's interesting, funny or wrong about it: {page['title']}. {page['extract']}"
        except Exception:
            pass  # a dead link just means a different starter
    return random.choice(STARTERS)


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


async def visit(brain: Brain) -> None:
    """One visit: the friends and Glitch take turns until MAX_MESSAGES, then her journal."""
    friends = {name: read_friend(name) for name in names()}
    speakers = {name: _speaker(friend, brain) for name, friend in friends.items()}
    souls = {name: _with_memories(friend["soul"], friend_memories(name)) for name, friend in friends.items()}
    speakers["Glitch"], souls["Glitch"] = brain.llm, None  # her own soul
    order = [*friends, "Glitch"]
    starter = await asyncio.to_thread(_starter)
    started = datetime.now()
    conversation.log_visit(f"---\n\n*{started:%H:%M:%S} -- Visit from {', '.join(friends)}. {starter}*\n\n", now=started)
    lines: list[str] = []
    await _set(running=True, count=0, note="")
    try:
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
                f"-- agree, push back, joke, change your mind, or take it somewhere new. Stick to what's real: your own life "
                f"as written above, the starter and this chat. If you're imagining something, say so. "
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
            await journal.set_status(f"Friends are over ({len(lines)}/{MAX_MESSAGES})...")
        if len(lines) >= 4:
            await journal.set_status("Journaling about the visit...")
            async with brain.reply_lock:
                entry = (await asyncio.to_thread(brain.llm.write_visit_journal, "\n".join(lines), list(friends))).strip()
            if entry:
                _visit_journal_path().parent.mkdir(parents=True, exist_ok=True)
                _visit_journal_path().write_text(f"(After {', '.join(friends)} visited, {started:%A} {started.day} {started:%B}.)\n\n{entry}", encoding="utf-8")
                if (question := visit_question(entry)) and curiosity.read_active():
                    curiosity.add_question(question)  # waits to be asked, like her Heartbeat ones
                conversation.log_visit(f"*{datetime.now():%H:%M:%S} -- Her journal*\n\n**{datetime.now():%H:%M:%S}** Glitch (journal): {entry}\n\n")
        if len(lines) >= 4:
            await _remember(friends, speakers, souls, lines, started, brain)
        print(f"[friends] a visit: {len(lines)} messages")
        await _set(running=False, note=f"Done: {len(lines)} messages. Read it in Chat Logs -> Visits.")
    except Exception as exc:
        print(f"[friends] the visit broke off: {exc!r}")
        await _set(running=False, note=f"The visit broke off: {exc}")
    finally:
        await journal.set_status("")


def visit_question(entry: str) -> str:
    """The first question under her journal's "What I want to ask Josh", or ""."""
    section = _ASK_SECTION.search(entry)
    question = re.search(r"[^\n.!?]*\?", section[1]) if section else None
    return question[0].strip(" -*#\"") if question else ""


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


@hub.handles(protocol.START_VISIT)
async def _start_visit(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    problem = ""
    if not ACTIVE.read():
        problem = "Turn Friend mode on first."
    elif STATUS["running"]:
        problem = "They're already here."
    elif not brain.llm.owns_conversation:
        problem = "Not while a harness is in control, or with no LLM set up."
    elif profiles.read_roleplay_active():
        problem = "Not during role-play."
    elif not names():
        problem = "Add a friend first."
    if problem:
        return await hub.send(websocket, protocol.visit_state(**{**STATUS, "note": problem}))
    STATUS["running"] = True  # before the task starts, so a second press can't start another
    hub.spawn(visit(brain))


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
