"""What every part of Brain shares: the one Brain, the set of connected
devices, and how to reach them -- broadcast to all, a debug_event to the ones
debugging, a background task that can't be garbage-collected mid-way.
"""

import asyncio
import contextlib
import ipaddress
import json
from collections.abc import Awaitable, Callable

import websockets

import protocol
from llm import ChatBackend
from voice import FasterWhisperSTT, NoneTTS, RemoteTTS

# Cap on the free-text fields saved to disk (profiles, souls, engine settings -- not
# avatar files, which have their own limit): a buggy or hostile client can't fill the
# disk or bloat her prompt. Far beyond any real field.
MAX_TEXT_FIELD_LENGTH = 100_000


# The connections with the Debugging toggle on -- per device, so one debugging phone
# doesn't flood a laptop with debug_events.
DEBUG_CONNECTIONS: set[websockets.ServerConnection] = set()


# Every open Renderer connection (added and removed in handle_renderer), for broadcast.
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


# Strong references to every fire-and-forget task (see spawn): asyncio only keeps a weak
# one, so a task nobody holds can be garbage-collected partway through.
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


async def broadcast(message: dict, skip: websockets.ServerConnection | None = None) -> None:
    """Sends one message to every connected Renderer -- for shared state (lists,
    selections, switches, health lights), so every device shows the same. A device
    that just dropped is skipped. `skip`: the device that already has it.
    """
    encoded = json.dumps(message)
    for websocket in list(RENDERER_CONNECTIONS - {skip}):
        try:
            await websocket.send(encoded)
        except Exception:
            pass


def fields_too_long(*values: str) -> bool:
    """True if any value exceeds MAX_TEXT_FIELD_LENGTH (checked before every save)."""
    return any(len(v) > MAX_TEXT_FIELD_LENGTH for v in values)


async def debug_log(websocket: websockets.ServerConnection, category: str, message: str, ms: float | None = None) -> None:
    """No-op unless this connection has debugging on. Never raises: a diagnostic
    must not take down the request it describes."""
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


# What she's busy with right now, on every device's status line (brain_status) -- her diary, a
# book, friends over -- for as long as each job runs. Jobs can overlap, so each keeps its own entry.
_DOING: dict[object, str] = {}


def doing_now() -> str:
    return " · ".join(_DOING.values())


@contextlib.asynccontextmanager
async def doing(what: str):
    """Shows `what` while the block runs; yields a function to change it (a visit's count)."""
    key = object()  # its own entry, alive as long as the job

    async def show(text: str) -> None:
        _DOING[key] = text
        await broadcast(protocol.brain_status(doing_now()))

    await show(what)
    try:
        yield show
    finally:
        _DOING.pop(key, None)
        await broadcast(protocol.brain_status(doing_now()))


class Brain:
    """The active LLM/TTS/STT engines. One Brain, shared by every connected device:
    one conversation, one soul, one set of engines -- a change from one device
    applies to all of them.
    """

    @contextlib.asynccontextmanager
    async def busy(self, what: str):
        """A background model job: its turn on the model (reply_lock), and `what` on the status
        line once it has it -- not while it waits."""
        async with self.reply_lock, doing(what) as show:
            yield show

    def __init__(self, llm: ChatBackend, tts: RemoteTTS | NoneTTS, stt: FasterWhisperSTT) -> None:
        self.llm = llm
        self.tts = tts
        self.stt = stt
        # One model job at a time, across every device: two replies at once would
        # interleave her one conversation and pair answers with the wrong questions.
        self.reply_lock = asyncio.Lock()
        # She's been talked over (speech_interrupted, or a new message): the rest of
        # the reply she's voicing goes unsaid. Reset as each reply starts (reply.py).
        self.speech_stopped = False


_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def auth_ip(websocket: websockets.ServerConnection) -> str:
    """Which device a connection is, for the failed-login lockout.

    Every device reaches Brain through the Vite proxy, so they'd all look like
    127.0.0.1 and one stale tab could lock out every device. The proxy appends the
    real address as the LAST X-Forwarded-For entry (vite.config.js, xfwd); only
    that entry is trusted -- earlier ones come from the device and could be made up
    -- and only for a connection from this machine.
    """
    peer = websocket.remote_address[0] if websocket.remote_address else "unknown"
    if peer in _LOOPBACK_ADDRESSES:
        headers = getattr(getattr(websocket, "request", None), "headers", None)
        forwarded = headers.get("X-Forwarded-For", "") if headers is not None else ""
        last_hop = forwarded.split(",")[-1].strip()
        if last_hop:
            return last_hop
    return peer
