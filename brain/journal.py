"""Her nightly journal: once a day, after JOURNAL_HOUR, she reads all her memories
and writes who they say she is -- brain/self/<date>.md, one file per day, so every
version is kept and any drift (toward herself, or toward the user) can be read back.

Only written, not used yet: it goes into her prompt once the entries read like her.
Early ones were mostly about the user and cast her as his carer -- her memories
were, at the time.
"""

import asyncio
from datetime import datetime
from pathlib import Path

import memory
from hub import Brain

JOURNAL_DIR = Path(__file__).parent / "self"
JOURNAL_HOUR = 3  # local time
CHECK_SEC = 30 * 60


def due_path(now: datetime) -> Path | None:
    """Today's entry, if it's time for it and it isn't written yet."""
    path = JOURNAL_DIR / f"{now:%Y-%m-%d}.md"
    return path if now.hour >= JOURNAL_HOUR and not path.exists() else None


async def journal_loop(brain: Brain) -> None:
    # ponytail: rewrites daily even with no new memories; skip unchanged days if that gets noisy.
    while True:
        await asyncio.sleep(CHECK_SEC)
        try:
            path = due_path(datetime.now())
            if not path or not brain.llm.owns_conversation or not memory.read_memory_active() or brain.reply_lock.locked():
                continue
            entries = await memory.read_entries()
            if not entries:
                continue
            async with brain.reply_lock:  # one model, one job at a time
                text = await asyncio.to_thread(brain.llm.write_self_portrait, entries)
            if text.strip():
                JOURNAL_DIR.mkdir(exist_ok=True)
                path.write_text(f"# {path.stem} -- from {len(entries)} memories\n\n{text.strip()}\n", encoding="utf-8")
                print(f"[journal] wrote {path.name}")
        except Exception as exc:  # never let one bad night stop the loop
            print(f"[journal] couldn't write today's entry: {exc!r}")
