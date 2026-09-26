"""Curiosity: she wants to know more about the user, and asks -- sparingly.

Two parts, both feeding the prompt LocalLLM builds each turn:

* Standing guidance (GUIDANCE) -- react to what the user just said and, when
  it fits, ask ONE short follow-up about it. No storage; it's just a nudge.
* Open questions -- a short list of things she doesn't know about the user
  yet, generated in the background from the conversation (LocalLLM.propose_question,
  parse_question here) and worked into a reply at most once every few turns.
  The answer needs no special handling: the user's reply goes through memory
  like anything else, and the question is closed once she's actually asked it.

Off during role-play (main.py clears it, same as memory and lessons) -- a
scene has its own story, and real-life questions would break it. The user's
own thumbs up/down on a question flows through the lessons system, so "stop
asking about X" is learned the same way as any other preference.

State: curiosity_active.txt (the toggle) and curiosity_questions.json (the open
list), both beside this file and gitignored. Counters that only pace things
(turns since the last question) live in memory and simply restart with Brain.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_DIR = Path(__file__).parent
ACTIVE_PATH = _DIR / "curiosity_active.txt"
QUESTIONS_PATH = _DIR / "curiosity_questions.json"

MAX_OPEN = 4  # she never hoards more than this waiting to be asked
MAX_CLOSED_KEPT = 30  # remembered so the same question isn't proposed again
MAX_QUESTION_CHARS = 200
OFFER_EVERY_TURNS = 6  # at least this many of the user's messages between questions she's nudged to ask
PROPOSE_EVERY_TURNS = 8  # how often the background "what am I curious about?" call runs
MAX_OFFERS_UNUSED = 3  # closed unasked if offered this many times and she never worked it in

# Backstop for the prompt's own "never ask about" list -- a small model ignores
# instructions now and then, and these are the topics that must never be asked about.
_OFF_LIMITS = re.compile(
    r"\b(loan|loans|debt|mortgage|salary|income|afford|money|paid|paycheck|bank|credit|rent"
    r"|sex|sexual|naked|nude|underwear|boxers|lingerie|bra|panties|body|weight|"
    r"pregnan\w*|diagnos\w*|medication|therap\w*|depress\w*|suicid\w*|divorce|ex-?(?:wife|husband|girlfriend|boyfriend))\b",
    re.I,
)
_STOPWORDS = frozenset(
    "the a an and or of to in on at for with your you you're youre is are was were be do does did it its this that "
    "what whats how when where which who why about into from more been have has had will would could can just like "
    "one first most really still".split()
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

_turns_since_offer = OFFER_EVERY_TURNS  # start ready: the first question needn't wait
_turns_since_propose = 0
_offered_id: str | None = None  # the question nudged into THIS turn's prompt, if any


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


def _topic_words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if w not in _STOPWORDS and len(w) > 2}


def _same_topic(a: str, b: str) -> bool:
    """Reworded repeats: most of the shorter question's content words appear in the other."""
    wa, wb = _topic_words(a), _topic_words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def open_questions() -> list[dict]:
    return [q for q in _read() if q["status"] == OPEN]


def all_question_texts() -> list[str]:
    """Everything ever kept, open or closed -- what a new proposal must not repeat."""
    return [q["text"] for q in _read()]


def add_question(text: str) -> bool:
    """False when it's a repeat, too long, or the open list is full."""
    text = text.strip()
    if not text or len(text) > MAX_QUESTION_CHARS:
        return False
    questions = _read()
    if sum(1 for q in questions if q["status"] == OPEN) >= MAX_OPEN:
        return False
    if _OFF_LIMITS.search(text):
        return False
    if any(_same_topic(text, q["text"]) for q in questions):
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
    global _turns_since_offer, _turns_since_propose, _offered_id
    _turns_since_offer += 1
    _turns_since_propose += 1
    _offered_id = None
    for q in _read():
        if q["status"] == ASKED:
            _mark(q["id"], status=CLOSED)  # text kept so it isn't proposed again
    if any("?" in reply for reply in list(recent_replies)[-QUESTION_COOLDOWN_REPLIES:]):
        return NO_QUESTION_GUIDANCE
    block = GUIDANCE
    if _turns_since_offer >= OFFER_EVERY_TURNS:
        pool = open_questions()
        if pool:
            _offered_id = pool[0]["id"]
            block += (
                f"\nIf the conversation gives you a natural opening, you could ask: \"{pool[0]['text']}\" "
                "-- work it in casually, skip it if it doesn't fit, and don't announce it."
            )
    return block


def end_turn(reply_text: str) -> None:
    """Called after her reply. If a question was nudged in and she actually asked
    something (the reply has a '?'), it's asked; otherwise it stays open, and is
    closed unasked after MAX_OFFERS_UNUSED misses so it can't sit there forever.
    """
    global _turns_since_offer, _offered_id
    if not _offered_id:
        return
    question_id, _offered_id = _offered_id, None
    questions = _read()
    for q in questions:
        if q["id"] != question_id:
            continue
        if "?" in reply_text:
            q["status"] = ASKED
            _turns_since_offer = 0
        else:
            q["offers"] = q.get("offers", 0) + 1
            if q["offers"] >= MAX_OFFERS_UNUSED:
                q["status"] = CLOSED
    _write(questions)


def should_propose() -> bool:
    """Whether it's time for the background call that thinks up a new question."""
    global _turns_since_propose
    if _turns_since_propose < PROPOSE_EVERY_TURNS or len(open_questions()) >= MAX_OPEN:
        return False
    _turns_since_propose = 0
    return True
