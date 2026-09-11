"""Manages Glitch's saved "souls" -- who she is and how she talks,
as opposed to profiles.py's user.md (the user's own role-play character
and scenario). Each soul is one markdown file in souls/ with two parts:
a free-text description and example dialogue (`<user>`/`<character>`
turns), kept as two separate fields end to end -- unlike profiles.py's
single combined blob -- so the editor can show them as two distinct
boxes and re-populate both correctly when editing a saved soul.

Loading a soul copies its content into soul.md, the file LocalLLM reads
to replace its *default* personality (llm/client.py's DEFAULT_PERSONALITY)
-- the mood-tag instruction stays fixed underneath whichever soul is
active, since the Renderer's expression system depends on it regardless
of who Glitch is currently supposed to be.
"""

from pathlib import Path

SOULS_DIR = Path(__file__).parent / "souls"
SOUL_MD_PATH = Path(__file__).parent / "soul.md"

# Stable marker splitting description from examples within a soul file --
# also doubles as the section header the LLM actually sees, so it reads
# naturally in the prompt rather than being a delimiter that's only
# meaningful to this code.
EXAMPLES_HEADER = "## Example dialogue"


def list_souls() -> list[str]:
    SOULS_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in SOULS_DIR.glob("*.md"))


def save_soul(name: str, description: str, examples: str) -> None:
    SOULS_DIR.mkdir(exist_ok=True)
    path = SOULS_DIR / f"{_sanitize_name(name)}.md"
    path.write_text(_combine(description, examples), encoding="utf-8")


def load_soul(name: str) -> str:
    """Copies the named soul's combined content into soul.md (making it
    the active one) and returns that combined content.
    """
    content = _read_combined(name)
    SOUL_MD_PATH.write_text(content, encoding="utf-8")
    return content


def read_soul(name: str) -> tuple[str, str]:
    """Returns (description, examples) for the editor -- pre-filling
    the two boxes for the Edit button (get_soul).
    """
    return _split(_read_combined(name))


def read_active_soul() -> str:
    """The combined content of soul.md, or "" if no soul has ever been
    loaded (Brain falls back to DEFAULT_PERSONALITY in that case).
    """
    if SOUL_MD_PATH.exists():
        return SOUL_MD_PATH.read_text(encoding="utf-8")
    return ""


def _read_combined(name: str) -> str:
    path = SOULS_DIR / f"{_sanitize_name(name)}.md"
    return path.read_text(encoding="utf-8")


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


def _sanitize_name(name: str) -> str:
    # Same allow-list approach as profiles.py -- soul names arrive over
    # the WS connection as plain user input.
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    if not cleaned:
        raise ValueError(f"soul name {name!r} has no usable characters")
    return cleaned
