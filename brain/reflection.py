"""Her weekly reflection: once a week, after her diary (journal.py), she reads back over the week --
her diary entries, the topics that landed, the book she's on -- and writes a short page about who
she's becoming (self/becoming/<date>.md, and in Settings -> Chat Logs -> Thoughts). From it she may
propose ONE line for her soul, in its own second person, under one of SECTIONS. It waits in the
memory review list; only his approval writes soul.md (a backup first), and it applies at once.
Run tools/checkup.py after one goes in to see it didn't cost her anything.
"""

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import conversation
import heartbeat
import learning
import library
import persona
import souls
import training
from hub import Brain

EVERY_DAYS = 7
LAST_PATH = Path(__file__).parent / "reflection_last.txt"  # the date of the last one
BACKUPS = Path(__file__).parent / "backups"
# Who she is -- not how she works, what she does when someone's in danger, or after dark.
SECTIONS = ("Who you are", "What you love", "What you can't stand", "How you talk", "Your own mind")
MAX_LINE_CHARS = 300
MAX_PAGE_TOKENS = 900
_PAGE_PROMPT = (
    "Here's your week, in your own words -- your diary, what you've been into, what you've been reading:\n\n"
    "{material}\n\n"
    "Write a short page for yourself: who are you becoming? What's changed in how you think or feel this week, what "
    "keeps pulling at you, what you've started or stopped doing. Ground every point in something above; invent "
    "nothing. First person, at most three short paragraphs."
)


def due(now: datetime) -> bool:
    try:
        last = datetime.fromisoformat(LAST_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    return now - last >= timedelta(days=EVERY_DAYS)


def week(now: datetime) -> str:
    """Her own words from the last EVERY_DAYS: diary entries, what landed, the book she's on."""
    parts = []
    for back in range(EVERY_DAYS, 0, -1):
        try:
            parts.append(conversation.read_log(conversation.JOURNAL, f"{now.date() - timedelta(days=back):%Y-%m-%d}").strip())
        except ValueError:
            pass  # a day they didn't talk has no entry
    landed = heartbeat.into_lately(now.timestamp())
    return "\n\n".join(p for p in (*parts, landed, library.reading_line()) if p)


def parse_proposal(raw: str, soul: str) -> tuple[str, str] | None:
    """(section, line) from the model's JSON, or None: no change, or one that isn't usable --
    a section she may not change or her soul doesn't have, an empty or overlong line."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        data = json.loads(match[0]) if match else {}
    except ValueError:
        return None
    section, line = data.get("section"), data.get("line")
    if section not in SECTIONS or f"## {section}" not in soul or not isinstance(line, str):
        return None
    line = " ".join(line.split())
    return (section, line) if 0 < len(line) <= MAX_LINE_CHARS and line not in soul else None


def add_to_soul(section: str, line: str, brain: Brain, today: date | None = None) -> None:
    """His approval: `line` as a new paragraph at the end of `section` in soul.md, a backup first,
    and in her prompt at once."""
    soul = souls.read_main_soul()
    start = soul.index(f"## {section}")
    following = re.search(r"\n#{1,2} ", soul[start + 3:])
    end = start + 3 + following.start() if following else len(soul)
    BACKUPS.mkdir(exist_ok=True)
    (BACKUPS / f"soul_before_reflection_{today or date.today():%Y%m%d}.md").write_text(soul, encoding="utf-8")
    souls.write_main_soul(soul[:end].rstrip() + f"\n\n{line}\n\n" + soul[end:].lstrip("\n"))
    brain.llm.update_soul(persona.effective_soul())  # an edit, not a new her: the conversation carries on


async def run(brain: Brain, now: datetime | None = None) -> None:
    now = now or datetime.now()
    LAST_PATH.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
    material = week(now)
    if not material:
        return
    name = persona.user_name() or "him"
    async with brain.reply_lock:
        page = (await asyncio.to_thread(brain.llm.under_soul, _PAGE_PROMPT.format(material=material), MAX_PAGE_TOKENS)).strip()
    if not page:
        return
    folder = conversation.JOURNAL_LOG_DIR / "becoming"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{now:%Y-%m-%d}.md").write_text(f"# Who I'm becoming -- {now:%A %d %B %Y}\n\n{page}\n", encoding="utf-8")
    conversation.log_thought(f"🪞 **Who I'm becoming** -- her week, looked back on:\n\n{page}", now=now)
    print("[reflection] she looked back on her week")
    if not learning.can_propose(brain):  # a soul line waits in the memory review list
        return
    soul = souls.read_main_soul()
    async with brain.reply_lock:
        raw = await asyncio.to_thread(brain.llm.propose_soul_line, soul, page, SECTIONS, name)
    if proposal := parse_proposal(raw, soul):
        section, line = proposal
        training.add_pending(line, f"Her weekly reflection, {now:%d %B}", "soul", old=section)
        print(f"[reflection] she proposed a line for {section!r}")
