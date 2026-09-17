"""Whether Glitch's spoken voice (TTS synthesis) is turned on -- a Renderer
settings-panel toggle, mirroring profiles.py's roleplay_active.txt pattern
(single flat "1"/"0" file -- not per-connection like the Debugging toggle,
since this is a real behavior switch anyone connected should see the same
way, same reasoning as roleplay_active being shared rather than
per-connection).

Off means Brain still sends set_expression/speak_text for every reply (the
mood/subtitle/text side keeps working) -- see main.py's _reply_to -- but
skips synthesize()/speak_audio/viseme_stream entirely: a text-only
conversation instead of a muted one, so there's no TTS compute spent on
audio nobody wants played.
"""

from pathlib import Path

VOICE_ACTIVE_PATH = Path(__file__).parent / "voice_active.txt"


def set_voice_active(active: bool) -> None:
    VOICE_ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_voice_active() -> bool:
    """Defaults to True (voice on) when never explicitly set -- matches
    this app's pre-existing behavior, where every reply was always spoken.
    """
    if VOICE_ACTIVE_PATH.exists():
        return VOICE_ACTIVE_PATH.read_text(encoding="utf-8").strip() != "0"
    return True
