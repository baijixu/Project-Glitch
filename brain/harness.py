"""Glitch's saved agent harnesses (Settings -> Harness), and whether she's
plugged into one instead of her own soul/profile/LLM engine. Each harness is
one JSON file in harnesses/ (see store.py): `endpoint`, `model` and `api_key`
for an OpenAI-compatible /v1/chat/completions -- e.g. Hermes Agent
(https://github.com/NousResearch/hermes-agent). main()'s one-time migration
seeds this list from config.yaml's older brain.harness block.

While one is active, Brain still does STT/TTS/lipsync as usual -- only "what
does she say" changes, to a relay through llm/client.py's HarnessLLM. Which
one is active is kept here, so the connection survives a Brain restart.
"""

import json
import uuid
from pathlib import Path

from store import Choice, NamedStore

NONE_NAME = "None"

STORE = NamedStore(Path(__file__).parent / "harnesses", kind="harness", reserved=NONE_NAME)
# The harness she's plugged into, or "" for none (her own LLM).
ACTIVE = Choice(Path(__file__).parent / "active_harness.txt")
# Which harness the Renderer's dropdown shows as picked, apart from ACTIVE:
# turning a harness off clears ACTIVE but keeps this, so the dropdown doesn't
# forget the choice across a refresh or a restart. "" = none picked.
SELECTED = Choice(Path(__file__).parent / "selected_harness.txt")
SESSIONS_PATH = Path(__file__).parent / "harness_sessions.json"

list_harnesses = STORE.names
read_harness = STORE.read  # {"endpoint", "model", "api_key"}
delete_harness = STORE.delete
read_active_harness = ACTIVE.read
set_active_harness = ACTIVE.write
read_selected_harness_name = SELECTED.read
set_selected_harness_name = SELECTED.write


def save_harness(name: str, endpoint: str, model: str, api_key: str) -> None:
    STORE.write(name, {"endpoint": endpoint, "model": model, "api_key": api_key})


# -- Conversation continuity ---------------------------------------------------
# Glitch sends a harness only the newest message; the harness keeps the
# conversation. It needs to be told which conversation a message belongs to
# (see HarnessLLM.__init__) -- without that Hermes started a brand-new session
# for every message, so she forgot what was said a message ago; OpenClaw is the
# same by default. One id per harness, kept on disk so a Brain restart
# continues the same conversation; Clear Chat starts a new one.


def _read_sessions() -> dict:
    try:
        data = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def new_session_id(name: str) -> str:
    """Starts a fresh conversation with the named harness and returns its id."""
    sessions = _read_sessions()
    sessions[name] = f"glitch-{uuid.uuid4().hex}"
    SESSIONS_PATH.write_text(json.dumps(sessions), encoding="utf-8")
    return sessions[name]


def session_id(name: str) -> str:
    """The conversation Glitch is in with the named harness (a new one if none yet)."""
    current = _read_sessions().get(name)
    return current if isinstance(current, str) and current else new_session_id(name)
