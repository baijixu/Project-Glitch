"""Creates a custom voice for a Kokoro-fastapi-shaped speech engine by
blending existing voices with weights -- a Renderer settings-panel
feature, not in SPEC.md.

kokoro-fastapi has no way to accept an externally-authored voice file at
all -- confirmed against its own source, after an earlier version of this
feature assumed a JSON-upload flow that turned out to match nothing this
server actually does (there's no export path that ever produces a JSON
voice file to upload in the first place). What it DOES support natively
is blending any of its own existing voices by weight (POST /v1/audio/
voices/combine, its own "voice1(2)+voice2(1)" syntax) and handing back a
real, correctly-shaped voice file -- this module calls that endpoint
directly (not through the openai client main.py's RemoteTTS otherwise
uses; combine isn't part of the OpenAI-compatible surface that treats
generically) and writes the result into `voices_dir`, the same shared
folder kokoro-fastapi's own VoiceManager reads from (see
docker-compose.yml's volume mount). Also requires that image's own
ALLOW_LOCAL_VOICE_SAVING=true -- confirmed live, /v1/audio/voices/combine
403s "Local voice saving is disabled" without it.

Every result is force-prefixed "af_", same reasoning as any other voice
file here: kokoro-fastapi picks its language/inference pipeline from the
voice filename's first letter, and a name that doesn't start with one of
its known letters produces a 200 response with silent, empty audio --
confirmed live, not documented anywhere.
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
    -- passed straight through to /v1/audio/voices/combine, not parsed or
    validated here; a bad spec (unknown voice name, malformed syntax)
    raises RuntimeError with Kokoro's own error message pulled out of the
    response body (confirmed live: it names exactly what's wrong, e.g.
    "Voice 'af_sky_' not found. Available voices: ..." -- httpx's default
    exception string for a 400 is just "Client error '400 Bad Request'",
    which throws that detail away). Returns the voice id actually written
    under `voices_dir`.
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
