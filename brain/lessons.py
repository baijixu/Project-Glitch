"""Behavior learning: short "lessons" about how the user wants Glitch to act,
learned from thumbs up/down on her replies and fed back into her prompt.

Not weight training -- a lesson is just a rule that gets added to her system
prompt each turn (llm/client.py's set_lessons), so a change takes effect on
her very next reply and every lesson is visible, editable and reversible.
Her soul (soul.md) is never touched; lessons sit alongside it.

Storage: one Hindsight "directive" per lesson (name, content, priority 0-10
= strength, is_active), in a bank of its own (`<memory bank>-lessons`) --
NOT the memory bank, because memory.py's Clear Memory deletes that whole
bank and would take the lessons with it. Directives need a Hindsight server,
so this whole feature is unavailable while the memory provider is "local".
A retired directive vanishes from Hindsight's list(), so retired lessons are
also recorded locally (RETIRED_PATH) -- the only place they can still be seen.

Flow: a rating comes in (main.py's rate_reply) -> logged to RATINGS_LOG_PATH
(local, gitignored -- also exactly the dataset a LoRA would need someday) ->
LocalLLM.propose_lesson asks the model what, if anything, that rating says ->
parse_distillation validates the answer -> handle_action applies it or queues
it for approval, depending on the autonomy level (ask / small / all).
"""

import json
import time
import uuid
from pathlib import Path

import memory

_DIR = Path(__file__).parent
ACTIVE_PATH = _DIR / "lessons_active.txt"
AUTONOMY_PATH = _DIR / "lessons_autonomy.txt"
RETIRED_PATH = _DIR / "lessons_retired.json"
PENDING_PATH = _DIR / "lessons_pending.json"
CANDIDATES_PATH = _DIR / "lessons_candidates.json"
RATINGS_LOG_PATH = _DIR / "ratings_log.jsonl"

# Autonomy levels. ASK: every change waits for approval. SMALL: strengthening
# / weakening an existing lesson applies on its own; create/revise/retire
# still ask. ALL: everything applies on its own (a brand-new lesson still
# needs the same signal twice, see handle_action).
ASK = "ask"
SMALL = "small"
ALL = "all"
AUTONOMY_LEVELS = (ASK, SMALL, ALL)

MAX_ACTIVE_LESSONS = 25
# How many make it into one prompt -- highest priority first. Keeps a large
# lesson set from bloating every turn's context.
MAX_INJECTED = 10
MAX_NAME_CHARS = 60
MAX_CONTENT_CHARS = 300
PRIORITY_MIN = 0
PRIORITY_MAX = 10
DEFAULT_PRIORITY = 5
MAX_PENDING = 30
MAX_CANDIDATES = 20
CACHE_TTL_SEC = 300

ACTIONS = ("create", "confirm", "strengthen", "weaken", "revise", "retire", "none")

_ensured_banks: set[str] = set()
_cache: list[dict] | None = None
_cache_at = 0.0


class LessonsUnavailable(Exception):
    """Lessons need a configured Hindsight server (memory provider "hindsight")."""


# -- Settings -----------------------------------------------------------------


def available() -> bool:
    return memory.read_provider() == memory.HINDSIGHT_PROVIDER and memory.hindsight_client() is not None


def set_active(active: bool) -> None:
    ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_active() -> bool:
    """Off by default -- unlike memory, this changes how she behaves, so it
    opts in. Always False while unavailable(), regardless of the saved flag.
    """
    if not available():
        return False
    return ACTIVE_PATH.exists() and ACTIVE_PATH.read_text(encoding="utf-8").strip() == "1"


def read_autonomy() -> str:
    if AUTONOMY_PATH.exists():
        level = AUTONOMY_PATH.read_text(encoding="utf-8").strip()
        if level in AUTONOMY_LEVELS:
            return level
    return ASK


def set_autonomy(level: str) -> None:
    if level not in AUTONOMY_LEVELS:
        raise ValueError(f"unknown autonomy level {level!r}")
    AUTONOMY_PATH.write_text(level, encoding="utf-8")


# -- Hindsight directives -----------------------------------------------------


def _bank() -> str:
    return f"{memory.hindsight_bank_id() or 'glitch-native'}-lessons"


async def _client():
    client = memory.hindsight_client()
    if client is None:
        raise LessonsUnavailable("no Hindsight server configured")
    bank = _bank()
    if bank not in _ensured_banks:
        await client.acreate_bank(bank)  # create-or-update, idempotent
        _ensured_banks.add(bank)
    return client, bank


def _lesson(directive) -> dict:
    return {
        "id": directive.id,
        "name": directive.name or "",
        "content": directive.content or "",
        "priority": directive.priority if directive.priority is not None else DEFAULT_PRIORITY,
    }


def invalidate() -> None:
    global _cache
    _cache = None


async def list_lessons() -> list[dict]:
    """Active lessons, strongest first. Hindsight's list() only returns
    active directives, so this is exactly the set that can affect her.
    """
    global _cache, _cache_at
    client, bank = await _client()
    response = await client.alist_directives(bank, limit=200)
    lessons = sorted((_lesson(d) for d in response.items), key=lambda l: (-l["priority"], l["name"]))
    _cache, _cache_at = lessons, time.monotonic()
    return lessons


