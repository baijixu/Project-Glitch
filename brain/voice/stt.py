"""Local STT (faster-whisper) -- spec section 5's "STT / TTS"."""

from faster_whisper import WhisperModel


class FasterWhisperSTT:
    def __init__(self, model_size: str = "base") -> None:
        # device="cpu" pinned deliberately: "auto" tries CUDA first on a
        # machine with an NVIDIA GPU, and without a properly available
        # cuBLAS install that fails outright instead of falling back to
        # CPU. A small model like this doesn't need the GPU anyway, and
        # leaving it free avoids contending with TTS/rendering for it.
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe(self, audio_path: str) -> str:
        segments, _info = self._model.transcribe(audio_path)
        return " ".join(segment.text.strip() for segment in segments).strip()
