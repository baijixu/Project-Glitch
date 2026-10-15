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
import wave

from pathlib import Path

import httpx
import numpy as np
from openai import OpenAI

import protocol
from store import Toggle

# Whether her voice is on. Off, every reply still gets its mood and text (reply.py's reply_to)
# but nothing is synthesized: no TTS compute spent on audio nobody wants played.
VOICE_ACTIVE = Toggle(Path(__file__).parent.parent / "voice_active.txt", default=True)

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


# Her mood tag as she wrote it (llm.py: the face presets and the finer words in MOOD_ALIASES) as a
# speaking-style hint, sent as OpenAI's `instructions` -- Qwen3-TTS (tools/qwen_tts_server.py)
# follows it, Kokoro ignores it. Her face has six presets, her voice every word: playful and
# curious both show "happy", but they don't sound alike. Neutral is calm too: Serena's own
# default delivery isn't the calm voice Josh knows her by.
MOOD_STYLES = {
    "neutral": "Calm, easy and unhurried.",
    "happy": "Warm and cheerful, with a smile in the voice.",
    "sad": "Soft, gentle and a little sad.",
    "angry": "Firm and frustrated, with an edge.",
    "relaxed": "Calm, easy and unhurried.",
    "surprised": "Surprised and animated.",
    "curious": "Curious and interested, leaning in.",
    "excited": "Excited and bright, a little quick.",
    "playful": "Playful and teasing, a little sly.",
    "amused": "Amused, like she's holding back a laugh.",
    "flirty": "Flirty, low and warm, with a smile in it.",
    "thoughtful": "Thoughtful and slow, weighing each word.",
    "calm": "Calm, easy and unhurried.",
    "content": "Content and warm, settled.",
    "tired": "Tired and soft, a little worn out.",
    "nostalgic": "Nostalgic and wistful, a little far away.",
    "concerned": "Concerned and gentle, careful.",
    "worried": "Worried, quiet and a little tense.",
    "sympathetic": "Warm, soft and sympathetic.",
    "annoyed": "Annoyed, clipped and dry.",
    "frustrated": "Frustrated, with an edge.",
    "shocked": "Shocked, caught off guard.",
    "amazed": "Amazed, full of wonder.",
}


# Qwen3-TTS reads "(chuckles)" / "*laughs*" out as words (13 of 16 takes) but really laughs at "haha",
# so a laugh cue (llm.VOICE_CUE_INSTRUCTION) is spoken as one. The subtitle keeps what she wrote.
_LAUGH_CUE = re.compile(r"[(*\[]\s*(?:laugh|chuckl|giggl)\w*[^)*\]]{0,20}[)*\]]", re.IGNORECASE)


# A bilingual voice (Qwen3-TTS's Serena, for one) can read digits in Mandarin, a number on its own
# most of all, but says "forty-three" in English, so numbers are spoken as English words.
# Money, times, ordinals, decimals, percents; 1100-2099 like a year.
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_NUMBER = re.compile(
    r"\$(\d(?:[\d,]*\d)?)(?:\.(\d\d))?"  # $51.25 (a comma after it stays, as a pause)
    r"|\b(\d{1,2}):(\d\d)\b"  # 6:30
    r"|\b(\d+)(?:st|nd|rd|th)\b"  # 15th
    r"|\b(\d(?:[\d,]*\d)?)(?:\.(\d+))?(%?)"  # 43, 1,000, 3.5, 70%
)


def _words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{_ONES[n // 100]} hundred" + (f" {_words(n % 100)}" if n % 100 else "")
    if n >= 10**12:  # a serial number or the like: digit by digit
        return " ".join(_ONES[int(d)] for d in str(n))
    for size, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= size:
            return f"{_words(n // size)} {name}" + (f" {_words(n % size)}" if n % size else "")


def _year(n: int) -> str:
    high, low = divmod(n, 100)
    if 2000 <= n < 2010 or low == 0 and high % 10 == 0:
        return _words(n)
    return f"{_words(high)} " + ("hundred" if low == 0 else f"oh {_ONES[low]}" if low < 10 else _words(low))


def _ordinal(n: int) -> str:
    words = _words(n)
    for end, nth in (("one", "first"), ("two", "second"), ("three", "third"), ("five", "fifth"), ("eight", "eighth"),
                     ("nine", "ninth"), ("twelve", "twelfth")):
        if words.endswith(end):
            return words[: -len(end)] + nth
    return words[:-1] + "ieth" if words.endswith("y") else words + "th"


def _say_number(m: re.Match) -> str:
    dollars, cents, hour, minute, nth, whole, fraction, percent = m.groups()
    if dollars:
        amount = f"{_words(int(dollars.replace(',', '')))} dollars"
        return amount + (f" {_words(int(cents))}" if cents and int(cents) else "")
    if hour:
        return _words(int(hour)) + (" o'clock" if minute == "00" else f" oh {_ONES[int(minute)]}" if minute < "10" else f" {_words(int(minute))}")
    if nth:
        return _ordinal(int(nth))
    n = int(whole.replace(",", ""))
    said = _year(n) if not fraction and not percent and "," not in whole and 1100 <= n < 2100 else _words(n)
    if fraction:
        said += " point " + " ".join(_ONES[int(d)] for d in fraction)
    return said + (" percent" if percent else "")


def _spoken(text: str) -> str:
    text = _NUMBER.sub(_say_number, _LAUGH_CUE.sub("haha", text))
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

    # Last-resort guesses for a saved engine with no `model`/`voice` -- real Kokoro values.
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
        text = _spoken(text)
        style = {"instructions": MOOD_STYLES[mood]} if mood in MOOD_STYLES else {}
        response = self._client.audio.speech.create(
            model=self._model, voice=self._voice, input=text, response_format="wav", **style
        )
        wav_bytes = response.content
        with wave.open(io.BytesIO(wav_bytes)) as wav:  # 16-bit PCM from both Qwen3-TTS and Kokoro
            if wav.getsampwidth() != 2:
                raise ValueError(f"expected 16-bit WAV, got {8 * wav.getsampwidth()}-bit")
            channels, sample_rate = wav.getnchannels(), wav.getframerate()
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype("float32") / 32768
        audio = audio.reshape(-1, channels).mean(axis=1)  # mono for the envelope calc
        self.SAMPLE_RATE = sample_rate
        return wav_bytes, _viseme_frames_from_audio(audio, sample_rate)
