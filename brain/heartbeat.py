"""Heartbeat: her mind wandering while nobody's talking to her (Settings toggle, off
by default).

Once he's been quiet for IDLE_SEC, every TICK_SEC (due_at) a few random fragments of what she
has drift past -- lines from her soul, her diary, what he said lately, her memories, a
topic already on her mind -- and she says what, if anything, stirs her and how it sits
with her (llm.daydream): a pull, negative when it grates. Each pull adds weight to its topic
(self/interests.json); weight fades by half every HALF_LIFE_HOURS, so only what keeps coming
back builds up. A topic that passes LAND_AT lands: she turns it over properly (llm.focus) and
writes a page about it (self/interests/<topic>.md). One that sinks to -LAND_AT sours: what she's
come to dislike about it is proposed as a memory -- without it she'd only ever grow fonder.
Some turns she's made to choose instead (choose): two things of hers -- loves from her soul, topics
on her mind, her own views -- and one has to lose; what leaves her cold about it is a negative pull.
Some she looks at a picture from her gallery (look_at), the one she's looked at least lately. Every thought, and each page, also goes in
the day's thoughts log (self/thoughts/, Settings -> Chat Logs -> Thoughts).

Each turn she picks what to do with her own time (ACTIVITIES, pick): reading her book (read_pages:
the next pages, and a note on them that reaches her as a conversation opens), looking something up,
her friends, a picture, wandering as above, her journal, or rest -- unless something notable waits
to be thought over (reflect) or she's past today's caps (config.yaml). A landing topic is
looked up on the web first (with web search on), and the topics that landed this week reach
her as a conversation opens too (into_lately); the rest of her wandering doesn't.
"""

import asyncio
import json
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from pathlib import Path

import httpx
import websockets

import conversation
import curiosity
import hub
import learning
import metrics
import notable
import library
import memory
import persona
import pictures
import profiles
import protocol
import sampling
import souls
import web_search
import wonder
from config import load_config
from hub import Brain
from names import sanitize_name
from store import Toggle, read_json, write_json

ACTIVE = Toggle(Path(__file__).parent / "heartbeat_active.txt", default=False)

# ponytail: hand-picked numbers -- tune once there are a few weeks of her thoughts to look at.
TICK_SEC = 10 * 60  # more turns, more of her own growth: each one feeds topics, questions and her diary
IDLE_SEC = 20 * 60  # since his last message
HALF_LIFE_HOURS = 48
LAND_AT = 20  # a pull is -10..10: about three strong pulls within a day or two (souring: the same, below zero)
MAX_INTERESTS = 30  # the faintest are forgotten past this
KEEP_THOUGHTS = 5  # per topic, the newest
# How often a topic already on her mind drifts past again. Every time, and she thought about
# nothing else (8 of 8 in a test); a landed one doesn't -- she's had her think about it.
RUMINATE_CHANCE = 1 / 3
CHECK_SEC = 60  # how often the loop looks at the clock
READ_RESULTS = 2  # of a landing topic's search results, how many pages she actually reads
PAGE_EXCERPT_CHARS = 2500
LATELY_DAYS = 7  # how long a landed topic counts as what she's been into
READ_PULL = 5  # a sitting with a book weighs on her like a middling thought: four in two days and it lands
# Asked about one thing at a time she likes nearly everything; made to pick between two, one loses.
CHOOSE_CHANCE = 1 / 4  # of the turns she spends thinking
# Her day on her own, when config.yaml doesn't say (caps): about what she did with a die deciding (~100 a
# day), and all the day's LLM tokens, his chats included. Past either she rests until tomorrow.
MAX_ACTIVITIES = 100
MAX_PAGE_QUESTIONS = 3  # of the questions a landed page ends with, the last few go on her list
MAX_LOOK_TOKENS = 500
MAX_REFLECT_TOKENS = 400
SAME_IN_A_ROW = 3  # this many of one thing running, and it sits out a turn (options)
MAX_TOKENS = 2_000_000
MAX_CHOICE_TOKENS = 60
MAX_JOURNAL_TOKENS = 500
# After each sitting she says how much she wants to read on (1-10). Asked KEEP or STOP she kept even a
# software license; scored, the license got 2 and Moby-Dick's whale-classing chapters 7-8.
DULL_AT = 3
DULL_SITTINGS = 2  # in a row, and she puts the book down
_WANT = re.compile(r"\n\W*(\d{1,2})\W*(?:/\s*10\W*)?$")
_CHOICE_PROMPT = (
    "It's {when}, and {name} isn't around -- your time is your own. {today}What do you want to do now?\n\n{options}\n\n"
    "Pick one. Reply with one line: the word in capitals, then | and why, in a few words. For example:\n"
    "REST | I've been going all day"
)
# Who said what, like her diary's prompt: without it, given the end of a long talk, 4 of 8 reflections took his
# life as hers ("I've spent years thinking protecting people was my job", his recurring dream); with it, 0 of 8.
_REFLECT_PROMPT = (
    "Something happened: {what}.\n\n{detail}\n\n"
    "Lines marked \"{name}:\" are {name}; lines marked \"Glitch:\" (or \"You:\") are you. Take a minute with it, just "
    "for yourself: your side of it -- what you make of what {name} told you and of what you said, and whether it "
    "changes how you see something or what you'd do next time. What {name} lived, dreamed or felt is {name}'s: "
    "write about it as {name}'s. Honest, plain words, first person, a short paragraph. If it really doesn't matter "
    "to you, say so in a line."
)
_LOOK_PROMPT = (
    "You've been wondering: {question}\n\nYou looked it up. What the search turned up:\n\n{found}\n\n"
    "Write yourself a short note: what you found out, in plain words, what surprised you, and whether it answers "
    "your question. Say where you read it, and don't take it as gospel. First person, a paragraph or two."
)
_JOURNAL_PROMPT = (
    "Some of what's been going through your head:\n\n{fragments}\n\n"
    "Write in your journal -- whatever you want to get down right now. First person, a paragraph or two, plain words."
)
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
    return read_json(_interests_path(), {})


