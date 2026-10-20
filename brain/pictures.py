"""Her gallery (Settings -> Gallery): pictures her agent made for her (llm._agent_tool), copied into
pictures/ (gitignored) as its report names it so they outlast the agent tidying its own folder, and
pictures he gives her -- uploaded there, or sent in chat. Each has a short description (INDEX) so she
knows what's in it without opening it; she can open one in conversation (llm.GALLERY_TOOL) or look at
one in her quiet time (heartbeat.look_at). A picture reaches a device only as a get_picture answer over
the authenticated socket -- never as a plain web link that would skip the auth token.
"""

import base64
import mimetypes
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from store import read_json, write_json

DIR = Path(__file__).parent / "pictures"
INDEX = Path(__file__).parent / "pictures.json"  # {name: {"by": "him" (absent: hers), "about": ..., "looked": time}}
MAX_BYTES = 20 * 1024 * 1024
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
# An absolute Windows or POSIX path to an image, as an agent writes it in a report.
_IMAGE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s\"'`<>|*?]*?\.(?:png|jpe?g|webp)\b", re.IGNORECASE)
_NAME = re.compile(r"[\w.-]+")


def keep(report: str) -> list[str]:
    """Copies each existing image `report` names into DIR and returns their names there."""
    names = []
    for match in dict.fromkeys(_IMAGE_PATH.findall(report)):
        source = Path(match)
        try:
            if not source.is_file() or source.stat().st_size > MAX_BYTES:
                continue
            DIR.mkdir(exist_ok=True)
            name = f"{datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^\w.-]', '_', source.name)}"
            shutil.copyfile(source, DIR / name)
        except OSError as exc:
            print(f"[pictures] couldn't keep {source}: {exc!r}")
            continue
        names.append(name)
    return names


def add(data_b64: str, mime: str, label: str = "") -> str | None:
    """A picture he gives her, kept in DIR; its name, or None if it isn't a usable image."""
    extension = _EXTENSIONS.get(mime)
    try:
        data = base64.b64decode(data_b64, validate=True)
    except (ValueError, TypeError):
        return None
    if not extension or not data or len(data) > MAX_BYTES:
        return None
    DIR.mkdir(exist_ok=True)
    stem = re.sub(r"[^\w-]+", "_", Path(label).stem)[:40].strip("_") or "picture"
    name = f"{datetime.now():%Y%m%d-%H%M%S}-{stem}{extension}"
    (DIR / name).write_bytes(data)
    _update(name, by="him")
    return name


def _read_index() -> dict:
    return read_json(INDEX, {})


def _update(name: str, **fields) -> None:
    index = _read_index()
    index[name] = {**index.get(name, {}), **fields}
    write_json(INDEX, index)


def describe(name: str, about: str) -> None:
    _update(name, about=about)


def looked(name: str) -> None:
    _update(name, looked=time.time())


def entries() -> list[dict]:
    """Settings -> Gallery: [{name, by ("her" or "him"), about}], newest first."""
    index = _read_index()
    return [{"name": n, "by": index.get(n, {}).get("by", "her"), "about": index.get(n, {}).get("about", "")} for n in listing()]


def find(about: str) -> str | None:
    """The picture whose description shares the most words with `about`, or None if none does."""
    index, words = _read_index(), _words(about)
    scored = [(len(words & _words(f"{n} {index.get(n, {}).get('about', '')}")), n) for n in listing()]
    best = max(scored, default=(0, None))
    return best[1] if best[0] else None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def least_looked() -> str | None:
    """The described picture she's looked at least lately (never first), or None."""
    index = _read_index()
    described = [n for n in listing() if index.get(n, {}).get("about")]
    return min(described, key=lambda n: index[n].get("looked", 0), default=None)


def _path(name: str) -> Path | None:
    """A kept picture's file -- only for a plain name inside DIR."""
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name.startswith("."):
        return None
    return DIR / name


def listing() -> list[str]:
    """Every kept picture's name, newest first (names start with when it was kept)."""
    if not DIR.is_dir():
        return []
    return sorted((p.name for p in DIR.iterdir() if p.is_file() and _path(p.name)), reverse=True)


def delete(name: str) -> bool:
    """Deletes a kept picture for good (Settings -> Artwork); False if there was none."""
    path = _path(name)
    try:
        path.unlink()
    except (AttributeError, OSError):
        return False
    index = _read_index()
    if index.pop(name, None) is not None:
        write_json(INDEX, index)
    return True


def read(name: str) -> tuple[str, str] | None:
    """(mime type, base64) of a kept picture, or None -- only a plain name inside DIR."""
    if (path := _path(name)) is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return mimetypes.guess_type(name)[0] or "image/png", base64.b64encode(data).decode("ascii")
