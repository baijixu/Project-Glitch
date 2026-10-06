"""Whether her voice (TTS) is on. Off, every reply still gets its mood and text
(see reply.py's reply_to) but no speech is synthesized: a text-only conversation,
with no TTS compute spent on audio nobody wants played. On by default.
"""

from pathlib import Path

from store import Toggle

ACTIVE = Toggle(Path(__file__).parent / "voice_active.txt", default=True)
set_voice_active = ACTIVE.write
read_voice_active = ACTIVE.read
