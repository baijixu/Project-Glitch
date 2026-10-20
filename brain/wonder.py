"""What she wants to find out: her own questions -- not about him (those wait in his 📥 inbox,
curiosity.py) -- from her topics, her diary, her landed pages and his chats, in brain/self/wonder.json.
On her own time she may look one up (heartbeat "look") or bring one to a friends' visit (friends.
helping_her_think); he sees the list in the 🧠 window and can add to it or take things off."""

import uuid
from datetime import datetime
from pathlib import Path

import curiosity
import persona
from store import JsonList

MAX_ITEMS = 30  # the oldest goes when a new one comes past this
MAX_CHARS = 300
STORE = JsonList(Path(__file__).parent / "self" / "wonder.json", MAX_ITEMS, ("id", "text"))


def read() -> list[dict]:
    """[{id, text, source, added}], oldest first."""
    return STORE.read()


def add(text: str, source: str = "") -> dict | None:
    """None for an empty, overlong or repeated one."""
    text = " ".join(text.split()).strip(" -*#\"")
    items = read()
    if not text or len(text) > MAX_CHARS or text.lower() in {i["text"].lower() for i in items}:
        return None
    item = {"id": uuid.uuid4().hex[:8], "text": text, "source": source.strip()[:120], "added": datetime.now().isoformat(timespec="seconds")}
    STORE.write([*items, item])
    return item


def remove(item_id: str) -> dict | None:
    items = read()
    gone = next((i for i in items if i["id"] == item_id), None)
    if gone:
        STORE.write([i for i in items if i["id"] != item_id])
    return gone


def keep(question: str, source: str) -> None:
    """One of her questions, wherever it came from: hers to find out, or -- about him -- his inbox."""
    if persona.not_about_him(question):
        add(question, source)
    elif curiosity.read_active():
        curiosity.add_question(question)
