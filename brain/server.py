"""The WebSocket side: one handle_renderer task per connected device. The
first message must be `ready` with the right token (_authenticate, with a
per-device lockout); after that every message is routed to its handler
(handle_message). Reply-generating messages run as their own tasks, one at a
time across every device, so Stop can cancel them.
"""

import asyncio
import errno
import json
import time

import websockets
from websockets.exceptions import ConnectionClosed

import characters
import curiosity
import debugging
import engines
import handshake
import hub
import learning
import llm_engines
import memory
import notes
import profiles
import protocol
import reach_out
import reply
import souls
import training
import voice_settings
import web_search
from hub import Brain

PING_INTERVAL_SEC = 15


# A restart (see debugging.handle_restart_brain) starts the replacement Brain while the old
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
        authenticated = await _authenticate(websocket, auth_token)
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


def _peek_message_type(raw: str) -> str | None:
    try:
        return json.loads(raw).get("type")
    except (json.JSONDecodeError, AttributeError):
        return None


# One reply at a time, across every connected device. She has one shared
# conversation (see Brain's docstring), and reply() appends to it before and
# after the model call -- two replies running at once (two devices sending
# together) interleaved as user, user, her, her and could pair answers with the
# wrong questions. A second message now waits for the first reply to finish;
# Stop cancels a waiting one as well as a running one. One lock per event loop
# (created on first use) -- an asyncio.Lock can't be shared across loops.
_REPLY_LOCKS: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def reply_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _REPLY_LOCKS:
        _REPLY_LOCKS[loop] = asyncio.Lock()
    return _REPLY_LOCKS[loop]


async def _run_reply_message(websocket: websockets.ServerConnection, raw: str, brain: Brain) -> None:
    """handle_message for a reply-generating message, run as its own task
    (see handle_renderer) -- so an exception here is reported instead of
    vanishing as an unretrieved task exception the way it otherwise would.
    Waits its turn behind any reply already in progress (reply_lock).
    """
    try:
        queued_at = time.monotonic()
        async with reply_lock():
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


async def _authenticate(websocket: websockets.ServerConnection, auth_token: str | None) -> bool:
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
    if data.get("type") != protocol.READY or data.get("token") != auth_token:
        _record_auth_failure(ip)
        return False
    _AUTH_FAILURES.pop(ip, None)
    # This first message doubles as the normal `ready` handshake -- handle
    # it now rather than dropping it, so an authenticated Renderer still
    # gets its profiles/souls/avatars lists exactly as before.
    await handshake.handle_ready(websocket, data)
    return True


async def ping_loop(websocket: websockets.ServerConnection) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_SEC)
        await websocket.send(json.dumps(protocol.ping()))


