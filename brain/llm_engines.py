"""Manages Glitch's saved LLM engines -- a Renderer settings-panel feature,
not in SPEC.md. Each engine is one JSON file in llm_engines/ with the
fields LocalLLM's constructor takes (brain/llm/client.py): `endpoint`,
`model` (optional -- null lets the server use whatever it has loaded, same
meaning as config.yaml's own `llm.model: null`), and an optional `api_key`.
Same gitignored-file-per-saved-item pattern as tts_engines.py/profiles.py/
souls.py/avatars.py, for the same reason (a per-engine api_key doesn't fit
a single flat .env the way one shared secret would).

"None" (NONE_NAME) is reserved -- config.yaml's own `brain.llm` block is
now optional (was required before this), so this means "whatever that
block has, or genuinely no LLM at all if it's empty/absent" rather than
assuming a working endpoint always exists. Never one of the files in here
and can't be deleted, same reserved-name role DEFAULT_PROFILE_NAME/
DEFAULT_SOUL_NAME/tts_engines.py's NONE_NAME play elsewhere, and same
"genuinely nothing configured" meaning as harness.py's own NONE_NAME --
see llm/client.py's NoneLLM for what actually runs when it really is
empty.
"""

import json
from pathlib import Path

from names import sanitize_name

LLM_ENGINES_DIR = Path(__file__).parent / "llm_engines"
ACTIVE_ENGINE_NAME_PATH = Path(__file__).parent / "active_llm_engine_name.txt"
# Which engine role-play switches to (Settings -> Role-play), or none to keep
# the current one -- see read_roleplay_engine.
ROLEPLAY_ENGINE_PATH = Path(__file__).parent / "roleplay_llm_engine.txt"

NONE_NAME = "None"


def list_engines() -> list[str]:
    LLM_ENGINES_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in LLM_ENGINES_DIR.glob("*.json"))


def save_engine(name: str, endpoint: str, model: str, api_key: str, provider: str = "openai", think: bool = False) -> None:
    """provider is "openai" (any OpenAI-compatible endpoint -- LM Studio,
    llama-server, Ollama's own /v1 compat layer) or "ollama" (Ollama's
    *native* /api/chat -- see llm/client.py's OllamaLLM for why that
    distinction is load-bearing: only the native API actually honors
    `think`). think is only meaningful for provider "ollama"; saved as
    given either way rather than silently dropped, so re-opening the
    editor for an "openai" engine that had it set doesn't lose the value.
    """
    sanitized = sanitize_name(name, kind="LLM engine")
    if sanitized == NONE_NAME:
        # Would otherwise create llm_engines/None.json, a real file
        # colliding with the reserved name the Renderer always prepends
        # to the dropdown -- same guard as tts_engines.py/profiles.py.
        raise ValueError(f"{NONE_NAME!r} is reserved and can't be used as an LLM engine name")
    LLM_ENGINES_DIR.mkdir(exist_ok=True)
    path = LLM_ENGINES_DIR / f"{sanitized}.json"
    path.write_text(
        json.dumps({"endpoint": endpoint, "model": model, "api_key": api_key, "provider": provider, "think": think}),
        encoding="utf-8",
    )


def read_engine(name: str) -> dict:
    """Returns {"endpoint": str, "model": str, "api_key": str, "provider":
    str, "think": bool} -- used both to actually connect (main.py's
    _build_llm) and to pre-fill the editor for the Edit button
    (get_llm_engine), api_key included -- same single-user-app reasoning
    as tts_engines.py's read_engine. provider/think default in here (not
    just at each call site) so an engine saved before either field existed
    reads back as "openai"/False -- a plain OpenAI-compatible engine,
    exactly what every engine was before this distinction existed at all.
    """
    path = LLM_ENGINES_DIR / f"{sanitize_name(name, kind='LLM engine')}.json"
    engine = json.loads(path.read_text(encoding="utf-8"))
    engine.setdefault("provider", "openai")
    engine.setdefault("think", False)
    return engine


def delete_engine(name: str) -> None:
    if name == NONE_NAME:
        raise ValueError(f"{NONE_NAME!r} can't be deleted")
    path = LLM_ENGINES_DIR / f"{sanitize_name(name, kind='LLM engine')}.json"
    path.unlink()


def set_active_engine_name(name: str) -> None:
    ACTIVE_ENGINE_NAME_PATH.write_text(name, encoding="utf-8")


def read_active_engine_name() -> str:
    """The name last passed to set_active_engine_name, or NONE_NAME if
    none has ever been explicitly selected -- for anyone who never
    touches this feature, that resolves to whatever config.yaml's now-
    optional brain.llm block has, or genuinely no LLM if it's absent (see
    NONE_NAME's own module docstring).
    """
    if ACTIVE_ENGINE_NAME_PATH.exists():
        return ACTIVE_ENGINE_NAME_PATH.read_text(encoding="utf-8").strip() or NONE_NAME
    return NONE_NAME


def read_roleplay_engine() -> str:
    """The saved engine role-play switches to, or "" to stay on whatever engine
    is active (the default). An engine that's since been deleted counts as "".
    Role-play used to always switch to an engine named "Ollama", which for
    anyone not running Ollama meant every first role-play reply failed.
    """
    try:
        name = ROLEPLAY_ENGINE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return name if name in list_engines() else ""


def set_roleplay_engine(name: str) -> None:
    """name is a saved engine, or "" to keep the current engine in role-play."""
    if name and name not in list_engines():
        raise ValueError(f"there's no saved LLM engine called {name!r}")
    ROLEPLAY_ENGINE_PATH.write_text(name, encoding="utf-8")


# -- Role-play sessions ---------------------------------------------------------
# While role-play has switched her to its own engine: {"previous": the engine to
# go back to ("" = stay), "engine": the role-play engine, "think": the confirm
# dialog's choice}. The think choice is applied when that engine is built
# (main.py's _build_llm) -- never written into the engine's saved settings, which
# role-play used to overwrite (and then force back to think=True when it ended).
ROLEPLAY_SESSION_PATH = Path(__file__).parent / "roleplay_session.json"
# The two-line text file role-play kept before sessions existed (previous engine,
# then the role-play engine) -- converted once at startup, see migrate_roleplay_record.
_LEGACY_ROLEPLAY_RECORD_PATH = Path(__file__).parent / "roleplay_previous_engine.txt"


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


def migrate_roleplay_record() -> None:
    """One-time: turns the old two-line record into a session. The old code had
    already written role-play's think choice into the engine's saved settings,
    so that's where the session's think comes from.
    """
    try:
        lines = _LEGACY_ROLEPLAY_RECORD_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    previous = lines[0].strip() if lines else ""
    engine = lines[1].strip() if len(lines) > 1 else ""
    try:
        think = bool(read_engine(engine)["think"]) if engine else False
    except (ValueError, OSError):
        think = False
    start_roleplay_session(previous, engine, think)
    _LEGACY_ROLEPLAY_RECORD_PATH.unlink()
