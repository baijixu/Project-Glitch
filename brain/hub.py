"""What every part of Brain shares: the one Brain, the set of connected
devices, and how to reach them -- broadcast to all, a debug_event to the ones
debugging, a background task that can't be garbage-collected mid-way.
"""

import asyncio
import ipaddress
import json
from collections.abc import Awaitable, Callable

import websockets

import protocol
from llm import ChatBackend
from voice import FasterWhisperSTT, NoneTTS, RemoteTTS

# Defense-in-depth cap on the free-text fields saved to disk (profile
# content, soul description/examples, engine name/endpoint/model/api_key --
# NOT the VRM binary itself, which has its own much larger, deliberate
# max_size on websockets.serve below). An authenticated client is already
# trusted with real control over Glitch, but there's no reason a name or
# endpoint string needs to be more than this to be useful, and without a
# cap a compromised/buggy client could wedge Brain by writing an
# arbitrarily large file per save (disk fill) or handing an arbitrarily
# large string to the LLM's system prompt every turn (memory/latency).
# 100k chars is far beyond any real profile/soul/engine field while still
# bounding the damage from a single message.
MAX_TEXT_FIELD_LENGTH = 100_000


# Which currently-open connections have turned on the Renderer's
# Debugging toggle (set_debug_active) -- a plain set of live
# ServerConnection objects, not a per-Brain flag, since debugging is a
# per-device setting (one phone debugging shouldn't spam debug_event
# messages at a laptop that hasn't asked for them). Membership is added
# by debugging._set_debug_active and removed both there and in
# handle_renderer's finally block (a closed connection left in here would
# just be a dead reference debug_log's own send would silently fail
# against anyway, but there's no reason to let it accumulate for the life
# of the process).
DEBUG_CONNECTIONS: set[websockets.ServerConnection] = set()


# Every currently-open Renderer connection, unconditionally (unlike
# DEBUG_CONNECTIONS, which is opt-in per device) -- exists so
# _harness_health_loop can broadcast to all of them, not just whichever
# one happened to ask. Added/removed in handle_renderer, same lifecycle
# as DEBUG_CONNECTIONS.
RENDERER_CONNECTIONS: set[websockets.ServerConnection] = set()


# What each connected device says it is ("Android phone · Chrome 140", sent in
# its `ready`), for the debug log: the setup snapshot lists every connected
# device, and Brain-wide events say which device caused them. Removed when the
# connection closes.
DEVICE_NAMES: dict[websockets.ServerConnection, str] = {}
MAX_DEVICE_NAME_CHARS = 120


def device_label(websocket: websockets.ServerConnection) -> str:
    """"Android phone · Chrome 140 via Tailscale (100.101.2.3)" -- the device's own
    description plus how it reached Brain, from its address (see auth_ip)."""
    name = DEVICE_NAMES.get(websocket, "unknown device")
    ip = auth_ip(websocket)
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return f"{name} ({ip})"
    if address.is_loopback:
        return f"{name} via this PC"
    if address in ipaddress.ip_network("100.64.0.0/10"):
        return f"{name} via Tailscale ({ip})"
    return f"{name} via {'home network' if address.is_private else 'the internet'} ({ip})"


# Strong references to every fire-and-forget task (see spawn). asyncio's own
# event loop only keeps a weak reference to a task, so a task nobody holds can be
# garbage-collected partway through -- the lessons refresh, memory saving and
# even the health-check loop used to be started that way.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """Starts `coro` in the background and keeps it alive until it finishes."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


Handler = Callable[[websockets.ServerConnection, dict, "Brain"], Awaitable[None]]

# message type -> the handler that answers it (see server.handle_message). Each
# module registers the messages it owns with @handles.
HANDLERS: dict[str, Handler] = {}


def handles(*message_types: str) -> Callable[[Handler], Handler]:
    def register(handler: Handler) -> Handler:
        for message_type in message_types:
            assert message_type not in HANDLERS, f"two handlers for {message_type!r}"
            HANDLERS[message_type] = handler
        return handler

    return register


async def send(websocket: websockets.ServerConnection, message: dict) -> None:
    """Sends one message to one device."""
    await websocket.send(json.dumps(message))


