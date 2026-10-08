"""TTS -- spec section 5's "STT / TTS". Two engines, both speaking the same
(wav_bytes, viseme_frames) contract so main.py never needs to know which one
is active (brain/tts_engines.py owns that choice):

  - NoneTTS: a no-op placeholder, tts_engines.NONE_NAME -- nothing
    configured/selected yet.
  - RemoteTTS: any HTTP TTS service speaking the OpenAI-compatible
    /v1/audio/speech shape (e.g. Kokoro run via Docker,
    docker-compose.yml's kokoro-tts service) -- lets the actual inference
    cost live in its own process/container instead of Brain's.

Neither engine exposes real phoneme/viseme timestamps, so lipsync is read
off the audio itself (_viseme_frames_from_audio): loudness for how open,
brightness for which vowel shape -- not a phoneme alignment, but the mouth
moves the way speech does.
"""

import io
import re
import time

import httpx
import numpy as np
import soundfile as sf
from openai import OpenAI

import protocol

ENVELOPE_WINDOW_MS = 30
TTS_TIMEOUT_SEC = 120  # ponytail: a guess with room for a long reply on a CPU engine; raise if real replies hit it
# A switched-off PC on the LAN takes ~20 s to refuse a connection; a live one answers in milliseconds.
TTS_CONNECT_TIMEOUT_SEC = 3
# After her engine fails, how long the fallback speaks before her own engine is tried again.
FALLBACK_SEC = 60

# The LLM's replies routinely include emoji (it's instructed to be
# conversational, not told to avoid them) -- Kokoro doesn't skip them, it
# tries to vocalize them, which is exactly as bad as it sounds. Stripped
# here, at the TTS boundary specifically, so speak_text (the on-screen
# subtitle) still shows them -- only what's actually spoken gets sanitized.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f680-\U0001f6ff"  # transport & map symbols
    "\U0001f700-\U0001f7ff"  # symbols and pictographs extended-A / geometric shapes ext.
    "\U0001f900-\U0001f9ff"  # supplemental symbols and pictographs
    "\U0001fa70-\U0001faff"  # symbols and pictographs extended-A
    "\U00002600-\U000026ff"  # miscellaneous symbols
    "\U00002700-\U000027bf"  # dingbats
    "\U0001f1e0-\U0001f1ff"  # regional indicators (flag emoji)
    "\U0000fe0f"  # variation selector-16 (emoji presentation)
    "\U0000200d"  # zero-width joiner (emoji sequences, e.g. family/skin-tone combos)
    "]+"
)


# Her mood tag (llm.py's VALID_MOODS) as a speaking-style hint, sent as OpenAI's
# `instructions` -- Qwen3-TTS (tools/qwen_tts_server.py) follows it, Kokoro
# ignores it. Neutral sends none: the voice's own default delivery.
MOOD_STYLES = {
    "happy": "Warm and cheerful, with a smile in the voice.",
    "sad": "Soft, gentle and a little sad.",
    "angry": "Firm and frustrated, with an edge.",
    "relaxed": "Calm, easy and unhurried.",
    "surprised": "Surprised and animated.",
}


def _strip_emoji(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_PATTERN.sub("", text)).strip()


# Below this share of the clip's loudest moment a window counts as silence: mouth
# closed, and left out when ranking how bright the sound is.
VOICED_LEVEL = 0.1
# Louder than this, a vowel gets the more open shape of its pair (oh over ou, ee over ih).
# Tuned on her Qwen3-TTS voice so all five shapes get used; the knob if her mouth looks off.
OPEN_LEVEL = 0.3


def _viseme_frames_from_audio(audio: np.ndarray, sample_rate: int) -> list[protocol.VisemeFrame]:
    """Her mouth over time, read off the audio in ENVELOPE_WINDOW_MS windows: how open
    from how loud, and which of VRM's five vowel shapes from how bright the sound is
    -- bright windows (a high spectral centroid) are front vowels (ee/ih), dark ones
    back vowels (oh/ou), the middle aa, and the louder of each pair the more open
    shape. Brightness is ranked within the clip, so it fits any voice. An approximation
    from the sound, not phonemes -- but the mouth changes shape the way speech does.
    sample_rate is the clip's own, since the engine could be anything.
    """
    window = int(sample_rate * ENVELOPE_WINDOW_MS / 1000)
    if window <= 0 or len(audio) < window:
        return []
    n_windows = len(audio) // window
    windows = audio[: n_windows * window].reshape(n_windows, window)
    rms = np.sqrt(np.mean(windows**2, axis=1))
    weights = rms / (rms.max() or 1.0)
    spectrum = np.abs(np.fft.rfft(windows * np.hanning(window), axis=1))
    centroid = (spectrum * np.fft.rfftfreq(window, 1 / sample_rate)).sum(axis=1) / (spectrum.sum(axis=1) + 1e-9)
    voiced = weights > VOICED_LEVEL
    brightness = np.full(n_windows, 0.5)
    brightness[voiced] = centroid[voiced].argsort().argsort() / max(voiced.sum() - 1, 1)  # 0 darkest .. 1 brightest
    frames = []
    for i, (weight, bright) in enumerate(zip(weights, brightness)):
        if bright < 1 / 3:
            shape = "oh" if weight > OPEN_LEVEL else "ou"
        elif bright > 2 / 3:
            shape = "ee" if weight > OPEN_LEVEL else "ih"
        else:
            shape = "aa"
        frames.append(protocol.VisemeFrame(t=round(i * ENVELOPE_WINDOW_MS / 1000, 3), shape=shape, weight=round(float(weight) if voiced[i] else 0.0, 3)))
    return frames


