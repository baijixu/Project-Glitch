"""Local TTS (Kokoro) -- spec section 5's "STT / TTS". Kokoro doesn't expose
real phoneme/viseme timestamps, so lipsync is approximated with an
amplitude envelope mapped entirely onto the "aa" (open mouth) viseme shape
-- a deliberate simplification against protocol.md's richer per-shape
viseme_stream design, not a full phoneme-to-viseme alignment. Good enough
to look right; revisit only if it doesn't.
"""

import io

import numpy as np
import soundfile as sf

import protocol

ENVELOPE_WINDOW_MS = 30


class KokoroTTS:
    SAMPLE_RATE = 24000

    def __init__(self, voice: str = "af_heart", lang_code: str = "a") -> None:
        from kokoro import KPipeline

        self._pipeline = KPipeline(lang_code=lang_code)
        self._voice = voice

    def synthesize(self, text: str) -> tuple[bytes, list[protocol.VisemeFrame]]:
        """Returns (wav_bytes, viseme_frames)."""
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
