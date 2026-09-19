"""Manages the user's saved role-play profiles (a Renderer settings-panel
feature, not in SPEC.md). Each profile is one freeform markdown file in
profiles/ -- character description and scenario combined into a single
blob, not separate structured fields, since it's meant to be loaded
straight into an LLM prompt rather than parsed. Loading a profile copies
its content into user.md, the one "currently active" file LocalLLM reads
its persona from -- similar in spirit to how this very session reads a
project's own instructions file for context.

user.md persisting on disk (not just in Brain's memory) means the active
profile survives a Brain restart -- main.py reads it once at startup and
primes the LLM with it, rather than relying on the Renderer to resend it
on every reconnect the way the first version of this feature did.
"""

from pathlib import Path

from names import sanitize_name

PROFILES_DIR = Path(__file__).parent / "profiles"
USER_MD_PATH = Path(__file__).parent / "user.md"
ROLEPLAY_ACTIVE_PATH = Path(__file__).parent / "roleplay_active.txt"
ACTIVE_PROFILE_NAME_PATH = Path(__file__).parent / "active_profile_name.txt"

# Reserved name for "no profile" -- never a real file in profiles/, always
# offered by the Renderer's dropdown (main.js/brain_client.js prepend it
# client-side, same pattern as avatars.py's DEFAULT_AVATAR_NAME), and
# can't be deleted. Selecting it clears user.md, so it's what the active
# selection falls back to if the profile that *was* active gets deleted --
# there always has to be something selected, and this is the one entry
# guaranteed to still exist.
DEFAULT_PROFILE_NAME = "Default"


def list_profiles() -> list[str]:
    PROFILES_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in PROFILES_DIR.glob("*.md"))


def save_profile(name: str, content: str) -> None:
    sanitized = sanitize_name(name, kind="profile")
    if sanitized == DEFAULT_PROFILE_NAME:
        # Would otherwise create profiles/Default.md, a real file colliding
        # with the reserved name the Renderer always prepends to the
        # dropdown -- list_profiles() would then return "Default" too,
        # showing up as a second, indistinguishable "Default" option.
        raise ValueError(f"{DEFAULT_PROFILE_NAME!r} is reserved and can't be used as a profile name")
    PROFILES_DIR.mkdir(exist_ok=True)
    path = PROFILES_DIR / f"{sanitized}.md"
    path.write_text(content, encoding="utf-8")


def load_profile(name: str) -> str:
    """Copies the named profile's content into user.md (making it the
    selected one) and returns that content. Always writes user.md
    regardless of the role-play toggle (set_roleplay_active) -- selecting
    a profile while role-play is off just queues it for whenever the user
    turns it back on; main.py's _handle_load_profile is what actually
    decides whether to apply it to the LLM right now.

    DEFAULT_PROFILE_NAME is special-cased to empty content rather than a
    file read -- it's never a real file (see its own docstring above).
    """
    content = "" if name == DEFAULT_PROFILE_NAME else read_profile(name)
    USER_MD_PATH.write_text(content, encoding="utf-8")
    ACTIVE_PROFILE_NAME_PATH.write_text(name, encoding="utf-8")
    return content


def read_profile(name: str) -> str:
    """Reads a profile's content without activating it -- used to
    pre-fill the profile editor for the Edit button (get_profile).
    """
    path = PROFILES_DIR / f"{sanitize_name(name, kind='profile')}.md"
    return path.read_text(encoding="utf-8")


def delete_profile(name: str) -> None:
    """Deletes a saved profile file. Raises ValueError for
    DEFAULT_PROFILE_NAME -- it isn't a real file, there's nothing to
    delete, and it must always stay selectable as the fallback. Does NOT
    touch user.md or the active-name bookkeeping itself even if the
    deleted profile happens to be the active one -- main.py's
    _handle_delete_profile decides whether that requires falling back to
    DEFAULT_PROFILE_NAME, since only it knows whether role-play is
    currently on (and so whether the LLM's persona needs updating too).
    """
    if name == DEFAULT_PROFILE_NAME:
        raise ValueError("the default profile can't be deleted")
    path = PROFILES_DIR / f"{sanitize_name(name, kind='profile')}.md"
    path.unlink()


def write_active_profile(content: str) -> None:
    """Directly overwrites user.md with raw content -- same manual-edit
    escape hatch and reasoning as souls.write_main_soul.
    """
    USER_MD_PATH.write_text(content, encoding="utf-8")


def read_active_profile() -> str:
    """The content of user.md, or "" if no profile has ever been loaded."""
    if USER_MD_PATH.exists():
        return USER_MD_PATH.read_text(encoding="utf-8")
    return ""


def read_active_profile_name() -> str:
    """The name last passed to load_profile, or DEFAULT_PROFILE_NAME if
    none has ever been explicitly selected -- lets the Renderer's dropdown
    restore the right selection on reconnect instead of always resetting
    to nothing, and lets _handle_delete_profile tell whether the profile
    being deleted is the one currently in effect.
    """
    if ACTIVE_PROFILE_NAME_PATH.exists():
        return ACTIVE_PROFILE_NAME_PATH.read_text(encoding="utf-8").strip() or DEFAULT_PROFILE_NAME
    return DEFAULT_PROFILE_NAME


def set_roleplay_active(active: bool) -> None:
    """Whether the selected profile is actually layered into the LLM's
    system prompt (see llm/client.py's set_persona) or ignored in favor of
    plain soul-only behavior -- independent of *which* profile is
    selected, so switching this off and back on doesn't lose the user's
    dropdown pick (see load_profile's docstring and main.py's
    _handle_set_roleplay_active).
    """
    ROLEPLAY_ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_roleplay_active() -> bool:
    """Defaults to True (role-play on) when never explicitly set -- matches
    this feature's pre-existing behavior, where a profile in user.md was
    always applied with no way to turn it off.
    """
    if ROLEPLAY_ACTIVE_PATH.exists():
        return ROLEPLAY_ACTIVE_PATH.read_text(encoding="utf-8").strip() != "0"
    return True
