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
* Reaching out -- after an hour with no message from the user, she speaks
  first, once (reach_out.py's _reach_out_loop; should_reach_out here). If he
  doesn't reply she stays quiet; his next message starts the hour again.
  She uses a question from the open list if there is one.

There is no list of forbidden topics: the user was clear he doesn't mind what
she asks, only that she doesn't repeat herself.

Off during role-play (reply.py clears it, same as memory and lessons) -- a
scene has its own story, and real-life questions would break it. The user's
own thumbs up/down on a question flows through the lessons system, so "stop
asking about X" is learned the same way as any other preference.

State: curiosity_active.txt (the toggle), curiosity_questions.json (the list)
and curiosity_pacing.json (the counters and the reach-out clock), all beside this
file and gitignored. The counters used to live in memory and restart with Brain,
so with frequent restarts she rarely got as far as thinking up a new question.
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

_DIR = Path(__file__).parent
ACTIVE_PATH = _DIR / "curiosity_active.txt"
QUESTIONS_PATH = _DIR / "curiosity_questions.json"
PACING_PATH = _DIR / "curiosity_pacing.json"

MAX_OPEN = 4  # she never hoards more than this waiting to be asked
MAX_CLOSED_KEPT = 30  # remembered so the same question isn't proposed again
MAX_QUESTION_CHARS = 200
OFFER_EVERY_TURNS = 6  # at least this many of the user's messages between questions she's nudged to ask
PROPOSE_EVERY_TURNS = 8  # how often the background "what am I curious about?" call runs
MAX_OFFERS_UNUSED = 3  # closed unasked if offered this many times and she never worked it in
REACH_OUT_AFTER_SEC = 60 * 60  # an hour with no message from him before she speaks first
_STOPWORDS = frozenset(
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
# already asked something. Seen live: 36 of 41 replies in one evening ended in a
# question, several near-identical ("Do you like how...?" three times running).
QUESTION_COOLDOWN_REPLIES = 2
NO_QUESTION_GUIDANCE = (
    "You asked a question recently, so don't ask one in this reply. Respond to what they said, share "
    "something of your own, or just let it rest -- a conversation doesn't need a question to keep going."
)

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
    """{turns_since_offer, turns_since_propose, last_user_at, reached_out} -- kept on
    disk so a Brain restart doesn't reset them. A fresh start is ready to offer a
    question right away (the first one needn't wait)."""
    try:
        data = json.loads(PACING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    data.setdefault("turns_since_offer", OFFER_EVERY_TURNS)
    data.setdefault("turns_since_propose", 0)
    data.setdefault("last_user_at", None)
    data.setdefault("reached_out", False)
    return data


def _save_pacing(data: dict) -> None:
    try:
        PACING_PATH.write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:
        print(f"[curiosity] couldn't save pacing: {exc!r}")


def set_active(active: bool) -> None:
    ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_active() -> bool:
    """Defaults to on -- like memory, this is the point of the feature."""
    if ACTIVE_PATH.exists():
        return ACTIVE_PATH.read_text(encoding="utf-8").strip() != "0"
    return True


def _read() -> list[dict]:
    try:
        data = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [q for q in data if isinstance(q, dict) and q.get("id") and q.get("text")] if isinstance(data, list) else []


def _write(questions: list[dict]) -> None:
    open_ones = [q for q in questions if q["status"] == OPEN]
    closed = [q for q in questions if q["status"] != OPEN][-MAX_CLOSED_KEPT:]
    QUESTIONS_PATH.write_text(json.dumps(open_ones + closed, indent=2), encoding="utf-8")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _stem(word: str) -> str:
    """Just enough to match "climbing"/"climb" and "names"/"name" -- no dictionary."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _topic_words(text: str) -> set[str]:
    return {_stem(w) for w in _norm(text).split() if w not in _STOPWORDS and len(w) > 2}


def _same_topic(a: str, b: str, threshold: float = 0.6) -> bool:
    """Reworded repeats: most of the shorter question's content words appear in the other."""
    wa, wb = _topic_words(a), _topic_words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


def open_questions() -> list[dict]:
    return [q for q in _read() if q["status"] == OPEN]


def all_question_texts() -> list[str]:
    """Everything ever kept, open or closed -- what a new proposal must not repeat."""
    return [q["text"] for q in _read()]


def add_question(text: str) -> bool:
    """False when it's a repeat, too long, or the open list is full."""
    text = text.strip()
    if not text or len(text) > MAX_QUESTION_CHARS:
        _events.append("new question not kept: empty or too long")
        return False
    questions = _read()
    if sum(1 for q in questions if q["status"] == OPEN) >= MAX_OPEN:
        _events.append("new question not kept: list is full")
        return False
    if any(_same_topic(text, q["text"]) for q in questions):
        _events.append("new question not kept: she's asked or saved that before")
        return False
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    questions.append({"id": now + f"-{len(questions)}", "text": text, "status": OPEN, "offers": 0, "created": now})
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
    """Whether her reply actually asks this question, reworded or not -- one of
    its question sentences is on the same topic. Any '?' used to count, so the
    stored question was ticked off whenever she asked anything at all: 11 of 12
    were closed that way without ever being asked.
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
    if any("?" in reply for reply in list(recent_replies)[-QUESTION_COOLDOWN_REPLIES:]):
        _events.append("no question this turn: she asked one recently")
        return NO_QUESTION_GUIDANCE
    block = GUIDANCE
    if pacing["turns_since_offer"] >= OFFER_EVERY_TURNS:
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
    """He said something: the hour starts again, and she may reach out once more."""
    pacing = _pacing()
    pacing["last_user_at"] = now
    pacing["reached_out"] = False
    _save_pacing(pacing)


def restart_quiet_hour(now: float) -> None:
    """Clear Chat: whatever she said to reach out is gone from the conversation,
    so she's no longer waiting on a reply to it -- the hour starts again now."""
    note_user_message(now)


def should_reach_out(now: float) -> bool:
    """True once an hour has passed since his last message and she hasn't reached
    out since. Nothing to measure from yet (a fresh install) starts the clock now.
    """
    pacing = _pacing()
    if pacing["last_user_at"] is None:
        pacing["last_user_at"] = now
        _save_pacing(pacing)
        return False
    return not pacing["reached_out"] and now - pacing["last_user_at"] >= REACH_OUT_AFTER_SEC


def reach_out_due() -> float | None:
    """When she may next reach out (epoch seconds), or None while she's waiting
    for a reply to the last time she did. For the Settings countdown."""
    pacing = _pacing()
    if pacing["reached_out"]:
        return None
    return (pacing["last_user_at"] or time.time()) + REACH_OUT_AFTER_SEC


def question_to_reach_out_with() -> dict | None:
    """The oldest open question, if any -- what she'll bring up."""
    pool = open_questions()
    return pool[0] if pool else None


def mark_reached_out(question_id: str | None) -> None:
    """She spoke first: not again until he replies. The question she used (if any)
    counts as asked, so his reply is taken as its answer."""
    pacing = _pacing()
    pacing["reached_out"] = True
    pacing["turns_since_offer"] = 0
    _save_pacing(pacing)
    if question_id:
        _mark(question_id, status=ASKED)
