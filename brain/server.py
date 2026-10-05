"""The WebSocket side: one handle_renderer task per connected device. The
first message must be `ready` with the right token (_authenticate, with a
per-device lockout); after that every message is routed to its handler
(handle_message). Reply-generating messages run as their own tasks, one at a
time across every device, so Stop can cancel them.
"""

import asyncio
import errno
import hmac
import json
import time

import websockets
from websockets.exceptions import ConnectionClosed

# Each of these registers the messages it handles (hub.handles).
import characters  # noqa: F401
import debugging  # noqa: F401
import engines  # noqa: F401
import handshake
import hub
import learning  # noqa: F401
import protocol
import reach_out  # noqa: F401
import reply  # noqa: F401
from hub import Brain

PING_INTERVAL_SEC = 15


# A restart (see debugging._restart_brain) starts the replacement Brain while the old
# one is still exiting, so the port can briefly still be taken. Rather than the
# replacement dying on the first "address already in use", it retries for up to
# this long.
SERVE_BIND_ATTEMPTS = 30
SERVE_BIND_RETRY_SEC = 1.0


# How long to wait for the first message (must be `ready` carrying the
# matching token, see _authenticate) before giving up on a connection that
# opened the socket but never sent anything -- bounds how long a handler
# task can sit open for a client that's just probing the port.
AUTH_TIMEOUT_SEC = 10


# Failed-auth throttling -- auth_token itself has no retry limit (a plain
# string compare in _authenticate), so without this, anything able to open
# a connection to this port (a compromised/malicious device on the LAN or
# Tailscale tailnet, not just a stranger off the internet) could brute-force
# it with unlimited attempts. AUTH_MAX_FAILURES wrong tokens from the same
# device lock that device out for AUTH_LOCKOUT_SEC (a device is its own
# address, even behind the Vite proxy -- see hub.auth_ip); a lockout doesn't even
# consume a message once triggered, it fails immediately in _authenticate.
# When it runs out, that device starts over from zero.
AUTH_MAX_FAILURES = 5
AUTH_LOCKOUT_SEC = 60


# Failed-auth counter per source IP, see AUTH_MAX_FAILURES/AUTH_LOCKOUT_SEC
# above. Maps ip -> (failure_count, locked_until monotonic timestamp).
# Never explicitly pruned -- realistic client counts on a home LAN/tailnet
# are tiny, not worth the complexity of an eviction policy.
_AUTH_FAILURES: dict[str, tuple[int, float]] = {}


async def handle_renderer(websocket: websockets.ServerConnection, brain: Brain, auth_token: str | None) -> None:
    print("[brain] renderer connected")
    # The handshake (_authenticate -> handshake.handle_ready) sends a couple dozen
    # messages, possibly including a whole avatar file, so a phone reloading or
    # dropping off Wi-Fi mid-way is ordinary -- treat it like the disconnect
    # the chat loop below already handles quietly, not a "connection handler
    # failed" traceback.
    try:
        authenticated = await _authenticate(websocket, auth_token, brain)
    except ConnectionClosed:
        print("[brain] renderer disconnected during the startup handshake")
        return
    if not authenticated:
        print("[brain] renderer failed auth, closing connection")
        await websocket.close(code=4001, reason="unauthorized")
        return

    hub.RENDERER_CONNECTIONS.add(websocket)
    ping_task = asyncio.create_task(ping_loop(websocket))
    # Reply-generating messages run as tasks instead of being awaited
    # inline like everything else -- an inline await would keep this loop
    # from ever reading a stop_reply until the very reply it's meant to
    # cancel had already finished. Everything else stays sequential.
    reply_tasks: set[asyncio.Task] = set()
    try:
        async for raw in websocket:
            msg_type = _peek_message_type(raw)
            if msg_type == protocol.STOP_REPLY:
                _stop_replies(reply_tasks, brain)
            elif msg_type in _REPLY_MESSAGE_TYPES:
                task = asyncio.create_task(_run_reply_message(websocket, raw, brain))
                reply_tasks.add(task)
                task.add_done_callback(reply_tasks.discard)
            elif msg_type in _WAITS_FOR_REPLY_TYPES:
                # Not a reply, so Stop doesn't cancel it -- it just goes after the one running.
                hub.spawn(_run_reply_message(websocket, raw, brain))
            else:
                # A handler that raises (a bug, or a message with a missing or
                # wrong-typed field) is logged and the connection carries on --
                # before, the exception escaped this loop and dropped the device.
                try:
                    await handle_message(websocket, raw, brain)
                except ConnectionClosed:
                    raise
                except Exception as exc:
                    print(f"[brain] {msg_type!r} handler failed: {exc!r}")
                    await hub.debug_log(websocket, "brain", f"{msg_type!r} handler failed: {exc!r}")
    except ConnectionClosed:
        pass
    finally:
        for task in reply_tasks:
            task.cancel()
        ping_task.cancel()
        hub.DEBUG_CONNECTIONS.discard(websocket)
        hub.RENDERER_CONNECTIONS.discard(websocket)
        hub.DEVICE_NAMES.pop(websocket, None)
        print("[brain] renderer disconnected")