class NoneTTS:
    """Placeholder used when tts_engines.NONE_NAME is the active speech
    engine -- i.e. no real engine has been configured/selected yet.
    Deliberately never produces audio; reply.py's reply_to checks for
    this class specifically and skips the synthesize call entirely (the
    same way it already skips when voice is toggled off), so this method
    existing is really just interface parity with RemoteTTS, not
    something expected to run in practice.
    """

    SAMPLE_RATE = 24000

    def synthesize(self, text: str, mood: str = "neutral") -> tuple[bytes, list[protocol.VisemeFrame]]:
        return b"", []


class RemoteTTS:
    """Speech via an HTTP TTS engine (brain/tts_engines.py) rather than a
    model loaded in-process -- anything speaking the OpenAI-compatible
    /v1/audio/speech shape works, using the openai client the exact same
    way llm.py's LocalLLM already does for a local LLM endpoint
    (openai>=1.0 is already a dependency; no new HTTP library needed).
    """

    # Only reached when a saved engine has no `voice`/`model` set at all
    # (e.g. one saved before tts_engines.py grew those fields) -- "kokoro"/
    # "af_heart" happen to be real Kokoro values, but this class has no
    # idea whether the endpoint on the other end is even Kokoro; they're
    # last-resort guesses, not assumptions every engine should share.
    # engines.build_tts always prefers the saved engine's own
    # `model`/`voice` first. Previously "kokoro" was hardcoded directly in
    # synthesize() below with no way to override it at all -- a non-Kokoro
    # OpenAI-compatible TTS server (a real one to plug in, not this
    # process's own model choice) would get the wrong model name on every
    # single request.
    DEFAULT_MODEL = "kokoro"
    DEFAULT_VOICE = "af_heart"

    def __init__(
        self, endpoint: str, api_key: str | None = None, voice: str = DEFAULT_VOICE, model: str = DEFAULT_MODEL
    ) -> None:
        # The client's defaults (600 s, 2 retries) let a hung engine hold the reply
        # queue for ~30 minutes -- every device and her reaching out waited on it.
        self._client = OpenAI(
            base_url=endpoint,
            api_key=api_key or "not-needed",
            timeout=httpx.Timeout(TTS_TIMEOUT_SEC, connect=TTS_CONNECT_TIMEOUT_SEC),
            max_retries=0,
        )
        self._endpoint = endpoint
        # Another engine to speak with when this one fails (engines.build_tts sets it),
        # and until when it does, so a dead engine costs one failed call, not one per sentence.
        self.fallback: "RemoteTTS | None" = None
        self._fallback_until = 0.0
        self._voice = voice
        self._model = model
        # Corrected in synthesize() once real audio comes back -- the
        # engine on the other end of an arbitrary HTTP endpoint isn't
        # guaranteed to render at 24000Hz. This default only matters for
        # the very first speak_audio message's sample_rate
        # field, which the Renderer doesn't actually read (the WAV header
        # carries the real rate for decodeAudioData) -- see protocol.md.
        self.SAMPLE_RATE = 24000

    def synthesize(self, text: str, mood: str = "neutral") -> tuple[bytes, list[protocol.VisemeFrame]]:
        if self.fallback and time.monotonic() < self._fallback_until:
            return self.fallback.synthesize(text, mood)
        try:
            return self._synthesize(text, mood)
        except Exception as exc:
            if not self.fallback:
                raise
            print(f"[tts] {self._endpoint} failed ({exc!r}) -- the fallback speaks for the next {FALLBACK_SEC}s")
            self._fallback_until = time.monotonic() + FALLBACK_SEC
            return self.fallback.synthesize(text, mood)

    def _synthesize(self, text: str, mood: str) -> tuple[bytes, list[protocol.VisemeFrame]]:
        text = _strip_emoji(text)
        style = {"instructions": MOOD_STYLES[mood]} if mood in MOOD_STYLES else {}
        response = self._client.audio.speech.create(
            model=self._model, voice=self._voice, input=text, response_format="wav", **style
        )
        wav_bytes = response.content
        audio, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)  # collapse to mono for the envelope calc
        self.SAMPLE_RATE = sample_rate
        return wav_bytes, _viseme_frames_from_audio(audio, sample_rate)
