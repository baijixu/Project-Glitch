"""Local TTS (Kokoro) -- spec section 5's "STT / TTS". Kokoro doesn't expose
real phoneme/viseme timestamps, so lipsync is approximated with an
amplitude envelope mapped entirely onto the "aa" (open mouth) viseme shape
-- a deliberate simplification against protocol.md's richer per-shape
viseme_stream design, not a full phoneme-to-viseme alignment. Good enough
to look right; revisit only if it doesn't.
"""

import io
import re

import numpy as np
import soundfile as sf

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


class KokoroTTS:
    SAMPLE_RATE = 24000

    def __init__(self, voice: str = "af_heart", lang_code: str = "a") -> None:
        from kokoro import KPipeline

        self._pipeline = KPipeline(lang_code=lang_code)
        self._voice = voice

    def synthesize(self, text: str) -> tuple[bytes, list[protocol.VisemeFrame]]:
        """Returns (wav_bytes, viseme_frames)."""
        text = _strip_emoji(text)
        chunks = [np.asarray(audio) for _graphemes, _phonemes, audio in self._pipeline(text, voice=self._voice)]
        full_audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

        buffer = io.BytesIO()
        sf.write(buffer, full_audio, self.SAMPLE_RATE, format="WAV")
        return buffer.getvalue(), self._viseme_frames(full_audio)

    def _viseme_frames(self, audio: np.ndarray) -> list[protocol.VisemeFrame]:
        window = int(self.SAMPLE_RATE * ENVELOPE_WINDOW_MS / 1000)
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