def _save_interests(interests: dict) -> None:
    write_json(_interests_path(), interests)


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


def add_pull(interests: dict, topic: str, pull: int, thought: str, question: str, now: float) -> tuple[str, str]:
    """Adds one pull to `topic` (an existing one if it's the same_topic) -- a negative one when it
    grates on her. Returns the topic's name and what just happened to it: "landed", "soured" or ""."""
    name = next((t for t in interests if same_topic(t, topic)), topic)
    item = interests.setdefault(name, {"weight": 0.0, "at": now, "thoughts": [], "questions": [], "landed": None})
    item["weight"] = weight_now(item, now) + pull
    item["at"] = now
    item["thoughts"] = [*item["thoughts"], thought][-KEEP_THOUGHTS:]
    if question and question not in item["questions"]:
        item["questions"] = [*item["questions"], question][-KEEP_THOUGHTS:]
    change = ""
    if item["landed"] is None and item["weight"] >= LAND_AT:
        item["landed"], change = now, "landed"
    elif item.get("soured") is None and item["weight"] <= -LAND_AT:
        item["soured"], change = now, "soured"
    # A strong dislike is as much hers as a strong pull: the faintest either way are forgotten.
    for faint in sorted(interests, key=lambda t: abs(weight_now(interests[t], now)))[: max(0, len(interests) - MAX_INTERESTS)]:
        del interests[faint]
    return name, change


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
        pull = min(10, max(-10, int(data.get("pull"))))
    except (TypeError, ValueError):
        pull = 1
    if not pull:  # it left her cold
        return None
    question = data.get("question") if isinstance(data.get("question"), str) else ""
    return {"topic": topic.strip()[:60], "thought": thought.strip()[:500], "pull": pull, "question": question.strip()[:300]}


# -- What drifts past ----------------------------------------------------------


def _sentences(text: str) -> list[str]:
    return [s.strip("-* ").strip() for line in text.splitlines() for s in _SENTENCE.split(line) if len(s.strip("-* ").strip()) > 15]


def soul_sentences(wanted: tuple[str, ...] = SOUL_SECTIONS) -> list[str]:
    try:
        text = souls.SOUL_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    sections = re.split(r"^#+\s*(.+)$", text, flags=re.MULTILINE)  # [before, heading, body, heading, body...]
    return [s for heading, body in zip(sections[1::2], sections[2::2]) if heading.strip() in wanted for s in _sentences(body)]


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
    open_topics = [t for t, item in interests.items() if not item["landed"] and not item.get("soured")]
    if open_topics and random.random() < RUMINATE_CHANCE:  # what grates comes back too
        kinds.append(("On your mind lately: ", [random.choices(open_topics, [abs(interests[t]["weight"]) for t in open_topics])[0]]))
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
        and not hub.PAUSED.read()
        and brain.llm.owns_conversation
        and not profiles.read_roleplay_active()
        and not brain.reply_lock.locked()
        and now >= due_at()
    )


