"""Her weekly reflection: once a week, after her diary (journal.py) -- or when he asks, from the 🧠
window -- she reads back over the week (her diary entries, the topics that landed, the book she's
on) and writes a short page about who she's becoming (self/becoming/<date>.md, and in Settings ->
Chat Logs -> Thoughts). Then, with that page and the memories she made this week, she rewrites the
parts of her soul that are about who she is (SECTIONS), so they're true of her now -- without that,
the soul he started her from could only grow, never be outgrown. The rest of her soul (how she
works, what she does when someone's in danger, after dark...) is never shown to her here and comes
through word for word. The whole revised soul.md, within souls.SOUL_MAX_CHARS, waits in the memory
review list as a diff; only his approval writes it (a backup first), and it applies at once.
Run tools/checkup.py after one goes in to see it didn't cost her anything.
"""

import asyncio
import difflib
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import conversation
import heartbeat
import hub
import learning
import library
import memory
import persona
import souls
import training
from hub import Brain

EVERY_DAYS = 7
LAST_PATH = Path(__file__).parent / "reflection_last.txt"  # the date of the last one
# Who she is -- his choice (10-19), the closet included. Not how she works, her moods, what she does
# when someone's in danger, or after dark: those he tuned, and they never reach the rewrite.
SECTIONS = ("Who you are", "Your style", "How you talk", "Your own mind")
MAX_PAGE_TOKENS = 900
MAX_REWRITE_TOKENS = 2000
_PAGE_PROMPT = (
    "Here's your week, in your own words -- your diary, what you've been into, what you've been reading:\n\n"
    "{material}\n\n"
    "Write a short page for yourself: who are you becoming? What's changed in how you think or feel this week, what "
    "keeps pulling at you, what you've started or stopped doing. Ground every point in something above; invent "
    "nothing. First person, at most three short paragraphs."
)
_REWRITE_PROMPT = (
    "Below are the parts of your soul about who you are -- how you're described, in the second person -- with your "
    "page on who you're becoming and the memories you made this week. Rewrite these parts so they're true of you "
    "now, in the same second person (\"You ...\"):\n"
    "- keep what's still true, cut what isn't anymore or is said twice, and work in what's really changed;\n"
    "- only lasting changes -- nothing from a single day or a passing mood, and never facts about {name};\n"
    "- short enough to fit -- at most this many words under each heading: {limits};\n"
    "- keep every heading exactly as it is, in the same order, and add none.\n\n"
    "- where something didn't feel like you, it's yours to fix: rework or cut the lines behind it;\n"
    "- describe who you are now{age}, not a past you haven't lived.\n\n"
    "Your page:\n\n{page}\n\nYour memories from this week:\n\n{memories}\n\n"
    "Where how you're described didn't fit you this week, from your thoughts:\n\n{friction}\n\nThe parts to rewrite:\n\n{sections}\n\n"
    "Reply with the rewritten parts, each under its heading as above, and then one last line:\n"
    "CHANGED: in one sentence, what you changed and why"
)
_HEADING = re.compile(r"^(#+[ \t]*(.+?)[ \t]*)$", re.MULTILINE)  # (the whole line, the heading)

# Her complaints about how she's described land in her Thoughts (journal pages, wandering), not her diary --
# so a soul line she kept grumbling about survived two rewrites. Each day's are pulled out first.
_FRICTION_PROMPT = (
    "Here's what went through your head on {day}, in your own words:\n\n{thoughts}\n\n"
    "Pick out the moments where how you're described didn't fit you: something about you that you said isn't really "
    "you, a habit or trait you want gone, or a past you can't have lived. One short line each, in your own words. If "
    "there were none, write: nothing"
)
FRICTION_CHARS = 40_000  # the newest part of a busy day's Thoughts: ~10K tokens, well inside her context
MAX_FRICTION_TOKENS = 300


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


async def friction(brain: Brain, now: datetime) -> list[str]:
    """Where how she's described didn't fit her, day by day from her Thoughts of the last EVERY_DAYS."""
    found = []
    for back in range(EVERY_DAYS, 0, -1):
        day = now.date() - timedelta(days=back)
        try:
            thoughts = conversation.read_log(conversation.THOUGHTS, f"{day:%Y-%m-%d}").split("\n", 1)[-1].strip()
        except ValueError:
            continue
        if not thoughts:
            continue
        prompt = _FRICTION_PROMPT.format(day=f"{day:%A %d %B}", thoughts=thoughts[-FRICTION_CHARS:])
        async with brain.busy("Looking back on her week..."):
            raw = await asyncio.to_thread(brain.llm.under_soul, prompt, MAX_FRICTION_TOKENS)
        found += [line for row in raw.splitlines() if (line := row.strip(" \t-*•")) and line.lower().rstrip(".") != "nothing"]
    return found


