"""Her conversation, kept on disk -- two separate things:

* conversation.json / conversation_roleplay.json -- what she currently sees of
  the conversation (LocalLLM._history), one file per mode, saved every time it
  changes and restored when Brain starts, the LLM engine is switched, or
  role-play is toggled. Without it, a Brain restart wiped her short-term memory
  while the chat history panel still showed everything. Separate files (they
  used to share one) so a role-play session can't overwrite the normal
  conversation: turning role-play off brings the normal one back, and turning it
  on resumes the last scene. Each file is also tagged with its mode, and only
  restored into that mode. Pictures are stored as a short "[picture]" note, not
  the image data.

* chat_logs/YYYY-MM-DD.md -- a plain, timestamped, append-only log of every
  exchange, one file per day, for the user to read. Never read back by Brain.

Both live beside this file and are gitignored (they're the user's conversations).
"""

import json
import re
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).parent
STATE_PATH = _DIR / "conversation.json"  # normal chat (the original file name, kept)
ROLEPLAY_STATE_PATH = _DIR / "conversation_roleplay.json"
LOG_DIR = _DIR / "chat_logs"
# Role-play gets its own folder, so the two can be read and deleted separately
# (Settings -> Chat Logs). Older logs had both in one file, role-play lines
# tagged "(role-play)" -- split_mixed_logs() moves those out once.
ROLEPLAY_LOG_DIR = LOG_DIR / "roleplay"
BACKUP_DIR = _DIR / "backups"

MAIN, ROLEPLAY = "main", "roleplay"
PICTURE_NOTE = "[picture]"


def _storable(message: dict) -> dict:
    """A history message with any image replaced by PICTURE_NOTE."""
    content = message.get("content")
    when = {"at": message["at"]} if isinstance(message.get("at"), str) else {}  # when it was said (llm/client.py)
    if not isinstance(content, list):
        return {"role": message.get("role"), "content": content, **when}
    text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text").strip()
    has_image = any(part.get("type") == "image_url" for part in content)
    return {"role": message.get("role"), "content": f"{text} {PICTURE_NOTE}".strip() if has_image else text, **when}


def _path(mode: str):
    return ROLEPLAY_STATE_PATH if mode == ROLEPLAY else STATE_PATH


def save_state(history: list[dict], mode: str) -> None:
    """Best-effort: a disk problem must never break a reply."""
    try:
        data = {"mode": mode, "saved": datetime.now().isoformat(timespec="seconds"), "messages": [_storable(m) for m in history]}
        path = _path(mode)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)  # atomic: a crash mid-write can't leave a half-written file
    except OSError as exc:
        print(f"[conversation] couldn't save the conversation: {exc!r}")


def load_state(mode: str) -> list[dict]:
    """The saved conversation if it belongs to `mode`, else []. Anything unreadable is []."""
    try:
        data = json.loads(_path(mode).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("mode") != mode or not isinstance(data.get("messages"), list):
        return []
    return [
        {"role": m["role"], "content": m["content"], **({"at": m["at"]} if isinstance(m.get("at"), str) else {})}
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


def _log_dir(mode: str) -> Path:
    return ROLEPLAY_LOG_DIR if mode == ROLEPLAY else LOG_DIR


def _append(mode: str, text: str, now: datetime) -> None:
    """Appends to today's log for this mode, starting it with a heading. Best-effort."""
    try:
        folder = _log_dir(mode)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{now:%Y-%m-%d}.md"
        title = "Role-play log" if mode == ROLEPLAY else "Chat log"
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
    """Her message when she spoke first (main.py's _reach_out) -- no "You:" line."""
    now = now or datetime.now()
    _append(MAIN, f"**{now:%H:%M:%S}** Glitch (reaching out): {text.strip()}\n\n", now)


def log_exchange(user_text: str, reply_text: str, *, roleplay: bool, picture: bool = False, now: datetime | None = None) -> None:
    """Appends one exchange to today's log -- the role-play one when role-play is on."""
    now = now or datetime.now()
    said = (user_text.strip() + (f" {PICTURE_NOTE}" if picture else "")).strip() or PICTURE_NOTE
    entry = f"**{now:%H:%M:%S}** You: {said}\n\n**{now:%H:%M:%S}** Glitch: {reply_text.strip()}\n\n"
    _append(ROLEPLAY if roleplay else MAIN, entry, now)


# -- Reading and deleting (Settings -> Chat Logs) -----------------------------

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day_path(mode: str, day: str) -> Path:
    """The log file for one day. Only a plain YYYY-MM-DD date is accepted, so a
    request can never reach a file outside the log folder."""
    if mode not in (MAIN, ROLEPLAY) or not isinstance(day, str) or not _DAY.match(day):
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


def delete_log(mode: str, day: str) -> None:
    path = _day_path(mode, day)
    if not path.exists():
        raise ValueError(f"there's no {mode} log for {day}")
    path.unlink()


# Where a new entry starts in a log: a timestamped message, or a "---" /
# "*time -- note*" marker. A message's own text can span several lines (and
# blank lines), so everything up to the next one of these belongs to it.
_ENTRY_START = re.compile(r"^(\*\*\d{2}:\d{2}:\d{2}\*\* |---$|\*\d{2}:\d{2}:\d{2} -- )")
_ROLEPLAY_TAG = re.compile(r"^(\*\*\d{2}:\d{2}:\d{2}\*\* (?:You|Glitch)) \(role-play\):")


def split_mixed_logs() -> int:
    """One-time move of role-play lines out of older mixed logs into the
    role-play folder (their "(role-play)" tag dropped -- the folder says it now).
    Each file is copied to backups/ before it's changed. Returns how many files
    were split; files with no role-play in them are left alone.
    """
    split = 0
    if not LOG_DIR.exists():
        return 0
    for path in sorted(LOG_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if "(role-play):" not in text:
            continue
        backup = BACKUP_DIR / "chat_logs_before_roleplay_split"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / path.name).write_text(text, encoding="utf-8")
        header, entries, current = [], [], None
        for line in text.split("\n"):
            if _ENTRY_START.match(line):
                current = [line]
                entries.append(current)
            elif current is None:
                header.append(line)
            else:
                current.append(line)
        main_part, roleplay_part = [], []
        for entry in entries:
            tagged = _ROLEPLAY_TAG.match(entry[0])
            if tagged:
                roleplay_part.append([_ROLEPLAY_TAG.sub(r"\1:", entry[0])] + entry[1:])
            else:
                main_part.append(entry)
        path.write_text("\n".join(header + [line for e in main_part for line in e]).rstrip("\n") + "\n\n", encoding="utf-8")
        ROLEPLAY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        target = ROLEPLAY_LOG_DIR / path.name
        existing = target.read_text(encoding="utf-8") if target.exists() else f"# Role-play log -- {path.stem}\n\n"
        moved = "\n".join(line for e in roleplay_part for line in e).rstrip("\n") + "\n\n"
        target.write_text(existing + moved, encoding="utf-8")
        split += 1
    return split
