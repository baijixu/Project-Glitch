"""Training mode: she proposes memories, the user decides what actually gets saved.

With it on (and Hindsight as the memory provider), the normal "hand every
exchange to Hindsight" path is replaced: after each reply a background call
(LocalLLM.propose_memory, parse_fact here) proposes at most ONE short fact, and
it waits in a review queue. In Settings the user can edit its wording, pick an
importance (core / normal / minor) and approve it -- only then is that text
retained (memory.retain_fact), tagged with its importance -- or reject it.
Nothing reaches Hindsight without the user's say-so, which is the point: it
keeps scene descriptions, jokes and mix-ups about who said what out of her
memory while she's still learning what's worth keeping.

Importance is stored as an `importance:<level>` tag on the memory. "core" facts
are always recalled into her prompt (memory.recall_core); the other two are
just recalled by relevance like anything else.

State: training_active.txt (the toggle -- off unless turned on) and
training_queue.json (proposals waiting for review), beside this file and
gitignored.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_DIR = Path(__file__).parent
ACTIVE_PATH = _DIR / "training_active.txt"
QUEUE_PATH = _DIR / "training_queue.json"

IMPORTANCE_LEVELS = ("core", "normal", "minor")
DEFAULT_IMPORTANCE = "normal"
MAX_PENDING = 100  # oldest are dropped past this -- a queue nobody reviews shouldn't grow forever
MAX_FACT_CHARS = 300


def set_active(active: bool) -> None:
    ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_active() -> bool:
    """Off by default -- unlike memory itself this stops things being saved until
    someone reviews them, so it's opt-in.
    """
    return ACTIVE_PATH.exists() and ACTIVE_PATH.read_text(encoding="utf-8").strip() == "1"


def importance_tag(level: str) -> str:
    return f"importance:{level if level in IMPORTANCE_LEVELS else DEFAULT_IMPORTANCE}"


def _read() -> list[dict]:
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in data if isinstance(p, dict) and p.get("id") and p.get("fact")] if isinstance(data, list) else []


def _write(pending: list[dict]) -> None:
    QUEUE_PATH.write_text(json.dumps(pending[-MAX_PENDING:], indent=2), encoding="utf-8")


def read_pending() -> list[dict]:
    """Oldest first: {id, fact, source, created} -- `source` is what the user said, for context."""
    return _read()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def parse_fact(raw: str) -> str | None:
    """The model's answer to LocalLLM.propose_memory: {"fact": "..." | null}.
    Anything else -- prose around the JSON, nothing worth keeping, something
    too long -- is None. Defensive on purpose; a small local model won't always
    follow the shape.
    """
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        fact = json.loads(match.group(0)).get("fact")
    except (ValueError, AttributeError):
        return None
    if not isinstance(fact, str):
        return None
    fact = fact.strip()
    if not fact or len(fact) > MAX_FACT_CHARS:
        return None
    return fact


def add_pending(fact: str, source: str = "") -> dict | None:
    """None when it repeats something already waiting."""
    fact = fact.strip()
    if not fact:
        return None
    pending = _read()
    if _norm(fact) in {_norm(p["fact"]) for p in pending}:
        return None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    item = {"id": f"{now}-{len(pending)}", "fact": fact, "source": source.strip()[:200], "created": now}
    pending.append(item)
    _write(pending)
    return item


def get_pending(proposal_id: str) -> dict | None:
    return next((p for p in _read() if p["id"] == proposal_id), None)


def remove_pending(proposal_id: str) -> bool:
    pending = _read()
    kept = [p for p in pending if p["id"] != proposal_id]
    if len(kept) == len(pending):
        return False
    _write(kept)
    return True