async def broadcast(message: dict) -> None:
    """Sends one message to every currently-connected Renderer. Used for
    anything that's shared state rather than a reply to one device: health
    lights, and every list or selection a device can change (profiles, souls,
    speech/LLM engines, harnesses, role-play) -- otherwise a phone and a PC
    connected at once each kept showing their own stale lists until they
    reconnected. A device that has just dropped is skipped (its send fails and
    is ignored; handle_renderer removes it from RENDERER_CONNECTIONS).
    """
    encoded = json.dumps(message)
    for websocket in list(RENDERER_CONNECTIONS):
        try:
            await websocket.send(encoded)
        except Exception:
            pass


def fields_too_long(*values: str) -> bool:
    """True if any of the given field values exceeds MAX_TEXT_FIELD_LENGTH.
    Checked before every save_* handler writes anything to disk.
    """
    return any(len(v) > MAX_TEXT_FIELD_LENGTH for v in values)


async def debug_log(websocket: websockets.ServerConnection, category: str, message: str, ms: float | None = None) -> None:
    """No-op unless this specific connection has debugging turned on.
    Deliberately never raises -- a debug send racing a connection that
    just closed (or any other transient send failure) must never take
    down the real request it's describing, since this is purely
    diagnostic and the caller's actual work is already done by the time
    this runs.
    """
    if websocket not in DEBUG_CONNECTIONS:
        return
    try:
        await websocket.send(json.dumps(protocol.debug_event(category, message, ms)))
    except Exception:
        pass


async def debug_broadcast(category: str, message: str, ms: float | None = None) -> None:
    """debug_log for things no one device asked for -- she reached out, role-play
    was toggled, a background save failed -- sent to every device that's
    debugging. Same rules: never conversation content, never raises.
    """
    for websocket in list(DEBUG_CONNECTIONS):
        await debug_log(websocket, category, message, ms)


class Brain:
    """Everything the WebSocket handler needs to answer a message: the
    active LLM/TTS/STT engines. Exactly one Brain instance is created in
    main() and shared by every simultaneous Renderer connection (each
    connection gets its own handle_renderer() task, but they all close
    over this same object) -- there is no per-connection state. That
    means two devices connected at once share one conversation history,
    one active persona/soul, and one TTS/LLM engine: a message from
    either shows up in both, and switching engines or loading a
    profile/soul from one affects what the other sees too. Fine for this
    app's actual use (one person, one Glitch, occasionally two tabs open
    on the same machine) but worth knowing before assuming connections
    are isolated the way separate browser tabs usually imply.
    """

    def __init__(self, llm: ChatBackend, tts: RemoteTTS | NoneTTS, stt: FasterWhisperSTT) -> None:
        self.llm = llm
        self.tts = tts
        self.stt = stt
        # One reply at a time, across every connected device. She has one shared
        # conversation, and reply() appends to it before and after the model call
        # -- two replies running at once (two devices sending together)
        # interleaved as user, user, her, her and could pair answers with the
        # wrong questions. A second message waits for the first reply to finish
        # (server._run_reply_message), and so does her reaching out.
        self.reply_lock = asyncio.Lock()


_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def auth_ip(websocket: websockets.ServerConnection) -> str:
    """Which device a connection is, for the failed-login lockout.

    Brain listens on this PC only, so every device reaches it through the
    Renderer's Vite proxy -- and to Brain they would all look like 127.0.0.1.
    One stale tab with an old token would then lock out *every* device. Vite
    is set to pass the real address on (vite.config.js, xfwd), appending it as
    the LAST entry of X-Forwarded-For. Only that last entry is used -- earlier
    ones are whatever the device itself sent, and could be made up -- and only
    when the connection really comes from this machine (the proxy). A direct
    connection from elsewhere is identified by its own address as before.
    """
    peer = websocket.remote_address[0] if websocket.remote_address else "unknown"
    if peer in _LOOPBACK_ADDRESSES:
        headers = getattr(getattr(websocket, "request", None), "headers", None)
        forwarded = headers.get("X-Forwarded-For", "") if headers is not None else ""
        last_hop = forwarded.split(",")[-1].strip()
        if last_hop:
            return last_hop
    return peer
