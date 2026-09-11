"""Manages Glitch's installed VRM avatar files. Brain-hosted (not
Renderer-local) so the list and file data are correct no matter which
device the Renderer's browser is actually running on -- SPEC.md's
same-machine-or-LAN design means that can be a phone on the same wifi,
which can't read this machine's filesystem directly. Binary VRM bytes
travel over the WS connection base64-encoded, same pattern as
speak_audio/user_audio.

Unlike profiles/souls (Brain-side state that shapes LLM behavior), which
avatar is loaded is purely a Renderer rendering concern -- Brain's role
here is just storage/hosting plus remembering which one is currently
active so a reconnecting Renderer knows what to load.

"Glitch" (the shipped default, renderer/assets/Glitch.vrm) is
deliberately NOT one of the files in here -- it ships git-tracked with
the Renderer and loads instantly with zero network round-trip on first
boot, unlike a custom avatar which has to be fetched over the WS
connection first. It's a reserved name main.py/brain_client.js both
special-case: active_avatar.txt can point to it (no accompanying .vrm
file needed, see read_active_avatar's docstring), but list_avatars()
never returns it and read_avatar() would raise for it -- the Renderer
already knows how to load it locally.
"""

from pathlib import Path

AVATARS_DIR = Path(__file__).parent / "avatars"
ACTIVE_AVATAR_PATH = Path(__file__).parent / "active_avatar.txt"

DEFAULT_AVATAR_NAME = "Glitch"


def list_avatars() -> list[str]:
    AVATARS_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in AVATARS_DIR.glob("*.vrm"))


def save_avatar(name: str, data: bytes) -> None:
    AVATARS_DIR.mkdir(exist_ok=True)
    path = AVATARS_DIR / f"{_sanitize_name(name)}.vrm"
    path.write_bytes(data)


def read_avatar(name: str) -> bytes:
    path = AVATARS_DIR / f"{_sanitize_name(name)}.vrm"
    return path.read_bytes()


def set_active_avatar(name: str) -> None:
    ACTIVE_AVATAR_PATH.write_text(name, encoding="utf-8")


def read_active_avatar() -> str | None:
    """The name of whichever avatar was last made active (DEFAULT_AVATAR_NAME
    included), or None if none has ever been explicitly chosen -- callers
    should fall back to the Renderer's own default-on-boot behavior in
    that case, same as profiles.py/souls.py's "" empty-string convention
    for "nothing set yet."
    """
    if ACTIVE_AVATAR_PATH.exists():
        return ACTIVE_AVATAR_PATH.read_text(encoding="utf-8").strip() or None
    return None


def _sanitize_name(name: str) -> str:
    # Same allow-list approach as profiles.py/souls.py -- avatar names
    # arrive over the WS connection as plain user input (the imported
    # file's own name, stripped of its .vrm extension).
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    if not cleaned:
        raise ValueError(f"avatar name {name!r} has no usable characters")
    return cleaned
