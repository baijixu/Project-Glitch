"""What happened that's worth her thinking over: a long talk with him ending, his thumbs-down, a
decision she made, a book finished or put down, a friends' visit. Each is noticed where it happens
and waits in brain/self/notable.json; her next turn of her own time reflects on the oldest
(heartbeat.reflect) before anything else -- and with nothing waiting there's nothing to reflect on."""

import json
from datetime import datetime
from pathlib import Path

PATH = Path(__file__).parent / "self" / "notable.json"
MAX_ITEMS = 10  # the oldest goes past this: a backlog of stale things isn't worth reflecting on
MAX_DETAIL_CHARS = 2500  # the end of a long talk, not all of it


def waiting() -> list[dict]:
    """[{what, detail, at}], oldest first."""
    try:
        items = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("what")] if isinstance(items, list) else []


def _write(items: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(items[-MAX_ITEMS:], ensure_ascii=False, indent=1), encoding="utf-8")


def notice(what: str, detail: str) -> None:
    item = {"what": what.strip(), "detail": detail.strip()[-MAX_DETAIL_CHARS:], "at": datetime.now().isoformat(timespec="seconds")}
    _write([*waiting(), item])


def take() -> dict | None:
    """The oldest thing waiting, off the list -- or None."""
    items = waiting()
    if not items:
        return None
    _write(items[1:])
    return items[0]
