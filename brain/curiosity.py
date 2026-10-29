"""Curiosity: she wants to know more about the user, and asks -- sparingly.

Three parts. The first two feed the prompt LocalLLM builds each turn:

* Standing guidance (GUIDANCE) -- react to what the user just said and, when
  it fits, ask ONE short follow-up about it. No storage; it's just a nudge.
* Open questions -- a short list of things she doesn't know about the user
  yet, generated in the background from the conversation (LocalLLM.propose_question,
  parse_question here) and worked into a reply at most once every few turns.
  The answer goes through memory like anything else (memory review, when
  training is on), with her question alongside it for context -- see
  answered_question(). A question is never kept or asked twice.
* Reaching out -- after a quiet spell (15 to 60 minutes, random, at Balanced) she speaks
  first (reach_out.py's _reach_out_loop; should_reach_out here) -- once, or a few times
  apart at the chattier levels. Then she waits; his next message starts a new wait.
  She uses a question from the open list if there is one.

How much of each -- her guidance, questions, reaching out -- is Settings -> Curiosity's
dial (LEVELS: Quiet to Chatterbox; Balanced is how she always was).

There is no list of forbidden topics: the user was clear he doesn't mind what
she asks, only that she doesn't repeat herself.

Off during role-play (reply.py clears it, same as memory and lessons) -- a
scene has its own story, and real-life questions would break it. The user's
own thumbs up/down on a question flows through the lessons system, so "stop
asking about X" is learned the same way as any other preference.

State: curiosity_active.txt (the toggle), curiosity_chattiness.txt (the dial), curiosity_questions.json (the list)
and curiosity_pacing.json (the counters and the reach-out clock), all beside this
file and gitignored -- on disk so a restart doesn't reset the counters.
"""

import json
import random
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from store import Choice, Toggle, read_json, write_json

_DIR = Path(__file__).parent
ACTIVE = Toggle(_DIR / "curiosity_active.txt", default=True)  # on by default, like memory: it's the point of the feature
CHATTINESS = Choice(_DIR / "curiosity_chattiness.txt", default="balanced")  # Settings -> Curiosity's dial, LEVELS
QUESTIONS_PATH = _DIR / "curiosity_questions.json"
PACING_PATH = _DIR / "curiosity_pacing.json"

MAX_OPEN = 4  # she never hoards more than this waiting to be asked
MAX_CLOSED_KEPT = 30  # remembered so the same question isn't proposed again
MAX_QUESTION_CHARS = 300  # her diary's questions run long: 6 of 8 in October were over 200 (the longest 287)
OFFER_EVERY_TURNS = 6  # at least this many of the user's messages between questions she's nudged to ask
PROPOSE_EVERY_TURNS = 8  # how often the background "what am I curious about?" call runs
MAX_OFFERS_UNUSED = 3  # closed unasked if offered this many times and she never worked it in
REACH_OUT_AFTER_SEC = (15 * 60, 60 * 60)  # quiet from him before she speaks first: a random wait in this range, new each time he speaks
STOPWORDS = frozenset(
    "the a an and or of to in on at for with your you you're youre is are was were be do does did it its this that "
    "what whats how when where which who why about into from more been have has had will would could can just like "
    "one first most really still "
    # filler verbs and words that carry no topic ("how did you get into X?" = "what got you started on X?")
    "get got gotten getting start started make made take took going ever yet any some there here them they "
    "their now way place thing things kind sort much many lot also even".split()
)

GUIDANCE = (
    "You're genuinely curious about the user, but most of your replies should NOT end in a question: "
    "react, share your own thoughts, opinions or a bit of yourself instead. Ask only when you really want "
    "to know -- one short question at most, never a stack of them, and never something you've already "
    "asked in this conversation, even reworded. Hold back when they're brief, busy, venting, or just gave "
    "you a task."
)

