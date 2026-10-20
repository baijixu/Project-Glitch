"""Her conversation, kept on disk -- two separate things:

* conversation.json / conversation_roleplay.json -- what she currently sees of
  the conversation (LocalLLM._history), one file per mode, saved every time it
  changes and restored when Brain starts, the LLM engine is switched, or
  role-play is toggled. Separate files, so a role-play scene can't overwrite the
  normal conversation: turning role-play off brings it back, and turning it on
  resumes the last scene. Each file is tagged with its mode and only restored into it. Pictures are stored as a short "[picture]" note, not
  the image data. A private chat stays in it (his phone syncs from it), between PRIVATE_START and PRIVATE_END.

* chat_logs/YYYY-MM-DD.md -- a plain, timestamped, append-only log of every
  exchange (none in private chat), one file per day, for the user to read.

Both live beside this file and are gitignored (they're the user's conversations).
"""

import re
from datetime import datetime
from pathlib import Path

from store import Toggle, read_json, write_json

_DIR = Path(__file__).parent
STATE_PATH = _DIR / "conversation.json"  # normal chat (the original file name, kept)
ROLEPLAY_STATE_PATH = _DIR / "conversation_roleplay.json"
LOG_DIR = _DIR / "chat_logs"
# Role-play gets its own folder, so the two can be read and deleted separately
# (Settings -> Chat Logs).
ROLEPLAY_LOG_DIR = LOG_DIR / "roleplay"
# Her nightly journal (journal.py), read from the same Settings section.
JOURNAL_LOG_DIR = _DIR / "self"
# Her note on where the last conversation left off, written as Clear Chat ends it (reply.py).
LEFT_OFF_PATH = _DIR / "left_off.md"

# Private chat (the PM badge): what he and she say isn't written to the logs, so her diary and the
# Chat Logs never see it. Everything else carries on -- her memories, the chat on screen.
PRIVATE = Toggle(_DIR / "private_active.txt", default=False)

MAIN, ROLEPLAY, JOURNAL, THOUGHTS, VISITS ="main", "roleplay", "journal", "thoughts", "visits"  # thoughts: heartbeat.py, visits: friends.py
PICTURE_NOTE = "[picture]"


def text_of(content) -> str:
    """A message's words: its text, or for a picture turn its text plus PICTURE_NOTE."""
    if not isinstance(content, list):
        return str(content or "")
    text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text").strip()
    return f"{text} {PICTURE_NOTE}".strip() if any(part.get("type") == "image_url" for part in content) else text


def _storable(message: dict) -> dict:
    """A history message with any image replaced by PICTURE_NOTE."""
    when = {"at": message["at"]} if isinstance(message.get("at"), str) else {}  # when it was said (llm.py)
    private = {"private": True} if message.get("private") else {}
    shown = {"pictures": message["pictures"]} if message.get("pictures") else {}  # the pictures she showed (pictures.py)
    return {"role": message.get("role"), "content": text_of(message.get("content")), **when, **private, **shown}


# A private stretch, marked off in conversation.json: kept so the chat still syncs to his phone,
# but anyone reading the file skips from START to END. Not messages: load_state drops them.
PRIVATE_START = {"role": "private", "content": "=== PRIVATE CHAT START -- skip everything until PRIVATE CHAT END ==="}
PRIVATE_END = {"role": "private", "content": "=== PRIVATE CHAT END ==="}


def _marked(messages: list[dict]) -> list[dict]:
    """`messages` with PRIVATE_START before each private stretch and PRIVATE_END after it."""
    out, inside = [], False
    for message in messages:
        if bool(message.get("private")) != inside:
            inside = not inside
            out.append(PRIVATE_START if inside else PRIVATE_END)
        out.append(message)
    return out


def _path(mode: str):
    return ROLEPLAY_STATE_PATH if mode == ROLEPLAY else STATE_PATH


def save_state(history: list[dict], mode: str) -> None:
    """Best-effort: a disk problem must never break a reply."""
    try:
        data = {"mode": mode, "saved": datetime.now().isoformat(timespec="seconds"), "messages": _marked([_storable(m) for m in history])}
        write_json(_path(mode), data)
    except OSError as exc:
        print(f"[conversation] couldn't save the conversation: {exc!r}")


def load_state(mode: str) -> list[dict]:
    """The saved conversation if it belongs to `mode`, else []. Anything unreadable is []."""
    data = read_json(_path(mode), {})
    if data.get("mode") != mode or not isinstance(data.get("messages"), list):
        return []
    return [
        {"role": m["role"], "content": m["content"], **({"at": m["at"]} if isinstance(m.get("at"), str) else {}),
         **({"private": True} if m.get("private") is True else {}),
         **({"pictures": [p for p in m["pictures"] if isinstance(p, str)]} if isinstance(m.get("pictures"), list) else {})}
        for m in data["messages"]
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"].strip()  # a blank turn (an old empty reply) is dropped, not restored
    ]


def clear_state(mode: str) -> None:
    """Forgets the saved conversation for `mode` (e.g. the old scene, once a
    different role-play profile or soul is picked)."""
    try:
        _path(mode).unlink(missing_ok=True)
    except OSError as exc:
        print(f"[conversation] couldn't clear the saved {mode} conversation: {exc!r}")


