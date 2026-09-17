"""Tracks whether Glitch is currently plugged into an external agent
harness (a Renderer settings-panel feature) instead of Brain's own
persona/soul/profile/LLM-engine system, and manages the saved list of
harnesses themselves.

An open-ended user-editable list now, same as profiles/souls/llm_engines/
tts_engines (add/edit/delete any harness by name/endpoint/model/api_key
through the settings panel) -- this used to be a small, fixed set
hardcoded in main.py (HARNESS_NAMES) and configured only via config.yaml's
brain.harness block, deliberately not open-ended. See main()'s one-time
migration: an existing config.yaml-configured harness (the original,
still-supported case being Hermes Agent, https://github.com/NousResearch/
hermes-agent, via its OpenAI-compatible /v1/chat/completions) gets seeded
into this saved-list system automatically the first time Brain starts
with none saved yet, so switching to this didn't silently drop anyone's
existing setup.

When active, Brain still handles STT/TTS/viseme lipsync exactly as
before -- only the "what does she say" step changes, from LocalLLM's own
system-prompt-built reply to a thin relay through llm/client.py's
HarnessLLM (see main.py's _handle_set_harness_active). Which one (if any)
is persisted here so the connection survives a Brain restart, the same as
which profile/soul/engine is active.
"""

import json
from pathlib import Path

from names import sanitize_name

ACTIVE_HARNESS_PATH = Path(__file__).parent / "active_harness.txt"
SELECTED_HARNESS_PATH = Path(__file__).parent / "selected_harness.txt"
SWITCH_KEY_PATH = Path(__file__).parent / "harness_switch_key.txt"
HARNESSES_DIR = Path(__file__).parent / "harnesses"

# Reserved -- means "not plugged into any harness, using her own profile/
# soul/LLM engine". Never one of the files in HARNESSES_DIR and can't be
# saved/deleted as one, same role llm_engines.NONE_NAME plays for LLM
# engines, tts_engines.NONE_NAME for speech engines, DEFAULT_PROFILE_NAME
# for profiles, etc. The Renderer's dropdown always prepends this itself
# -- list_harnesses() below never includes it.
NONE_NAME = "None"


def list_harnesses() -> list[str]:
    HARNESSES_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in HARNESSES_DIR.glob("*.json"))


def save_harness(name: str, endpoint: str, model: str, api_key: str) -> None:
    sanitized = sanitize_name(name, kind="harness")
    if sanitized == NONE_NAME:
        raise ValueError(f"{NONE_NAME!r} is reserved and can't be used as a harness name")
    HARNESSES_DIR.mkdir(exist_ok=True)
    path = HARNESSES_DIR / f"{sanitized}.json"
    path.write_text(json.dumps({"endpoint": endpoint, "model": model, "api_key": api_key}), encoding="utf-8")


def read_harness(name: str) -> dict:
    """Returns {"endpoint": str, "model": str, "api_key": str} -- used both
    to actually connect (main.py's _build_harness_llm) and to pre-fill the
    editor for the Edit button (get_harness), api_key included -- same
    single-user-app reasoning as llm_engines.py's read_engine.
    """
    path = HARNESSES_DIR / f"{sanitize_name(name, kind='harness')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def delete_harness(name: str) -> None:
    if name == NONE_NAME:
        raise ValueError(f"{NONE_NAME!r} can't be deleted")
    path = HARNESSES_DIR / f"{sanitize_name(name, kind='harness')}.json"
    path.unlink()


def set_active_harness(name: str) -> None:
    """name="" means no harness active -- Glitch is using her own
    profile/soul/LLM engine again.
    """
    ACTIVE_HARNESS_PATH.write_text(name, encoding="utf-8")


def read_active_harness() -> str:
    """"" if no harness is active (never set, or explicitly turned off)."""
    if ACTIVE_HARNESS_PATH.exists():
        return ACTIVE_HARNESS_PATH.read_text(encoding="utf-8").strip()
    return ""


def set_selected_harness_name(name: str) -> None:
    """Remembers which harness the Renderer's dropdown should show as
    picked, independent of read_active_harness -- turning a harness off
    clears the active connection (as it should) but this stays put, so
    the dropdown doesn't forget the choice across a refresh/reconnect or
    a Brain restart the way it used to. Updated whenever a harness is
    successfully turned on (see main.py's _handle_set_harness_active);
    never cleared by turning one off, only by deleting the harness it
    points at (see _handle_delete_harness) or picking a different one.
    """
    SELECTED_HARNESS_PATH.write_text(name, encoding="utf-8")


def read_selected_harness_name() -> str:
    """"" if nothing has ever been selected."""
    if SELECTED_HARNESS_PATH.exists():
        return SELECTED_HARNESS_PATH.read_text(encoding="utf-8").strip()
    return ""


def set_switch_key(key: str) -> None:
    """The secret required to turn a harness ON (see main.py's
    _HARNESS_SWITCH_KEY) -- persisted here, set via the Renderer's Harness
    settings, so it survives a Brain restart and the Renderer only ever
    has to send it automatically on every set_harness_active request
    instead of the user retyping it each time (protocol.md's
    save_harness_key/harness_state). key="" clears it, meaning "no extra
    gate beyond the confirm dialog", same as config.yaml's own null.
    """
    SWITCH_KEY_PATH.write_text(key, encoding="utf-8")


def read_switch_key() -> str:
    """"" if no switch key has ever been saved via the app -- callers
    should fall back to config.yaml's own brain.harness_switch_key in
    that case (a one-time seed for the very first run, see main()).
    """
    if SWITCH_KEY_PATH.exists():
        return SWITCH_KEY_PATH.read_text(encoding="utf-8").strip()
    return ""