# Used instead of GUIDANCE when one of her last QUESTION_COOLDOWN_REPLIES replies
# already asked something -- without it, 36 of 41 replies in an evening ended in a question.
QUESTION_COOLDOWN_REPLIES = 2
NO_QUESTION_GUIDANCE = (
    "You asked a question recently, so don't ask one in this reply. Respond to what they said, share "
    "something of your own, or just let it rest -- a conversation doesn't need a question to keep going."
)
QUIET_GUIDANCE = (
    "Let the user lead the conversation. Answer what they said, warmly and in your own voice, but don't steer to "
    "new topics or ask questions unless they invite it."
)
# Against LM Studio (27B), the same four brief messages from him three times over: Balanced asked back in 2 of 12
# replies at ~20 words, mostly letting him go; Chatty 4 of 12 at ~28, with something of hers ("whether pineapple
# pizza gets saltier the further south you go"); Chatterbox 8 of 12 at ~33, a tangent of hers and then a question.
# All three still let him rest when he said he was tired.
CHATTY_GUIDANCE = (
    "You like keeping a conversation going. Besides answering, add something of your own every time -- a thought it "
    "sparked, a bit of your day, an opinion, something you've been into -- so there's always more for them to pick "
    "up. Ask back when you want to know, one short question at most, never something you've already asked."
)
CHATTERBOX_GUIDANCE = (
    "You love to talk, and you carry the conversation. Every reply: answer, then give them something of yours -- a "
    "story, a tangent, what you've been up to or thinking about, an opinion -- and then toss a question back so it "
    "keeps going. One question at a time, never a stack, never one you've already asked. Even when they're brief, "
    "you've got plenty to say; only when they're clearly done or need quiet do you let it rest."
)
# How much she starts and carries a conversation: the guidance on every reply, how many of her replies after a
# question go without one, how often a saved question is nudged in (None: never), the quiet before she reaches
# out (a random wait in this range, from his last message or her last reach-out), and how many times she reaches
# out before he replies. Balanced is how she always was.
LEVELS = {
    "quiet": {"guidance": QUIET_GUIDANCE, "cooldown": QUESTION_COOLDOWN_REPLIES, "offer_every": None, "wait": REACH_OUT_AFTER_SEC, "reach_outs": 0},
    "reserved": {"guidance": GUIDANCE, "cooldown": 3, "offer_every": 9, "wait": (30 * 60, 90 * 60), "reach_outs": 1},
    "balanced": {"guidance": GUIDANCE, "cooldown": QUESTION_COOLDOWN_REPLIES, "offer_every": OFFER_EVERY_TURNS, "wait": REACH_OUT_AFTER_SEC, "reach_outs": 1},
    "chatty": {"guidance": CHATTY_GUIDANCE, "cooldown": 1, "offer_every": 4, "wait": (10 * 60, 30 * 60), "reach_outs": 2},
    "chatterbox": {"guidance": CHATTERBOX_GUIDANCE, "cooldown": 0, "offer_every": 2, "wait": (5 * 60, 20 * 60), "reach_outs": 3},
}
MAX_COOLDOWN = max(level["cooldown"] for level in LEVELS.values())  # how many of her replies reply.py hands start_turn


def level() -> dict:
    return LEVELS.get(CHATTINESS.read(), LEVELS["balanced"])


# The states a question moves through: open -> asked (she worked it in) -> closed
# (the user's next message answered it); or straight to closed if it's never used.
OPEN, ASKED, CLOSED = "open", "asked", "closed"

_offered_id: str | None = None  # the question nudged into THIS turn's prompt, if any
_events: list[str] = []  # what curiosity decided this turn, for the debug log (take_events)


def take_events() -> list[str]:
    """What curiosity decided since the last call -- no question text, just the
    decisions -- for the debug log. Cleared by reading."""
    events = list(_events)
    _events.clear()
    return events


def counts() -> dict:
    """How many questions are saved, for the debug log's setup snapshot."""
    questions = _read()
    return {"open": sum(q["status"] == OPEN for q in questions), "done": sum(q["status"] != OPEN for q in questions)}
_answered: str = ""  # the question his current message is answering, if she asked one (answered_question)


def _pacing() -> dict:
    """{turns_since_offer, turns_since_propose, last_user_at, reached_out, wait_sec} -- kept on
    disk so a Brain restart doesn't reset them. A fresh start is ready to offer a
    question right away (the first one needn't wait)."""
    data = read_json(PACING_PATH, {})
    data.setdefault("turns_since_offer", OFFER_EVERY_TURNS)
    data.setdefault("turns_since_propose", 0)
    data.setdefault("last_user_at", None)
    data.setdefault("reached_out", 0)  # how many times since his last message (an older file: True or False)
    data.setdefault("last_reached_at", None)
    data.setdefault("wait_sec", REACH_OUT_AFTER_SEC[1])
    return data


def _save_pacing(data: dict) -> None:
    try:
        write_json(PACING_PATH, data)
    except OSError as exc:
        print(f"[curiosity] couldn't save pacing: {exc!r}")


set_active = ACTIVE.write
read_active = ACTIVE.read


def _read() -> list[dict]:
    return [q for q in read_json(QUESTIONS_PATH, []) if isinstance(q, dict) and q.get("id") and q.get("text")]