def timer_message(brain: Brain) -> dict:
    """Settings -> Heartbeat's countdown."""
    if not ACTIVE.read():
        return protocol.heartbeat_timer("off", None)
    if hub.PAUSED.read():
        return protocol.heartbeat_timer("paused", None)
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


def caps() -> tuple[int, int]:
    """(activities, LLM tokens) she may use a day on her own -- his numbers, config.yaml's heartbeat section."""
    cfg = load_config().get("heartbeat") or {}
    return int(cfg.get("max_activities_per_day", MAX_ACTIVITIES)), int(cfg.get("max_tokens_per_day", MAX_TOKENS))


def day_usage(now: datetime) -> dict:
    """Today against his caps: {did: what she did with her own time, in order (the Growth chart's names),
    done: how many of those weren't resting, most, tokens: all of today's LLM tokens -- his chats included,
    it's one GPU -- and budget}."""
    rows = metrics.today(now)
    did = [r["what"] for r in rows if r["e"] == "activity"]
    most, budget = caps()
    tokens = sum(r.get("tokens") or 0 for r in rows if r["e"] == "llm")
    return {"did": did, "done": sum(what != "rest" for what in did), "most": most, "tokens": tokens, "budget": budget}


def over_cap(now: datetime) -> str:
    """Why she has to rest until tomorrow, or "": past either of his caps (day_usage)."""
    day = day_usage(now)
    if day["done"] >= day["most"]:
        return f"that's {day['done']} things today, her limit"
    if day["tokens"] >= day["budget"]:
        return f"{day['tokens']:,} tokens used today, her limit"
    return ""


@dataclass
class Turn:
    """One turn of her own time: what an activity needs to offer itself and to run."""

    brain: Brain
    now: datetime
    shelf: dict
    interests: dict
    why: str = ""  # her reason, once she's picked


@dataclass(frozen=True)
class Activity:
    name: str  # what the Growth chart (and her "today so far") calls it
    offer: Callable[[Turn], str]  # how it reads on her menu, or "" when it can't happen right now
    run: Callable[[Turn], Awaitable[str]]  # does it; returns what happened, in a line (for Think now)
    logs_itself: bool = False  # a visit is logged by friends.visit, whoever started it


def _offer_read(turn: Turn) -> str:
    if not (names := library.unread(turn.shelf)):
        return ""
    book = turn.shelf["books"][turn.shelf["reading"] if turn.shelf["reading"] in names else names[0]]
    return f"read some of {book['title']} ({library.percent(book)}% in)"


def _offer_look(turn: Turn) -> str:
    wondering = wonder.read()
    return f"look into something you've wondered: \"{wondering[0]['text']}\"" if wondering and web_search.read_active() else ""


def _offer_visit(turn: Turn) -> str:
    import friends  # friends imports heartbeat

    return "" if friends.why_not(turn.brain) or friends.visited(turn.now.date()) else f"have your friends over -- {', '.join(friends.names())}"


async def _visit(turn: Turn) -> str:
    import friends

    if problem := friends.start_visit(turn.brain):
        return f"Her friends couldn't come: {problem}"
    await friends.visit(turn.brain, turn.why)
    return "She had her friends over."


async def _rest(turn: Turn) -> str:
    return f"She's resting: {turn.why}" if turn.why else "She's resting."


# Her menu, in no order (pick shuffles it): her word -> the activity. Resting is always on it.
ACTIVITIES = {
    "read": Activity("read", _offer_read, lambda t: read_pages(t.brain, t.shelf)),
    "look": Activity("look into", _offer_look, lambda t: look_into(t.brain, wonder.read()[0], t.now)),
    "visit": Activity("visit friends", _offer_visit, _visit, logs_itself=True),
    "art": Activity("look at art", lambda t: "look at a picture in your gallery" if pictures.least_looked() else "",
                    lambda t: look_at(t.brain, t.interests, pictures.least_looked(), t.now)),
    "think": Activity("think", lambda t: "think something over", lambda t: think(t.brain, t.interests, t.now)),
    "journal": Activity("journal", lambda t: "write in your journal, whatever you want to get down",
                        lambda t: write_journal(t.brain, t.interests, t.now)),
    "rest": Activity("rest", lambda t: "rest -- do nothing for a while (always fine)", _rest),
}


