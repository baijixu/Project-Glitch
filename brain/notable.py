"""What happened that's worth her thinking over: a long talk with him ending, his thumbs-down, a
decision she made, a book finished or put down, a friends' visit. Each is noticed where it happens
and waits in brain/self/notable.json; her next turn of her own time reflects on the oldest
(heartbeat.reflect) before anything else -- and with nothing waiting there's nothing to reflect on."""

import re
from datetime import datetime
from pathlib import Path

from store import JsonList

MAX_ITEMS = 10  # the oldest goes past this: a backlog of stale things isn't worth reflecting on
MAX_DETAIL_CHARS = 2500  # the end of a long talk, not all of it
STORE = JsonList(Path(__file__).parent / "self" / "notable.json", MAX_ITEMS, ("what",))


def waiting() -> list[dict]:
    """[{what, detail, at}], oldest first."""
    return STORE.read()


def for_him(what: str) -> str:
    """`what` as he reads it (the 🧠 window, Think now): it's worded to her -- "you finished R.U.R.", "Josh gave
    one of your replies a thumbs-down" -- so read by him, "you" would be him."""
    return re.sub(r"\byou(rs?)?\b", lambda m: {"": "she", "r": "her", "rs": "hers"}[m[1] or ""], what)


def notice(what: str, detail: str) -> None:
    item = {"what": what.strip(), "detail": detail.strip()[-MAX_DETAIL_CHARS:], "at": datetime.now().isoformat(timespec="seconds")}
    STORE.write([*waiting(), item])


def take() -> dict | None:
    """The oldest thing waiting, off the list -- or None."""
    items = waiting()
    if not items:
        return None
    STORE.write(items[1:])
    return items[0]
