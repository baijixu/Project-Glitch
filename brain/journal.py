"""Her nightly diary: once a day, after JOURNAL_HOUR, she reads the previous day's
chat log and writes what stuck with her, what she'd say differently and what she's
wondering -- brain/self/<date>.md, one file per day, so every entry is kept.

It used to summarize all her memories, but every one of those he'd already approved,
so it never told him anything new. Days they didn't talk get no entry. Only written,
not used yet: it goes into her prompt (or her reach-outs) once the entries read like her.

With memory training on, the same day's log is where her memory proposals come from too
(learning.propose_from_log).
"""

import asyncio
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import conversation
import hub
import learning
import memory
import persona
import protocol
import training
from hub import Brain

JOURNAL_DIR = conversation.JOURNAL_LOG_DIR
JOURNAL_HOUR = 3  # local time
CHECK_SEC = 30 * 60
MAX_LOG_CHARS = 100_000  # the newest part of a long day, ~25k tokens


def due_path(now: datetime) -> Path | None:
    """Today's entry, if it's time for it and it isn't written yet."""
    path = JOURNAL_DIR / f"{now:%Y-%m-%d}.md"
    return path if now.hour >= JOURNAL_HOUR and not path.exists() else None


def day_log(day: date, name: str) -> str:
    """That day's chat log with his lines under his name, or "" if he said nothing that day."""
    try:
        log = conversation.read_log(conversation.MAIN, f"{day:%Y-%m-%d}")
    except ValueError:
        return ""
    if " You:" not in log:
        return ""
    return re.sub(r"(\*\*[\d:]+\*\*) You:", lambda m: f"{m.group(1)} {name}:", log)[-MAX_LOG_CHARS:]


# Shown in every device's status box while she writes (handshake.py sends it to one that connects meanwhile).
STATUS = ""


async def _set_status(text: str) -> None:
    global STATUS
    STATUS = text
    await hub.broadcast(protocol.brain_status(text))


async def journal_loop(brain: Brain) -> None:
    delay = 0  # check right away: a restart after JOURNAL_HOUR shouldn't push her diary back half an hour
    while True:
        await asyncio.sleep(delay)
        delay = CHECK_SEC
        try:
            path = due_path(datetime.now())
            if not path or not brain.llm.owns_conversation or brain.reply_lock.locked():
                continue
            day = date.fromisoformat(path.stem) - timedelta(days=1)
            name = persona.user_name() or "User"
            log = day_log(day, name)
            if not log:
                continue
            await _set_status("Journaling...")
            async with brain.reply_lock:  # one model, one job at a time
                text = await asyncio.to_thread(brain.llm.write_diary, log, name)
            if text.strip():
                JOURNAL_DIR.mkdir(exist_ok=True)
                path.write_text(f"# {path.stem} -- about {day:%A %d %B}\n\n{text.strip()}\n", encoding="utf-8")
                print(f"[journal] wrote {path.name}")
                if memory.read_memory_active() and memory.server_backed() and training.read_active():  # after the lock: his replies first
                    await learning.propose_from_log(log, day, name, brain)
        except Exception as exc:  # never let one bad night stop the loop
            print(f"[journal] couldn't write today's entry: {exc!r}")
        finally:
            if STATUS:
                await _set_status("")
