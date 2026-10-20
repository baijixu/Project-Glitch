"""His journal (Settings -> Your journal): dated entries he writes about his own life, so she knows what's
going on when he isn't talking to her. She reads them in two places only: her nightly diary (the entries he
wrote that day, so each reaches it once) and when she reaches out after a quiet spell (his last RECENT_DAYS).
Nothing else sees them -- not her memory proposals, her Thoughts, her friends or a web search -- and an entry
he hides is never used. Every one is framed as his: in his words "I" is him, which she once took for herself
(his dream, written up as hers in a reflection)."""

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import websockets

import hub
import protocol
from hub import Brain
from store import JsonList

MAX_ENTRIES = 60
MAX_CHARS = 2000
RECENT_DAYS = 2  # what she may bring up when she reaches out
RECENT_SHOWN = 2
STORE = JsonList(Path(__file__).parent / "his_journal.json", MAX_ENTRIES, ("id", "text"))


def entries() -> list[dict]:
    """[{id, text, written (ISO), hidden}], oldest first."""
    return STORE.read()


def add(text: str, now: datetime | None = None) -> dict | None:
    text = text.strip()[:MAX_CHARS]
    if not text:
        return None
    item = {"id": uuid.uuid4().hex[:8], "text": text, "written": (now or datetime.now()).isoformat(timespec="minutes"), "hidden": False}
    STORE.write([*entries(), item])
    return item


def _usable(since: datetime, until: datetime | None = None) -> list[dict]:
    found = [e for e in entries() if not e.get("hidden") and since.isoformat() <= e.get("written", "") < (until or datetime.max).isoformat()]
    return sorted(found, key=lambda e: e["written"])


def for_her(items: list[dict], name: str) -> str:
    """Entries as she reads them -- his, in his words -- or "" for none."""
    if not items:
        return ""
    lines = "\n".join(f"- {datetime.fromisoformat(e['written']):%A %d %B, %I:%M %p}: {e['text']}" for e in items)
    return (
        f"{name}'s own journal -- his life, in his words (\"I\" in it is {name}, not you). What he wrote about is his: "
        f"think and talk about it as his.\n{lines}"
    )


def of_day(day: date, name: str) -> str:
    """What he wrote on `day`, for her diary about that day."""
    start = datetime.combine(day, datetime.min.time())
    return for_her(_usable(start, start + timedelta(days=1)), name)


def recent(name: str, now: datetime | None = None) -> str:
    """His last RECENT_SHOWN entries from the last RECENT_DAYS, for when she reaches out."""
    return for_her(_usable((now or datetime.now()) - timedelta(days=RECENT_DAYS))[-RECENT_SHOWN:], name)


@hub.handles(protocol.GET_HIS_JOURNAL, protocol.ADD_JOURNAL_ENTRY, protocol.HIDE_JOURNAL_ENTRY, protocol.DELETE_JOURNAL_ENTRY)
async def _journal(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Settings -> Your journal: write an entry, hide one from her (or show it again), or delete one."""
    kind, entry_id = data["type"], str(data.get("id") or "")
    if kind == protocol.ADD_JOURNAL_ENTRY:
        add(str(data.get("text") or ""))
    elif kind in (protocol.HIDE_JOURNAL_ENTRY, protocol.DELETE_JOURNAL_ENTRY):
        items = entries()
        if kind == protocol.DELETE_JOURNAL_ENTRY:
            items = [e for e in items if e["id"] != entry_id]
        else:
            items = [{**e, "hidden": bool(data.get("hidden"))} if e["id"] == entry_id else e for e in items]
        STORE.write(items)
    if kind == protocol.GET_HIS_JOURNAL:
        await hub.send(websocket, protocol.his_journal(entries()[::-1]))
    else:
        await hub.broadcast(protocol.his_journal(entries()[::-1]))
