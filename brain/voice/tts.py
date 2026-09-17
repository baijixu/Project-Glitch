"""TTS -- spec section 5's "STT / TTS". Two engines, both speaking the same
(wav_bytes, viseme_frames) contract so main.py never needs to know which one
is active (brain/tts_engines.py owns that choice):

  - NoneTTS: a no-op placeholder, tts_engines.NONE_NAME -- nothing
    configured/selected yet.
  - RemoteTTS: any HTTP TTS service speaking the OpenAI-compatible
    /v1/audio/speech shape (e.g. Kokoro run via Docker,
    docker-compose.yml's kokoro-tts service) -- lets the actual inference
    cost live in its own process/container instead of Brain's.

Neither engine exposes real phoneme/viseme timestamps, so lipsync is
approximated the same way for both: an amplitude envelope mapped entirely
onto the "aa" (open mouth) viseme shape -- a deliberate simplification
against protocol.md's richer per-shape viseme_stream design, not a full
phoneme-to-viseme alignment. Good enough to look right; revisit only if
it doesn't.
"""

import io
import re

import numpy as np
import soundfile as sf
from openai import OpenAI

import protocol

ENVELOPE_WINDOW_MS = 30

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


def _strip_emoji(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_PATTERN.sub("", text)).strip()


def _viseme_frames_from_audio(audio: np.ndarray, sample_rate: int) -> list[protocol.VisemeFrame]:
    """Shared by both engines -- an RMS envelope over fixed windows, mapped
    to the "aa" viseme shape (see module docstring). Takes sample_rate as a
    parameter rather than assuming a fixed one: RemoteTTS's engine could be
    anything, and this is what turns "elapsed samples" into the actual
    seconds the Renderer's _startSubtitleStream/viseme playback times
    itself against.
    """
    window = int(sample_rate * ENVELOPE_WINDOW_MS / 1000)
    if window <= 0 or len(audio) == 0:
        return []
    n_windows = len(audio) // window
    trimmed = audio[: n_windows * window].reshape(n_windows, window)
    rms = np.sqrt(np.mean(trimmed**2, axis=1))
    peak = rms.max() if rms.max() > 0 else 1.0
    weights = (rms / peak).round(3)
    return [
        protocol.VisemeFrame(t=round(i * ENVELOPE_WINDOW_MS / 1000, 3), shape="aa", weight=float(w))
        for i, w in enumerate(weights)
    ]


class NoneTTS:
    """Placeholder used when tts_engines.NONE_NAME is the active speech
    engine -- i.e. no real engine has been configured/selected yet.
    Deliberately never produces audio; main.py's _reply_to checks for
    this class specifically and skips the synthesize call entirely (the
    same way it already skips when voice is toggled off), so this method
    existing is really just interface parity with RemoteTTS, not
    something expected to run in practice.
    """

    SAMPLE_RATE = 24000

    def synthesize(self, text: str) -> tuple[bytes, list[protocol.VisemeFrame]]:
        return b"", []


class RemoteTTS:
    """Speech via an HTTP TTS engine (brain/tts_engines.py) rather than a
    model loaded in-process -- anything speaking the OpenAI-compatible
    /v1/audio/speech shape works, using the openai client the exact same
    way llm/client.py's LocalLLM already does for a local LLM endpoint
    (openai>=1.0 is already a dependency; no new HTTP library needed).
    """

    # Only reached when a saved engine has no `voice`/`model` set at all
    # (e.g. one saved before tts_engines.py grew those fields) -- "kokoro"/
    # "af_heart" happen to be real Kokoro values, but this class has no
    # idea whether the endpoint on the other end is even Kokoro; they're
    # last-resort guesses, not assumptions every engine should share.
    # main.py's _build_tts always prefers the saved engine's own
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
        self._client = OpenAI(base_url=endpoint, api_key=api_key or "not-needed")
        self._voice = voice
        self._model = model
        # Corrected in synthesize() once real audio comes back -- the
        # engine on the other end of an arbitrary HTTP endpoint isn't
        # guaranteed to render at 24000Hz. This default only matters for
        # the very first speak_audio message's sample_rate
        # field, which the Renderer doesn't actually read (the WAV header
        # carries the real rate for decodeAudioData) -- see protocol.md.
        self.SAMPLE_RATE = 24000

    def synthesize(self, text: str) -> tuple[bytes, list[protocol.VisemeFrame]]:
        text = _strip_emoji(text)
        response = self._client.audio.speech.create(
            model=self._model, voice=self._voice, input=text, response_format="wav"
        )
        wav_bytes = response.content
        audio, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)  # collapse to mono for the envelope calc
        self.SAMPLE_RATE = sample_rate
        return wav_bytes, _viseme_frames_from_audio(audio, sample_rate)
