"""Discord voice-channel presence: the bot auto-joins your voice channel,
listens continuously, and replies out loud when it hears its wake word
("glitch") -- a separate, later phase from the text-chat bot in bot.py,
deliberately deferred until that was verified working first. discord.py
alone can't receive voice audio; this needs the discord-ext-voice-recv
extension, checked for compatibility with the installed discord.py 2.7.1
(imports cleanly, installs alongside it with no conflicts) before writing
any of this.

Anyone in the channel can wake her, not just the configured allowed
user -- text chat gates on user identity, voice gates on the wake word
instead (an explicit choice, not an oversight).

Untested against a real Discord voice call as of writing -- there's no
way to join one from this environment. The silence-timeout/RMS-threshold
constants below are a first guess, not a tuned value; expect to adjust
them once someone's actually spoken to her.
"""

from __future__ import annotations

import audioop
import re
import threading
import time

from discord.ext import voice_recv

SILENCE_TIMEOUT_SEC = 0.8  # gap after which an utterance is considered finished
MAX_UTTERANCE_SEC = 20.0  # hard cap so one person talking forever can't grow unbounded
MIN_UTTERANCE_SEC = 0.4  # shorter than this is almost certainly noise, not speech
WATCHER_POLL_SEC = 0.2
RMS_SILENCE_THRESHOLD = 300  # out of 16-bit PCM's ~32768 range -- filters near-silent frames

# discord.opus.Decoder's constants -- what AudioSink.write() delivers when
# wants_opus() returns False.
SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2

WAKE_WORD_PATTERN = re.compile(r"\bglitch\b", re.IGNORECASE)
WAKE_WORD_SEARCH_WORDS = 4  # only look for it near the start of the utterance


def extract_wake_query(transcript: str) -> str | None:
    """Returns the text after the wake word if it appears near the start
    of the utterance, or None if this wasn't directed at her. Tolerant of
    "hey glitch" / "ok glitch" / a bare "glitch" -- voice STT on a wake
    word specifically is never going to be perfectly reliable, an exact
    phrase match would miss too much.
    """
    words = transcript.split()
    for i, word in enumerate(words[:WAKE_WORD_SEARCH_WORDS]):
        if WAKE_WORD_PATTERN.search(word):
            remainder = " ".join(words[i + 1 :]).strip(" ,.:;!?")
            return remainder or None
    return None


def _rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    return audioop.rms(pcm, SAMPLE_WIDTH)


class UtteranceSink(voice_recv.AudioSink):
    """Buffers each speaker's PCM separately and, once a speaker has gone
    quiet for SILENCE_TIMEOUT_SEC, hands their buffered utterance off to
    on_utterance -- called from a short-lived worker thread per
    utterance, never voice_recv's own reader thread or the asyncio loop,
    so one utterance's STT/LLM/TTS pipeline can't stall detection of the
    next one.
    """

    def __init__(self, on_utterance) -> None:
        super().__init__()
        self._on_utterance = on_utterance
        self._lock = threading.Lock()
        self._buffers: dict[int, bytearray] = {}
        self._last_write: dict[int, float] = {}
        self._started: dict[int, float] = {}
        self._users: dict[int, object] = {}
        self._stop = threading.Event()
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data) -> None:
        if user is None or not data.pcm:
            return
        if _rms(data.pcm) < RMS_SILENCE_THRESHOLD:
            return
        now = time.monotonic()
        with self._lock:
            self._buffers.setdefault(user.id, bytearray()).extend(data.pcm)
            self._last_write[user.id] = now
            self._started.setdefault(user.id, now)
            self._users[user.id] = user

    def _watch(self) -> None:
        while not self._stop.wait(WATCHER_POLL_SEC):
            now = time.monotonic()
            to_flush = []
            with self._lock:
                for user_id, last in list(self._last_write.items()):
                    duration = now - self._started.get(user_id, now)
                    silent_for = now - last
                    if silent_for >= SILENCE_TIMEOUT_SEC or duration >= MAX_UTTERANCE_SEC:
                        buf = self._buffers.pop(user_id, None)
                        self._last_write.pop(user_id, None)
                        self._started.pop(user_id, None)
                        user = self._users.pop(user_id, None)
                        if buf and duration >= MIN_UTTERANCE_SEC:
                            to_flush.append((user, bytes(buf)))
            for user, pcm in to_flush:
                threading.Thread(target=self._on_utterance, args=(user, pcm), daemon=True).start()

    def cleanup(self) -> None:
        self._stop.set()
