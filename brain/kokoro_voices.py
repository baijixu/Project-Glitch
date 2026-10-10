"""Custom voices for a Kokoro-fastapi speech engine, by blending existing ones with
weights (Settings). Kokoro can't take a voice file from outside, but it can blend its
own (POST /v1/audio/voices/combine, "voice1(2)+voice2(1)"); the result is written into
`voices_dir`, the folder its VoiceManager reads (docker-compose.yml's volume mount).
Needs ALLOW_LOCAL_VOICE_SAVING=true on its side, or combine 403s.

Every result is prefixed "af_": Kokoro picks its language pipeline from a voice name's
first letter, and an unknown one gives silent audio with a 200.
"""

from pathlib import Path

import httpx

from names import sanitize_name

VOICE_PREFIX = "af_"


def voice_id_for(name: str) -> str:
    """The actual <name>.pt / `voice` API value a display name maps to --
    always force-prefixed, see module docstring. Exposed so main.py can
    record the same id it actually wrote (tts_engines.add_custom_voice)
    without duplicating the prefix logic.
    """
    return VOICE_PREFIX + sanitize_name(name, kind="voice")


def combine_voices(endpoint: str, api_key: str | None, spec: str, name: str, voices_dir: str) -> str:
    """`spec` is Kokoro's own blend syntax, e.g. "af_sky(1)+af_bella(0.8)"
    -- passed through as-is. A bad one raises RuntimeError with Kokoro's own message
    ("Voice 'af_sky_' not found...", which httpx's own error leaves out). Returns the
    voice id written under `voices_dir`.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = httpx.post(
        f"{endpoint.rstrip('/')}/audio/voices/combine",
        json=spec,
        headers=headers,
        timeout=30,
    )
    if response.is_error:
        try:
            detail = response.json()["detail"]
            message = detail["message"] if isinstance(detail, dict) else detail
        except Exception:
            message = response.text or f"HTTP {response.status_code}"
        raise RuntimeError(message)
    voice_id = voice_id_for(name)
    dir_path = Path(voices_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{voice_id}.pt").write_bytes(response.content)
    return voice_id
