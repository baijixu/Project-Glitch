"""One-time: fills brain/metrics.jsonl (the 🧠 window's Growth tab) with the history from before
it existed, so the charts start from what's already there instead of from nothing:

* each memory in her bank as approved on the day it's dated, and her bank's size day by day;
* what she did with her time, from her Thoughts logs (📖/📕 reading, ⚖️ a choice, 🖼️ a picture,
  anything else a thought), her friends' visits (self/visits/) and her diary (self/<date>.md).

Rejections and recalled memories were never kept, so those start now. Every line it writes says
"backfill": true, and it won't run twice. Run it once, from the glitch folder:
    brain\\.venv\\Scripts\\python.exe tools\\backfill_metrics.py
"""

import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BRAIN = Path(__file__).resolve().parent.parent / "brain"
sys.path.insert(0, str(BRAIN))

import memory  # noqa: E402
import metrics  # noqa: E402

THOUGHT = re.compile(r"^\*\*(\d\d:\d\d)(?::\d\d)?\*\* (\S+)", re.MULTILINE)
VISIT = re.compile(r"^\*(\d\d:\d\d:\d\d) -- Visit from", re.MULTILINE)
DIARY = re.compile(r"^\d{4}-\d\d-\d\d\.md$")


def _what(mark: str) -> str | None:
    if mark.startswith(("📖", "📕")):
        return "read"
    if mark.startswith("⚖"):
        return "choose"
    if mark.startswith("🖼"):
        return "look at art"
    if mark.startswith("💡"):
        return None  # a topic landing comes after the thought that tipped it -- already counted
    return "think"


def activities() -> list[dict]:
    rows = []
    for path in sorted((BRAIN / "self" / "thoughts").glob("*.md")):
        for hhmm, mark in THOUGHT.findall(path.read_text(encoding="utf-8")):
            if what := _what(mark):
                rows.append({"t": f"{path.stem}T{hhmm}:00", "e": "activity", "what": what})
    for path in sorted((BRAIN / "self" / "visits").glob("*.md")):
        rows += [{"t": f"{path.stem}T{hms}", "e": "activity", "what": "visit friends"} for hms in VISIT.findall(path.read_text(encoding="utf-8"))]
    for path in sorted((BRAIN / "self").iterdir()):
        if DIARY.match(path.name):  # written the night after the day it's about
            rows.append({"t": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"), "e": "activity", "what": "journal"})
    return rows


async def memories() -> list[dict]:
    await memory.activate()
    if not memory.server_backed():
        print("No memory server -- skipping her memories.")
        return []
    days = sorted(str(row.get("mentioned_at") or "")[:10] for row in await memory.list_memories())
    days = [day for day in days if day]
    rows = [{"t": f"{day}T12:00:00", "e": "approved", "type": "fact"} for day in days]
    per_day, total = Counter(days), 0
    for day in sorted(per_day):
        total += per_day[day]
        rows.append({"t": f"{day}T23:59:00", "e": "bank", "size": total})
    return rows


def main() -> None:
    if any(row.get("backfill") for row in metrics.read()):
        sys.exit("Already backfilled -- nothing to do.")
    rows = activities() + asyncio.run(memories())
    with metrics.PATH.open("a", encoding="utf-8") as f:  # their own times, so not metrics.log
        f.writelines(json.dumps({**row, "backfill": True}, ensure_ascii=False) + "\n" for row in rows)
    print(f"Added {len(rows)} lines: {Counter(row.get('what') or row['e'] for row in rows).most_common()}")


if __name__ == "__main__":
    main()
