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