def options(turn: Turn) -> dict[str, str]:
    """What she can do right now -- only what would actually happen -- as {word: what it is}. Not THINK's
    topics: shown "on your mind lately: R.U.R." she picked THINK 8 of 8 even after seven in a row. And
    whatever she's done SAME_IN_A_ROW times running sits out a turn -- she leans to thinking and journaling,
    and without it her book, her pictures and her friends would only ever wait."""
    found = {word: what for word, activity in ACTIVITIES.items() if (what := activity.offer(turn))}
    recent = day_usage(turn.now)["did"][-SAME_IN_A_ROW:]
    if len(recent) == SAME_IN_A_ROW and len(set(recent)) == 1 and recent[0] != "rest":
        found = {word: what for word, what in found.items() if ACTIVITIES[word].name != recent[0]}
    return found


def parse_choice(raw: str, found: dict[str, str]) -> tuple[str, str]:
    """(word, why) from "READ | I want to know how it ends"; resting when it's not one she had."""
    match = re.match(r"\W*([A-Za-z]+)[^\w|]*(?:\|\s*(.*))?", next((line for line in raw.splitlines() if line.strip()), ""))
    word = match[1].lower() if match else ""
    if word not in found:
        return "rest", "couldn't make up her mind"
    return word, " ".join((match[2] or "").split())[:160]


def today_so_far(now: datetime) -> str:
    """What she's done with her own time today, for her next choice: "Today so far: think x4, read x2 (last: read). "
    Without it she's asked the same question with the same options and gives the same answer (6 of 6)."""
    if not (today := day_usage(now)["did"]):
        return ""
    counts = ", ".join(f"{what} x{n}" for what, n in Counter(today).most_common())
    return f"Today so far: {counts} (last: {today[-1]}). "


async def pick(brain: Brain, found: dict[str, str], now: datetime) -> tuple[str, str]:
    """She decides what to do with her time, and why (parse_choice). The options in a fresh order each
    time, with her own sampling (not the server's), so a list's first line isn't the default."""
    items = list(found.items())
    random.shuffle(items)
    prompt = _CHOICE_PROMPT.format(
        when=f"{now:%A}, {now:%I:%M %p}".replace(" 0", " "), name=persona.user_name() or "he", today=today_so_far(now),
        options="\n".join(f"- {word.upper()}: {what}" for word, what in items),
    )
    async with brain.busy("Deciding what to do..."):
        raw = await asyncio.to_thread(brain.llm.under_soul, prompt, MAX_CHOICE_TOKENS, sampling.active_values())
    return parse_choice(raw, found)


async def beat(brain: Brain) -> str:
    """One turn of her own time: she picks what to do (or rests), and does it. Returns what
    happened, in a line (for Think now)."""
    global _last_beat
    _last_beat = time.time()
    now = datetime.now()
    if why := over_cap(now):
        metrics.log("activity", what="rest", reason=why)
        return f"She's resting until tomorrow: {why}."
    if event := notable.take():
        metrics.log("activity", what="reflect", reason=notable.for_him(event["what"]))
        return await reflect(brain, event, now)
    turn = Turn(brain, now, library.read_state(), read_interests())
    word, turn.why = await pick(brain, options(turn), now)
    activity = ACTIVITIES[word]
    if not activity.logs_itself:
        metrics.log("activity", what=activity.name, reason=turn.why)
    print(f"[heartbeat] she chose to {word}: {turn.why}")
    return await activity.run(turn)


async def think(brain: Brain, interests: dict, now: datetime) -> str:
    """Made to pick between two things of hers now and then (choose); otherwise her mind wanders."""
    if random.random() < CHOOSE_CHANCE and len(pair := await _two_of_hers(interests)) == 2:
        return await choose(brain, interests, *pair, now)
    drifted = await fragments(now, interests)
    if not drifted:
        return "Nothing to think about yet -- no soul, diary, chats or memories to wander through."
    async with brain.busy("Daydreaming..."):  # one model, one job at a time -- his message waits a few seconds at most
        found = parse_daydream(await asyncio.to_thread(brain.llm.daydream, "\n".join(f"- {d}" for d in drifted), persona.user_name() or "he"))
    if not found:
        print("[heartbeat] nothing pulled at her")
        return "Nothing pulled at her this time."
    return await _weigh(brain, interests, found, now)


