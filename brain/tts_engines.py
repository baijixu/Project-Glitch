"""Glitch's saved speech (TTS) engines (Settings -> Voice). Each engine is one
JSON file in tts_engines/ (see store.py) with what RemoteTTS needs to reach an
OpenAI-compatible /v1/audio/speech endpoint (voice/tts.py): `endpoint`, an
optional `api_key`, and the `voice`/`model` names sent with every request.

Two fields are only for a Kokoro-fastapi-shaped engine (see kokoro_voices.py):
`voices_dir`, a local folder Brain can write a newly-blended voice into, and
`custom_voices`, the voices created that way -- tracked here because that
folder also holds Kokoro's own presets, which a listing couldn't tell apart.

NONE_NAME is reserved: no speech engine, so a fresh install doesn't load one
before the user picks it.
"""

from pathlib import Path

from store import Choice, NamedStore

NONE_NAME = "None"

STORE = NamedStore(Path(__file__).parent / "tts_engines", kind="speech engine", reserved=NONE_NAME)
ACTIVE = Choice(Path(__file__).parent / "active_tts_engine_name.txt", default=NONE_NAME)

list_engines = STORE.names
delete_engine = STORE.delete
read_active_engine_name = ACTIVE.read
set_active_engine_name = ACTIVE.write


def save_engine(name: str, endpoint: str, api_key: str, voice: str = "", voices_dir: str = "", model: str = "") -> None:
    """The Edit dialog's save: overwrites the editable fields but carries
    `custom_voices` forward from any existing file for this name -- tweaking
    the endpoint shouldn't forget the voices already created.
    """
    try:
        custom_voices = STORE.read(name).get("custom_voices", [])
    except (OSError, ValueError):
        custom_voices = []
    STORE.write(name, {
        "endpoint": endpoint,
        "api_key": api_key,
        "voice": voice,
        "model": model,
        "voices_dir": voices_dir,
        "custom_voices": custom_voices,
    })


def read_engine(name: str) -> dict:
    """Returns {"endpoint", "api_key", "voice", "model", "voices_dir",
    "custom_voices"} -- used both to connect (engines.build_tts) and to
    pre-fill the Edit dialog, api_key included (a single-user local app editing
    its own saved config). Engines saved before the last four fields existed
    lack them, so callers .get() them.
    """
    return STORE.read(name)


def set_engine_voice(name: str, voice: str) -> None:
    """The voice picker's "make this the default" -- only the `voice` field."""
    if name == NONE_NAME:
        raise ValueError(f"{NONE_NAME!r} has no voice to set")
    STORE.update(name, voice=voice)


def add_custom_voice(name: str, voice_id: str) -> None:
    """Records a newly-created voice id (kokoro_voices.combine_voices) against
    the engine that owns it -- once, since re-creating a voice under the same
    display name reuses the same id.
    """
    custom_voices = STORE.read(name).get("custom_voices", [])
    if voice_id not in custom_voices:
        STORE.update(name, custom_voices=[*custom_voices, voice_id])