def _is_locked_out(ip: str) -> bool:
    count, locked_until = _AUTH_FAILURES.get(ip, (0, 0.0))
    if count < AUTH_MAX_FAILURES:
        return False
    if time.monotonic() < locked_until:
        return True
    # The lockout has run out: start that device's count over. Before, the
    # count kept climbing, so after the minute was up a single further miss
    # locked it out again at once.
    _AUTH_FAILURES.pop(ip, None)
    return False


def _record_auth_failure(ip: str) -> None:
    count = _AUTH_FAILURES.get(ip, (0, 0.0))[0] + 1
    locked_until = time.monotonic() + AUTH_LOCKOUT_SEC if count >= AUTH_MAX_FAILURES else 0.0
    _AUTH_FAILURES[ip] = (count, locked_until)
    if count >= AUTH_MAX_FAILURES:
        print(f"[brain] auth lockout: {ip} failed {count} times, locked {AUTH_LOCKOUT_SEC}s")


_REPLY_MESSAGE_TYPES = {protocol.USER_TEXT, protocol.USER_AUDIO, protocol.REGENERATE_LAST}

# Messages that change her mode, engine, persona or history. Run mid-reply, they
# swapped the conversation under the model call: a role-play reply landed in her
# real conversation, chat log and memory. So they wait for the reply in progress.
_WAITS_FOR_REPLY_TYPES = {
    protocol.SET_ROLEPLAY_ACTIVE,
    protocol.LOAD_LLM_ENGINE,
    protocol.SET_HARNESS_ACTIVE,
    protocol.LOAD_SOUL,
    protocol.LOAD_PROFILE,
    protocol.DELETE_SOUL,
    protocol.DELETE_PROFILE,
    protocol.DELETE_LLM_ENGINE,
    protocol.DELETE_HARNESS,
}


def _peek_message_type(raw: str) -> str | None:
    try:
        return json.loads(raw).get("type")
    except (json.JSONDecodeError, AttributeError):
        return None


async def _run_reply_message(websocket: websockets.ServerConnection, raw: str, brain: Brain) -> None:
    """handle_message for a reply-generating message, run as its own task
    (see handle_renderer) -- so an exception here is reported instead of
    vanishing as an unretrieved task exception the way it otherwise would.
    Waits its turn behind any reply already in progress (Brain.reply_lock);
    Stop cancels a waiting one as well as a running one.
    """
    try:
        queued_at = time.monotonic()
        async with brain.reply_lock:
            waited = time.monotonic() - queued_at
            if waited > 0.5:  # another reply (or her reaching out) was still going
                await hub.debug_log(websocket, "brain", f"waited {waited:.1f}s for another reply to finish first", waited * 1000)
            await handle_message(websocket, raw, brain)
    except ConnectionClosed:
        pass
    except Exception as exc:
        print(f"[brain] reply handler failed: {exc!r}")