async def _write(brain: Brain, doing: str, prompt: str, max_tokens: int) -> str:
    """Something she writes on her own time, under her soul at her sampling (see LocalLLM.write_visit_journal) --
    "" if nothing came."""
    async with brain.busy(doing):
        return (await asyncio.to_thread(brain.llm.under_soul, prompt, max_tokens, sampling.active_values())).strip()


async def reflect(brain: Brain, event: dict, now: datetime) -> str:
    """Something that happened (notable.py), thought over -- in her Thoughts."""
    prompt = _REFLECT_PROMPT.format(what=event["what"], detail=event["detail"], name=persona.user_name() or "User")  # how a day log labels him with no name set
    page = await _write(brain, "Thinking it over...", prompt, MAX_REFLECT_TOKENS)
    if not page:
        return f"She thought about it ({notable.for_him(event['what'])}) and had nothing to add."
    conversation.log_thought(f"💭 **{event['what'][:1].upper()}{event['what'][1:]}**\n\n{page}", now=now)  # not .capitalize(): it lowercased his name
    print(f"[heartbeat] she reflected on: {event['what']}")
    return f"She thought over this: {notable.for_him(event['what'])}. {page[:120]}"


async def look_into(brain: Brain, item: dict, now: datetime) -> str:
    """One of her questions (wonder.py), looked up on the web: a note to herself in her Thoughts, the
    question off her list, and what she found out kept like her other conclusions (own: low risk)."""
    question = item["text"]
    async with hub.doing("Looking something up..."):
        found = await asyncio.to_thread(look_up, question)
    if not found:
        return f"She went to look up {question!r} and found nothing."
    note = await _write(brain, "Reading up...", _LOOK_PROMPT.format(question=question, found=found), MAX_LOOK_TOKENS)
    if not note:
        return f"She read up on {question!r} and had nothing to say."
    conversation.log_thought(f"🔎 **{question}**\n\n{note}", now=now)
    wonder.remove(item["id"])
    await learning.propose_own(f"You wondered: {question}\nWhat you found out:\n{note}", f"Looked into: {question}", brain, own=True)
    return f"She looked into {question!r}: {note[:120]}"


async def write_journal(brain: Brain, interests: dict, now: datetime) -> str:
    """A page in her own journal, from what's drifting through her head -- in her Thoughts log."""
    drifted = await fragments(now, interests)
    page = await _write(brain, "Writing in her journal...", _JOURNAL_PROMPT.format(fragments="\n".join(f"- {d}" for d in drifted)), MAX_JOURNAL_TOKENS)
    if not page:
        return "She sat down to write and nothing came."
    conversation.log_thought(f"📓 {page}", now=now)
    return f"She wrote in her journal: {page[:120]}"


async def _weigh(brain: Brain, interests: dict, found: dict, now: datetime, mark: str = "", lost: bool = False) -> str:
    """A thought of hers (parse_daydream's) on its topic, logged in her Thoughts: a page if the
    topic lands, a dislike proposal if it sours. Returns what happened, in a line."""
    topic, change = add_pull(interests, found["topic"], found["pull"], found["thought"], found["question"], time.time())
    _save_interests(interests)
    conversation.log_thought(f"{mark}{found['thought']}  \n*{topic}{' lost' if lost else ''} · pull {found['pull']}*", now=now)
    print(f"[heartbeat] a thought about {topic!r}{' (it lost)' if lost else ''} (pull {found['pull']})")
    if change == "landed" and await _land(brain, interests, topic, now):
        return f"{found['thought']} -- and {topic} landed: she wrote a page about it (Chat Logs -> Thoughts)."
    if change == "soured":
        await _sour(brain, interests, topic)
        return f"{found['thought']} -- and she's soured on {topic}."
    return f"{found['thought']} ({topic}{' lost' if lost else ''}, pull {found['pull']})"


