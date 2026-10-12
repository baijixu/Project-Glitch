"""Heartbeat: her mind wandering while nobody's talking to her (Settings toggle, off
by default).

Once he's been quiet for IDLE_SEC, every TICK_SEC (due_at) a few random fragments of what she
has drift past -- lines from her soul, her diary, what he said lately, her memories, a
topic already on her mind -- and she says what, if anything, pulls at her and how
strongly (llm.daydream). Each pull adds weight to its topic (self/interests.json);
weight fades by half every HALF_LIFE_HOURS, so only what keeps coming back builds up.
A topic that passes LAND_AT lands: she turns it over properly (llm.focus) and writes a
page about it (self/interests/<topic>.md). Every thought, and each page, also goes in
the day's thoughts log (self/thoughts/, Settings -> Chat Logs -> Thoughts).

With a book in her library (library.py) unfinished, half the ticks she reads instead (read_pages):
the next pages, and a note on them that reaches her as a conversation opens. A landing topic is
looked up on the web first (with web search on), and the topics that landed this week reach
her as a conversation opens too (into_lately); the rest of her wandering doesn't.
"""

import asyncio
import json
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import websockets

import conversation
import curiosity
import hub
import learning
import library
import memory
import persona
import profiles
import protocol
import souls
import web_search
from hub import Brain
from names import sanitize_name
from store import Toggle

ACTIVE = Toggle(Path(__file__).parent / "heartbeat_active.txt", default=False)

# ponytail: hand-picked numbers -- tune once there are a few weeks of her thoughts to look at.
TICK_SEC = 20 * 60
IDLE_SEC = 20 * 60  # since his last message
HALF_LIFE_HOURS = 48
LAND_AT = 20  # a pull is 1-10: about three strong pulls within a day or two
MAX_INTERESTS = 30  # the faintest are forgotten past this
KEEP_THOUGHTS = 5  # per topic, the newest
# How often a topic already on her mind drifts past again. Every time, and she thought about
# nothing else (8 of 8 in a test); a landed one doesn't -- she's had her think about it.
RUMINATE_CHANCE = 1 / 3
CHECK_SEC = 60  # how often the loop looks at the clock
READ_CHANCE = 1 / 2  # with a book on her shelf unfinished, how often a tick is reading instead of wandering
LATELY_DAYS = 7  # how long a landed topic counts as what she's been into
READ_PULL = 5  # a sitting with a book weighs on her like a middling thought: four in two days and it lands
_last_beat = time.time()  # her last thought (or Brain's start: the first comes TICK_SEC in)
_last_timer: dict | None = None  # the heartbeat_timer last sent to everyone

# Her soul's sections about who she is -- not the ones about how the app works, her
# looks, or how to talk.
SOUL_SECTIONS = ("Who you are", "What you love", "What you can't stand", "Your own mind")
RECENT_DAYS = 3  # his chats and her diary from this far back
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_HIS_LINE = re.compile(r"^\*\*[\d:]+\*\* You: (.+)$", re.MULTILINE)


def _interests_path() -> Path:
    return conversation.JOURNAL_LOG_DIR / "interests.json"


def read_interests() -> dict:
    """{topic: {weight, at (time.time()), thoughts, questions, landed}}."""
    try:
        data = json.loads(_interests_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_interests(interests: dict) -> None:
    path = _interests_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(interests, indent=1, ensure_ascii=False), encoding="utf-8")


def weight_now(item: dict, now: float) -> float:
    return item["weight"] * 0.5 ** ((now - item["at"]) / 3600 / HALF_LIFE_HOURS)


_SMALL_WORDS = {"the", "a", "an", "of", "and", "at", "in", "on", "my", "your", "his", "her"}


