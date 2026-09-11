"""Brain entry point -- hosts the WebSocket server the Renderer connects to
(spec section 5). This starting version only proves the protocol/connection
direction works (build order step 3: bare-bones ping/pong) -- LLM/STT/TTS/
Discord/skills subsystems get layered in incrementally on top of this same
server loop (build order step 4), not rewritten from scratch.

Run with:
    uv run main.py
"""

import asyncio
import json

import websockets
from websockets.exceptions import ConnectionClosed

import protocol
from config import load_config

PING_INTERVAL_SEC = 15


async def handle_renderer(websocket: websockets.ServerConnection) -> None:
    print("[brain] renderer connected")
    ping_task = asyncio.create_task(_ping_loop(websocket))
    try:
        async for raw in websocket:
            await _handle_message(websocket, raw)
    except ConnectionClosed:
        pass
    finally:
        ping_task.cancel()
        print("[brain] renderer disconnected")


async def _ping_loop(websocket: websockets.ServerConnection) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_SEC)
        await websocket.send(json.dumps(protocol.ping()))


async def _handle_message(websocket: websockets.ServerConnection, raw: str) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[brain] ignoring non-JSON message: {raw!r}")
        return

    msg_type = data.get("type")

    if msg_type == protocol.READY:
        print(f"[brain] renderer ready, model={data.get('model')!r}")
    elif msg_type == protocol.PONG:
        print("[brain] pong")
    elif msg_type == protocol.ANIMATION_FINISHED:
        print(f"[brain] animation finished: {data.get('name')!r}")
    elif msg_type == protocol.ERROR:
        print(f"[brain] renderer reported error: {data.get('message')!r}")
    else:
        print(f"[brain] ignoring unknown message type: {msg_type!r}")


async def main() -> None:
    config = load_config()
    brain_cfg = config["brain"]
    host = brain_cfg.get("host", "localhost")
    port = brain_cfg["port"]

    print(f"[brain] listening on ws://{host}:{port}")
    async with websockets.serve(handle_renderer, host, port):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
