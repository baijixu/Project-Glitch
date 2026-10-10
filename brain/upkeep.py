"""Memory upkeep: once a week, after her diary (journal.py), her memories are checked for
ones gone out of date ("Josh is moving next month", saved six weeks ago) and ones that
repeat another. Each becomes a proposal in the memory-training review list -- new wording
dated as of when it was said, or retiring the repeat -- and nothing changes until the user
applies it. Needs a Hindsight memory server, with memory training on.
"""

import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path

import memory
import training
from hub import Brain

EVERY_DAYS = 7
LAST_PATH = Path(__file__).parent / "upkeep_last.txt"  # the date of the last run
MIN_AGE_DAYS = 7  # a newer memory isn't checked for going out of date
COMPARE_WITH = 2  # her closest matching memories each one is checked against for repeats


def due(now: datetime) -> bool:
    try:
        last = datetime.fromisoformat(LAST_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    return now - last >= timedelta(days=EVERY_DAYS)


def _saved(memory_row: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(memory_row.get("mentioned_at")).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def _out_of_date(raw: str) -> bool:
    """The model said {"current": false}; anything unreadable counts as still current."""
    return bool(re.search(r'"current"\s*:\s*false', raw or "", re.IGNORECASE))


def _keeper(a: dict, b: dict) -> tuple[dict, dict]:
    """(keep, retire) of two memories that say the same thing: a core one stays, else the older."""
    core = "importance:core"
    if (core in (a.get("tags") or [])) != (core in (b.get("tags") or [])):
        return (a, b) if core in (a.get("tags") or []) else (b, a)
    return (a, b) if (_saved(a) or datetime.max) <= (_saved(b) or datetime.max) else (b, a)


async def run(brain: Brain, now: datetime | None = None) -> int:
    """One pass over her memories; returns how many proposals were queued."""
    now = now or datetime.now()
    added, compared, retiring = 0, set(), set()
    for row in await memory.list_memories():
        if row["id"] in retiring:
            continue
        saved = _saved(row)
        if saved and (now - saved).days >= MIN_AGE_DAYS:
            if _out_of_date(await asyncio.to_thread(brain.llm.still_current, row["text"], saved, now)):
                dated = f"As of {saved:%d %B %Y}: {row['text']}"
                if training.add_pending(dated, f"Out of date? Saved {(now - saved).days} days ago", "update", row["id"], row["text"]):
                    added += 1
                continue
        for other in [m for m in await memory.similar_memories(row["text"]) if m["id"] != row["id"]][:COMPARE_WITH]:
            pair = frozenset((row["id"], other["id"]))
            if pair in compared or other["id"] in retiring:
                continue
            compared.add(pair)
            word = (await asyncio.to_thread(brain.llm.compare_memories, row["text"], other["text"])).strip().upper()
            if word.startswith("SAME"):
                keep, drop = _keeper(row, other)
                if training.add_pending(drop["text"], f'Repeats: "{keep["text"]}"', "retire", drop["id"]):
                    added += 1
                retiring.add(drop["id"])
                break
    LAST_PATH.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
    print(f"[upkeep] checked her memories: {added} proposals")
    return added
