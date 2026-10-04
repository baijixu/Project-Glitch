"""Glitch's saved LLM engines (Settings -> LLM). Each engine is one JSON file
in llm_engines/ (see store.py) with what LocalLLM needs to connect
(llm.py): `endpoint`, `model` (empty lets the server use whatever it
has loaded), an optional `api_key`, and for Ollama `provider`/`think`.

NONE_NAME is reserved: config.yaml's optional brain.llm block, or no LLM at
all if that's empty -- see llm.py's NoneLLM for what runs then.
"""

import json
from pathlib import Path

from store import Choice, NamedStore

NONE_NAME = "None"

STORE = NamedStore(Path(__file__).parent / "llm_engines", kind="LLM engine", reserved=NONE_NAME)
# The engine she's on -- NONE_NAME (config.yaml's brain.llm block, or no LLM at
# all) until one is picked.
ACTIVE = Choice(Path(__file__).parent / "active_llm_engine_name.txt", default=NONE_NAME)
# Which engine role-play switches to (Settings -> Role-play), or "" to keep the
# current one -- see read_roleplay_engine.
ROLEPLAY_ENGINE = Choice(Path(__file__).parent / "roleplay_llm_engine.txt")

list_engines = STORE.names
delete_engine = STORE.delete
read_active_engine_name = ACTIVE.read
set_active_engine_name = ACTIVE.write


def save_engine(name: str, endpoint: str, model: str, api_key: str, provider: str = "openai", think: bool = False) -> None:
    """provider is "openai" (any OpenAI-compatible endpoint -- LM Studio,
    llama-server, Ollama's own /v1 compat layer) or "ollama" (Ollama's
    *native* /api/chat -- see llm.py's OllamaLLM for why that
    distinction is load-bearing: only the native API actually honors
    `think`). think is only meaningful for provider "ollama"; saved as
    given either way, so re-opening the editor doesn't lose the value.
    """
    STORE.write(name, {"endpoint": endpoint, "model": model, "api_key": api_key, "provider": provider, "think": think})


def read_engine(name: str) -> dict:
    """Returns {"endpoint", "model", "api_key", "provider", "think"} -- used both
    to connect (engines.build_llm) and to pre-fill the Edit dialog, api_key
    included (a single-user local app editing its own saved config). An engine
    saved before provider/think existed reads back as a plain OpenAI-compatible
    one.
    """
    engine = STORE.read(name)
    engine.setdefault("provider", "openai")
    engine.setdefault("think", False)
    return engine


def read_roleplay_engine() -> str:
    """The saved engine role-play switches to, or "" to stay on whatever engine
    is active (the default). An engine that's since been deleted counts as "".
    """
    name = ROLEPLAY_ENGINE.read()
    return name if name in list_engines() else ""


def set_roleplay_engine(name: str) -> None:
    """name is a saved engine, or "" to keep the current engine in role-play."""
    if name and name not in list_engines():
        raise ValueError(f"there's no saved LLM engine called {name!r}")
    ROLEPLAY_ENGINE.write(name)

# -- Role-play sessions ---------------------------------------------------------
# While role-play has switched her to its own engine: {"previous": the engine to
# go back to ("" = stay), "engine": the role-play engine, "think": the confirm
# dialog's choice}. The think choice is applied when that engine is built
# (engines.build_llm) -- never written into the engine's saved settings, which
# role-play used to overwrite (and then force back to think=True when it ended).
ROLEPLAY_SESSION_PATH = Path(__file__).parent / "roleplay_session.json"


def start_roleplay_session(previous: str, engine: str, think: bool) -> None:
    ROLEPLAY_SESSION_PATH.write_text(json.dumps({"previous": previous, "engine": engine, "think": think}), encoding="utf-8")


def read_roleplay_session() -> dict:
    """The current role-play session, or {} if role-play didn't switch engines."""
    try:
        return json.loads(ROLEPLAY_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def end_roleplay_session() -> dict:
    """Removes and returns the current role-play session ({} if there wasn't one)."""
    session = read_roleplay_session()
    ROLEPLAY_SESSION_PATH.unlink(missing_ok=True)
    return session

