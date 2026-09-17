"""Manages Glitch's saved speech (TTS) engines -- a Renderer settings-panel
feature, not in SPEC.md. Each engine is one JSON file in tts_engines/ with
the fields needed to reach an OpenAI-compatible /v1/audio/speech HTTP
endpoint (see brain/voice/tts.py's RemoteTTS): `endpoint`, an optional
`api_key`, and the `voice`/`model` names to send on every request (any
engine can set these -- plain strings, not something only Kokoro-shaped
engines have; RemoteTTS.DEFAULT_VOICE/DEFAULT_MODEL are just its
last-resort guesses when an engine leaves them blank). This is exactly the
kind of secret config.example.yaml's own comment already points at -- "the
.env or whatever the secrets folder is" -- so it follows the same pattern
already established for profiles/souls/avatars: one gitignored file per
saved item under brain/, not a single flat .env (a per-engine api_key
doesn't fit a flat KEY=VALUE file the way a single shared one would).

Two fields are specifically for a Kokoro-fastapi-shaped engine (see
kokoro_voices.py's module docstring) and stay empty/unused for anything
else: `voices_dir`, a local folder Brain can write a newly-blended custom
voice into, and `custom_voices`, the names of whatever's been created
that way so far -- tracked here rather than by scanning voices_dir itself,
since that folder also holds Kokoro's own shipped presets and there'd be
no way to tell those apart from a directory listing alone.

"None" (NONE_NAME) is a no-op placeholder -- nothing configured/selected
yet, same role llm_engines.NONE_NAME plays for LLM engines, so a fresh
install doesn't implicitly load any particular engine before the user has
actually chosen one. Never sent by Brain in the `tts_engines` list -- the
Renderer always prepends it itself, same pattern as DEFAULT_PROFILE_NAME/
DEFAULT_SOUL_NAME in profiles.py/souls.py.
"""

import json
from pathlib import Path

from names import sanitize_name

TTS_ENGINES_DIR = Path(__file__).parent / "tts_engines"
ACTIVE_ENGINE_NAME_PATH = Path(__file__).parent / "active_tts_engine_name.txt"

NONE_NAME = "None"


def list_engines() -> list[str]:
    TTS_ENGINES_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in TTS_ENGINES_DIR.glob("*.json"))


def save_engine(name: str, endpoint: str, api_key: str, voice: str = "", voices_dir: str = "", model: str = "") -> None:
    """Overwrites the editable fields (endpoint/api_key/voice/model/
    voices_dir) but carries forward `custom_voices` from any existing file
    for this name -- this is the Edit-modal save path, not the
    voice-creation path, and shouldn't ever silently forget what's already
    been created just because the user tweaked the endpoint.
    """
    sanitized = sanitize_name(name, kind="speech engine")
    if sanitized == NONE_NAME:
        # Would otherwise create tts_engines/None.json, a real file
        # colliding with the reserved name the Renderer always prepends to
        # the dropdown -- same guard as profiles.py/souls.py.
        raise ValueError(f"{NONE_NAME!r} is reserved and can't be used as a speech engine name")
    TTS_ENGINES_DIR.mkdir(exist_ok=True)
    path = TTS_ENGINES_DIR / f"{sanitized}.json"
    custom_voices = []
    if path.exists():
        custom_voices = json.loads(path.read_text(encoding="utf-8")).get("custom_voices", [])
    path.write_text(
        json.dumps(
            {
                "endpoint": endpoint,
                "api_key": api_key,
                "voice": voice,
                "model": model,
                "voices_dir": voices_dir,
                "custom_voices": custom_voices,
            }
        ),
        encoding="utf-8",
    )


def read_engine(name: str) -> dict:
    """Returns {"endpoint", "api_key", "voice", "model", "voices_dir",
    "custom_voices"} -- used both to actually connect (main.py's
    _build_tts) and to pre-fill the editor for the Edit button
    (get_tts_engine), api_key included -- this is a single-user local app
    editing its own saved config back, not exposing one user's secret to
    another. The last four fields default in via .get() at every call site
    rather than here, so an engine saved before this module grew them
    still reads back cleanly.
    """
    path = TTS_ENGINES_DIR / f"{sanitize_name(name, kind='speech engine')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def set_engine_voice(name: str, voice: str) -> None:
    """Changes only the `voice` field of an already-saved engine -- the
    picker's "make this the default" action (main.py's
    _handle_set_tts_voice), distinct from save_engine's full-form edit.
    """
    if name == NONE_NAME:
        raise ValueError(f"{NONE_NAME!r} has no voice to set")
    path = TTS_ENGINES_DIR / f"{sanitize_name(name, kind='speech engine')}.json"
    engine = json.loads(path.read_text(encoding="utf-8"))
    engine["voice"] = voice
    path.write_text(json.dumps(engine), encoding="utf-8")


def add_custom_voice(name: str, voice_id: str) -> None:
    """Records a newly-created voice id (kokoro_voices.combine_voices's
    return value) against the engine that owns it -- deduplicated, since
    re-creating a voice under the same display name reuses the same id.
    """
    path = TTS_ENGINES_DIR / f"{sanitize_name(name, kind='speech engine')}.json"
    engine = json.loads(path.read_text(encoding="utf-8"))
    custom_voices = engine.setdefault("custom_voices", [])
    if voice_id not in custom_voices:
        custom_voices.append(voice_id)
    path.write_text(json.dumps(engine), encoding="utf-8")


def delete_engine(name: str) -> None:
    if name == NONE_NAME:
        raise ValueError(f"{NONE_NAME!r} is reserved and can't be deleted")
    path = TTS_ENGINES_DIR / f"{sanitize_name(name, kind='speech engine')}.json"
    path.unlink()


def set_active_engine_name(name: str) -> None:
    ACTIVE_ENGINE_NAME_PATH.write_text(name, encoding="utf-8")


def read_active_engine_name() -> str:
    """The name last passed to set_active_engine_name, or NONE_NAME if
    none has ever been explicitly selected -- a fresh install (or one
    where brain/config.yaml's optional speech setup was skipped) gets no
    TTS at all rather than silently assuming one.
    """
    if ACTIVE_ENGINE_NAME_PATH.exists():
        return ACTIVE_ENGINE_NAME_PATH.read_text(encoding="utf-8").strip() or NONE_NAME
    return NONE_NAME