async def active_lessons() -> list[dict]:
    """list_lessons() through a short cache -- this runs before every reply,
    and a lesson only changes through this module (which invalidates), so a
    few minutes of staleness costs nothing.
    """
    if _cache is not None and time.monotonic() - _cache_at < CACHE_TTL_SEC:
        return _cache
    return await list_lessons()


async def prompt_block() -> str:
    """What goes into LocalLLM.set_lessons() this turn."""
    lessons = (await active_lessons())[:MAX_INJECTED]
    return "\n".join(f"- {l['content']}" for l in lessons)


def _clean(text: str, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def _clamp_priority(priority: int) -> int:
    return max(PRIORITY_MIN, min(PRIORITY_MAX, int(priority)))


async def create_lesson(name: str, content: str, priority: int = DEFAULT_PRIORITY) -> dict:
    content = _clean(content, MAX_CONTENT_CHARS)
    if not content:
        raise ValueError("a lesson needs some content")
    name = _clean(name, MAX_NAME_CHARS) or content[:MAX_NAME_CHARS]
    if len(await active_lessons()) >= MAX_ACTIVE_LESSONS:
        raise ValueError(f"already at the {MAX_ACTIVE_LESSONS}-lesson limit -- retire or delete one first")
    client, bank = await _client()
    created = await client.acreate_directive(bank, name=name, content=content, priority=_clamp_priority(priority))
    invalidate()
    return _lesson(created)


async def update_lesson(lesson_id: str, name: str | None = None, content: str | None = None, priority: int | None = None) -> dict:
    kwargs = {}
    if name is not None:
        kwargs["name"] = _clean(name, MAX_NAME_CHARS)
    if content is not None:
        content = _clean(content, MAX_CONTENT_CHARS)
        if not content:
            raise ValueError("a lesson needs some content")
        kwargs["content"] = content
    if priority is not None:
        kwargs["priority"] = _clamp_priority(priority)
    client, bank = await _client()
    updated = await client.aupdate_directive(bank, lesson_id, **kwargs)
    invalidate()
    return _lesson(updated)


def _read_json_list(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


async def retire_lesson(lesson_id: str, reason: str = "") -> None:
    """Turns a lesson off (is_active=false) and records it locally first --
    Hindsight stops listing a retired directive, so this record is the only
    place it can still be looked at afterwards.
    """
    lesson = next((l for l in await active_lessons() if l["id"] == lesson_id), None)
    client, bank = await _client()
    await client.aupdate_directive(bank, lesson_id, is_active=False)
    if lesson:
        retired = _read_json_list(RETIRED_PATH)
        retired.append({**lesson, "retired_at": time.time(), "reason": reason})
        _write_json(RETIRED_PATH, retired)
    invalidate()


async def delete_lesson(lesson_id: str) -> None:
    client, bank = await _client()
    await client.adelete_directive(bank, lesson_id)
    invalidate()


# -- Ratings log --------------------------------------------------------------


def log_rating(user_text: str, reply_text: str, rating: str, note: str, roleplay: bool) -> None:
    """One JSON line per rating, kept locally forever (gitignored) -- the
    raw feedback, independent of whether any lesson ever came of it.
    """
    entry = {
        "time": time.time(),
        "rating": rating,
        "note": note,
        "user_text": user_text,
        "reply_text": reply_text,
        "roleplay": roleplay,
    }
    with RATINGS_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# -- Distillation -------------------------------------------------------------


def parse_distillation(raw: str, lessons: list[dict], candidates: list[dict]) -> dict | None:
    """Validates the model's answer to LocalLLM.propose_lesson into one
    normalized action dict, or None for "nothing to learn" / anything
    unusable -- models return messy JSON (code fences, prose around it,
    out-of-range indexes), and a bad answer must never turn into a bad
    lesson, so anything doubtful is dropped rather than repaired.

    `target` in the model's answer is a 1-based index into the numbered list
    it was shown (lessons for strengthen/weaken/revise/retire, candidates for
    confirm), mapped back here to a real id so the model never has to copy
    a uuid.
    """
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "")).strip().lower()
    if action not in ACTIONS or action == "none":
        return None

    def pick(pool: list[dict]) -> dict | None:
        try:
            index = int(data.get("target"))
        except (TypeError, ValueError):
            return None
        return pool[index - 1] if 1 <= index <= len(pool) else None

    def text(key: str, limit: int) -> str:
        # Models often send null for a field that doesn't apply -- must
        # become "", not the string "None" that str(None) would give.
        value = data.get(key)
        return _clean(value, limit) if isinstance(value, str) else ""

    content = text("content", MAX_CONTENT_CHARS)
    result = {"action": action, "target_id": None, "target_name": "", "name": text("name", MAX_NAME_CHARS), "content": content, "reason": text("reason", 200)}

    if action == "create":
        if not content:
            return None
    elif action == "confirm":
        candidate = pick(candidates)
        if candidate is None:
            return None
        result["content"] = candidate["content"]
    else:
        target = pick(lessons)
        if target is None:
            return None
        result["target_id"] = target["id"]
        result["target_name"] = target["content"]
        if action == "revise" and not content:
            return None
    return result