async def _sour(brain: Brain, interests: dict, topic: str) -> None:
    if not persona.not_about_him(topic):  # a hard thought about his life isn't a dislike to keep
        return
    thoughts = "\n".join(f"- {t}" for t in interests[topic]["thoughts"])
    await learning.propose_own(f"{topic} keeps coming back to you, and it doesn't sit well. What you've thought about it lately:\n{thoughts}", f"Soured on {topic}", brain, own=True)


async def _two_of_hers(interests: dict) -> list[str]:
    """Two different things of hers to choose between: loves from her soul, topics she's into, her
    own views among her memories -- never his, so it's never him that loses."""
    mine = soul_sentences(("What you love",)) + [t for t, item in interests.items() if not item.get("soured")]
    if memory.read_memory_active():
        try:
            mine += [m for m in await memory.read_entries() if m.startswith(("I ", "I'", "My "))]
        except Exception:
            pass  # a memory server that's down just means fewer to choose from
    # "Josh's health anxiety" lost to a topic in a test: his life is never what she grows cold on.
    mine = sorted({m for m in mine if persona.not_about_him(m)})
    return random.sample(mine, 2) if len(mine) >= 2 else []


async def choose(brain: Brain, interests: dict, a: str, b: str, now: datetime) -> str:
    """One forced choice: which of `a` and `b` loses, and what about it leaves her cold -- a
    negative pull on the loser, which can sour it like any thought."""
    async with brain.busy("Choosing..."):
        found = parse_daydream(await asyncio.to_thread(brain.llm.choose, a, b))
    if not found:
        return "She couldn't pick between them this time."
    return await _weigh(brain, interests, {**found, "pull": -abs(found["pull"]), "question": ""}, now, "⚖️ ", lost=True)


async def look_at(brain: Brain, interests: dict, name: str, now: datetime) -> str:
    """A picture from her gallery, looked at again on her own: her thought on it, weighed like any."""
    pictures.looked(name)
    mime, data_b64 = pictures.read(name) or ("", "")
    about = pictures.seen_as(name)
    if not data_b64:
        return "The picture she went to look at is gone."
    async with brain.busy("Looking at a picture..."):
        found = parse_daydream(await asyncio.to_thread(brain.llm.look_at_picture, data_b64, mime, about))
    if not found:
        return "Nothing in it caught her this time."
    return await _weigh(brain, interests, found, now, "🖼️ ")


def look_up(topic: str) -> str:
    """What a web search says about a topic that's landed, as lines for her page -- "" with web
    search off, and for a topic naming him: a query leaves the house, what's between them doesn't."""
    name = (persona.user_name() or "").lower()
    if not web_search.read_active() or (name and name in topic.lower()):
        return ""
    results = web_search.search(topic)
    found = "\n".join(f"- {r['title']} ({r['domain']}): {r['snippet']}" for r in results)
    read = 0
    for r in results:  # past a site that refuses or has no prose, to the next
        if read < READ_RESULTS and (text := _page_text(r["url"])):
            found += f"\n\nFrom {r['title']} ({r['domain']}):\n{text}"
            read += 1
    return found


def _page_text(url: str) -> str:
    """The body of a web page: its real paragraphs (menus and footers are short), cut to
    PAGE_EXCERPT_CHARS. "" for anything that isn't a readable HTML page."""
    try:
        page = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Glitch/1.0"})
        if page.status_code != 200 or "html" not in page.headers.get("content-type", ""):
            return ""
        parser = library.HtmlText()
        parser.feed(page.text)
    except Exception:
        return ""
    paragraphs = [" ".join(p.split()) for p in "".join(parser.parts).split("\n\n")]
    # Menus and footers are short; a reference list's long entries are full of links.
    return "\n\n".join(p for p in paragraphs if len(p) >= 200 and "http" not in p and "www." not in p)[:PAGE_EXCERPT_CHARS]


def into_lately(now: float | None = None) -> str:
    """The topics that landed for her in the last LATELY_DAYS, for her prompt as a conversation opens."""
    now = time.time() if now is None else now
    landed = [t for t, item in read_interests().items() if item.get("landed") and now - item["landed"] < LATELY_DAYS * 86400]
    return f"What you've been into on your own lately (you wrote pages on them): {', '.join(landed)}." if landed else ""


