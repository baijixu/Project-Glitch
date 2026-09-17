"""Manages notes.md -- a free-form scratchpad for the user's own ideas and
todos, unrelated to anything Glitch says, remembers, or role-plays (see
memory.py for facts *she* learns during conversation, souls.py/profiles.py
for role-play content). Never read by the LLM or included in any
prompt -- purely a note-to-self file the user can also open directly on
disk, or hand to an assistant working on this codebase. One flat file, no
naming/sanitization needed since there's only ever the one.
"""

from pathlib import Path

NOTES_PATH = Path(__file__).parent / "notes.md"


def read_notes() -> str:
    if NOTES_PATH.exists():
        return NOTES_PATH.read_text(encoding="utf-8")
    return ""


def write_notes(content: str) -> None:
    NOTES_PATH.write_text(content, encoding="utf-8")