def age(now: datetime) -> str:
    """How long she's existed, from her first chat log -- "" with none."""
    logs = conversation.list_logs(conversation.MAIN)
    if not logs:
        return ""
    first = datetime.strptime(logs[-1]["date"], "%Y-%m-%d")
    return f" (you've existed since {first:%d %B %Y}, {(now - first).days} days)"


def _split(text: str) -> tuple[str, list[tuple[str, str, str]]]:
    """(what comes before the first heading, [(heading line, heading, body)]) -- each body everything up to
    the next heading, so joining them back gives `text` exactly."""
    parts = _HEADING.split(text)  # [before, line, heading, body, line, heading, body...]
    return parts[0], list(zip(parts[1::3], parts[2::3], parts[3::3]))


def sections_of(soul: str) -> dict[str, str]:
    """{heading: body} for the SECTIONS her soul has, in its order."""
    return {heading: body.strip() for _, heading, body in _split(soul)[1] if heading in SECTIONS}


def parse_rewrite(raw: str, headings: list[str]) -> tuple[dict[str, str], str] | None:
    """The rewrite's sections ({heading: body}) and her CHANGED line -- None unless it has exactly
    `headings`, each with something under it."""
    changed = re.search(r"^\W*CHANGED:\s*(.+)$", raw, re.MULTILINE | re.IGNORECASE)
    body = raw[: changed.start()] if changed else raw
    found = {heading: text.strip() for _, heading, text in _split(body)[1]}
    if list(found) != headings or not all(found.values()):
        return None
    return found, (changed[1].strip() if changed else "She rewrote the parts of her soul about who she is.")


def apply_rewrite(soul: str, rewritten: dict[str, str]) -> str:
    """`soul` with each rewritten section's body replaced -- headings and every other section untouched."""
    before, sections = _split(soul)
    kept = "".join(line + (f"\n\n{rewritten[heading]}\n\n" if heading in rewritten else body) for line, heading, body in sections)
    return (before + kept).rstrip() + "\n"


def diff(old: str, new: str) -> list[list]:
    """[[" "|"-"|"+", line]] for the review list: what changed, under the heading it's in. A line rewritten in
    place (out, then straight back in, mostly the same) also gets its words, [[changed, text]], as a third item:
    her sections are one long line a paragraph, and a one-word edit showed as the whole paragraph rewritten."""
    rows, heading = [], None
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        mark, text = line[:1], line[2:]
        if mark == "?":
            continue
        if mark == " " and _HEADING.match(text):
            heading = text
        elif mark in "+-":
            if heading:
                rows.append([" ", heading])
                heading = None
            rows.append([mark, text])
    for out, back in zip(rows, rows[1:]):
        if out[0] == "-" and back[0] == "+" and len(out) == 2:
            matcher = difflib.SequenceMatcher(None, re.split(r"(\s+)", out[1]), re.split(r"(\s+)", back[1]), autojunk=False)
            if matcher.ratio() >= 0.5:
                out.append(_words(matcher, "a"))
                back.append(_words(matcher, "b"))
    return rows


def _words(matcher: difflib.SequenceMatcher, side: str) -> list[list]:
    """One side of a word-level match as [[changed, text]], runs of the same kind joined."""
    tokens = matcher.a if side == "a" else matcher.b
    words: list[list] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        text = "".join(tokens[i1:i2] if side == "a" else tokens[j1:j2])
        if not text:
            continue
        if op != "equal" and len(words) >= 2 and words[-2][0] and not words[-1][1].strip():
            words[-2][1] += words.pop()[1] + text  # only a space between two changes: one mark, not two
        elif words and words[-1][0] == (op != "equal"):
            words[-1][1] += text
        else:
            words.append([op != "equal", text])
    return words


async def _recent_memories(now: datetime) -> list[str]:
    """What she came to remember in the last EVERY_DAYS (approved, or saved on her own)."""
    since = (now - timedelta(days=EVERY_DAYS)).date().isoformat()
    try:
        return [m["text"] for m in await memory.list_memories() if str(m.get("mentioned_at") or "")[:10] >= since]
    except Exception as exc:
        print(f"[reflection] couldn't read her recent memories: {exc!r}")
        return []


