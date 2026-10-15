"""Pictures she shows in the chat: one Hermes made for her (llm.ASK_HERMES_TOOL) is copied into
pictures/ (gitignored) as its report names it, so it outlasts Hermes tidying its own folder, and
reaches a device only as a get_picture answer over the authenticated socket -- never as a plain
web link that would skip the auth token.
"""

import base64
import mimetypes
import re
import shutil
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).parent / "pictures"
MAX_BYTES = 20 * 1024 * 1024
# An absolute Windows or POSIX path to an image, as Hermes writes it in a report.
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
