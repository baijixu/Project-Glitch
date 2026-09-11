"""Brain entry point -- hosts the WebSocket server the Renderer connects to
(spec section 5). Build order step 4 (LLM slice): user_text in, an LLM
reply out as speak_text -- no TTS/visemes yet, that's its own incremental
step once this is verified working.

Run with:
    uv run main.py
"""

import asyncio
import json

import websockets
from websockets.exceptions import ConnectionClosed

import protocol
from config import load_config
from llm import LocalLLM

PING_INTERVAL_SEC = 15


async def handle_renderer(websocket: websockets.ServerConnection, llm: LocalLLM) -> None:
    print("[brain] renderer connected")
    ping_task = asyncio.create_task(_ping_loop(websocket))
    try:
        async for raw in websocket:
            await _handle_message(websocket, raw, llm)
    except ConnectionClosed:
        pass
    finally:
        ping_task.cancel()
        print("[brain] renderer disconnected")


async def _ping_loop(websocket: websockets.ServerConnection) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_SEC)
        await websocket.send(json.dumps(protocol.ping()))


async def _handle_message(websocket: websockets.ServerConnection, raw: str, llm: LocalLLM) -> None:
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
    elif msg_type == protocol.USER_TEXT:
        await _handle_user_text(websocket, data.get("text", ""), llm)
    else:
        print(f"[brain] ignoring unknown message type: {msg_type!r}")


async def _handle_user_text(websocket: websockets.ServerConnection, text: str, llm: LocalLLM) -> None:
    text = text.strip()
    if not text:
        return
    print(f"[brain] user_text: {text}")
    try:
        reply_text = await asyncio.to_thread(llm.reply, text)
    except Exception as exc:
        # No Brain -> Renderer error message type exists yet (protocol.md's
        # `error` is Renderer -> Brain only) -- surfacing this as speak_text
        # is a deliberate, minimal stand-in rather than adding a new message
        # type just for this. Revisit if/when that actually gets in the way.
        print(f"[brain] LLM call failed: {exc!r}")
        reply_text = f"(couldn't reach the LLM: {exc})"
    await websocket.send(json.dumps(protocol.speak_text(reply_text)))


async def main() -> None:
    config = load_config()
    brain_cfg = config["brain"]
    host = brain_cfg.get("host", "localhost")
    port = brain_cfg["port"]

    llm_cfg = brain_cfg["llm"]
    llm = LocalLLM(endpoint=llm_cfg["endpoint"], model=llm_cfg.get("model"), api_key=llm_cfg.get("api_key"))

    print(f"[brain] listening on ws://{host}:{port}")
    async with websockets.serve(lambda ws: handle_renderer(ws, llm), host, port):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
