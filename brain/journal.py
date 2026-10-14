"""Her nightly diary: once a day, after JOURNAL_HOUR, she reads the previous day's
chat log -- and what went through her head that day (Heartbeat's thoughts and reading
notes) -- and writes what stuck with her, what she'd say differently and what she's
wondering -- brain/self/<date>.md, named for the day it's about (like that day's chat
log), one file per day, so every entry is kept. Days they didn't talk get no entry. The
newest one is in her prompt, and its question can start a reach-out.

With memory training on, the same day's log is where her memory proposals come from too
(learning.propose_from_log).
"""

import asyncio
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import conversation
import curiosity
import hub
import learning
import persona
import protocol
import reflection
import upkeep
from hub import Brain

JOURNAL_DIR = conversation.JOURNAL_LOG_DIR
JOURNAL_HOUR = 1  # local time
CHECK_SEC = 30 * 60
MAX_LOG_CHARS = 100_000  # the newest part of a long day, ~25k tokens
MAX_THOUGHT_CHARS = 20_000  # her Heartbeat thoughts and reading notes that day, newest kept


def due_path(now: datetime) -> Path | None:
    """Yesterday's entry, if it's time for it (after JOURNAL_HOUR, once that day is over)
    and it isn't written yet."""
    path = JOURNAL_DIR / f"{now.date() - timedelta(days=1):%Y-%m-%d}.md"
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


def day_thoughts(day: date) -> str:
    """What went through her head that day while nobody talked to her (heartbeat.py), or ""."""
    try:
        log = conversation.read_log(conversation.THOUGHTS, f"{day:%Y-%m-%d}")
    except ValueError:
        return ""
    return log.split("\n", 1)[-1].strip()[-MAX_THOUGHT_CHARS:]  # without the "# Thoughts -- <day>" title


# Shown in every device's status box while she writes (handshake.py sends it to one that connects meanwhile).
STATUS = ""


async def set_status(text: str) -> None:
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
            day = date.fromisoformat(path.stem)
            name = persona.user_name() or "User"
            log = day_log(day, name)
            if not log:
                continue
            await set_status("Journaling...")
            async with brain.reply_lock:  # one model, one job at a time
                text = await asyncio.to_thread(brain.llm.write_diary, log, name, day_thoughts(day))
            if text.strip():
                JOURNAL_DIR.mkdir(exist_ok=True)
                path.write_text(f"# {path.stem} -- {day:%A %d %B}\n\n{text.strip()}\n", encoding="utf-8")
                print(f"[journal] wrote {path.name}")
                if (question := curiosity.question_after("What I.m wondering", text)) and curiosity.read_active():
                    curiosity.add_question(question)  # his inbox, and her next reach-out
                await learning.lesson_from_diary(text, brain)  # after the lock, like her memory proposals
                await learning.propose_unread(day, brain)  # after the lock: his replies first
                if learning.can_propose(brain) and upkeep.due(datetime.now()):
                    await upkeep.run(brain)
                    await learning.broadcast_training_state()
                if reflection.due(datetime.now()):  # her week, looked back on, and maybe a line for her soul
                    await reflection.run(brain)
                    await learning.broadcast_training_state()
        except Exception as exc:  # never let one bad night stop the loop
            print(f"[journal] couldn't write today's entry: {exc!r}")
        finally:
            if STATUS:
                await set_status("")
