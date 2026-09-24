"""Her conversation, kept on disk -- two separate things:

* conversation.json -- what she currently sees of the conversation (LocalLLM's
  _history), saved every time it changes and restored when Brain starts or the
  LLM engine is switched. Without it, a Brain restart wiped her short-term memory
  while the chat history panel still showed everything. Tagged with the mode it
  belongs to ("main" or "roleplay"), and only restored into the same mode, so a
  role-play scene can't come back as part of a normal chat or vice versa.
  Pictures are stored as a short "[picture]" note, not the image data.

* chat_logs/YYYY-MM-DD.md -- a plain, timestamped, append-only log of every
  exchange, one file per day, for the user to read. Never read back by Brain.

Both live beside this file and are gitignored (they're the user's conversations).
"""

import json
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).parent
STATE_PATH = _DIR / "conversation.json"
LOG_DIR = _DIR / "chat_logs"

MAIN, ROLEPLAY = "main", "roleplay"
PICTURE_NOTE = "[picture]"


def _storable(message: dict) -> dict:
    """A history message with any image replaced by PICTURE_NOTE."""
    content = message.get("content")
    if not isinstance(content, list):
        return {"role": message.get("role"), "content": content}
    text = "\n".join(part.get("text", "") for part in content if part.get("type") == "text").strip()
    has_image = any(part.get("type") == "image_url" for part in content)
    return {"role": message.get("role"), "content": f"{text} {PICTURE_NOTE}".strip() if has_image else text}


def save_state(history: list[dict], mode: str) -> None:
    """Best-effort: a disk problem must never break a reply."""
    try:
        data = {"mode": mode, "saved": datetime.now().isoformat(timespec="seconds"), "messages": [_storable(m) for m in history]}
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_PATH)  # atomic: a crash mid-write can't leave a half-written file
    except OSError as exc:
        print(f"[conversation] couldn't save the conversation: {exc!r}")


def load_state(mode: str) -> list[dict]:
    """The saved conversation if it belongs to `mode`, else []. Anything unreadable is []."""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("mode") != mode or not isinstance(data.get("messages"), list):
        return []
    return [
        {"role": m["role"], "content": m["content"]}
        for m in data["messages"]
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]


def log_exchange(user_text: str, reply_text: str, *, roleplay: bool, picture: bool = False, now: datetime | None = None) -> None:
    """Appends one exchange to today's log. Best-effort, like save_state."""
    now = now or datetime.now()
    tag = " (role-play)" if roleplay else ""
    said = (user_text.strip() + (f" {PICTURE_NOTE}" if picture else "")).strip() or PICTURE_NOTE
    entry = f"**{now:%H:%M:%S}** You{tag}: {said}\n\n**{now:%H:%M:%S}** Glitch{tag}: {reply_text.strip()}\n\n"
    try:
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"{now:%Y-%m-%d}.md"
        header = "" if path.exists() else f"# Chat log -- {now:%A %d %B %Y}\n\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(header + entry)
    except OSError as exc:
        print(f"[conversation] couldn't write the chat log: {exc!r}")
