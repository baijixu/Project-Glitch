"""Her saved "souls" -- who she is and how she talks. Each is one markdown file in souls/
with two parts, a description and example dialogue, kept separate so the editor can show
them as two boxes.

Two separate files, never mixed up:

- soul.md is her MAIN soul. Only written by hand (Settings' editor, or the file itself);
  used whenever role-play is off.
- rp_soul.md is the selected ROLE-PLAY soul: loading a saved soul copies it here, and it
  replaces soul.md while role-play is on.

Kept apart so a role-play soul can never overwrite her own. Either replaces llm.py's
DEFAULT_PERSONALITY; the mood-tag instruction stays under whichever is active.
"""

import re
from pathlib import Path

from store import Choice, NamedStore

SOUL_MD_PATH = Path(__file__).parent / "soul.md"  # her main soul -- see the module docstring; written only by write_main_soul
RP_SOUL_PATH = Path(__file__).parent / "rp_soul.md"  # the selected role-play soul -- everything here that "loads" a soul writes this

# Splits description from examples in a soul file -- and reads as a heading in her prompt.
EXAMPLES_HEADER = "## Example dialogue"

# "No role-play soul" (her main soul then): not a file, always offered, can't be deleted.
# Selecting it clears rp_soul.md; it's the fallback when the active soul is deleted.
DEFAULT_SOUL_NAME = "Default"

# One .md per saved soul, reserved name and active-name bookkeeping as in profiles.py.
STORE = NamedStore(Path(__file__).parent / "souls", kind="soul", reserved=DEFAULT_SOUL_NAME, suffix=".md")
ACTIVE = Choice(Path(__file__).parent / "active_soul_name.txt", default=DEFAULT_SOUL_NAME)

list_souls = STORE.names
# Not the active-name bookkeeping, even for the active one -- characters._delete_soul
# decides whether to fall back to DEFAULT_SOUL_NAME.
delete_soul = STORE.delete
read_active_soul_name = ACTIVE.read


def save_soul(name: str, description: str, examples: str) -> None:
    STORE.write_text(name, _combine(description, examples))


def load_soul(name: str) -> str:
    """Copies the named soul into rp_soul.md (selecting it) and returns its content -- ""
    for DEFAULT_SOUL_NAME. Never touches soul.md."""
    content = "" if name == DEFAULT_SOUL_NAME else STORE.read_text(name)
    RP_SOUL_PATH.write_text(content, encoding="utf-8")
    ACTIVE.write(name)
    return content


def read_soul(name: str) -> tuple[str, str]:
    """Returns (description, examples) for the editor -- pre-filling
    the two boxes for the Edit button (get_soul).
    """
    return _split(STORE.read_text(name))


def write_main_soul(content: str) -> None:
    """Overwrites soul.md -- the only writer of it, called only by Settings' editor."""
    SOUL_MD_PATH.write_text(content, encoding="utf-8")


def read_main_soul() -> str:
    """The content of soul.md, or "" if she has none yet (LocalLLM falls
    back to its built-in DEFAULT_PERSONALITY in that case).
    """
    if SOUL_MD_PATH.exists():
        return SOUL_MD_PATH.read_text(encoding="utf-8")
    return ""


def topics(soul: str) -> list[str]:
    """The things she loves, from a soul's "What you love" section: its comma-separated
    items, with a leading "Label:" and "and" dropped. A line's first item loses its
    sentence capital ("Reading" -> "reading"); the rest are kept as written ("Christmas").
    """
    section = re.search(r"^#+\s*What you love\s*$(.*?)(?=^#|\Z)", soul, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    items = []
    for line in section[1].splitlines() if section else []:
        parts = [re.sub(r"^and\s+", "", p.strip().rstrip(".")) for p in line.split(":", 1)[-1].split(",")]
        parts = [p for p in parts if p]
        if parts:
            parts[0] = parts[0][0].lower() + parts[0][1:]
        items += parts
    return items


def read_active_soul() -> str:
    """The selected ROLE-PLAY soul's content (rp_soul.md), or "" if none is
    selected. Not her main soul -- see read_main_soul.
    """
    if RP_SOUL_PATH.exists():
        return RP_SOUL_PATH.read_text(encoding="utf-8")
    return ""


def _combine(description: str, examples: str) -> str:
    description = description.strip()
    examples = examples.strip()
    if not examples:
        return description
    return f"{description}\n\n{EXAMPLES_HEADER}\n{examples}"


def _split(content: str) -> tuple[str, str]:
    marker = f"\n{EXAMPLES_HEADER}\n"
    if marker in content:
        description, examples = content.split(marker, 1)
        return description.strip(), examples.strip()
    return content.strip(), ""