def _words(topic: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", topic.lower())) - _SMALL_WORDS


def same_topic(a: str, b: str) -> bool:
    """"The warehouse" and "warehouse at night" are one topic; "octopus dreams" and
    "octopus intelligence" aren't. ponytail: word overlap -- a synonym is a new topic."""
    x, y = _words(a), _words(b)
    return bool(x and y) and (x <= y or y <= x or len(x & y) / len(x | y) >= 0.5)


def add_pull(interests: dict, topic: str, pull: int, thought: str, question: str, now: float) -> tuple[str, bool]:
    """Adds one pull to `topic` (an existing one if it's the same_topic). Returns the
    topic's name and whether it landed just now."""
    name = next((t for t in interests if same_topic(t, topic)), topic)
    item = interests.setdefault(name, {"weight": 0.0, "at": now, "thoughts": [], "questions": [], "landed": None})
    item["weight"] = weight_now(item, now) + pull
    item["at"] = now
    item["thoughts"] = [*item["thoughts"], thought][-KEEP_THOUGHTS:]
    if question and question not in item["questions"]:
        item["questions"] = [*item["questions"], question][-KEEP_THOUGHTS:]
    landed = item["landed"] is None and item["weight"] >= LAND_AT
    if landed:
        item["landed"] = now
    for faint in sorted(interests, key=lambda t: weight_now(interests[t], now))[: max(0, len(interests) - MAX_INTERESTS)]:
        del interests[faint]
    return name, landed


def parse_daydream(raw: str) -> dict | None:
    """The model's JSON answer (llm.daydream) as {thought, topic, pull, question}, or None
    when nothing pulled at her or it can't be read."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        return None
    topic, thought = data.get("topic"), data.get("thought")
    if not isinstance(topic, str) or not topic.strip() or not isinstance(thought, str) or not thought.strip():
        return None
    try:
        pull = min(10, max(1, int(data.get("pull"))))
    except (TypeError, ValueError):
        pull = 1
    question = data.get("question") if isinstance(data.get("question"), str) else ""
    return {"topic": topic.strip()[:60], "thought": thought.strip()[:500], "pull": pull, "question": question.strip()[:300]}


# -- What drifts past ----------------------------------------------------------


def _sentences(text: str) -> list[str]:
    return [s.strip("-* ").strip() for line in text.splitlines() for s in _SENTENCE.split(line) if len(s.strip("-* ").strip()) > 15]


def soul_sentences() -> list[str]:
    try:
        text = souls.SOUL_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    sections = re.split(r"^#+\s*(.+)$", text, flags=re.MULTILINE)  # [before, heading, body, heading, body...]
    return [s for heading, body in zip(sections[1::2], sections[2::2]) if heading.strip() in SOUL_SECTIONS for s in _sentences(body)]


def _recent_days(now: datetime) -> list[str]:
    return [f"{now.date() - timedelta(days=n):%Y-%m-%d}" for n in range(RECENT_DAYS + 1)]


def diary_sentences(now: datetime) -> list[str]:
    found = []
    for day in _recent_days(now):
        try:
            found += _sentences(conversation.read_log(conversation.JOURNAL, day).split("\n", 1)[-1])  # past its heading
        except ValueError:
            pass
    return found


def his_lines(now: datetime) -> list[str]:
    found = []
    for day in _recent_days(now):
        try:
            found += [line for line in _HIS_LINE.findall(conversation.read_log(conversation.MAIN, day)) if len(line.split()) >= 4]
        except ValueError:
            pass
    return found


async def fragments(now: datetime, interests: dict) -> list[str]:
    """One random line from each kind of thing she has, labeled, in random order."""
    name = persona.user_name() or "He"
    kinds = [
        ("About you: ", soul_sentences()),
        ("From your diary: ", diary_sentences(now)),
        (f"{name} said: ", his_lines(now)),
    ]
    if memory.read_memory_active():
        try:
            kinds.append(("You remember: ", await memory.read_entries()))
        except Exception:
            pass  # a memory server that's down just means one fragment fewer
    open_topics = [t for t, item in interests.items() if not item["landed"]]
    if open_topics and random.random() < RUMINATE_CHANCE:
        kinds.append(("On your mind lately: ", [random.choices(open_topics, [interests[t]["weight"] for t in open_topics])[0]]))
    picked = [label + random.choice(lines) for label, lines in kinds if lines]
    random.shuffle(picked)
    return picked


# -- The beat ------------------------------------------------------------------


def due_at() -> float:
    """When her next thought is due: TICK_SEC after her last one, and IDLE_SEC after his last message."""
    return max(_last_beat + TICK_SEC, (curiosity.last_user_at() or 0) + IDLE_SEC)


def _free(brain: Brain, now: float) -> bool:
    return (
        ACTIVE.read()
        and brain.llm.owns_conversation
        and not profiles.read_roleplay_active()
        and not brain.reply_lock.locked()
        and now >= due_at()
    )


def timer_message(brain: Brain) -> dict:
    """Settings -> Heartbeat's countdown."""
    if not ACTIVE.read():
        return protocol.heartbeat_timer("off", None)
    if profiles.read_roleplay_active():
        return protocol.heartbeat_timer("roleplay", None)
    if not brain.llm.owns_conversation:
        return protocol.heartbeat_timer("harness", None)
    return protocol.heartbeat_timer("counting", due_at())


async def broadcast_timer(brain: Brain, only_if_changed: bool = False) -> None:
    global _last_timer
    message = timer_message(brain)
    if only_if_changed and message == _last_timer:
        return
    _last_timer = message
    await hub.broadcast(message)


async def beat(brain: Brain) -> str:
    """One wander: fragments in, maybe a thought out -- and a page, if a topic lands.
    Returns what happened, in a line (for Think now)."""
    global _last_beat
    _last_beat = time.time()
    now = datetime.now()
    shelf = library.read_state()
    if library.unread(shelf) and random.random() < READ_CHANCE:
        return await read_pages(brain, shelf)
    interests = read_interests()
    drifted = await fragments(now, interests)
    if not drifted:
        return "Nothing to think about yet -- no soul, diary, chats or memories to wander through."
    async with brain.reply_lock:  # one model, one job at a time -- his message waits a few seconds at most
        found = parse_daydream(await asyncio.to_thread(brain.llm.daydream, "\n".join(f"- {d}" for d in drifted), persona.user_name() or "he"))
    if not found:
        print("[heartbeat] nothing pulled at her")
        return "Nothing pulled at her this time."
    topic, landed = add_pull(interests, found["topic"], found["pull"], found["thought"], found["question"], time.time())
    _save_interests(interests)
    conversation.log_thought(f"{found['thought']}  \n*{topic} · pull {found['pull']}*", now=now)
    print(f"[heartbeat] a thought about {topic!r} (pull {found['pull']})")
    if landed and await _land(brain, interests, topic, now):
        return f"{found['thought']} -- and {topic} landed: she wrote a page about it (Chat Logs -> Thoughts)."
    return f"{found['thought']} ({topic}, pull {found['pull']})"


def look_up(topic: str) -> str:
    """What a web search says about a topic that's landed, as lines for her page -- "" with web
    search off, and for a topic naming him: a query leaves the house, what's between them doesn't."""
    name = (persona.user_name() or "").lower()
    if not web_search.read_active() or (name and name in topic.lower()):
        return ""
    return "\n".join(f"- {r['title']} ({r['domain']}): {r['snippet']}" for r in web_search.search(topic))


def into_lately(now: float | None = None) -> str:
    """The topics that landed for her in the last LATELY_DAYS, for her prompt as a conversation opens."""
    now = time.time() if now is None else now
    landed = [t for t, item in read_interests().items() if item.get("landed") and now - item["landed"] < LATELY_DAYS * 86400]
    return f"What you've been into on your own lately (you wrote pages on them): {', '.join(landed)}." if landed else ""


async def _land(brain: Brain, interests: dict, topic: str, now: datetime) -> bool:
    """A topic that's landed: her notebook page on it. False if she wrote nothing."""
    thoughts = "\n".join(f"- {t}" for t in interests[topic]["thoughts"] + interests[topic]["questions"])
    found = await asyncio.to_thread(look_up, topic)
    async with brain.reply_lock:
        page = (await asyncio.to_thread(brain.llm.focus, topic, thoughts, persona.user_name() or "him", found)).strip()
    if not page:
        return False
    folder = conversation.JOURNAL_LOG_DIR / "interests"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sanitize_name(topic, kind='topic')}.md").write_text(f"# {topic}\n\n*Landed {now:%A %d %B %Y}*\n\n{page}\n", encoding="utf-8")
    conversation.log_thought(f"💡 **{topic}** landed -- it kept pulling at her, so she wrote about it:\n\n{page}", now=datetime.now())
    print(f"[heartbeat] {topic!r} landed")
    # Her newest question about it waits to be asked (Josh's "question inbox"): only on landing,
    # so a passing thought doesn't fill the short list.
    if interests[topic]["questions"] and curiosity.read_active():
        curiosity.add_question(interests[topic]["questions"][-1])
    await learning.propose_own(page, f"Heartbeat: {topic} landed", brain)  # what she came to think stays with her
    return True


