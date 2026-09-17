"""Glitch's own native memory of who the user is -- separate from, and never
touched by, the Hermes harness (brain/harness.py). Hermes has its own
sophisticated built-in memory system when it's active; this only exists for
her own personality (llm.LocalLLM), so the two never blend together. See
main.py's _reply_to for the extraction trigger and llm/client.py's
LocalLLM.maybe_extract_memory for how a new fact actually gets decided.

One fact per line in memory.md, newest last -- much simpler than Hermes's
own MEMORY.md (no §-delimited multi-line entries, no LLM-driven
consolidation). Over MAX_MEMORY_CHARS, the oldest entries are just dropped
(FIFO) rather than intelligently merged -- a deliberate v1 simplification,
worth revisiting only if it actually becomes a problem in practice.
"""

from pathlib import Path

MEMORY_PATH = Path(__file__).parent / "memory.md"
MEMORY_ACTIVE_PATH = Path(__file__).parent / "memory_active.txt"

MAX_MEMORY_CHARS = 2000


def read_memory_entries() -> list[str]:
    if not MEMORY_PATH.exists():
        return []
    return [line for line in MEMORY_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_memory_block() -> str:
    """Joined entries for feeding straight into LocalLLM.set_memory -- ""
    (not None) when empty, since _system_prompt just skips a falsy block.
    """
    return "\n".join(f"- {entry}" for entry in read_memory_entries())


def add_memory_entry(fact: str) -> bool:
    """Appends a new fact, skipping an exact-duplicate re-add. Trims the
    OLDEST entries first when over MAX_MEMORY_CHARS -- see this module's
    own docstring for why that's FIFO rather than Hermes-style
    consolidation. Returns whether anything was actually added -- main.py
    uses this to decide whether the memory_learned notification should
    fire at all (a no-op duplicate shouldn't tell the user "learned
    something" when nothing changed).
    """
    fact = fact.strip()
    if not fact:
        return False
    entries = read_memory_entries()
    if fact in entries:
        return False
    entries.append(fact)
    while entries and sum(len(e) for e in entries) > MAX_MEMORY_CHARS:
        entries.pop(0)
    MEMORY_PATH.write_text("\n".join(entries), encoding="utf-8")
    return True


def clear_memory() -> None:
    MEMORY_PATH.write_text("", encoding="utf-8")


def set_memory_active(active: bool) -> None:
    MEMORY_ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_memory_active() -> bool:
    """Defaults to True (on) -- unlike Mic Always-On, this feature's whole
    point is to be on so she actually gets to know the user, so it opts in
    by default rather than requiring the user to find and flip it.
    """
    if MEMORY_ACTIVE_PATH.exists():
        return MEMORY_ACTIVE_PATH.read_text(encoding="utf-8").strip() != "0"
    return True
