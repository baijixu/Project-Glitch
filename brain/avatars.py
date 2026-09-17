"""Manages Glitch's installed avatar files -- either a full 3D .vrm model
or a flat .png reference image (the Renderer disables camera panning for
the latter, see brain_client.js/main.js). Brain-hosted (not
Renderer-local) so the list and file data are correct no matter which
device the Renderer's browser is actually running on -- SPEC.md's
same-machine-or-LAN design means that can be a phone on the same wifi,
which can't read this machine's filesystem directly. Binary bytes travel
over the WS connection base64-encoded, same pattern as speak_audio/
user_audio.

Unlike profiles/souls (Brain-side state that shapes LLM behavior), which
avatar is loaded is purely a Renderer rendering concern -- Brain's role
here is just storage/hosting plus remembering which one is currently
active so a reconnecting Renderer knows what to load.

"Glitch" (the shipped default, renderer/assets/Glitch.vrm) is
deliberately NOT one of the files in here -- it ships git-tracked with
the Renderer and loads instantly with zero network round-trip on first
boot, unlike a custom avatar which has to be fetched over the WS
connection first. It's a reserved name main.py/brain_client.js both
special-case: active_avatar.txt can point to it (no accompanying file
needed, see read_active_avatar's docstring), but list_avatars() never
returns it and read_avatar() would raise for it -- the Renderer already
knows how to load it locally.
"""

from pathlib import Path

from names import sanitize_name

AVATARS_DIR = Path(__file__).parent / "avatars"
ACTIVE_AVATAR_PATH = Path(__file__).parent / "active_avatar.txt"

DEFAULT_AVATAR_NAME = "Glitch"

# The only two file extensions an avatar can be saved as -- checked
# against on every save_avatar (never trust a Renderer-supplied `kind`
# string enough to write it straight into a filename's extension; an
# unvalidated value there would let a compromised/malicious authenticated
# client write a file with an arbitrary extension into AVATARS_DIR).
AVATAR_KINDS = ("vrm", "png")


def list_avatars() -> list[dict]:
    """[{"name": ..., "kind": "vrm" | "png"}, ...], sorted by name -- the
    Renderer needs `kind` up front (not just discoverable after picking
    one) so its avatar dropdown/picker can be built without a round trip
    per entry.
    """
    AVATARS_DIR.mkdir(exist_ok=True)
    entries = [{"name": p.stem, "kind": kind} for kind in AVATAR_KINDS for p in AVATARS_DIR.glob(f"*.{kind}")]
    return sorted(entries, key=lambda e: e["name"])


def save_avatar(name: str, data: bytes, kind: str) -> None:
    if kind not in AVATAR_KINDS:
        raise ValueError(f"unknown avatar kind {kind!r}")
    AVATARS_DIR.mkdir(exist_ok=True)
    safe_name = sanitize_name(name, kind="avatar")
    # A name is one avatar, not one slot per file type -- if it previously
    # existed as the other kind (e.g. re-importing "Casual" as a .png after
    # it used to be a .vrm), drop that old file. Otherwise both would stick
    # around: list_avatars would show "Casual" twice, and read_avatar's
    # "whichever extension exists" lookup below would have to arbitrarily
    # pick one instead of reflecting the save that just happened.
    for other_kind in AVATAR_KINDS:
        if other_kind != kind:
            (AVATARS_DIR / f"{safe_name}.{other_kind}").unlink(missing_ok=True)
    (AVATARS_DIR / f"{safe_name}.{kind}").write_bytes(data)


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
    """The name of whichever avatar was last made active (DEFAULT_AVATAR_NAME
    included), or None if none has ever been explicitly chosen -- callers
    should fall back to the Renderer's own default-on-boot behavior in
    that case, same as profiles.py/souls.py's "" empty-string convention
    for "nothing set yet."
    """
    if ACTIVE_AVATAR_PATH.exists():
        return ACTIVE_AVATAR_PATH.read_text(encoding="utf-8").strip() or None
    return None