async def read_pages(brain: Brain, shelf: dict) -> str:
    """A sitting with a book from her library: she picks one if she's between books, reads the
    next pages and notes what she makes of them. The book then weighs on her mind like any
    topic, so it can land too."""
    names = library.unread(shelf)
    name = shelf["reading"] if shelf["reading"] in names else names[0]
    if shelf["reading"] not in names and len(names) > 1:
        async with brain.reply_lock:
            raw = await asyncio.to_thread(brain.llm.pick_book, [shelf["books"][n]["title"] for n in names])
        if (pick := re.search(r"\d+", raw)) and 1 <= int(pick[0]) <= len(names):
            name = names[int(pick[0]) - 1]
    book = shelf["books"][name]
    pages, end = library.next_pages(library.STORE.read_text(name), book["pos"])
    async with brain.reply_lock:
        note = (await asyncio.to_thread(brain.llm.read_pages, book["title"], library.percent(book), pages, book["note"])).strip()
    if not note:
        return f"She couldn't get into {book['title']} this time."
    book.update(pos=end, note=note, finished=end >= book["chars"])
    shelf["reading"] = None if book["finished"] else name
    shelf["last"] = name
    library.save_state(shelf)
    await hub.broadcast(library.library_message())  # Settings shows how far she's got
    now = datetime.now()
    conversation.log_thought(f"📖 {note}  \n*{book['title']} · {library.percent(book)}%*", now=now)
    print(f"[heartbeat] she read some of {book['title']!r} ({library.percent(book)}%)")
    interests = read_interests()
    topic, landed = add_pull(interests, book["title"], READ_PULL, note, "", time.time())
    _save_interests(interests)
    if landed:
        await _land(brain, interests, topic, now)
    if book["finished"]:
        await learning.propose_own(f"You just finished {book['title']}. Your note on its last pages: {note}", f"Finished reading {book['title']}", brain)
    return f"She read some of {book['title']} ({library.percent(book)}%): {note}"


