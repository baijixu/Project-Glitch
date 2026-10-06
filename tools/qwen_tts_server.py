"""Qwen3-TTS (through faster-qwen3-tts, ~5x the official package's speed)
behind the OpenAI speech API (POST /v1/audio/speech) -- the shape
Glitch's RemoteTTS already speaks, so it's just another speech engine in
Settings. Plain Python, for a Windows PC that can't run Docker.

Setup on the GPU machine (Python 3.12, NVIDIA GPU, ~4.5 GB VRAM free):

    py -3.12 -m venv qwen-tts-env
    qwen-tts-env\\Scripts\\pip install torch --index-url https://download.pytorch.org/whl/cu128
    qwen-tts-env\\Scripts\\pip install faster-qwen3-tts
    qwen-tts-env\\Scripts\\python qwen_tts_server.py

The model (~4 GB) downloads on first start, then a short warm-up runs. In Glitch: Settings -> Speech
Engine -> add one with endpoint http://<this PC>:8001/v1 and a voice: Serena,
Vivian, Ryan, Aiden, Dylan, Eric, Sohee, Ono_Anna or Uncle_Fu.

`instructions` (OpenAI's own field) is passed through as Qwen's speaking-style
hint, e.g. "Speak warmly, a little amused."
"""

import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import soundfile as sf
from faster_qwen3_tts import FasterQwen3TTS

MODEL = os.environ.get("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")  # the 0.6B one ignores instructions
PORT = int(os.environ.get("QWEN_TTS_PORT", "8001"))
DEFAULT_SPEAKER = "Serena"  # for a voice it doesn't know, e.g. Glitch's blank-voice default "af_heart"

print(f"loading {MODEL}...")
model = FasterQwen3TTS.from_pretrained(MODEL)  # CUDA, bf16
speakers = {name.lower(): name for name in model.model.get_supported_speakers()}
model.generate_custom_voice(text="Ready.", speaker=DEFAULT_SPEAKER, language="Auto")  # warm-up: the first real reply isn't the slow one
gpu = threading.Lock()  # one generation at a time


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path.rstrip("/") != "/v1/audio/speech":
            return self._json(404, {"error": f"no such endpoint {self.path}"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            text = str(body.get("input") or "").strip()
            if not text:
                raise ValueError("input is empty")
        except ValueError as exc:  # bad JSON too
            return self._json(400, {"error": str(exc)})
        speaker = speakers.get(str(body.get("voice") or "").lower(), DEFAULT_SPEAKER)
        style = str(body.get("instructions") or "").strip()
        try:
            with gpu:
                wavs, rate = model.generate_custom_voice(text=text, speaker=speaker, language="Auto", instruct=style or None)
        except Exception as exc:
            return self._json(500, {"error": repr(exc)})
        # ponytail: always WAV -- all Glitch asks for; add mp3/pcm if another client needs them.
        audio = io.BytesIO()
        sf.write(audio, wavs[0], rate, format="WAV", subtype="PCM_16")
        self._send(200, audio.getvalue(), "audio/wav")

    def _json(self, code, payload):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _send(self, code, data, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"Qwen3-TTS ready on http://0.0.0.0:{PORT}/v1 (speakers: {', '.join(speakers.values())})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