async def _land(brain: Brain, interests: dict, topic: str, now: datetime) -> bool:
    """A topic that's landed: her notebook page on it. False if she wrote nothing."""
    thoughts = "\n".join(f"- {t}" for t in interests[topic]["thoughts"] + interests[topic]["questions"])
    async with hub.doing(f"Reading up on {topic}..."):
        found = await asyncio.to_thread(look_up, topic)
    async with brain.busy(f"Writing about {topic}..."):
        page = (await asyncio.to_thread(brain.llm.focus, topic, thoughts, persona.user_name() or "him", found)).strip()
    if not page:
        return False
    folder = conversation.JOURNAL_LOG_DIR / "interests"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sanitize_name(topic, kind='topic')}.md").write_text(f"# {topic}\n\n*Landed {now:%A %d %B %Y}*\n\n{page}\n", encoding="utf-8")
    conversation.log_thought(f"💡 **{topic}** landed -- it kept pulling at her, so she wrote about it:\n\n{page}", now=datetime.now())
    print(f"[heartbeat] {topic!r} landed")
    # Her newest question about it, and the ones her page ends with: only on landing, so a passing thought
    # doesn't fill the list. Hers to find out -- or, about him, his inbox (wonder.keep).
    if interests[topic]["questions"]:
        wonder.keep(interests[topic]["questions"][-1], f"Thinking about {topic}")
    for question in re.findall(r"[^\n?]*\?", page)[-MAX_PAGE_QUESTIONS:]:
        wonder.keep(question, f"Her page on {topic}")
    await learning.propose_own(page, f"Heartbeat: {topic} landed", brain, own=True)  # what she came to think stays with her
    return True


async def read_pages(brain: Brain, shelf: dict) -> str:
    """A sitting with a book from her library: she picks one if she's between books, reads the
    next pages and notes what she makes of them. The book then weighs on her mind like any
    topic, so it can land too."""
    names = library.unread(shelf)
    name = shelf["reading"] if shelf["reading"] in names else names[0]
    if shelf["reading"] not in names and len(names) > 1:
        async with brain.busy("Picking a book..."):
            raw = await asyncio.to_thread(brain.llm.pick_book, [shelf["books"][n]["title"] for n in names])
        if (pick := re.search(r"\d+", raw)) and 1 <= int(pick[0]) <= len(names):
            name = names[int(pick[0]) - 1]
    book = shelf["books"][name]
    pages, end = library.next_pages(library.STORE.read_text(name), book["pos"])
    async with brain.busy(f"Reading {book['title']}..."):
        note = (await asyncio.to_thread(brain.llm.read_pages, book["title"], library.percent(book), pages, book["note"])).strip()
    if want := _WANT.search(note):
        note = note[: want.start()].strip()
    if not note:
        return f"She couldn't get into {book['title']} this time."
    dull = book.get("dull", 0) + 1 if want and int(want[1]) <= DULL_AT else 0
    book.update(pos=end, note=note, finished=end >= book["chars"], dull=dull)
    book["dropped"] = not book["finished"] and dull >= DULL_SITTINGS
    shelf["reading"] = None if book["finished"] or book["dropped"] else name
    shelf["last"] = name
    library.save_state(shelf)
    await hub.broadcast(library.library_message())  # Settings shows how far she's got
    now = datetime.now()
    conversation.log_thought(f"📖 {note}  \n*{book['title']} · {library.percent(book)}%*", now=now)
    print(f"[heartbeat] she read some of {book['title']!r} ({library.percent(book)}%)")
    interests = read_interests()
    topic, change = add_pull(interests, book["title"], READ_PULL, note, "", time.time())
    _save_interests(interests)
    if change == "landed":
        await _land(brain, interests, topic, now)
    if book["finished"]:
        notable.notice(f"you finished {book['title']}", note)
        await learning.propose_own(f"You just finished {book['title']}. Your note on its last pages: {note}", f"Finished reading {book['title']}", brain, own=True)
    if book["dropped"]:
        notable.notice(f"you put {book['title']} down, {library.percent(book)}% in", note)
        conversation.log_thought(f"📕 She put {book['title']} down, {library.percent(book)}% in -- it lost her.", now=now)
        await learning.propose_own(f"You put {book['title']} down {library.percent(book)}% in -- it lost you. Your note on the last pages you read: {note}", f"Put down {book['title']}", brain, own=True)
        return f"She put {book['title']} down ({library.percent(book)}%): {note}"
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