async def heartbeat_loop(brain: Brain) -> None:
    while True:
        await asyncio.sleep(CHECK_SEC)
        try:
            if _free(brain, time.time()):
                await beat(brain)
            await broadcast_timer(brain, only_if_changed=True)  # also catches his messages, role-play, a harness
        except Exception as exc:  # never let one bad beat stop the loop
            print(f"[heartbeat] couldn't wander: {exc!r}")


@hub.handles(protocol.THINK_NOW)
async def _think_now(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Settings -> Heartbeat's Think now: one wander right away, whether the toggle is on or he's
    been quiet. Answered to that device with what she thought (heartbeat_note)."""
    if not brain.llm.owns_conversation:
        note = "She can't while a harness is in control, or with no LLM set up."
    elif profiles.read_roleplay_active():
        note = "Not during role-play."
    elif brain.reply_lock.locked():
        note = "She's busy right now -- try again in a moment."
    else:
        try:
            note = await beat(brain)
        except Exception as exc:
            note = f"Couldn't think: {exc}"
    await hub.send(websocket, protocol.heartbeat_note(note))
    await broadcast_timer(brain)


@hub.handles(protocol.SET_HEARTBEAT_ACTIVE)
async def _set_heartbeat_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    ACTIVE.write(bool(data.get("active")))
    await hub.broadcast(protocol.heartbeat_state(ACTIVE.read()))
    await broadcast_timer(brain)