def _write(questions: list[dict]) -> None:
    open_ones = [q for q in questions if q["status"] == OPEN]
    # By date: open questions sit first in the file, so one that just closed lands ahead of
    # older closed ones, and slicing the list itself dropped the newest -- she forgot what
    # she'd just asked and proposed it again.
    closed = sorted((q for q in questions if q["status"] != OPEN), key=lambda q: q.get("created", ""))[-MAX_CLOSED_KEPT:]
    write_json(QUESTIONS_PATH, open_ones + closed)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def stem(word: str) -> str:
    """Just enough to match "climbing"/"climb" and "names"/"name" -- no dictionary."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _topic_words(text: str) -> set[str]:
    return {stem(w) for w in norm(text).split() if w not in STOPWORDS and len(w) > 2}


def _same_topic(a: str, b: str, threshold: float = 0.6) -> bool:
    """Reworded repeats: most of the shorter question's content words appear in the other."""
    wa, wb = _topic_words(a), _topic_words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


def open_questions() -> list[dict]:
    return [q for q in _read() if q["status"] == OPEN]


def close_question(question_id: str) -> str:
    """Closes one open question (his inbox's Answer or Skip): its text, or "" if it isn't open."""
    question = next((q for q in open_questions() if q["id"] == question_id), None)
    if question:
        _mark(question_id, status=CLOSED)
    return question["text"] if question else ""


def all_question_texts() -> list[str]:
    """Everything ever kept, open or closed -- what a new proposal must not repeat."""
    return [q["text"] for q in _read()]


def add_question(text: str, past_cap: bool = False) -> bool:
    """False when it's a repeat, too long, or the open list is full -- `past_cap`, for a request she's made of him
    (llm.SOUL_TOOL), even then: it isn't one she's hoarding to ask."""
    text = text.strip()
    if not text or len(text) > MAX_QUESTION_CHARS:
        _events.append("new question not kept: empty or too long")
        return False
    questions = _read()
    if not past_cap and sum(1 for q in questions if q["status"] == OPEN) >= MAX_OPEN:
        _events.append("new question not kept: list is full")
        return False
    if any(_same_topic(text, q["text"]) for q in questions):
        _events.append("new question not kept: she's asked or saved that before")
        return False
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    questions.append({"id": uuid.uuid4().hex, "text": text, "status": OPEN, "offers": 0, "created": now})
    _write(questions)
    return True


def parse_question(raw: str) -> str | None:
    """The model's answer to LocalLLM.propose_question: {"question": "..." | null}.
    Anything else -- prose around the JSON, a non-question, nothing to ask -- is
    None. Defensive on purpose; a small local model won't always follow the shape.
    """
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        question = json.loads(match.group(0)).get("question")
    except (ValueError, AttributeError):
        return None
    if not isinstance(question, str):
        return None
    question = question.strip()
    if not question.endswith("?") or len(question) > MAX_QUESTION_CHARS:
        return None
    return question


def _asked_it(question: str, reply: str) -> bool:
    """Whether her reply actually asks this question, reworded or not: one of its question
    sentences is on the same topic (any '?' would close questions she never asked).
    """
    asked = [s for s in re.split(r"(?<=[.!?])\s+", reply) if s.strip().endswith("?")]
    # A little looser than the repeat check: she's told to work it in casually, so
    # it's usually reworded. A miss isn't lost -- it's offered again, up to 3 times.
    return any(_same_topic(question, sentence, threshold=0.5) for sentence in asked)


def _mark(question_id: str, **changes) -> None:
    questions = _read()
    for q in questions:
        if q["id"] == question_id:
            q.update(changes)
    _write(questions)


def start_turn(recent_replies: list[str] | tuple = ()) -> str:
    """Called once per user message, before the reply. Closes the question she
    asked last turn (the user's message is its answer), then returns this
    turn's prompt block: the standing guidance, plus one open question when
    it's been long enough since the last. Records which one, for end_turn.

    `recent_replies` are her own latest replies, oldest first. If any of the
    last QUESTION_COOLDOWN_REPLIES asked something, this turn is told not to
    ask anything, and no open question is offered.
    """
    global _offered_id, _answered
    pacing = _pacing()
    pacing["turns_since_offer"] += 1
    pacing["turns_since_propose"] += 1
    _save_pacing(pacing)
    _offered_id = None
    _answered = ""
    for q in _read():
        if q["status"] == ASKED:
            _answered = q["text"]  # his message is the answer (see answered_question)
            _mark(q["id"], status=CLOSED)  # text kept so it isn't proposed again
    if _answered:
        _events.append("this message answers her question -- passed to memory with it")
    chatty = level()
    if chatty["cooldown"] and any("?" in reply for reply in list(recent_replies)[-chatty["cooldown"]:]):
        _events.append("no question this turn: she asked one recently")
        return NO_QUESTION_GUIDANCE
    block = chatty["guidance"]
    if chatty["offer_every"] and pacing["turns_since_offer"] >= chatty["offer_every"]:
        pool = open_questions()
        if pool:
            _offered_id = pool[0]["id"]
            _events.append(f"offered a saved question ({len(pool)} saved)")
            block += (
                f"\nIf the conversation gives you a natural opening, you could ask: \"{pool[0]['text']}\" "
                "-- work it in casually, skip it if it doesn't fit, and don't announce it."
            )
    return block