def save_left_off(note: str, now: datetime | None = None) -> None:
    """Her note on where a cleared conversation left off, dated. Best-effort."""
    now = now or datetime.now()
    try:
        LEFT_OFF_PATH.write_text(f"(Written {now:%A} {now.day} {now:%B}, {now:%H:%M}.) {note.strip()}", encoding="utf-8")
    except OSError as exc:
        print(f"[conversation] couldn't save where we left off: {exc!r}")


def read_left_off() -> str:
    try:
        return LEFT_OFF_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_latest_journal() -> str:
    """Her newest diary entry (journal.py), or "" before her first."""
    days = list_logs(JOURNAL)
    return read_log(JOURNAL, days[0]["date"]).strip() if days else ""


def _log_dir(mode: str) -> Path:
    return {ROLEPLAY: ROLEPLAY_LOG_DIR, JOURNAL: JOURNAL_LOG_DIR, THOUGHTS: JOURNAL_LOG_DIR / "thoughts", VISITS: JOURNAL_LOG_DIR / "visits"}.get(mode, LOG_DIR)


def _append(mode: str, text: str, now: datetime) -> None:
    """Appends to today's log for this mode, starting it with a heading. Best-effort."""
    try:
        folder = _log_dir(mode)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{now:%Y-%m-%d}.md"
        title = {ROLEPLAY: "Role-play log", THOUGHTS: "Thoughts", VISITS: "Visits"}.get(mode, "Chat log")
        header = "" if path.exists() else f"# {title} -- {now:%A %d %B %Y}\n\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(header + text)
    except OSError as exc:
        print(f"[conversation] couldn't write the chat log: {exc!r}")


def log_marker(text: str, now: datetime | None = None, *, mode: str = MAIN) -> None:
    """A one-line note in today's log, e.g. where the chat was cleared."""
    now = now or datetime.now()
    _append(mode, f"---\n\n*{now:%H:%M:%S} -- {text}*\n\n", now)


def log_reach_out(text: str, *, now: datetime | None = None) -> None:
    """Her message when she spoke first (reach_out.py's _reach_out) -- no "You:" line."""
    if PRIVATE.read():
        return
    now = now or datetime.now()
    _append(MAIN, f"**{now:%H:%M:%S}** Glitch (reaching out): {text.strip()}\n\n", now)


def log_thought(text: str, *, now: datetime | None = None) -> None:
    """One of her own passing thoughts (heartbeat.py), in today's thoughts log."""
    now = now or datetime.now()
    _append(THOUGHTS, f"**{now:%H:%M}** {text.strip()}\n\n", now)


def log_visit(text: str, *, now: datetime | None = None) -> None:
    """Part of a friends' visit (friends.py), in today's visits log."""
    now = now or datetime.now()
    _append(VISITS, text, now)


def log_exchange(user_text: str, reply_text: str, *, roleplay: bool, picture: bool = False, now: datetime | None = None) -> None:
    """Appends one exchange to today's log -- the role-play one when role-play is on."""
    if PRIVATE.read():
        return
    now = now or datetime.now()
    said = (user_text.strip() + (f" {PICTURE_NOTE}" if picture else "")).strip() or PICTURE_NOTE
    entry = f"**{now:%H:%M:%S}** You: {said}\n\n**{now:%H:%M:%S}** Glitch: {reply_text.strip()}\n\n"
    _append(ROLEPLAY if roleplay else MAIN, entry, now)


# -- Reading and deleting (Settings -> Chat Logs) -----------------------------

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day_path(mode: str, day: str) -> Path:
    """The log file for one day. Only a plain YYYY-MM-DD date is accepted, so a
    request can never reach a file outside the log folder."""
    if mode not in (MAIN, ROLEPLAY, JOURNAL, THOUGHTS, VISITS) or not isinstance(day, str) or not _DAY.match(day):
        raise ValueError(f"not a chat log: {mode!r} {day!r}")
    return _log_dir(mode) / f"{day}.md"


def list_logs(mode: str) -> list[dict]:
    """[{"date", "size"}], newest first."""
    folder = _log_dir(mode)
    if not folder.exists():
        return []
    days = [p for p in folder.glob("*.md") if _DAY.match(p.stem)]
    return [{"date": p.stem, "size": p.stat().st_size} for p in sorted(days, key=lambda p: p.stem, reverse=True)]


def read_log(mode: str, day: str) -> str:
    path = _day_path(mode, day)
    if not path.exists():
        raise ValueError(f"there's no {mode} log for {day}")
    return path.read_text(encoding="utf-8")


def write_log(mode: str, day: str, content: str) -> None:
    """Replaces an existing day's log with the user's edited text (Settings -> Chat Logs' Edit)."""
    path = _day_path(mode, day)
    if not path.exists():
        raise ValueError(f"there's no {mode} log for {day}")
    path.write_text(content, encoding="utf-8")


def delete_log(mode: str, day: str) -> None:
    path = _day_path(mode, day)
    if not path.exists():
        raise ValueError(f"there's no {mode} log for {day}")
    path.unlink()

