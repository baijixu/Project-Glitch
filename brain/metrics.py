"""Her growth in numbers, for the 🧠 window's Growth tab: one JSON line per event in
brain/metrics.jsonl -- memory proposals and his verdicts on them, the memories recalled for
each reply, what she does with her time, soul.md edits, his thumbs, LLM tokens, and once a
day how big her memory bank is and how much is waiting for him (memories and lessons). Append-only, local, never in her prompt;
growth() turns it into the weekly and daily series the charts draw.

Only standard-library imports: training, souls and llm log here, so this can't import them back."""

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

PATH = Path(__file__).parent / "metrics.jsonl"
WEEKS_SHOWN = 12
DAYS_SHOWN = 30
TOP_RECALLED = 10
# Proposals that change her soul or tidy existing memories aren't new memories: they get charts of their own.
NOT_NEW_MEMORIES = ("soul", "upkeep")
_LEARNED = re.compile(r" \(learned [^)]*\)$")  # memory._dated's age, which changes daily while the memory doesn't

_snapshot_day = ""


def log(event: str, **detail) -> None:
    """Never raises: a metrics hiccup must not cost a reply or an approval."""
    line = {"t": datetime.now().isoformat(timespec="seconds"), "e": event, **detail}
    try:
        with PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[metrics] couldn't log {event}: {exc!r}")


def log_recalled(memory_block: str) -> None:
    log("recalled", memories=[_LEARNED.sub("", line[2:]) for line in memory_block.splitlines() if line.startswith("- ")])


def snapshot_due() -> bool:
    """True once a day (the first time it's asked, unless today's snapshot is already in the log)."""
    global _snapshot_day
    today = date.today().isoformat()
    if _snapshot_day == today:
        return False
    _snapshot_day = today
    return not any(row["e"] == "bank" and row["t"].startswith(today) for row in read())


def read() -> list[dict]:
    if not PATH.exists():
        return []
    rows = []
    for line in PATH.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and "t" in row and "e" in row:
            rows.append(row)
    return sorted(rows, key=lambda row: row["t"])  # backfilled history is appended after newer lines


def today(now: datetime | None = None) -> list[dict]:
    """Today's rows as they happened -- not tools/backfill_metrics.py's history, which is dated by when it was."""
    day = (now or datetime.now()).date().isoformat()
    return [row for row in read() if row["t"].startswith(day) and not row.get("backfill")]


def _bucket(day: date, period: str) -> date:
    return day - timedelta(days=day.weekday()) if period == "week" else day


def growth(today: date | None = None) -> dict:
    """{"week": series, "day": series, "top": [[memory, times recalled]]}. A series covers the last
    WEEKS_SHOWN weeks (Mondays) or DAYS_SHOWN days, starting at the first one with anything in it."""
    rows, today = read(), today or date.today()
    recalled = Counter(m for row in rows if row["e"] == "recalled" for m in set(row.get("memories", [])))
    first_recall = next((date.fromisoformat(row["t"][:10]) for row in rows if row["e"] == "recalled"), None)
    out: dict = {"top": [list(pair) for pair in recalled.most_common(TOP_RECALLED)]}
    for period, count, step in (("week", WEEKS_SHOWN, 7), ("day", DAYS_SHOWN, 1)):
        keys = [_bucket(today, period) - timedelta(days=step * i) for i in range(count - 1, -1, -1)]
        index = {key: i for i, key in enumerate(keys)}
        approved: dict = defaultdict(lambda: [0] * count)
        distinct = [set() for _ in keys]
        bank: list = [None] * count
        waiting: dict = {"memories": [None] * count, "lessons": [None] * count}
        activities: Counter = Counter()
        first = count
        for row in rows:
            i = index.get(_bucket(date.fromisoformat(row["t"][:10]), period))
            if i is None:
                continue
            first = min(first, i)
            if row["e"] == "approved" and row.get("type") not in NOT_NEW_MEMORIES:
                approved[row.get("type") or "fact"][i] += 1
            elif row["e"] == "recalled":
                distinct[i].update(row.get("memories", []))
            elif row["e"] == "bank":
                bank[i] = row.get("size")  # in time order, so the day's (or week's) last one
                if "queue" in row:
                    waiting["memories"][i], waiting["lessons"][i] = row["queue"], row.get("lessons", 0)
            elif row["e"] == "activity":
                activities[row.get("what") or "other"] += 1
        first = min(first, count - 1)  # an empty log still shows this week / today
        since = _bucket(first_recall, period) if first_recall else None
        tracked = next((i for i, key in enumerate(keys) if since and key >= since), count)
        out[period] = {
            "buckets": [key.isoformat() for key in keys[first:]],
            "approved": {kind: series[first:] for kind, series in approved.items()},
            # None before recall was first logged: those weeks weren't tracked, not empty.
            "recalled": [len(s) if i >= tracked else None for i, s in enumerate(distinct) if i >= first],
            "bank": bank[first:],
            "waiting": {kind: series[first:] for kind, series in waiting.items()},
            "activities": [list(pair) for pair in activities.most_common()],
        }
    return out