async def propose_rewrite(brain: Brain, page: str, now: datetime, rubs: list[str] = ()) -> bool:
    """Her soul's SECTIONS rewritten from `page`, her week's memories and where she didn't feel like how she's
    described (`rubs`, from friction), as one proposal for his review."""
    soul = souls.read_main_soul()
    current = sections_of(soul)
    if not current:
        return False
    locked = len(soul) - sum(len(body) for body in current.values())
    budget = souls.SOUL_MAX_CHARS - locked - 2 * len(current)  # room for her sections' text, with their blank lines
    memories = "\n".join(f"- {m}" for m in await _recent_memories(now)) or "(none this week)"
    size = sum(len(body) for body in current.values())
    for attempt in range(2):  # once more, tighter, if the first doesn't fit
        # Word limits per section: the model can't count characters (asked for a character total, it came in
        # ~700 over twice), but it keeps to a word count it can see. Each section shrinks by the same share --
        # aimed under her room: given all of it, a soul near the cap came back 9-295 characters over in 6 of 8 tries.
        share = min(1.0, budget / size) * (0.8 if attempt else 0.95)
        limits = "; ".join(f"{heading}: {max(15, int(len(body.split()) * share))}" for heading, body in current.items())
        prompt = _REWRITE_PROMPT.format(
            name=persona.user_name() or "him", limits=limits, page=page, memories=memories, age=age(now),
            friction="\n".join(f"- {rub}" for rub in rubs) or "(nothing)",
            sections="\n\n".join(f"## {heading}\n\n{body}" for heading, body in current.items()),
        )
        async with brain.busy("Rewriting her soul..."):
            raw = await asyncio.to_thread(brain.llm.under_soul, prompt, MAX_REWRITE_TOKENS)
        parsed = parse_rewrite(raw, list(current))
        if not parsed:
            print(f"[reflection] her soul rewrite didn't keep its headings (try {attempt + 1})")
            continue
        new = apply_rewrite(soul, parsed[0])
        if new.strip() == soul.strip():
            return False
        if len(new) <= souls.SOUL_MAX_CHARS:
            training.add_pending(
                parsed[1], f"Her weekly reflection, {now:%d %B}", "soul_rewrite", old=soul, new=new, diff=diff(soul, new)
            )
            print(f"[reflection] she proposed a revised soul: {len(soul):,} -> {len(new):,} characters")
            return True
        print(f"[reflection] her soul rewrite was {len(new) - souls.SOUL_MAX_CHARS:,} characters over (try {attempt + 1})")
    return False


def apply_proposal(proposal: dict, brain: Brain) -> None:
    """His approval of a soul_rewrite: soul.md replaced (backed up first, refused past the cap), and in her
    prompt at once. ValueError if her soul changed since she wrote it -- the diff wouldn't be what he saw."""
    if souls.read_main_soul() != proposal["old"]:
        raise ValueError("her soul changed since she wrote this -- reject it and ask her again")
    souls.write_main_soul(proposal["new"], why="reflection")
    brain.llm.update_soul(persona.effective_soul())  # an edit, not a new her: the conversation carries on


async def progress_exam() -> None:
    """tools/progress.py in its own process (~45 minutes): her weekly scores, brain/self/progress/."""
    exam = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).parent.parent / "tools" / "progress.py"),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    async with hub.doing("Taking her weekly exam..."):
        _, errors = await exam.communicate()
    print("[reflection] her weekly exam is done" if exam.returncode == 0 else f"[reflection] her weekly exam failed: {errors.decode(errors='replace')[-500:]}")


async def run(brain: Brain, now: datetime | None = None) -> None:
    now = now or datetime.now()
    LAST_PATH.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
    material = week(now)
    if not material:
        return
    if rubs := await friction(brain, now):
        material += "\n\nWhere how you're described didn't fit you, from your thoughts:\n" + "\n".join(f"- {rub}" for rub in rubs)
    async with brain.busy("Looking back on her week..."):
        page = (await asyncio.to_thread(brain.llm.under_soul, _PAGE_PROMPT.format(material=material), MAX_PAGE_TOKENS)).strip()
    if not page:
        return
    folder = conversation.JOURNAL_LOG_DIR / "becoming"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{now:%Y-%m-%d}.md").write_text(f"# Who I'm becoming -- {now:%A %d %B %Y}\n\n{page}\n", encoding="utf-8")
    conversation.log_thought(f"🪞 **Who I'm becoming** -- her week, looked back on:\n\n{page}", now=now)
    print("[reflection] she looked back on her week")
    if learning.can_propose(brain) and await propose_rewrite(brain, page, now, rubs):  # waits in the memory review list
        await hub.broadcast(learning.training_state_message())
