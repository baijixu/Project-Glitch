"""Sampling profiles -- named sets of generation settings (temperature, top_p,
top_k, min_p, presence/repeat penalty) sent with each of her replies.

Why here and not in LM Studio/Ollama: both servers take these values on every
request and use them over their own saved defaults for that one request, so a
profile switch applies from her very next reply without touching the server.
Only her replies use them (see llm.py's reply); the short background
calls (memory/question/lesson proposals) keep the server's defaults, since
they need predictable JSON rather than personality.

A value left blank (missing from a profile) isn't sent at all, so the server's
own setting is used for that one. SERVER_DEFAULTS_NAME sends nothing.

Everything lives in one gitignored file, sampling_profiles.json:
    {"active": "<name>", "profiles": {"<name>": {"temperature": 1.0, ...}}}
DEFAULT_NAME is built in, not stored there, so it can't be lost or changed --
tune it by saving the boxes under a new name.
"""

import json
import math
from pathlib import Path

from names import sanitize_name

PROFILES_PATH = Path(__file__).parent / "sampling_profiles.json"

# Qwen's published settings for Qwen3.6-35B-A3B in thinking mode, general tasks
# (huggingface.co/Qwen/Qwen3.6-35B-A3B). presence_penalty 1.5 is theirs too --
# it's what keeps a thinking model from looping on the same idea.
DEFAULT_NAME = "Qwen 3.6 Thinking"
DEFAULT_VALUES = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repeat_penalty": 1.0,
}
SERVER_DEFAULTS_NAME = "Server defaults"
BUILTIN_NAMES = (DEFAULT_NAME, SERVER_DEFAULTS_NAME)

# key -> (lowest, highest, whole numbers only). The ranges are the ones LM
# Studio, Ollama and llama.cpp all accept; anything outside is refused rather
# than clamped, so a typo can't quietly become a different setting.
LIMITS = {
    "temperature": (0.0, 2.0, False),
    "top_p": (0.0, 1.0, False),
    "top_k": (0, 200, True),
    "min_p": (0.0, 1.0, False),
    "presence_penalty": (-2.0, 2.0, False),
    "repeat_penalty": (0.5, 2.0, False),
}
KEYS = tuple(LIMITS)


def _read_file() -> dict:
    try:
        data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(data: dict) -> None:
    tmp = PROFILES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(PROFILES_PATH)


def clean_values(values: dict) -> dict:
    """The settings from `values` that are set, checked and typed. Blank or
    missing ones are dropped (the server's own setting is used). Raises
    ValueError naming the first bad one.
    """
    cleaned = {}
    for key in KEYS:
        raw = values.get(key) if isinstance(values, dict) else None
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number") from None
        low, high, whole = LIMITS[key]
        if math.isnan(number) or not low <= number <= high:
            raise ValueError(f"{key} must be between {low:g} and {high:g}")
        if whole:
            if number != int(number):
                raise ValueError(f"{key} must be a whole number")
            cleaned[key] = int(number)
        else:
            cleaned[key] = round(number, 4)
    return cleaned


def list_profiles() -> dict[str, dict]:
    """Every profile by name: the built-in ones first, then the saved ones."""
    profiles = {DEFAULT_NAME: dict(DEFAULT_VALUES), SERVER_DEFAULTS_NAME: {}}
    saved = _read_file().get("profiles")
    for name, values in sorted((saved or {}).items()):
        if name in BUILTIN_NAMES or not isinstance(values, dict):
            continue
        try:
            profiles[name] = clean_values(values)
        except ValueError:
            continue  # hand-edited into something invalid: skip it rather than break Settings
    return profiles


def read_active_name() -> str:
    name = _read_file().get("active")
    return name if isinstance(name, str) and name in list_profiles() else DEFAULT_NAME


def active_values() -> dict:
    """What to send with her next reply."""
    return list_profiles()[read_active_name()]


def set_active(name: str) -> None:
    if name not in list_profiles():
        raise ValueError(f"there's no sampling profile called {name!r}")
    data = _read_file()
    data["active"] = name
    _write_file(data)


def save_profile(name: str, values: dict) -> str:
    """Saves (or overwrites) a profile and makes it the active one. Returns the
    name as saved. The built-in profiles can't be overwritten.
    """
    sanitized = sanitize_name(name, kind="sampling profile")
    # Compared both ways: sanitizing drops the "." in "Qwen 3.6 Thinking", so
    # that name would otherwise sneak through as a look-alike "Qwen 36 Thinking".
    builtin = {n.casefold() for n in BUILTIN_NAMES} | {sanitize_name(n, kind="").casefold() for n in BUILTIN_NAMES}
    if name.strip().casefold() in builtin or sanitized.casefold() in builtin:
        raise ValueError(f"{sanitized!r} is built in -- save your changes under a new name")
    cleaned = clean_values(values)
    data = _read_file()
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    profiles[sanitized] = cleaned
    data["profiles"] = profiles
    data["active"] = sanitized
    _write_file(data)
    return sanitized


def delete_profile(name: str) -> None:
    """Deletes a saved profile. If it was the active one, the default takes over."""
    if name in BUILTIN_NAMES:
        raise ValueError(f"{name!r} is built in and can't be deleted")
    data = _read_file()
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
    if name not in profiles:
        raise ValueError(f"there's no sampling profile called {name!r}")
    del profiles[name]
    data["profiles"] = profiles
    if data.get("active") == name:
        data["active"] = DEFAULT_NAME
    _write_file(data)