async def handle_message(websocket: websockets.ServerConnection, raw: str, brain: Brain) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[brain] ignoring non-JSON message: {raw!r}")
        return

    msg_type = data.get("type")

    if msg_type == protocol.READY:
        await handshake.handle_ready(websocket, data)
    elif msg_type == protocol.PONG:
        pass
    elif msg_type == protocol.ANIMATION_FINISHED:
        print(f"[brain] animation finished: {data.get('name')!r}")
    elif msg_type == protocol.ERROR:
        print(f"[brain] renderer reported error: {data.get('message')!r}")
    elif msg_type == protocol.USER_TEXT:
        await reply.reply_to(
            websocket,
            data.get("text", ""),
            brain,
            image_b64=data.get("image_b64"),
            image_mime=data.get("image_mime", "image/jpeg"),
        )
    elif msg_type == protocol.USER_AUDIO:
        await reply.handle_user_audio(websocket, data, brain)
    elif msg_type == protocol.CLEAR_CONVERSATION:
        await reply.handle_clear_conversation(brain)
    elif msg_type == protocol.REGENERATE_LAST:
        await reply.handle_regenerate_last(websocket, data, brain)
    elif msg_type == protocol.SAVE_PROFILE:
        await characters.handle_save_profile(websocket, data, brain)
    elif msg_type == protocol.LOAD_PROFILE:
        characters.handle_load_profile(data, brain)
        await hub.broadcast(protocol.profiles(profiles.list_profiles(), profiles.read_active_profile_name()))
    elif msg_type == protocol.GET_PROFILE:
        await characters.handle_get_profile(websocket, data)
    elif msg_type == protocol.DELETE_PROFILE:
        await characters.handle_delete_profile(websocket, data, brain)
    elif msg_type == protocol.SAVE_SOUL:
        await characters.handle_save_soul(websocket, data)
    elif msg_type == protocol.LOAD_SOUL:
        characters.handle_load_soul(data, brain)
        await hub.broadcast(protocol.souls(souls.list_souls(), souls.read_active_soul_name()))
    elif msg_type == protocol.GET_SOUL:
        await characters.handle_get_soul(websocket, data)
    elif msg_type == protocol.DELETE_SOUL:
        await characters.handle_delete_soul(websocket, data, brain)
    elif msg_type == protocol.SAVE_AVATAR:
        await characters.handle_save_avatar(websocket, data)
    elif msg_type == protocol.LOAD_AVATAR:
        await characters.handle_load_avatar(websocket, data)
    elif msg_type in (protocol.RENAME_AVATAR, protocol.DELETE_AVATAR):
        await characters.handle_manage_avatar(websocket, msg_type, data)
    elif msg_type in (protocol.GET_CHAT_LOGS, protocol.GET_CHAT_LOG, protocol.DELETE_CHAT_LOG):
        await characters.handle_chat_logs_message(websocket, msg_type, data)
    elif msg_type == protocol.SET_ROLEPLAY_ACTIVE:
        characters.handle_set_roleplay_active(data, brain)
        await hub.debug_broadcast(
            "roleplay",
            f"role-play {'on' if profiles.read_roleplay_active() else 'off'} (from {hub.DEVICE_NAMES.get(websocket, 'unknown device')}), "
            f"LLM engine now {llm_engines.read_active_engine_name()!r}",
        )
        await hub.broadcast(protocol.roleplay_state(profiles.read_roleplay_active()))
        await hub.broadcast(protocol.llm_engines(llm_engines.list_engines(), llm_engines.read_active_engine_name()))
    elif msg_type == protocol.SET_ROLEPLAY_ENGINE:
        try:
            llm_engines.set_roleplay_engine(str(data.get("name") or ""))
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't set the role-play engine: {exc!r}")
        await hub.broadcast(protocol.roleplay_engine(llm_engines.read_roleplay_engine()))
    elif msg_type == protocol.SET_VOICE_ACTIVE:
        voice_settings.set_voice_active(bool(data.get("active")))
    elif msg_type == protocol.SET_WEB_SEARCH_ACTIVE:
        web_search.set_active(bool(data.get("active")))
    elif msg_type == protocol.SET_TRAINING_ACTIVE:
        training.set_active(bool(data.get("active")))
        await hub.broadcast(learning.training_state_message())
    elif msg_type == protocol.RESOLVE_MEMORY_PROPOSAL:
        await learning.handle_resolve_memory_proposal(data)
    elif msg_type == protocol.SET_CURIOSITY_ACTIVE:
        curiosity.set_active(bool(data.get("active")))
        await reach_out.broadcast_curiosity_timer()
    elif msg_type == protocol.TEST_REACH_OUT:
        await reach_out.handle_test_reach_out(websocket, brain)
    elif msg_type in (protocol.SET_SAMPLING_PROFILE, protocol.SAVE_SAMPLING_PROFILE, protocol.DELETE_SAMPLING_PROFILE):
        await engines.handle_sampling_message(websocket, msg_type, data)
    elif msg_type == protocol.SET_MEMORY_ACTIVE:
        memory.set_memory_active(bool(data.get("active")))
    elif msg_type == protocol.CLEAR_MEMORY:
        await memory.clear()
        brain.llm.set_memory("")
    elif msg_type == protocol.GET_MEMORY_CONTENT:
        await websocket.send(json.dumps(protocol.memory_content(await memory.read_entries())))
    elif msg_type == protocol.RATE_REPLY:
        await learning.handle_rate_reply(websocket, data, brain)
    elif msg_type in learning.LESSON_SETTINGS_TYPES:
        await learning.handle_lessons_message(msg_type, data)
    elif msg_type in learning.MEMORY_PROFILE_TYPES:
        await learning.handle_memory_profile_message(websocket, msg_type, data)
    elif msg_type == protocol.GET_SOUL_AND_USER:
        await websocket.send(
            json.dumps(protocol.soul_and_user_content(souls.read_main_soul(), profiles.read_main_user()))
        )
    elif msg_type == protocol.SAVE_SOUL_AND_USER:
        characters.handle_save_soul_and_user(data, brain)
    elif msg_type == protocol.GET_NOTES:
        await websocket.send(json.dumps(protocol.notes_content(notes.read_notes())))
    elif msg_type == protocol.SAVE_NOTES:
        characters.handle_save_notes(data)
    elif msg_type == protocol.SAVE_TTS_ENGINE:
        await engines.handle_save_tts_engine(websocket, data)
    elif msg_type == protocol.LOAD_TTS_ENGINE:
        await engines.handle_load_tts_engine(websocket, data, brain)
    elif msg_type == protocol.GET_TTS_ENGINE:
        await engines.handle_get_tts_engine(websocket, data)
    elif msg_type == protocol.DELETE_TTS_ENGINE:
        await engines.handle_delete_tts_engine(websocket, data, brain)
    elif msg_type == protocol.GET_TTS_VOICES:
        await engines.handle_get_tts_voices(websocket, data)
    elif msg_type == protocol.SET_TTS_VOICE:
        await engines.handle_set_tts_voice(websocket, data, brain)
    elif msg_type == protocol.COMBINE_KOKORO_VOICE:
        await engines.handle_combine_kokoro_voice(websocket, data, brain)
    elif msg_type == protocol.SAVE_LLM_ENGINE:
        await engines.handle_save_llm_engine(websocket, data)
    elif msg_type == protocol.LOAD_LLM_ENGINE:
        await engines.handle_load_llm_engine(websocket, data, brain)
    elif msg_type == protocol.GET_LLM_ENGINE:
        await engines.handle_get_llm_engine(websocket, data)
    elif msg_type == protocol.DELETE_LLM_ENGINE:
        await engines.handle_delete_llm_engine(websocket, data, brain)
    elif msg_type == protocol.GET_LLM_MODELS:
        await engines.handle_get_llm_models(websocket, data)
    elif msg_type == protocol.SET_HARNESS_ACTIVE:
        await engines.handle_set_harness_active(websocket, data, brain)
    elif msg_type == protocol.SELECT_HARNESS:
        await engines.handle_select_harness(websocket, data)
    elif msg_type == protocol.SAVE_HARNESS:
        await engines.handle_save_harness(websocket, data)
    elif msg_type == protocol.GET_HARNESS:
        await engines.handle_get_harness(websocket, data)
    elif msg_type == protocol.DELETE_HARNESS:
        await engines.handle_delete_harness(websocket, data, brain)
    elif msg_type == protocol.SET_DEBUG_ACTIVE:
        await debugging.handle_set_debug_active(websocket, data, brain)
    elif msg_type == protocol.DEBUG_PING:
        await websocket.send(json.dumps(protocol.debug_pong(data.get("ts"))))
    elif msg_type == protocol.RESTART_BRAIN:
        await debugging.handle_restart_brain(websocket)
    else:
        print(f"[brain] ignoring unknown message type: {msg_type!r}")


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
