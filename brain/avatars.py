"""Her installed avatars -- a 3D .vrm or a flat .png -- kept by Brain so every device
(a phone too) gets the same list and files, sent base64 over the WebSocket. Brain
stores them and remembers which is active; showing them is the Renderer's job.

"Glitch", the shipped default (renderer/assets/Glitch.vrm), isn't stored here: the
Renderer loads it locally. active_avatar.txt can name it, but list_avatars() never
returns it and read_avatar() raises for it.
"""

from pathlib import Path

from names import sanitize_name

AVATARS_DIR = Path(__file__).parent / "avatars"
ACTIVE_AVATAR_PATH = Path(__file__).parent / "active_avatar.txt"

DEFAULT_AVATAR_NAME = "Glitch"

# The only extensions an avatar is saved with -- a client's `kind` never goes into a filename unchecked.
AVATAR_KINDS = ("vrm", "png")


def list_avatars() -> list[dict]:
    """[{"name", "kind": "vrm" | "png"}], sorted by name."""
    AVATARS_DIR.mkdir(exist_ok=True)
    entries = [{"name": p.stem, "kind": kind} for kind in AVATAR_KINDS for p in AVATARS_DIR.glob(f"*.{kind}")]
    return sorted(entries, key=lambda e: e["name"])


def save_avatar(name: str, data: bytes, kind: str) -> str:
    """Returns the name it was saved under (sanitized)."""
    if kind not in AVATAR_KINDS:
        raise ValueError(f"unknown avatar kind {kind!r}")
    AVATARS_DIR.mkdir(exist_ok=True)
    safe_name = sanitize_name(name, kind="avatar")
    # One name is one avatar: saved as the other kind (a .png "Casual" over a .vrm one),
    # the old file goes, or the name would be listed twice.
    for other_kind in AVATAR_KINDS:
        if other_kind != kind:
            (AVATARS_DIR / f"{safe_name}.{other_kind}").unlink(missing_ok=True)
    (AVATARS_DIR / f"{safe_name}.{kind}").write_bytes(data)
    return safe_name


def read_avatar(name: str) -> tuple[bytes, str]:
    """Returns (data, kind) -- callers need `kind` to know whether to hand
    the bytes to the VRM loader or just display them as an image.
    """
    safe_name = sanitize_name(name, kind="avatar")
    for kind in AVATAR_KINDS:
        path = AVATARS_DIR / f"{safe_name}.{kind}"
        if path.exists():
            return path.read_bytes(), kind
    raise ValueError(f"no avatar named {name!r}")


def set_active_avatar(name: str) -> None:
    ACTIVE_AVATAR_PATH.write_text(name, encoding="utf-8")


def read_active_avatar() -> str | None:
    """The avatar last made active (DEFAULT_AVATAR_NAME included), or None if none ever was."""
    if ACTIVE_AVATAR_PATH.exists():
        return ACTIVE_AVATAR_PATH.read_text(encoding="utf-8").strip() or None
    return None


def _file_for(name: str) -> Path | None:
    for kind in AVATAR_KINDS:
        path = AVATARS_DIR / f"{name}.{kind}"
        if path.exists():
            return path
    return None


def rename_avatar(old_name: str, new_name: str) -> str:
    """Renames a saved avatar's file and returns the name as saved. The shipped
    default can't be renamed, and a name already in use is refused rather than
    overwritten. If it was the active avatar, the new name becomes active.
    """
    old = sanitize_name(old_name, kind="avatar")
    new = sanitize_name(new_name, kind="avatar")
    if DEFAULT_AVATAR_NAME in (old, new):
        raise ValueError(f"{DEFAULT_AVATAR_NAME!r} is the built-in avatar and can't be renamed or replaced")
    source = _file_for(old)
    if source is None:
        raise ValueError(f"no avatar named {old_name!r}")
    if new == old:
        return new
    if _file_for(new) is not None and new.lower() != old.lower():
        raise ValueError(f"there's already an avatar called {new!r}")
    source.rename(source.with_name(f"{new}{source.suffix}"))
    if read_active_avatar() == old:
        set_active_avatar(new)
    return new


def delete_avatar(name: str) -> None:
    """Deletes a saved avatar's file. The shipped default can't be deleted. If it
    was the active avatar, the default becomes active again.
    """
    safe = sanitize_name(name, kind="avatar")
    if safe == DEFAULT_AVATAR_NAME:
        raise ValueError(f"{DEFAULT_AVATAR_NAME!r} is the built-in avatar and can't be deleted")
    path = _file_for(safe)
    if path is None:
        raise ValueError(f"no avatar named {name!r}")
    path.unlink()
    if read_active_avatar() == safe:
        set_active_avatar(DEFAULT_AVATAR_NAME)
