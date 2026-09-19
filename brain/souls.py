"""Manages Glitch's saved "souls" -- who she is and how she talks,
as opposed to profiles.py's user.md (the user's own role-play character
and scenario). Each soul is one markdown file in souls/ with two parts:
a free-text description and example dialogue (`<user>`/`<character>`
turns), kept as two separate fields end to end -- unlike profiles.py's
single combined blob -- so the editor can show them as two distinct
boxes and re-populate both correctly when editing a saved soul.

Two separate files, never mixed up:

- soul.md is her MAIN soul -- her permanent everyday personality. Only ever
  written by hand (the Settings manual editor, or the file itself); nothing
  in this module can write it. LocalLLM uses it whenever role-play is off.
- rp_soul.md is the currently-selected ROLE-PLAY soul. Loading a saved soul
  copies it here, and this is the only file that copying, saving, editing
  or deleting a saved soul ever touches. It's only used while role-play is
  on, replacing soul.md for the duration.

They used to be one file (soul.md), so loading a role-play soul silently
overwrote her permanent personality -- keeping them apart is the point.
Either way, the personality replaces llm/client.py's DEFAULT_PERSONALITY,
and the mood-tag instruction stays fixed underneath whichever is active,
since the Renderer's expression system depends on it regardless of who
Glitch is currently supposed to be.
"""

from pathlib import Path

from names import sanitize_name

SOULS_DIR = Path(__file__).parent / "souls"
SOUL_MD_PATH = Path(__file__).parent / "soul.md"  # her main soul -- see the module docstring; written only by write_main_soul
RP_SOUL_PATH = Path(__file__).parent / "rp_soul.md"  # the selected role-play soul -- everything here that "loads" a soul writes this
ACTIVE_SOUL_NAME_PATH = Path(__file__).parent / "active_soul_name.txt"

# Stable marker splitting description from examples within a soul file --
# also doubles as the section header the LLM actually sees, so it reads
# naturally in the prompt rather than being a delimiter that's only
# meaningful to this code.
EXAMPLES_HEADER = "## Example dialogue"

# Reserved name for "no custom soul" -- never a real file in souls/,
# always offered by the Renderer's dropdown (prepended client-side, same
# pattern as avatars.py's DEFAULT_AVATAR_NAME), and can't be deleted.
# Selecting it clears rp_soul.md (never soul.md), which main.py's
# _effective_soul treats as "no role-play soul, use her main soul" -- so it's what
# the active selection falls back to if the soul that *was* active gets
# deleted, same role DEFAULT_PROFILE_NAME plays in profiles.py.
DEFAULT_SOUL_NAME = "Default"


def list_souls() -> list[str]:
    SOULS_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in SOULS_DIR.glob("*.md"))


def save_soul(name: str, description: str, examples: str) -> None:
    sanitized = sanitize_name(name, kind="soul")
    if sanitized == DEFAULT_SOUL_NAME:
        # Same collision this guards against in profiles.py's save_profile
        # -- souls/Default.md would show up as a second "Default" entry
        # indistinguishable from the reserved one.
        raise ValueError(f"{DEFAULT_SOUL_NAME!r} is reserved and can't be used as a soul name")
    SOULS_DIR.mkdir(exist_ok=True)
    path = SOULS_DIR / f"{sanitized}.md"
    path.write_text(_combine(description, examples), encoding="utf-8")


def load_soul(name: str) -> str:
    """Copies the named soul's combined content into rp_soul.md (making it
    the selected role-play soul) and returns that combined content. Never
    touches soul.md -- her main soul.

    DEFAULT_SOUL_NAME is special-cased to empty content rather than a
    file read -- it's never a real file (see its own docstring above).
    """
    content = "" if name == DEFAULT_SOUL_NAME else _read_combined(name)
    RP_SOUL_PATH.write_text(content, encoding="utf-8")
    ACTIVE_SOUL_NAME_PATH.write_text(name, encoding="utf-8")
    return content


def read_soul(name: str) -> tuple[str, str]:
    """Returns (description, examples) for the editor -- pre-filling
    the two boxes for the Edit button (get_soul).
    """
    return _split(_read_combined(name))


def delete_soul(name: str) -> None:
    """Deletes a saved soul file. Raises ValueError for DEFAULT_SOUL_NAME
    -- it isn't a real file, there's nothing to delete, and it must
    always stay selectable as the fallback. Does NOT touch soul.md, rp_soul.md or the
    active-name bookkeeping itself even if the deleted soul happens to be
    the active one -- main.py's _handle_delete_soul decides whether that
    requires falling back to DEFAULT_SOUL_NAME.
    """
    if name == DEFAULT_SOUL_NAME:
        raise ValueError("the default soul can't be deleted")
    path = SOULS_DIR / f"{sanitize_name(name, kind='soul')}.md"
    path.unlink()


def write_main_soul(content: str) -> None:
    """Overwrites soul.md -- her main soul -- with raw content. The ONLY
    function that writes that file, and only the Settings manual editor
    calls it: nothing about creating, editing, saving, loading or deleting a
    saved role-play soul can reach it.
    """
    SOUL_MD_PATH.write_text(content, encoding="utf-8")


def read_main_soul() -> str:
    """The content of soul.md, or "" if she has none yet (LocalLLM falls
    back to its built-in DEFAULT_PERSONALITY in that case).
    """
    if SOUL_MD_PATH.exists():
        return SOUL_MD_PATH.read_text(encoding="utf-8")
    return ""


def read_active_soul() -> str:
    """The selected ROLE-PLAY soul's content (rp_soul.md), or "" if none is
    selected. Not her main soul -- see read_main_soul.
    """
    if RP_SOUL_PATH.exists():
        return RP_SOUL_PATH.read_text(encoding="utf-8")
    return ""


def read_active_soul_name() -> str:
    """The name last passed to load_soul, or DEFAULT_SOUL_NAME if none
    has ever been explicitly selected -- lets the Renderer's dropdown
    restore the right selection on reconnect, and lets
    _handle_delete_soul tell whether the soul being deleted is the one
    currently in effect.
    """
    if ACTIVE_SOUL_NAME_PATH.exists():
        return ACTIVE_SOUL_NAME_PATH.read_text(encoding="utf-8").strip() or DEFAULT_SOUL_NAME
    return DEFAULT_SOUL_NAME


def _read_combined(name: str) -> str:
    path = SOULS_DIR / f"{sanitize_name(name, kind='soul')}.md"
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
