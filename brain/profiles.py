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

PROFILES_DIR = Path(__file__).parent / "profiles"
USER_MD_PATH = Path(__file__).parent / "user.md"


def list_profiles() -> list[str]:
    PROFILES_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in PROFILES_DIR.glob("*.md"))


def save_profile(name: str, content: str) -> None:
    PROFILES_DIR.mkdir(exist_ok=True)
    path = PROFILES_DIR / f"{_sanitize_name(name)}.md"
    path.write_text(content, encoding="utf-8")


def load_profile(name: str) -> str:
    """Copies the named profile's content into user.md (making it the
    active one) and returns that content.
    """
    content = read_profile(name)
    USER_MD_PATH.write_text(content, encoding="utf-8")
    return content


def read_profile(name: str) -> str:
    """Reads a profile's content without activating it -- used to
    pre-fill the profile editor for the Edit button (get_profile).
    """
    path = PROFILES_DIR / f"{_sanitize_name(name)}.md"
    return path.read_text(encoding="utf-8")


def read_active_profile() -> str:
    """The content of user.md, or "" if no profile has ever been loaded."""
    if USER_MD_PATH.exists():
        return USER_MD_PATH.read_text(encoding="utf-8")
    return ""


def _sanitize_name(name: str) -> str:
    # Profile names arrive over the WS connection as plain user input --
    # allow-list to alphanumerics/space/dash/underscore rather than just
    # rejecting "..": treat it the same as any other external input, not
    # as implicitly trusted just because it's this project's own Renderer
    # on the other end.
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    if not cleaned:
        raise ValueError(f"profile name {name!r} has no usable characters")
    return cleaned