# -- Candidates (a brand-new lesson seen once, "all" autonomy only) -----------


def read_candidates() -> list[dict]:
    return _read_json_list(CANDIDATES_PATH)


def _add_candidate(content: str) -> None:
    candidates = read_candidates()
    if any(c["content"] == content for c in candidates):
        return
    candidates.append({"content": content, "created": time.time()})
    _write_json(CANDIDATES_PATH, candidates[-MAX_CANDIDATES:])


def _drop_candidate(content: str) -> None:
    _write_json(CANDIDATES_PATH, [c for c in read_candidates() if c["content"] != content])


# -- Pending proposals (awaiting the user's approval) --------------------------


def read_pending() -> list[dict]:
    return _read_json_list(PENDING_PATH)


def _add_pending(action: dict) -> dict:
    pending = read_pending()
    proposal = {**action, "id": uuid.uuid4().hex[:8], "created": time.time()}
    duplicate = any(
        p["action"] == proposal["action"] and p.get("target_id") == proposal.get("target_id") and p["content"] == proposal["content"]
        for p in pending
    )
    if not duplicate:
        pending.append(proposal)
        _write_json(PENDING_PATH, pending[-MAX_PENDING:])
    return proposal


async def _apply(action: dict) -> str:
    """Actually performs one action against Hindsight and returns a short
    human-readable description. Raises LookupError if the lesson it targets
    has since been retired/deleted.
    """
    kind = action["action"]
    if kind == "create":
        lesson = await create_lesson(action["name"], action["content"])
        return f"New lesson: {lesson['content']}"
    target = next((l for l in await active_lessons() if l["id"] == action["target_id"]), None)
    if target is None:
        raise LookupError("that lesson no longer exists")
    if kind == "revise":
        lesson = await update_lesson(target["id"], content=action["content"])
        return f"Lesson updated: {lesson['content']}"
    if kind == "retire":
        await retire_lesson(target["id"], action.get("reason", ""))
        return f"Lesson retired: {target['content']}"
    if kind == "strengthen":
        await update_lesson(target["id"], priority=target["priority"] + 1)
        return f"Lesson strengthened: {target['content']}"
    if kind == "weaken":
        if target["priority"] - 1 <= PRIORITY_MIN:
            await retire_lesson(target["id"], "weakened to zero")
            return f"Lesson faded out: {target['content']}"
        await update_lesson(target["id"], priority=target["priority"] - 1)
        return f"Lesson weakened: {target['content']}"
    raise ValueError(f"unknown action {kind!r}")


async def handle_action(action: dict) -> tuple[str, str]:
    """Applies or queues one parsed action according to the autonomy level.
    Returns (kind, text) for the Renderer's lesson_event: "applied" (done),
    "proposed" (waiting for approval in Settings), or "noted" (a possible new
    lesson seen once, waiting for the same signal again).
    """
    level = read_autonomy()
    kind = action["action"]

    if kind == "confirm":
        _drop_candidate(action["content"])
        kind = "create"
        action = {**action, "action": "create", "name": ""}
        confirmed = True
    else:
        confirmed = False

    small = kind in ("strengthen", "weaken")
    auto = level == ALL or (level == SMALL and small)
    if not auto:
        _add_pending(action)
        return "proposed", _describe(action)

    if kind == "create" and not confirmed:
        _add_candidate(action["content"])
        return "noted", f"Possible lesson (waiting to see it again): {action['content']}"
    return "applied", await _apply(action)


def _describe(action: dict) -> str:
    kind = action["action"]
    if kind == "create":
        return f"New lesson: {action['content']}"
    if kind == "revise":
        return f"Change \"{action['target_name']}\" to: {action['content']}"
    if kind == "retire":
        return f"Retire: {action['target_name']}"
    if kind == "strengthen":
        return f"Strengthen: {action['target_name']}"
    return f"Weaken: {action['target_name']}"


async def resolve_pending(proposal_id: str, approve: bool) -> str | None:
    """The user's answer to a queued proposal. Removes it either way;
    applies it only on approve. Returns the applied description, or None if
    rejected/unknown.
    """
    pending = read_pending()
    proposal = next((p for p in pending if p["id"] == proposal_id), None)
    if proposal is None:
        return None
    _write_json(PENDING_PATH, [p for p in pending if p["id"] != proposal_id])
    if not approve:
        return None
    return await _apply(proposal)


# -- State for the Settings panel ---------------------------------------------


async def state() -> dict:
    """Everything the Settings panel's Behavior learning section shows. Never
    raises -- an unreachable Hindsight server just becomes an `error` string
    with an empty lesson list, so opening Settings can't break on it.
    """
    result = {
        "available": available(),
        "active": read_active(),
        "autonomy": read_autonomy(),
        "lessons": [],
        "pending": read_pending(),
        "error": "",
    }
    if result["available"]:
        try:
            result["lessons"] = await list_lessons()
        except Exception as exc:
            result["error"] = f"couldn't reach the lessons store: {exc!r}"
    return result