def _stop_replies(reply_tasks: set[asyncio.Task], brain: Brain) -> None:
    """The Renderer's Stop button. Cancels every in-flight reply task on
    this connection, and tells the LLM to discard whatever its worker
    thread eventually returns (see LocalLLM.cancel_reply -- the thread
    itself can't be killed). A no-op when nothing's in flight, e.g. Stop
    raced with a reply that had just finished.
    """
    in_flight = [task for task in reply_tasks if not task.done()]
    if not in_flight:
        return
    for task in in_flight:
        task.cancel()
    brain.llm.cancel_reply()
    print("[brain] reply stopped by renderer")


async def _authenticate(websocket: websockets.ServerConnection, auth_token: str | None, brain: Brain) -> bool:
    """Gates every message type on this connection, not just the ones that
    happen to check a credential themselves -- the whole protocol is only
    reachable after this passes. Requires the connection's very first
    message to be `ready` carrying a matching `token` field; anything else
    (wrong token, wrong message type, malformed JSON, nothing sent within
    AUTH_TIMEOUT_SEC) fails closed and counts as a failure toward that IP's
    lockout (see AUTH_MAX_FAILURES/AUTH_LOCKOUT_SEC). Skipped entirely when
    no auth_token is configured (returns True immediately without consuming
    a message) -- a strictly localhost-only setup with nothing else able to
    reach this port has nothing to check a token against.
    """
    if not auth_token:
        return True
    ip = hub.auth_ip(websocket)
    if _is_locked_out(ip):
        return False
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=AUTH_TIMEOUT_SEC)
    except (ConnectionClosed, TimeoutError):
        _record_auth_failure(ip)
        return False
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _record_auth_failure(ip)
        return False
    token = str(data.get("token") or "").encode()
    if data.get("type") != protocol.READY or not hmac.compare_digest(token, auth_token.encode()):
        _record_auth_failure(ip)
        return False
    _AUTH_FAILURES.pop(ip, None)
    # This first message doubles as the normal `ready` handshake -- handle
    # it now rather than dropping it, so an authenticated Renderer still
    # gets its profiles/souls/avatars lists exactly as before.
    await handshake.handle_ready(websocket, data, brain)
    return True


async def ping_loop(websocket: websockets.ServerConnection) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_SEC)
        await hub.send(websocket, protocol.ping())


async def handle_message(websocket: websockets.ServerConnection, raw: str, brain: Brain) -> None:
    """Routes one message to the handler its module registered for its type (hub.handles)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[brain] ignoring non-JSON message: {raw!r}")
        return
    handler = hub.HANDLERS.get(data.get("type"))
    if handler is None:
        print(f"[brain] ignoring unknown message type: {data.get('type')!r}")
        return
    await handler(websocket, data, brain)


@hub.handles(protocol.PONG)
async def _pong(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    pass


@hub.handles(protocol.ANIMATION_FINISHED)
async def _animation_finished(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    print(f"[brain] animation finished: {data.get('name')!r}")


@hub.handles(protocol.ERROR)
async def _renderer_error(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    print(f"[brain] renderer reported error: {data.get('message')!r}")


async def serve_when_free(handler, host: str, port: int, attempts: int = SERVE_BIND_ATTEMPTS, **serve_kwargs):
    """websockets.serve, retrying while the port is still in use -- which is
    normal for a moment during a restart, when the old process hasn't quite
    let go yet. Any other startup error is raised immediately, and running
    out of attempts raises the last "in use" error.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await websockets.serve(handler, host, port, **serve_kwargs)
        except OSError as exc:
            in_use = exc.errno in (errno.EADDRINUSE, 10048) or getattr(exc, "winerror", None) == 10048
            if not in_use or attempt == attempts:
                raise
            print(f"[brain] port {port} isn't free yet ({exc!r}) -- retrying ({attempt}/{attempts})")
            await asyncio.sleep(SERVE_BIND_RETRY_SEC)
