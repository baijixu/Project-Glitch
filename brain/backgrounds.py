"""The pictures behind her (Settings -> Background). Kept by Brain, like avatars.py,
so every device has the same ones and shows the same one: one image file per
background in backgrounds/, and the one in use in active_background.txt -- ""
for none, the plain backdrop each UI style draws for itself.
"""

from pathlib import Path

from names import sanitize_name
from store import Choice

BACKGROUNDS_DIR = Path(__file__).parent / "backgrounds"
ACTIVE = Choice(Path(__file__).parent / "active_background.txt")

# Checked on every save: a Renderer-supplied kind never goes into a filename unchecked.
KINDS = ("png", "jpg", "webp")


def _file(name: str) -> Path | None:
    for kind in KINDS:
        path = BACKGROUNDS_DIR / f"{name}.{kind}"
        if path.exists():
            return path
    return None


def list_backgrounds() -> list[str]:
    BACKGROUNDS_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for kind in KINDS for p in BACKGROUNDS_DIR.glob(f"*.{kind}"))


def save(name: str, data: bytes, kind: str) -> str:
    """Returns the name it was saved under. Saving over a name replaces it, whatever its type was."""
    if kind not in KINDS:
        raise ValueError(f"unknown background type {kind!r}")
    safe = sanitize_name(name, kind="background")
    BACKGROUNDS_DIR.mkdir(exist_ok=True)
    if old := _file(safe):
        old.unlink()
    (BACKGROUNDS_DIR / f"{safe}.{kind}").write_bytes(data)
    return safe


def read(name: str) -> tuple[bytes, str]:
    """(data, kind) -- the Renderer needs the kind to show the bytes as an image."""
    path = _file(sanitize_name(name, kind="background"))
    if path is None:
        raise ValueError(f"no background named {name!r}")
    return path.read_bytes(), path.suffix[1:]


def read_active() -> str:
    """The one in use, or "" -- also when the saved one has since gone."""
    name = ACTIVE.read()
    return name if name in list_backgrounds() else ""


def set_active(name: str) -> None:
    """"" for none; anything else has to be a saved background."""
    if name and name not in list_backgrounds():
        raise ValueError(f"no background named {name!r}")
    ACTIVE.write(name)


def delete(name: str) -> None:
    path = _file(sanitize_name(name, kind="background"))
    if path is None:
        raise ValueError(f"no background named {name!r}")
    path.unlink()
