"""Saved memory backends -- Settings -> Memory. Each profile is one JSON file in
memory_profiles/ naming a memory server someone runs themselves: which kind it
is (`type`), where it is (`url`), its `api_key`, and `space` -- the bank
(Hindsight) or user id (Mem0) her memories live under. Same
gitignored-file-per-saved-item pattern as llm_engines.py/harness.py.

Memory servers don't share one API the way LLM servers share OpenAI's, so each
`type` has its own small client in memory.py (TYPES below). Adding an engine
means adding it there and here.

LOCAL_NAME is reserved: the built-in flat file (memory.md), nothing to set up.
Never one of the files in here and can't be saved or deleted, the same role
llm_engines.NONE_NAME plays for LLM engines.
"""

import json
from pathlib import Path

from names import sanitize_name

PROFILES_DIR = Path(__file__).parent / "memory_profiles"
ACTIVE_PATH = Path(__file__).parent / "active_memory_profile.txt"

LOCAL_NAME = "Local file"

HINDSIGHT = "hindsight"
MEM0 = "mem0"
# type -> (label for Settings, default space, what `space` is called there)
TYPES = {
    HINDSIGHT: {"label": "Hindsight", "default_space": "glitch-native", "space_label": "Bank ID"},
    MEM0: {"label": "Mem0", "default_space": "glitch", "space_label": "User ID"},
}


def _path(name: str) -> Path:
    return PROFILES_DIR / f"{sanitize_name(name, kind='memory profile')}.json"


def list_profiles() -> list[dict]:
    """[{"name", "type"}] for every saved profile, by name (LOCAL_NAME not included)."""
    PROFILES_DIR.mkdir(exist_ok=True)
    profiles = []
    for path in sorted(PROFILES_DIR.glob("*.json")):
        try:
            kind = json.loads(path.read_text(encoding="utf-8")).get("type")
        except (OSError, ValueError, AttributeError):
            continue
        if kind in TYPES:
            profiles.append({"name": path.stem, "type": kind})
    return profiles


def save_profile(name: str, kind: str, url: str, api_key: str, space: str, replaces: str = "") -> str:
    """Creates or overwrites a profile; returns the name it was saved under.
    `replaces` is the profile being edited: if the edit renamed it, the old one
    goes, and if it was the active one the new name is active instead.
    """
    if kind not in TYPES:
        raise ValueError(f"unknown memory type {kind!r}")
    if not url.strip():
        raise ValueError("a memory server needs its URL")
    path = _path(name)
    if path.stem.casefold() == LOCAL_NAME.casefold():
        raise ValueError(f"{LOCAL_NAME!r} is built in and can't be used as a profile name")
    PROFILES_DIR.mkdir(exist_ok=True)
    profile = {"type": kind, "url": url.strip(), "api_key": api_key.strip(), "space": space.strip() or TYPES[kind]["default_space"]}
    path.write_text(json.dumps(profile), encoding="utf-8")
    if replaces and replaces not in (path.stem, LOCAL_NAME):
        was_active = read_active() == replaces
        _path(replaces).unlink(missing_ok=True)
        if was_active:
            set_active(path.stem)
    return path.stem


def read_profile(name: str) -> dict:
    """{"type", "url", "api_key", "space"}. Raises for an unknown or unreadable one."""
    profile = json.loads(_path(name).read_text(encoding="utf-8"))
    if profile.get("type") not in TYPES:
        raise ValueError(f"memory profile {name!r} has an unknown type")
    profile.setdefault("space", TYPES[profile["type"]]["default_space"])
    return profile


def delete_profile(name: str) -> None:
    if name == LOCAL_NAME:
        raise ValueError(f"{LOCAL_NAME!r} is built in and can't be deleted")
    _path(name).unlink()
    if read_active() == name:
        set_active(LOCAL_NAME)


def set_active(name: str) -> None:
    ACTIVE_PATH.write_text(name, encoding="utf-8")


def has_active() -> bool:
    return ACTIVE_PATH.exists()


def read_active() -> str:
    """The active profile's name -- LOCAL_NAME if none was picked or it's gone."""
    try:
        name = ACTIVE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return LOCAL_NAME
    if name != LOCAL_NAME and not any(p["name"] == name for p in list_profiles()):
        return LOCAL_NAME
    return name or LOCAL_NAME
