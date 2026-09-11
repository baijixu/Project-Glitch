"""Brain entry point -- hosts the WebSocket server the Renderer connects to
(spec section 5). Build order step 4 (voice slice): user_audio (mic) or
user_text (chat box) -> [STT ->] LLM -> TTS -> speak_text + speak_audio +
viseme_stream, so either input path ends up going through the exact same
reply pipeline.

Run with:
    uv run main.py
"""

import asyncio
import base64
import json
import logging
import sys
import tempfile
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed

# discord.py and discord-ext-voice-recv report real errors (voice socket
# failures, opus/decrypt issues, etc.) through Python's logging module,
# not print() -- with no handler configured those were completely
# invisible, which is exactly what made a live voice-audio problem
# (zero packets ever reaching the sink) impossible to diagnose beyond
# "nothing happened." WARNING+ is enough to surface real failures
# without the DEBUG-level packet-by-packet chatter these libraries emit.
logging.basicConfig(level=logging.WARNING, format="[%(name)s] %(levelname)s: %(message)s")

# The LLM is prompted to be "friendly and conversational" and routinely
# replies with emoji -- Windows' default console codepage (cp1252) can't
# encode those, so any print() touching raw LLM text would crash the whole
# connection handler (confirmed: a debug print of the raw reply took down a
# live request with UnicodeEncodeError on a party-popper emoji). Reconfigure
# to UTF-8 with replacement so logging never crashes on content the LLM is
# explicitly encouraged to produce.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import protocol
from config import load_config
from discord_bot import build_discord_client, run_discord_bot
from llm import LocalLLM
from voice import FasterWhisperSTT, KokoroTTS

PING_INTERVAL_SEC = 15

# Browsers' MediaRecorder doesn't produce WAV -- map its common mime types
# to a file extension so faster-whisper's decoder gets a useful hint.
AUDIO_EXTENSION_BY_MIME = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


class Brain:
    def __init__(self, llm: LocalLLM, tts: KokoroTTS, stt: FasterWhisperSTT) -> None:
        self.llm = llm
        self.tts = tts
        self.stt = stt


async def handle_renderer(websocket: websockets.ServerConnection, brain: Brain) -> None:
    print("[brain] renderer connected")
    ping_task = asyncio.create_task(_ping_loop(websocket))
    try:
        async for raw in websocket:
            await _handle_message(websocket, raw, brain)
    except ConnectionClosed:
        pass
    finally:
        ping_task.cancel()
        print("[brain] renderer disconnected")


async def _ping_loop(websocket: websockets.ServerConnection) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_SEC)
        await websocket.send(json.dumps(protocol.ping()))


async def _handle_message(websocket: websockets.ServerConnection, raw: str, brain: Brain) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[brain] ignoring non-JSON message: {raw!r}")
        return

    msg_type = data.get("type")

    if msg_type == protocol.READY:
        print(f"[brain] renderer ready, model={data.get('model')!r}")
    elif msg_type == protocol.PONG:
        pass
    elif msg_type == protocol.ANIMATION_FINISHED:
        print(f"[brain] animation finished: {data.get('name')!r}")
    elif msg_type == protocol.ERROR:
        print(f"[brain] renderer reported error: {data.get('message')!r}")
    elif msg_type == protocol.USER_TEXT:
        await _reply_to(websocket, data.get("text", ""), brain)
    elif msg_type == protocol.USER_AUDIO:
        await _handle_user_audio(websocket, data, brain)
    else:
        print(f"[brain] ignoring unknown message type: {msg_type!r}")


async def _handle_user_audio(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    audio_b64 = data.get("audio_b64") or ""
    if not audio_b64:
        return
    try:
        text = await asyncio.to_thread(_transcribe, brain.stt, audio_b64, data.get("mime_type", ""))
    except Exception as exc:
        print(f"[brain] STT failed: {exc!r}")
        return
    print(f"[brain] user_audio transcribed: {text!r}")
    await _reply_to(websocket, text, brain)


def _transcribe(stt: FasterWhisperSTT, audio_b64: str, mime_type: str) -> str:
    audio_bytes = base64.b64decode(audio_b64)
    suffix = AUDIO_EXTENSION_BY_MIME.get(mime_type.split(";")[0].strip(), ".webm")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        temp_path = f.name
    try:
        return stt.transcribe(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


async def _reply_to(websocket: websockets.ServerConnection, text: str, brain: Brain) -> None:
    text = text.strip()
    if not text:
        return
    print(f"[brain] user said: {text}")

    try:
        reply_text, mood = await asyncio.to_thread(brain.llm.reply, text)
    except Exception as exc:
        # No Brain -> Renderer error message type exists yet (protocol.md's
        # `error` is Renderer -> Brain only) -- surfacing this as speak_text
        # is a deliberate, minimal stand-in rather than adding a new message
        # type just for this. Revisit if/when that actually gets in the way.
        print(f"[brain] LLM call failed: {exc!r}")
        await websocket.send(json.dumps(protocol.speak_text(f"(couldn't reach the LLM: {exc})")))
        return

    print(f"[brain] mood: {mood}")
    # Sent before speak_text/speak_audio so her face is already changing by
    # the time she starts talking, not lagging a beat behind. Sent even for
    # "neutral" -- the Renderer treats that as "fade every mood expression
    # back to 0", which is exactly right after a mood-carrying reply.
    await websocket.send(json.dumps(protocol.set_expression(mood, 1.0)))
    await websocket.send(json.dumps(protocol.speak_text(reply_text)))

    try:
        wav_bytes, frames = await asyncio.to_thread(brain.tts.synthesize, reply_text)
    except Exception as exc:
        print(f"[brain] TTS failed: {exc!r}")
        return

    audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
    await websocket.send(json.dumps(protocol.speak_audio(audio_b64, brain.tts.SAMPLE_RATE)))
    await websocket.send(json.dumps(protocol.viseme_stream(frames)))


async def main() -> None:
    config = load_config()
    brain_cfg = config["brain"]
    host = brain_cfg.get("host", "localhost")
    port = brain_cfg["port"]

    llm_cfg = brain_cfg["llm"]
    llm = LocalLLM(endpoint=llm_cfg["endpoint"], model=llm_cfg.get("model"), api_key=llm_cfg.get("api_key"))

    print("[brain] loading TTS (Kokoro)...")
    tts = KokoroTTS()
    print("[brain] loading STT (faster-whisper)...")
    stt = FasterWhisperSTT()
    brain = Brain(llm=llm, tts=tts, stt=stt)

    # Reuses the same already-loaded TTS/STT instances (expensive to
    # load) rather than creating separate ones for Discord. Optional: an
    # unset token just skips starting the bot, so nobody who isn't using
    # Discord needs any of this.
    discord_cfg = brain_cfg.get("discord") or {}
    discord_token = discord_cfg.get("token")
    if discord_token:
        print("[brain] starting Discord bot...")
        discord_client = build_discord_client(
            llm_cfg=llm_cfg,
            allowed_user_id=discord_cfg.get("allowed_user_id"),
            allowed_channel_ids=discord_cfg.get("allowed_channel_ids") or [],
            stt=stt,
            tts=tts,
        )
        asyncio.create_task(run_discord_bot(discord_client, discord_token))
    else:
        print("[brain] no Discord token configured, skipping Discord bot")

    print(f"[brain] listening on ws://{host}:{port}")
    async with websockets.serve(lambda ws: handle_renderer(ws, brain), host, port, max_size=20 * 1024 * 1024):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