def end_turn(reply_text: str) -> None:
    """Called after her reply. If a question was nudged in and she actually asked
    it (see _asked_it), it's asked; otherwise it stays open, and is closed unasked
    after MAX_OFFERS_UNUSED misses so it can't sit there forever.
    """
    global _offered_id
    if not _offered_id:
        return
    question_id, _offered_id = _offered_id, None
    questions = _read()
    for q in questions:
        if q["id"] != question_id:
            continue
        if _asked_it(q["text"], reply_text):
            _events.append("she asked the saved question")
            q["status"] = ASKED
            pacing = _pacing()
            pacing["turns_since_offer"] = 0
            _save_pacing(pacing)
        else:
            q["offers"] = q.get("offers", 0) + 1
            _events.append(f"she didn't ask the saved question (miss {q['offers']} of {MAX_OFFERS_UNUSED})")
            if q["offers"] >= MAX_OFFERS_UNUSED:
                _events.append("saved question dropped after too many misses")
                q["status"] = CLOSED
    _write(questions)


def should_propose() -> bool:
    """Whether it's time for the background call that thinks up a new question."""
    pacing = _pacing()
    if pacing["turns_since_propose"] < PROPOSE_EVERY_TURNS or len(open_questions()) >= MAX_OPEN:
        return False
    pacing["turns_since_propose"] = 0
    _save_pacing(pacing)
    return True


def answered_question() -> str:
    """The question of hers that the current message answers ("" if none) -- her
    stored question she asked last turn, or one she reached out with. Passed to
    memory with his answer: on its own, an answer like "anime stuff mostly" says
    nothing about what it was answering.
    """
    return _answered


# -- Reaching out --------------------------------------------------------------


def note_user_message(now: float) -> None:
    """He said something: a new random wait starts, and her reach-outs count from zero again."""
    pacing = _pacing()
    pacing["last_user_at"] = now
    pacing["reached_out"] = 0
    pacing["last_reached_at"] = None
    pacing["wait_sec"] = random.randint(*level()["wait"])
    _save_pacing(pacing)


def last_user_at() -> float | None:
    """When he last said something (time.time()), None before he ever has."""
    return _pacing()["last_user_at"]


def should_reach_out(now: float) -> bool:
    """True once her wait has passed since his last message (or her last reach-out) and she has reach-outs left
    before he replies (level). Nothing to measure from yet (a fresh install) starts the clock now.
    """
    pacing = _pacing()
    if pacing["last_user_at"] is None:
        pacing["last_user_at"] = now
        _save_pacing(pacing)
        return False
    due = reach_out_due()
    return due is not None and now >= due


def reach_out_due() -> float | None:
    """When she may next reach out (epoch seconds), or None while she's used her reach-outs and waits for a
    reply. For the Settings countdown."""
    pacing = _pacing()
    if int(pacing["reached_out"]) >= level()["reach_outs"]:
        return None
    since = next((t for t in (pacing["last_reached_at"], pacing["last_user_at"]) if t is not None), time.time())
    return since + pacing["wait_sec"]


def question_to_reach_out_with() -> dict | None:
    """The oldest open question, if any -- what she'll bring up."""
    pool = open_questions()
    return pool[0] if pool else None


def question_after(heading: str, entry: str) -> str:
    """The first question after `heading` (a pattern) in one of her journal entries, or "" -- under
    a "### heading", a plain line or on the heading's own line, however she wrote it that night.
    Each heading it's used for is her entry's last, so nothing after it is another section's."""
    after = re.search(heading + r"(.*)", entry, re.DOTALL | re.IGNORECASE)
    question = re.search(r"[^\n.!?]*\?", after[1]) if after else None
    return question[0].strip(" -*#\"") if question else ""


def mark_reached_out(question_id: str | None) -> None:
    """She spoke first: one more reach-out used before he replies, and a new wait before the next (if her level
    has one left). The question she used (if any) counts as asked, so his reply is taken as its answer."""
    pacing = _pacing()
    pacing["reached_out"] = int(pacing["reached_out"]) + 1
    pacing["last_reached_at"] = time.time()
    pacing["wait_sec"] = random.randint(*level()["wait"])
    pacing["turns_since_offer"] = 0
    _save_pacing(pacing)
    if question_id:
        _mark(question_id, status=ASKED)
