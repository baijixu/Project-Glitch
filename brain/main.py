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
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed

# The LLM is prompted to be "friendly and conversational" and routinely
# replies with emoji -- Windows' default console codepage (cp1252) can't
# encode those, so any print() touching raw LLM text would crash the whole
# connection handler (confirmed: a debug print of the raw reply took down a
# live request with UnicodeEncodeError on a party-popper emoji). Reconfigure
# to UTF-8 with replacement so logging never crashes on content the LLM is
# explicitly encouraged to produce.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import avatars
import harness
import kokoro_voices
import llm_engines
import lessons
import memory
import notes
import profiles
import protocol
import souls
import tts_engines
import voice_settings
import web_search
from config import load_config
from llm import REQUEST_TIMEOUT_SEC, HarnessLLM, LocalLLM, NoneLLM, OllamaLLM, list_models, list_ollama_models
from voice import FasterWhisperSTT, NoneTTS, RemoteTTS

# config.yaml's brain.llm block, captured once at startup (main()) -- kept
# reachable here (not just a local in main()) so _build_llm can rebuild
# llm_engines.NONE_NAME on a live load_llm_engine switch too, the same way
# main()'s own startup does, without needing config.yaml re-read from disk.
# Optional now (brain.llm itself, and every key inside it) -- an empty
# dict here (nothing in config.yaml at all) means _build_llm falls back to
# NoneLLM rather than crashing on a missing endpoint, see its own comment.
_DEFAULT_LLM_CONFIG: dict = {}

# The saved LLM engine (llm_engines.py) the Renderer's role-play confirm
# modal switches to and enables/disables thinking on -- fixed, not
# user-configurable: this is a code-level behavior (see
# _switch_to_roleplay_engine), not an open-ended list the settings UI lets
# someone create on the fly (unlike harnesses, see harness.py, which used
# to work this same fixed way but no longer does). Must be an "ollama"
# provider engine (see OllamaLLM's docstring) for the "disable thinking"
# half of that promise to actually mean anything -- an "openai" provider
# engine of this name would still switch to it, just without a working
# think toggle.
ROLEPLAY_LLM_ENGINE_NAME = "Ollama"

# config.yaml's brain.harness block, captured once at startup -- {"hermes":
# {"endpoint":..., "model":..., "api_key":...}, ...}. Only used as a
# one-time migration seed now (see main()'s own migration step) -- once
# harness.py's saved-harness list is non-empty, this is never consulted
# again; harness.read_harness(name) is the live source of truth from then
# on, the same as llm_engines.py already is for LLM engines.
_HARNESS_CONFIGS: dict = {}

PING_INTERVAL_SEC = 15

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
# IP locks that IP out for AUTH_LOCKOUT_SEC; a lockout doesn't even consume
# a message once triggered, it fails immediately in _authenticate.
AUTH_MAX_FAILURES = 5
AUTH_LOCKOUT_SEC = 60

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

# Browsers' MediaRecorder doesn't produce WAV -- map its common mime types
# to a file extension so faster-whisper's decoder gets a useful hint.
AUDIO_EXTENSION_BY_MIME = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}

# Which currently-open connections have turned on the Renderer's
# Debugging toggle (set_debug_active) -- a plain set of live
# ServerConnection objects, not a per-Brain flag, since debugging is a
# per-device setting (one phone debugging shouldn't spam debug_event
# messages at a laptop that hasn't asked for them). Membership is added
# by _handle_set_debug_active and removed both there and in
# handle_renderer's finally block (a closed connection left in here would
# just be a dead reference _debug_log's own send would silently fail
# against anyway, but there's no reason to let it accumulate for the life
# of the process).
_DEBUG_CONNECTIONS: set[websockets.ServerConnection] = set()

# Every currently-open Renderer connection, unconditionally (unlike
# _DEBUG_CONNECTIONS, which is opt-in per device) -- exists so
# _harness_health_loop can broadcast to all of them, not just whichever
# one happened to ask. Added/removed in handle_renderer, same lifecycle
# as _DEBUG_CONNECTIONS.
_RENDERER_CONNECTIONS: set[websockets.ServerConnection] = set()

# Failed-auth counter per source IP, see AUTH_MAX_FAILURES/AUTH_LOCKOUT_SEC
# above. Maps ip -> (failure_count, locked_until monotonic timestamp).
# Never explicitly pruned -- realistic client counts on a home LAN/tailnet
# are tiny, not worth the complexity of an eviction policy.
_AUTH_FAILURES: dict[str, tuple[int, float]] = {}

ENDPOINT_HEALTH_CHECK_INTERVAL_SEC = 20
# Short and TCP-only on purpose -- this only needs to tell "something's
# listening" from "nothing is", not exercise a full request. A slow-but-up
# harness/engine shouldn't read as down just because a real API call
# would take longer than this.
ENDPOINT_HEALTH_CHECK_TIMEOUT_SEC = 3

# name -> last-known reachability, per harness.list_harnesses() -- the
# indicator light's source of truth for "reachable" (harness_health's
# `reachable`), separate from harness_state's `active`. Populated by
# _health_check_loop's very first pass at startup; _send_harness_health
# falls back to an on-demand check only in the brief window before that
# first pass completes. _LAST_TTS_REACHABLE is the same thing for saved
# speech engines (tts_health) -- two dicts, not one keyed by kind, since a
# harness and a speech engine could coincidentally share a name.
_LAST_HARNESS_REACHABLE: dict[str, bool | None] = {}
_LAST_TTS_REACHABLE: dict[str, bool | None] = {}


def _check_endpoint_reachable(endpoint: str) -> bool:
    """Plain TCP connect -- whether *something* is listening at endpoint's
    host:port, not a full HTTP round trip or a check that it's actually
    Hermes (or a speech engine) answering correctly. Enough to tell "down"
    from "up" for an indicator light without needing valid auth or a real
    request shape for whatever's running there; harness_state's `active`/
    actually using a given speech engine is what actually proves it's
    working. Shared by both the harness and speech-engine health checks --
    a reachability check is a reachability check regardless of what kind
    of thing is on the other end.
    """
    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=ENDPOINT_HEALTH_CHECK_TIMEOUT_SEC):
            return True
    except OSError:
        return False


async def _broadcast(message: dict) -> None:
    """Sends one message to every currently-connected Renderer -- used for
    health-check updates (see _health_check_loop), which need to reach
    every open tab/device, not just whichever one happens to be asking.
    """
    encoded = json.dumps(message)
    for websocket in list(_RENDERER_CONNECTIONS):
        try:
            await websocket.send(encoded)
        except Exception:
            pass


async def _send_harness_health(websocket: websockets.ServerConnection) -> None:
    """Sends harness_health for every known harness to this one connection
    -- used on _handle_ready so a freshly-opened tab doesn't have to wait
    for _health_check_loop's next tick to paint the indicator light.
    Echoes the loop's own cached value; only does a fresh on-demand check
    in the brief startup window before that loop's first pass has run.
    """
    for name in harness.list_harnesses():
        reachable = _LAST_HARNESS_REACHABLE[name] if name in _LAST_HARNESS_REACHABLE else await _check_harness_health(name)
        await websocket.send(json.dumps(protocol.harness_health(name, reachable)))


async def _send_tts_health(websocket: websockets.ServerConnection) -> None:
    """Same as _send_harness_health, for saved speech engines -- never
    sent (and never checked) for tts_engines.NONE_NAME, since it has no
    network endpoint to check at all; _renderTtsLight (Renderer-side)
    already treats "no data for this name" as grey for exactly this
    reason.
    """
    for name in tts_engines.list_engines():
        reachable = _LAST_TTS_REACHABLE[name] if name in _LAST_TTS_REACHABLE else await _check_tts_health(name)
        await websocket.send(json.dumps(protocol.tts_health(name, reachable)))


async def _check_harness_health(name: str) -> bool | None:
    """None if `name` has no endpoint configured (its saved harness file
    is unreadable, or has no endpoint set) -- distinct from a real
    reachability check finding it down, so the indicator light can tell
    "nothing set up" from "set up but unreachable" apart (see
    protocol.py's harness_health). Shared by _send_harness_health and
    _health_check_loop so that distinction is only written once.
    """
    try:
        config = harness.read_harness(name)
    except (ValueError, OSError):
        return None
    endpoint = config.get("endpoint")
    if not endpoint:
        return None
    return await asyncio.to_thread(_check_endpoint_reachable, endpoint)


async def _check_tts_health(name: str) -> bool | None:
    """Same as _check_harness_health, for one saved speech engine."""
    try:
        config = tts_engines.read_engine(name)
    except (ValueError, OSError):
        return None
    endpoint = config.get("endpoint")
    if not endpoint:
        return None
    return await asyncio.to_thread(_check_endpoint_reachable, endpoint)


async def _health_check_loop() -> None:
    """Runs forever, checking every known harness's and saved speech
    engine's reachability on an interval and broadcasting harness_health/
    tts_health to every currently-connected Renderer whenever either
    changes -- a single global task (unlike _ping_loop, which is
    per-connection) since reachability is a property of the harness/
    engine itself, not of any one connection to Brain. Both kinds share
    one loop/one sleep rather than running as two separate tasks -- same
    cadence, no reason to duplicate the loop machinery. The first
    iteration runs immediately (loop body before the sleep), not after an
    initial delay, so both _LAST_*_REACHABLE dicts are populated within
    moments of Brain starting rather than leaving the indicator lights
    with nothing to show for a full interval.
    """
    while True:
        for name in harness.list_harnesses():
            reachable = await _check_harness_health(name)
            if _LAST_HARNESS_REACHABLE.get(name) != reachable:
                _LAST_HARNESS_REACHABLE[name] = reachable
                await _broadcast(protocol.harness_health(name, reachable))
        for name in tts_engines.list_engines():
            reachable = await _check_tts_health(name)
            if _LAST_TTS_REACHABLE.get(name) != reachable:
                _LAST_TTS_REACHABLE[name] = reachable
                await _broadcast(protocol.tts_health(name, reachable))
        await asyncio.sleep(ENDPOINT_HEALTH_CHECK_INTERVAL_SEC)


def _fields_too_long(*values: str) -> bool:
    """True if any of the given field values exceeds MAX_TEXT_FIELD_LENGTH.
    Checked before every save_* handler writes anything to disk.
    """
    return any(len(v) > MAX_TEXT_FIELD_LENGTH for v in values)


async def _debug_log(websocket: websockets.ServerConnection, category: str, message: str, ms: float | None = None) -> None:
    """No-op unless this specific connection has debugging turned on.
    Deliberately never raises -- a debug send racing a connection that
    just closed (or any other transient send failure) must never take
    down the real request it's describing, since this is purely
    diagnostic and the caller's actual work is already done by the time
    this runs.
    """
    if websocket not in _DEBUG_CONNECTIONS:
        return
    try:
        await websocket.send(json.dumps(protocol.debug_event(category, message, ms)))
    except Exception:
        pass


async def _send_harness_state(websocket: websockets.ServerConnection) -> None:
    """Sends the current harness_state -- reads harness.py's own saved
    active-harness file and saved-harness list fresh each call (not a
    cached value) so it's always accurate regardless of what just changed
    it. Shared by every handler that can change whether/which harness is
    active or the saved list itself (_handle_ready,
    _handle_set_harness_active, _handle_save_harness,
    _handle_delete_harness), so all of them stay in sync by construction
    instead of by copy-pasted agreement.
    """
    active_harness_name = harness.read_active_harness()
    # While active, the selected one is by definition the active one --
    # only fall back to the separately-persisted "last selected" record
    # while inactive, see protocol.py's harness_state docstring.
    selected_harness_name = active_harness_name or harness.read_selected_harness_name()
    await websocket.send(
        json.dumps(
            protocol.harness_state(
                bool(active_harness_name),
                active_harness_name,
                selected_harness_name,
                harness.list_harnesses(),
            )
        )
    )


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

    def __init__(self, llm: LocalLLM | HarnessLLM | NoneLLM, tts: RemoteTTS | NoneTTS, stt: FasterWhisperSTT) -> None:
        self.llm = llm
        self.tts = tts
        self.stt = stt


async def handle_renderer(websocket: websockets.ServerConnection, brain: Brain, auth_token: str | None) -> None:
    print("[brain] renderer connected")
    # The handshake (_authenticate -> _handle_ready) sends a couple dozen
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

    _RENDERER_CONNECTIONS.add(websocket)
    ping_task = asyncio.create_task(_ping_loop(websocket))
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
                await _handle_message(websocket, raw, brain)
    except ConnectionClosed:
        pass
    finally:
        for task in reply_tasks:
            task.cancel()
        ping_task.cancel()
        _DEBUG_CONNECTIONS.discard(websocket)
        _RENDERER_CONNECTIONS.discard(websocket)
        print("[brain] renderer disconnected")


def _auth_ip(websocket: websockets.ServerConnection) -> str:
    return websocket.remote_address[0] if websocket.remote_address else "unknown"


def _is_locked_out(ip: str) -> bool:
    count, locked_until = _AUTH_FAILURES.get(ip, (0, 0.0))
    return count >= AUTH_MAX_FAILURES and time.monotonic() < locked_until


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


async def _run_reply_message(websocket: websockets.ServerConnection, raw: str, brain: Brain) -> None:
    """_handle_message for a reply-generating message, run as its own task
    (see handle_renderer) -- so an exception here is reported instead of
    vanishing as an unretrieved task exception the way it otherwise would.
    """
    try:
        await _handle_message(websocket, raw, brain)
    except ConnectionClosed:
        pass
    except Exception as exc:
        print(f"[brain] reply handler failed: {exc!r}")


def _stop_replies(reply_tasks: set[asyncio.Task], brain: Brain) -> None:
    """The Renderer's Stop button. Cancels every in-flight reply task on
    this connection, and tells a LocalLLM to discard whatever its worker
    thread eventually returns (see LocalLLM.cancel_reply -- the thread
    itself can't be killed). A no-op when nothing's in flight, e.g. Stop
    raced with a reply that had just finished.
    """
    in_flight = [task for task in reply_tasks if not task.done()]
    if not in_flight:
        return
    for task in in_flight:
        task.cancel()
    if isinstance(brain.llm, LocalLLM):
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
    ip = _auth_ip(websocket)
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
    await _handle_ready(websocket, data)
    return True


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
        await _handle_ready(websocket, data)
    elif msg_type == protocol.PONG:
        pass
    elif msg_type == protocol.ANIMATION_FINISHED:
        print(f"[brain] animation finished: {data.get('name')!r}")
    elif msg_type == protocol.ERROR:
        print(f"[brain] renderer reported error: {data.get('message')!r}")
    elif msg_type == protocol.USER_TEXT:
        await _reply_to(
            websocket,
            data.get("text", ""),
            brain,
            image_b64=data.get("image_b64"),
            image_mime=data.get("image_mime", "image/jpeg"),
        )
    elif msg_type == protocol.USER_AUDIO:
        await _handle_user_audio(websocket, data, brain)
    elif msg_type == protocol.REGENERATE_LAST:
        await _handle_regenerate_last(websocket, data, brain)
    elif msg_type == protocol.SAVE_PROFILE:
        await _handle_save_profile(websocket, data, brain)
    elif msg_type == protocol.LOAD_PROFILE:
        _handle_load_profile(data, brain)
    elif msg_type == protocol.GET_PROFILE:
        await _handle_get_profile(websocket, data)
    elif msg_type == protocol.DELETE_PROFILE:
        await _handle_delete_profile(websocket, data, brain)
    elif msg_type == protocol.SAVE_SOUL:
        await _handle_save_soul(websocket, data)
    elif msg_type == protocol.LOAD_SOUL:
        _handle_load_soul(data, brain)
    elif msg_type == protocol.GET_SOUL:
        await _handle_get_soul(websocket, data)
    elif msg_type == protocol.DELETE_SOUL:
        await _handle_delete_soul(websocket, data, brain)
    elif msg_type == protocol.SAVE_AVATAR:
        await _handle_save_avatar(websocket, data)
    elif msg_type == protocol.LOAD_AVATAR:
        await _handle_load_avatar(websocket, data)
    elif msg_type == protocol.SET_ROLEPLAY_ACTIVE:
        _handle_set_roleplay_active(data, brain)
    elif msg_type == protocol.SET_VOICE_ACTIVE:
        voice_settings.set_voice_active(bool(data.get("active")))
    elif msg_type == protocol.SET_WEB_SEARCH_ACTIVE:
        web_search.set_active(bool(data.get("active")))
    elif msg_type == protocol.SET_MEMORY_ACTIVE:
        memory.set_memory_active(bool(data.get("active")))
    elif msg_type == protocol.CLEAR_MEMORY:
        await memory.clear()
        if isinstance(brain.llm, LocalLLM):
            brain.llm.set_memory("")
    elif msg_type == protocol.GET_MEMORY_CONTENT:
        await websocket.send(json.dumps(protocol.memory_content(await memory.read_entries())))
    elif msg_type == protocol.RATE_REPLY:
        await _handle_rate_reply(websocket, data, brain)
    elif msg_type in _LESSON_SETTINGS_TYPES:
        await _handle_lessons_message(msg_type, data)
    elif msg_type == protocol.SET_MEMORY_PROVIDER:
        memory.set_provider(data.get("provider") or memory.LOCAL_PROVIDER)
        await _broadcast(protocol.memory_provider_state(memory.read_provider()))
        await _broadcast_lessons_state()  # lessons need the hindsight provider, so switching it changes their availability
    elif msg_type == protocol.SAVE_HINDSIGHT_CONFIG:
        await _handle_save_hindsight_config(data)
    elif msg_type == protocol.GET_SOUL_AND_USER:
        await websocket.send(
            json.dumps(protocol.soul_and_user_content(souls.read_main_soul(), profiles.read_main_user()))
        )
    elif msg_type == protocol.SAVE_SOUL_AND_USER:
        _handle_save_soul_and_user(data, brain)
    elif msg_type == protocol.GET_NOTES:
        await websocket.send(json.dumps(protocol.notes_content(notes.read_notes())))
    elif msg_type == protocol.SAVE_NOTES:
        _handle_save_notes(data)
    elif msg_type == protocol.SAVE_TTS_ENGINE:
        await _handle_save_tts_engine(websocket, data)
    elif msg_type == protocol.LOAD_TTS_ENGINE:
        await _handle_load_tts_engine(websocket, data, brain)
    elif msg_type == protocol.GET_TTS_ENGINE:
        await _handle_get_tts_engine(websocket, data)
    elif msg_type == protocol.DELETE_TTS_ENGINE:
        await _handle_delete_tts_engine(websocket, data, brain)
    elif msg_type == protocol.GET_TTS_VOICES:
        await _handle_get_tts_voices(websocket, data)
    elif msg_type == protocol.SET_TTS_VOICE:
        await _handle_set_tts_voice(websocket, data, brain)
    elif msg_type == protocol.COMBINE_KOKORO_VOICE:
        await _handle_combine_kokoro_voice(websocket, data, brain)
    elif msg_type == protocol.SAVE_LLM_ENGINE:
        await _handle_save_llm_engine(websocket, data)
    elif msg_type == protocol.LOAD_LLM_ENGINE:
        await _handle_load_llm_engine(websocket, data, brain)
    elif msg_type == protocol.GET_LLM_ENGINE:
        await _handle_get_llm_engine(websocket, data)
    elif msg_type == protocol.DELETE_LLM_ENGINE:
        await _handle_delete_llm_engine(websocket, data, brain)
    elif msg_type == protocol.GET_LLM_MODELS:
        await _handle_get_llm_models(websocket, data)
    elif msg_type == protocol.SET_HARNESS_ACTIVE:
        await _handle_set_harness_active(websocket, data, brain)
    elif msg_type == protocol.SELECT_HARNESS:
        await _handle_select_harness(websocket, data)
    elif msg_type == protocol.SAVE_HARNESS:
        await _handle_save_harness(websocket, data)
    elif msg_type == protocol.GET_HARNESS:
        await _handle_get_harness(websocket, data)
    elif msg_type == protocol.DELETE_HARNESS:
        await _handle_delete_harness(websocket, data, brain)
    elif msg_type == protocol.SET_DEBUG_ACTIVE:
        _handle_set_debug_active(websocket, data)
    elif msg_type == protocol.DEBUG_PING:
        await websocket.send(json.dumps(protocol.debug_pong(data.get("ts"))))
    elif msg_type == protocol.RESTART_BRAIN:
        await _handle_restart_brain(websocket)
    else:
        print(f"[brain] ignoring unknown message type: {msg_type!r}")


async def _handle_ready(websocket: websockets.ServerConnection, data: dict) -> None:
    print(f"[brain] renderer ready, model={data.get('model')!r}")
    await websocket.send(json.dumps(protocol.profiles(profiles.list_profiles(), profiles.read_active_profile_name())))
    await websocket.send(json.dumps(protocol.souls(souls.list_souls(), souls.read_active_soul_name())))
    await websocket.send(json.dumps(protocol.avatars(avatars.list_avatars())))
    await websocket.send(json.dumps(protocol.roleplay_state(profiles.read_roleplay_active())))
    await websocket.send(json.dumps(protocol.voice_state(voice_settings.read_voice_active())))
    await websocket.send(json.dumps(protocol.web_search_state(web_search.read_active())))
    await websocket.send(json.dumps(protocol.memory_state(memory.read_memory_active())))
    await websocket.send(json.dumps(protocol.memory_provider_state(memory.read_provider())))
    # Its own task -- fetching lessons is a network call to Hindsight, and an
    # unreachable server shouldn't hold up the rest of the ready handshake
    # (including the avatar_data that signals it's finished).
    asyncio.create_task(_send_lessons_state(websocket))
    hindsight_cfg = memory.read_hindsight_config()
    await websocket.send(
        json.dumps(
            protocol.hindsight_config(
                hindsight_cfg.get("api_url", ""), hindsight_cfg.get("api_key", ""), hindsight_cfg.get("bank_id", "")
            )
        )
    )
    await websocket.send(json.dumps(protocol.tts_engines(tts_engines.list_engines(), tts_engines.read_active_engine_name())))
    await _send_tts_voices(websocket, tts_engines.read_active_engine_name())
    await websocket.send(json.dumps(protocol.llm_engines(llm_engines.list_engines(), llm_engines.read_active_engine_name())))
    await _send_harness_state(websocket)
    await _send_harness_health(websocket)
    await _send_tts_health(websocket)
    # If a custom avatar was active last time, the Renderer needs its
    # bytes to swap to it -- it just booted with the shipped default,
    # which needs no round trip at all (see avatars.py's docstring).
    active_avatar = avatars.read_active_avatar()
    if active_avatar and active_avatar != avatars.DEFAULT_AVATAR_NAME:
        try:
            avatar_bytes, kind = avatars.read_avatar(active_avatar)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't restore active avatar {active_avatar!r}: {exc!r}")
        else:
            data_b64 = base64.b64encode(avatar_bytes).decode("ascii")
            await websocket.send(json.dumps(protocol.avatar_data(active_avatar, data_b64, kind)))


async def _handle_save_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    content = data.get("content", "")
    if not name.strip() or not content.strip() or _fields_too_long(name, content):
        return
    try:
        profiles.save_profile(name, content)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save profile {name!r}: {exc!r}")
        return
    print(f"[brain] saved profile {name!r}")
    await websocket.send(json.dumps(protocol.profiles(profiles.list_profiles(), profiles.read_active_profile_name())))


async def _handle_get_profile(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    try:
        content = profiles.read_profile(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read profile {name!r}: {exc!r}")
        return
    await websocket.send(json.dumps(protocol.profile_content(name, content)))


async def _handle_delete_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        profiles.delete_profile(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete profile {name!r}: {exc!r}")
        return
    print(f"[brain] deleted profile {name!r}")
    # The profile that was active just got deleted out from under it --
    # fall back to DEFAULT_PROFILE_NAME rather than leaving rp_user.md/the
    # LLM's persona pointing at a file that no longer exists.
    if profiles.read_active_profile_name() == name:
        content = profiles.load_profile(profiles.DEFAULT_PROFILE_NAME)
        # isinstance guard: brain.llm could be a HarnessLLM (no set_persona
        # at all, see its own docstring) if a harness happened to be active
        # when this arrived -- the Renderer's UI already disables profile
        # deletion in that state, but two devices can race (brain.llm is
        # shared across every connection, see Brain's own docstring), so
        # this can't just assume the message implies brain.llm is LocalLLM.
        if profiles.read_roleplay_active() and isinstance(brain.llm, LocalLLM):
            brain.llm.set_persona(content)
        print(f"[brain] active profile was deleted -- reset to {profiles.DEFAULT_PROFILE_NAME!r}")
    await websocket.send(json.dumps(protocol.profiles(profiles.list_profiles(), profiles.read_active_profile_name())))


async def _handle_save_soul(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    description = data.get("description", "")
    examples = data.get("examples", "")
    if not name.strip() or not description.strip() or _fields_too_long(name, description, examples):
        return
    try:
        souls.save_soul(name, description, examples)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save soul {name!r}: {exc!r}")
        return
    print(f"[brain] saved soul {name!r}")
    await websocket.send(json.dumps(protocol.souls(souls.list_souls(), souls.read_active_soul_name())))


def _effective_soul() -> str:
    """Which soul she should be running right now. During role-play it's the
    selected role-play soul (rp_soul.md), if one is selected; otherwise --
    role-play off, or on with no RP soul picked -- it's her main soul
    (soul.md), and "" from that means LocalLLM's built-in default. The two
    files are never merged or copied into each other (see souls.py), so a
    role-play soul can't leak into or overwrite her permanent personality.
    """
    if profiles.read_roleplay_active():
        rp_soul = souls.read_active_soul()
        if rp_soul.strip():
            return rp_soul
    return souls.read_main_soul()


# Cap on how much of the user's own user.md goes into one prompt -- it's
# their own writing, but an enormous file would eat context on every turn.
MAX_USER_INFO_CHARS = 4000


def _effective_user_info() -> str:
    """What the user wrote about themselves (their main user.md), or "" during
    role-play -- the selected role-play profile (rp_user.md) stands in for
    "who the user is" then, and their real details pause the same way memory
    and lessons do. Read from disk fresh each turn, so an edit made in a text
    editor applies to her very next reply with no restart.
    """
    if profiles.read_roleplay_active():
        return ""
    return profiles.read_main_user().strip()[:MAX_USER_INFO_CHARS]


def _handle_load_soul(data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        content = souls.load_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load soul {name!r}: {exc!r}")
        return
    # isinstance guard: see _handle_delete_profile's own comment -- a
    # HarnessLLM has no set_soul at all, and brain.llm is shared across
    # every connected device, so a harness turning on elsewhere can race
    # with this one arriving.
    if profiles.read_roleplay_active():
        if isinstance(brain.llm, LocalLLM):
            # Not `content` directly: loading "Default" clears the RP soul,
            # which means "no RP soul -- use her main soul", not "no soul".
            brain.llm.set_soul(_effective_soul())
        print(f"[brain] loaded soul {name!r}")
    else:
        # Still recorded above (in rp_soul.md, never her main soul.md) -- just
        # not applied while role-play is off, same as _handle_load_profile.
        print(f"[brain] selected soul {name!r} (role-play is off, not applied)")


async def _handle_get_soul(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    try:
        description, examples = souls.read_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read soul {name!r}: {exc!r}")
        return
    await websocket.send(json.dumps(protocol.soul_content(name, description, examples)))


def _handle_save_notes(data: dict) -> None:
    content = data.get("content", "")
    if _fields_too_long(content):
        return
    notes.write_notes(content)


def _handle_save_soul_and_user(data: dict, brain: Brain) -> None:
    """Manual-edit escape hatch (Settings' soul/user editor) -- writes her
    MAIN soul (soul.md) and main user.md directly, bypassing the named saved
    soul/profile system entirely. This is the only thing that ever writes
    either file (see souls.py/profiles.py); the saved role-play souls and
    profiles can't. The live LLM is re-primed to match her main soul, which
    applies whenever role-play is off (or on with no RP soul selected). The
    role-play profile is a separate file (rp_user.md) this never touches.
    Guarded on LocalLLM since HarnessLLM has neither method -- if the
    harness is active the files are still saved for whenever it's turned
    back off, just not applied to anything now.
    """
    soul_content = data.get("soul", "")
    user_content = data.get("user", "")
    if _fields_too_long(soul_content, user_content):
        return
    souls.write_main_soul(soul_content)
    profiles.write_main_user(user_content)
    if isinstance(brain.llm, LocalLLM):
        brain.llm.set_soul(_effective_soul())
    print("[brain] soul.md/user.md updated via manual editor")


async def _handle_delete_soul(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        souls.delete_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete soul {name!r}: {exc!r}")
        return
    print(f"[brain] deleted soul {name!r}")
    # Same reasoning as _handle_delete_profile: don't leave rp_soul.md/the
    # LLM pointing at a soul that no longer exists.
    if souls.read_active_soul_name() == name:
        souls.load_soul(souls.DEFAULT_SOUL_NAME)
        # isinstance guard: see _handle_delete_profile's own comment.
        if profiles.read_roleplay_active() and isinstance(brain.llm, LocalLLM):
            brain.llm.set_soul(_effective_soul())
        print(f"[brain] active soul was deleted -- reset to {souls.DEFAULT_SOUL_NAME!r}")
    await websocket.send(json.dumps(protocol.souls(souls.list_souls(), souls.read_active_soul_name())))


async def _handle_save_avatar(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    data_b64 = data.get("data_b64", "")
    kind = data.get("kind", "vrm")
    # Only the name is length-checked here, not data_b64 (the avatar's raw
    # bytes) -- that's a real, deliberately large payload already bounded
    # by websockets.serve's own max_size in main() below, not a free-text
    # field this cap is meant for. `kind` is checked against
    # avatars.AVATAR_KINDS rather than trusted outright -- avatars.save_avatar
    # already re-validates it too (defense in depth, not redundant: this
    # check just avoids doing a base64 decode for a request that's going to
    # be rejected anyway).
    if not name.strip() or not data_b64 or _fields_too_long(name) or kind not in avatars.AVATAR_KINDS:
        return
    try:
        avatar_bytes = base64.b64decode(data_b64)
        avatars.save_avatar(name, avatar_bytes, kind)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save avatar {name!r}: {exc!r}")
        return
    avatars.set_active_avatar(name)
    print(f"[brain] saved and activated avatar {name!r} ({kind}, {len(avatar_bytes)} bytes)")
    await websocket.send(json.dumps(protocol.avatars(avatars.list_avatars())))


async def _handle_load_avatar(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    if not name.strip():
        return
    avatars.set_active_avatar(name)
    if name == avatars.DEFAULT_AVATAR_NAME:
        # Bookkeeping only -- the Renderer already knows how to load the
        # shipped default locally, no bytes to send.
        print(f"[brain] activated default avatar {name!r}")
        return
    try:
        avatar_bytes, kind = avatars.read_avatar(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load avatar {name!r}: {exc!r}")
        return
    print(f"[brain] activated avatar {name!r} ({kind}), sending {len(avatar_bytes)} bytes")
    data_b64 = base64.b64encode(avatar_bytes).decode("ascii")
    await websocket.send(json.dumps(protocol.avatar_data(name, data_b64, kind)))


async def _handle_save_tts_engine(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    endpoint = data.get("endpoint", "")
    api_key = data.get("api_key", "")
    voice = data.get("voice", "")
    voices_dir = data.get("voices_dir", "")
    model = data.get("model", "")
    if not name.strip() or not endpoint.strip() or _fields_too_long(name, endpoint, api_key, voice, voices_dir, model):
        return
    try:
        tts_engines.save_engine(name, endpoint, api_key, voice, voices_dir, model)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save speech engine {name!r}: {exc!r}")
        return
    print(f"[brain] saved speech engine {name!r}")
    # A saved/edited endpoint invalidates whatever the health-check loop
    # last knew for this name -- drop it rather than showing a stale color
    # until the next periodic tick happens to overwrite it.
    _LAST_TTS_REACHABLE.pop(name, None)
    await websocket.send(
        json.dumps(protocol.tts_engines(tts_engines.list_engines(), tts_engines.read_active_engine_name()))
    )


async def _handle_get_tts_engine(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    try:
        engine = tts_engines.read_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read speech engine {name!r}: {exc!r}")
        return
    await websocket.send(
        json.dumps(
            protocol.tts_engine_content(
                name,
                engine["endpoint"],
                engine.get("api_key", ""),
                engine.get("voice", ""),
                engine.get("voices_dir", ""),
                engine.get("model", ""),
            )
        )
    )


async def _handle_load_tts_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    if not name.strip():
        return
    start = time.monotonic()
    try:
        # Off the event loop on principle, same as _build_llm's own
        # to_thread call -- neither engine actually blocks meaningfully
        # right now, but a future one might. Caught broadly (not just
        # ValueError/OSError, which _build_tts already handles internally)
        # since a real client/library failure here could raise nearly
        # anything -- same reasoning as _reply_to's broad guard around
        # brain.llm.reply/brain.tts.synthesize.
        brain.tts = await asyncio.to_thread(_build_tts, name)
    except Exception as exc:
        await _debug_log(websocket, "tts", f"couldn't switch to speech engine {name!r}: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] couldn't switch to speech engine {name!r}: {exc!r}")
        return
    tts_engines.set_active_engine_name(name)
    await _debug_log(websocket, "tts", f"switched speech engine to {name!r}", (time.monotonic() - start) * 1000)
    print(f"[brain] switched speech engine to {name!r}")


async def _handle_delete_tts_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        tts_engines.delete_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete speech engine {name!r}: {exc!r}")
        return
    print(f"[brain] deleted speech engine {name!r}")
    _LAST_TTS_REACHABLE.pop(name, None)
    # Same reasoning as _handle_delete_profile/_handle_delete_soul: don't
    # leave brain.tts pointed at an engine config that no longer exists.
    if tts_engines.read_active_engine_name() == name:
        try:
            brain.tts = await asyncio.to_thread(_build_tts, tts_engines.NONE_NAME)
        except Exception as exc:
            print(f"[brain] couldn't fall back to {tts_engines.NONE_NAME!r} after deleting {name!r}: {exc!r}")
        else:
            tts_engines.set_active_engine_name(tts_engines.NONE_NAME)
            print(f"[brain] active speech engine was deleted -- reset to {tts_engines.NONE_NAME!r}")
    await websocket.send(
        json.dumps(protocol.tts_engines(tts_engines.list_engines(), tts_engines.read_active_engine_name()))
    )


async def _send_tts_voices(websocket: websockets.ServerConnection, name: str) -> None:
    """Sent in reply to get_tts_voices, and again after every
    combine_kokoro_voice/set_tts_voice for the affected engine -- tells the
    Renderer which custom voices exist for this saved engine, its current
    default, and whether creating another is even possible for it
    (voices_dir configured). Empty/false across the board for NONE_NAME or
    a name that isn't a real saved engine, rather than an error -- picking
    "None" or briefly seeing a stale name in the dropdown shouldn't need
    special-casing on the Renderer side.
    """
    try:
        engine = tts_engines.read_engine(name)
    except (ValueError, OSError):
        await websocket.send(json.dumps(protocol.tts_voices(name, [], "", False)))
        return
    await websocket.send(
        json.dumps(
            protocol.tts_voices(name, engine.get("custom_voices", []), engine.get("voice", ""), bool(engine.get("voices_dir")))
        )
    )


async def _handle_get_tts_voices(websocket: websockets.ServerConnection, data: dict) -> None:
    await _send_tts_voices(websocket, data.get("name", ""))


async def _handle_set_tts_voice(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Changes a saved speech engine's default voice -- reuses _build_tts
    the same way _handle_load_tts_engine does, so if this engine happens
    to be the active one, brain.tts is rebuilt with the new voice right
    away; if it's not active, only the saved file changes, taking effect
    next time this engine is loaded. voice="" is a real, valid value here
    (unlike most other string fields in this codebase) -- it clears back
    to RemoteTTS.DEFAULT_VOICE, the Renderer's picker's "Default" entry.
    """
    name = data.get("name", "")
    voice = data.get("voice", "")
    if not name:
        return
    try:
        tts_engines.set_engine_voice(name, voice)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't set voice for speech engine {name!r}: {exc!r}")
        return
    if tts_engines.read_active_engine_name() == name:
        try:
            brain.tts = await asyncio.to_thread(_build_tts, name)
        except Exception as exc:
            print(f"[brain] couldn't apply new voice for speech engine {name!r}: {exc!r}")
    print(f"[brain] set speech engine {name!r} voice to {voice!r}")
    await _send_tts_voices(websocket, name)


async def _handle_combine_kokoro_voice(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Asks the named engine's own server to blend existing voices by
    weight (kokoro_voices.py's combine_voices, `spec` in Kokoro's own
    "voice1(2)+voice2(1)" syntax) and makes the result that engine's new
    default voice -- see _handle_set_tts_voice for why that's safe to do
    unconditionally (only actually rebuilds brain.tts if this engine is
    the active one). Refused, not erroring, if the engine has no
    voices_dir set (nowhere for Brain to save the result). Every refusal
    replies with a tts_voices carrying a concrete `error` (and logs to
    the debug panel, if it's on) -- silently doing nothing here would be
    indistinguishable from "worked, still shows the old voice" from the
    Renderer's side.
    """
    name = data.get("name", "")
    display_name = data.get("voice_name", "")
    spec = data.get("spec", "")
    if not name or not display_name or not spec:
        return
    try:
        engine = tts_engines.read_engine(name)
    except (ValueError, OSError) as exc:
        error = f"no such speech engine {name!r}"
        print(f"[brain] couldn't create blended voice -- {error}: {exc!r}")
        await websocket.send(json.dumps(protocol.tts_voices(name, [], "", False, error)))
        return
    voices_dir = engine.get("voices_dir") or ""
    if not voices_dir:
        error = f"speech engine {name!r} has no voices folder configured"
        print(f"[brain] {error} -- refusing to create blended voice")
        await _debug_log(websocket, "tts", f"refused blended voice: {error}")
        await _send_tts_voices(websocket, name)
        return
    try:
        voice_id = await asyncio.to_thread(
            kokoro_voices.combine_voices, engine["endpoint"], engine.get("api_key") or None, spec, display_name, voices_dir
        )
    except Exception as exc:
        error = f"couldn't create blended voice {display_name!r} ({spec}): {exc}"
        print(f"[brain] {error}")
        await _debug_log(websocket, "tts", error)
        await websocket.send(json.dumps(protocol.tts_voices(name, engine.get("custom_voices", []), engine.get("voice", ""), True, error)))
        return
    tts_engines.add_custom_voice(name, voice_id)
    print(f"[brain] created blended voice {voice_id!r} ({spec}) for speech engine {name!r}")
    await _handle_set_tts_voice(websocket, {"name": name, "voice": voice_id}, brain)


def _build_tts(name: str) -> RemoteTTS | NoneTTS:
    """NoneTTS for tts_engines.NONE_NAME, or a RemoteTTS pointed at a saved
    engine's endpoint/api_key otherwise. Falls back to NoneTTS if the named
    engine's saved config can't be read (deleted out from under a stale
    reference, corrupted file, etc.) rather than crashing.
    """
    if name == tts_engines.NONE_NAME:
        return NoneTTS()
    try:
        engine = tts_engines.read_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load speech engine {name!r}, falling back to {tts_engines.NONE_NAME!r}: {exc!r}")
        return NoneTTS()
    print(f"[brain] using speech engine {name!r} at {engine['endpoint']!r}")
    return RemoteTTS(
        engine["endpoint"],
        engine.get("api_key") or None,
        engine.get("voice") or RemoteTTS.DEFAULT_VOICE,
        engine.get("model") or RemoteTTS.DEFAULT_MODEL,
    )


async def _handle_save_llm_engine(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    endpoint = data.get("endpoint", "")
    model = data.get("model", "")
    api_key = data.get("api_key", "")
    provider = data.get("provider") or "openai"
    think = bool(data.get("think", False))
    if not name.strip() or not endpoint.strip() or _fields_too_long(name, endpoint, model, api_key, provider):
        return
    try:
        llm_engines.save_engine(name, endpoint, model, api_key, provider=provider, think=think)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save LLM engine {name!r}: {exc!r}")
        return
    print(f"[brain] saved LLM engine {name!r}")
    await websocket.send(
        json.dumps(protocol.llm_engines(llm_engines.list_engines(), llm_engines.read_active_engine_name()))
    )


async def _handle_get_llm_engine(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    try:
        engine = llm_engines.read_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read LLM engine {name!r}: {exc!r}")
        return
    await websocket.send(
        json.dumps(
            protocol.llm_engine_content(
                name,
                engine["endpoint"],
                engine.get("model", ""),
                engine.get("api_key", ""),
                provider=engine.get("provider", "openai"),
                think=engine.get("think", False),
            )
        )
    )


async def _handle_get_llm_models(websocket: websockets.ServerConnection, data: dict) -> None:
    """Populates the LLM-engine editor's model field with the endpoint's
    actual loaded models (GET /v1/models, or Ollama's own GET /api/tags
    when provider is "ollama") instead of leaving the user to guess a
    string -- see llm/client.py's list_models docstring for the live crash
    this was written in response to. `endpoint` is echoed back unchanged
    so the Renderer can tell this reply apart from a stale one for an
    endpoint the user has since changed in the still-open editor.
    """
    endpoint = data.get("endpoint", "")
    if not endpoint.strip():
        return
    list_fn = list_ollama_models if data.get("provider") == "ollama" else list_models
    start = time.monotonic()
    try:
        models = await asyncio.to_thread(list_fn, endpoint, data.get("api_key") or None)
    except Exception as exc:
        await _debug_log(websocket, "llm", f"couldn't list models at {endpoint!r}: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] couldn't list models at {endpoint!r}: {exc!r}")
        models = []
    else:
        await _debug_log(websocket, "llm", f"listed {len(models)} model(s) at {endpoint!r}", (time.monotonic() - start) * 1000)
    await websocket.send(json.dumps(protocol.llm_models(endpoint, models)))


async def _handle_load_llm_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    if not name.strip():
        return
    try:
        # Building a LocalLLM is cheap (an OpenAI client constructor + a
        # couple small local file reads) -- no real model-load work
        # happens here, so this stays sync rather than needing
        # asyncio.to_thread the way _handle_load_tts_engine does.
        brain.llm = _build_llm(name)
    except Exception as exc:
        await _debug_log(websocket, "llm", f"couldn't switch to LLM engine {name!r}: {exc!r}")
        print(f"[brain] couldn't switch to LLM engine {name!r}: {exc!r}")
        return
    llm_engines.set_active_engine_name(name)
    await _debug_log(websocket, "llm", f"switched LLM engine to {name!r}")
    print(f"[brain] switched LLM engine to {name!r}")


async def _handle_delete_llm_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        llm_engines.delete_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete LLM engine {name!r}: {exc!r}")
        return
    print(f"[brain] deleted LLM engine {name!r}")
    # Same reasoning as _handle_delete_tts_engine: don't leave brain.llm
    # pointed at an engine config that no longer exists.
    if llm_engines.read_active_engine_name() == name:
        try:
            brain.llm = _build_llm(llm_engines.NONE_NAME)
        except Exception as exc:
            print(f"[brain] couldn't fall back to {llm_engines.NONE_NAME!r} after deleting {name!r}: {exc!r}")
        else:
            llm_engines.set_active_engine_name(llm_engines.NONE_NAME)
            print(f"[brain] active LLM engine was deleted -- reset to {llm_engines.NONE_NAME!r}")
    await websocket.send(
        json.dumps(protocol.llm_engines(llm_engines.list_engines(), llm_engines.read_active_engine_name()))
    )


def _build_llm(name: str) -> LocalLLM | NoneLLM:
    """LocalLLM (or OllamaLLM, see below) built from config.yaml's brain.llm
    block (_DEFAULT_LLM_CONFIG) for llm_engines.NONE_NAME, or a saved
    engine's endpoint/model/api_key otherwise. Always re-primes the fresh
    instance with whatever soul/profile is currently active -- persona/
    soul state lives on the instance itself (llm/client.py), not
    externally, so a new instance (main()'s own startup, or a live
    load_llm_engine switch) would otherwise silently drop who Glitch
    currently is. Falls back to NONE_NAME if the named engine's saved
    config can't be read.

    Returns a NoneLLM specifically when NONE_NAME resolves to no endpoint
    at all -- config.yaml's brain.llm block is optional now, so a fresh
    install with nothing configured there and no saved engine chosen yet
    is a real, expected state, not something to crash on.
    """
    if name == llm_engines.NONE_NAME:
        config = _DEFAULT_LLM_CONFIG
    else:
        try:
            engine = llm_engines.read_engine(name)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't load LLM engine {name!r}, falling back to {llm_engines.NONE_NAME!r}: {exc!r}")
            name = llm_engines.NONE_NAME
            config = _DEFAULT_LLM_CONFIG
        else:
            config = {
                "endpoint": engine["endpoint"],
                "model": engine.get("model") or None,
                "api_key": engine.get("api_key") or None,
                "provider": engine.get("provider", "openai"),
                "think": engine.get("think", False),
            }

    if not config.get("endpoint"):
        print(f"[brain] no LLM engine configured ({name!r} has no endpoint) -- add one in Settings under LLM")
        llm = NoneLLM()
    else:
        print(f"[brain] using LLM engine {name!r} at {config['endpoint']!r}")
        # See OllamaLLM's own docstring for why this distinction matters:
        # Ollama's OpenAI-compatible endpoint silently ignores `think`, only
        # its native API (what OllamaLLM talks to) actually honors it.
        if config.get("provider") == "ollama":
            llm = OllamaLLM(
                endpoint=config["endpoint"],
                model=config.get("model"),
                api_key=config.get("api_key"),
                think=bool(config.get("think", False)),
            )
        else:
            llm = LocalLLM(endpoint=config["endpoint"], model=config.get("model"), api_key=config.get("api_key"))

    # Her main soul always applies unless role-play is on with an RP soul
    # selected (see _effective_soul); the RP profile only applies during
    # role-play (below).
    llm.set_soul(_effective_soul())
    # No memory priming here -- unlike the old flat-file version, there's
    # no fixed block to prime with at build time, only whatever's relevant
    # to each turn's own message (see _reply_to, which calls set_memory
    # fresh before every reply). self._memory already defaults to "".
    active_profile = profiles.read_active_profile()
    if active_profile and profiles.read_roleplay_active():
        llm.set_persona(active_profile)
    return llm


def _build_harness_llm(name: str) -> HarnessLLM | None:
    """A HarnessLLM for the named saved harness (brain/harness.py), or None
    if it's unknown/unreadable or has no endpoint saved -- callers fall
    back to _build_llm in that case, same "don't leave Brain with nothing
    to talk to" reasoning as _build_llm's own fallback when a saved LLM
    engine can't be read.
    """
    try:
        config = harness.read_harness(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't activate unknown/unreadable harness {name!r}: {exc!r}")
        return None
    if not config.get("endpoint"):
        print(f"[brain] couldn't activate harness {name!r}: no endpoint saved")
        return None
    print(f"[brain] using harness {name!r} at {config['endpoint']!r}")
    return HarnessLLM(endpoint=config["endpoint"], model=config.get("model"), api_key=config.get("api_key"))


async def _handle_set_harness_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Always replies with the true post-attempt harness_state, success or
    failure -- this used to be silent (no reply at all), which meant the
    Renderer's own optimistic toggle just stayed wrong forever if
    _build_harness_llm returned None (unknown/unconfigured harness). A
    reply the Renderer can resync from closes that gap.
    """
    active = bool(data.get("active"))
    name = data.get("name", "")
    if active:
        harness_llm = _build_harness_llm(name)
        if harness_llm is not None:
            brain.llm = harness_llm
            harness.set_active_harness(name)
            harness.set_selected_harness_name(name)
            await _debug_log(websocket, "harness", f"connected to harness {name!r}")
            print(f"[brain] plugged into harness {name!r} -- her profile/soul/LLM engine are bypassed while this is active")
        else:
            await _debug_log(websocket, "harness", f"couldn't activate harness {name!r} (unknown or unconfigured)")
    else:
        await _debug_log(websocket, "harness", "disconnected from harness")
        harness.set_active_harness("")
        brain.llm = _build_llm(llm_engines.read_active_engine_name())
        print("[brain] disconnected from harness -- restored her own profile/soul/LLM engine")

    await _send_harness_state(websocket)


async def _handle_select_harness(websocket: websockets.ServerConnection, data: dict) -> None:
    """Just moves the Renderer's dropdown pick -- no connection attempt,
    no switch key needed (that's still only enforced by
    _handle_set_harness_active). Only meaningful while inactive (the
    Renderer's own dropdown is disabled while a harness is actually
    connected, see _renderHarnessState), so there's nothing here to
    reconcile with brain.llm/harness.set_active_harness. Lets picking
    harness.NONE_NAME actually stick after a refresh instead of the
    dropdown reverting to whatever was last connected -- see
    harness.set_selected_harness_name's docstring for why that needed
    its own persisted value in the first place.
    """
    name = data.get("name", "")
    harness.set_selected_harness_name("" if name == harness.NONE_NAME else name)
    await _send_harness_state(websocket)


async def _handle_save_harness(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    endpoint = data.get("endpoint", "")
    model = data.get("model", "")
    api_key = data.get("api_key", "")
    if not name.strip() or not endpoint.strip() or _fields_too_long(name, endpoint, model, api_key):
        return
    try:
        harness.save_harness(name, endpoint, model, api_key)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save harness {name!r}: {exc!r}")
        return
    print(f"[brain] saved harness {name!r}")
    # A saved/edited endpoint invalidates whatever the health-check loop
    # last knew for this name -- drop it rather than showing a stale color
    # until the next periodic tick happens to overwrite it.
    _LAST_HARNESS_REACHABLE.pop(name, None)
    await _send_harness_state(websocket)


async def _handle_get_harness(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    try:
        h = harness.read_harness(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read harness {name!r}: {exc!r}")
        return
    await websocket.send(
        json.dumps(protocol.harness_content(name, h["endpoint"], h.get("model", ""), h.get("api_key", "")))
    )


async def _handle_delete_harness(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        harness.delete_harness(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete harness {name!r}: {exc!r}")
        return
    print(f"[brain] deleted harness {name!r}")
    _LAST_HARNESS_REACHABLE.pop(name, None)
    # Same reasoning as deleting the active LLM/TTS engine -- don't leave
    # Brain plugged into (or configured to reconnect to) a harness that no
    # longer exists.
    if harness.read_active_harness() == name:
        harness.set_active_harness("")
        brain.llm = _build_llm(llm_engines.read_active_engine_name())
        print(f"[brain] active harness {name!r} was deleted -- disconnected, restored her own profile/soul/LLM engine")
    # Same reasoning, for the separately-persisted dropdown pick -- don't
    # leave it pointing at a harness that no longer exists.
    if harness.read_selected_harness_name() == name:
        harness.set_selected_harness_name("")
    await _send_harness_state(websocket)



_LESSON_SETTINGS_TYPES = {
    protocol.SET_LESSONS_ACTIVE,
    protocol.SET_LESSONS_AUTONOMY,
    protocol.SAVE_LESSON,
    protocol.RETIRE_LESSON,
    protocol.DELETE_LESSON,
    protocol.RESOLVE_LESSON_PROPOSAL,
}

LESSONS_STATE_TIMEOUT_SEC = 8
LESSON_PROMPT_TIMEOUT_SEC = 5

# Strong references to in-flight background learn tasks -- asyncio only
# holds weak ones, so an unreferenced task can be garbage-collected mid-run.
_LESSON_TASKS: set[asyncio.Task] = set()


async def _lessons_state_message() -> dict:
    try:
        state = await asyncio.wait_for(lessons.state(), timeout=LESSONS_STATE_TIMEOUT_SEC)
    except Exception as exc:
        state = {
            "available": lessons.available(),
            "active": lessons.read_active(),
            "autonomy": lessons.read_autonomy(),
            "lessons": [],
            "pending": lessons.read_pending(),
            "error": f"couldn't reach the lessons store: {exc!r}",
        }
    return protocol.lessons_state(**state)


async def _send_lessons_state(websocket: websockets.ServerConnection) -> None:
    try:
        await websocket.send(json.dumps(await _lessons_state_message()))
    except Exception:
        pass  # connection closed before it finished -- nothing to tell


async def _broadcast_lessons_state() -> None:
    await _broadcast(await _lessons_state_message())


async def _handle_lessons_message(msg_type: str, data: dict) -> None:
    """The Settings panel's Behavior learning controls (toggle, autonomy,
    add/edit/retire/delete a lesson, approve/reject a proposal). Every one
    ends by broadcasting the fresh lessons_state to every connected device.
    A refused change (over the lesson limit, a lesson that no longer exists)
    is reported as an "error" lesson_event so the click doesn't just
    silently do nothing.
    """
    try:
        if msg_type == protocol.SET_LESSONS_ACTIVE:
            lessons.set_active(bool(data.get("active")))
        elif msg_type == protocol.SET_LESSONS_AUTONOMY:
            lessons.set_autonomy(str(data.get("level", "")))
        elif msg_type == protocol.SAVE_LESSON:
            name, content = str(data.get("name", "")), str(data.get("content", ""))
            if _fields_too_long(name, content):
                return
            priority = data.get("priority")
            priority = int(priority) if priority is not None else None
            if data.get("id"):
                await lessons.update_lesson(str(data["id"]), name=name, content=content, priority=priority)
            else:
                await lessons.create_lesson(name, content, priority if priority is not None else lessons.DEFAULT_PRIORITY)
        elif msg_type == protocol.RETIRE_LESSON:
            await lessons.retire_lesson(str(data.get("id", "")), "retired by user")
        elif msg_type == protocol.DELETE_LESSON:
            await lessons.delete_lesson(str(data.get("id", "")))
        elif msg_type == protocol.RESOLVE_LESSON_PROPOSAL:
            applied = await lessons.resolve_pending(str(data.get("id", "")), bool(data.get("approve")))
            if applied:
                await _broadcast(protocol.lesson_event("applied", applied))
    except (ValueError, LookupError, lessons.LessonsUnavailable) as exc:
        await _broadcast(protocol.lesson_event("error", str(exc)))
    except Exception as exc:
        print(f"[brain] lessons change failed: {exc!r}")
        await _broadcast(protocol.lesson_event("error", "couldn't reach the lessons store"))
    await _broadcast_lessons_state()


async def _handle_rate_reply(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """A thumbs up/down on one of her replies. The rating itself is always
    logged locally (lessons.log_rating); learning from it only happens when
    the feature is on, role-play is off (in-character replies shouldn't
    teach her habits for normal conversation) and this is a LocalLLM (a
    harness manages its own behavior). That part runs as a background task
    -- it's an extra LLM call plus Hindsight writes, and the user shouldn't
    wait on it.
    """
    rating = data.get("rating")
    user_text = str(data.get("user_text", ""))
    reply_text = str(data.get("reply_text", ""))
    note = str(data.get("note", "")).strip()
    if rating not in ("up", "down") or not reply_text.strip() or _fields_too_long(user_text, reply_text, note):
        return
    roleplay = profiles.read_roleplay_active()
    lessons.log_rating(user_text, reply_text, rating, note, roleplay)
    await _debug_log(websocket, "lessons", f"rating logged ({rating})")
    if roleplay or not lessons.read_active() or not isinstance(brain.llm, LocalLLM):
        return
    task = asyncio.create_task(_learn_from_rating(websocket, brain, user_text, reply_text, rating, note))
    _LESSON_TASKS.add(task)
    task.add_done_callback(_LESSON_TASKS.discard)


async def _learn_from_rating(
    websocket: websockets.ServerConnection, brain: Brain, user_text: str, reply_text: str, rating: str, note: str
) -> None:
    start = time.monotonic()
    try:
        active = await lessons.active_lessons()
        candidates = lessons.read_candidates() if lessons.read_autonomy() == lessons.ALL else []
        raw = await asyncio.to_thread(
            brain.llm.propose_lesson,
            user_text,
            reply_text,
            rating,
            note,
            [l["content"] for l in active],
            [c["content"] for c in candidates],
        )
        action = lessons.parse_distillation(raw, active, candidates, has_note=bool(note))
        if action is None:
            await _debug_log(websocket, "lessons", "nothing to learn from that rating", (time.monotonic() - start) * 1000)
            return
        kind, text = await lessons.handle_action(action)
    except Exception as exc:
        await _debug_log(websocket, "lessons", f"learning from rating failed: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] learning from rating failed: {exc!r}")
        return
    await _debug_log(websocket, "lessons", f"{kind} ({action['action']})", (time.monotonic() - start) * 1000)
    print(f"[brain] lesson {kind}: {text}")
    await _broadcast(protocol.lesson_event(kind, text))
    await _broadcast_lessons_state()


async def _handle_save_hindsight_config(data: dict) -> None:
    """Saves the Settings panel's Memory Server fields, points memory.py's
    Hindsight client at the new server, and ensures the bank exists there
    -- broadcast to every connected device (not just the one that saved
    it) since this is shared state the same way harness_health/tts_health
    are, not a per-connection preference. Doesn't touch which provider is
    active (memory.py's own read_provider()) -- that's the Memory backend
    dropdown's job (set_memory_provider), kept independent so filling in
    connection details doesn't silently switch someone off local memory
    before they're ready to.
    """
    api_url = data.get("api_url", "").strip()
    api_key = data.get("api_key", "").strip()
    bank_id = data.get("bank_id", "").strip() or "glitch-native"
    if _fields_too_long(api_url, api_key, bank_id):
        return
    memory.save_hindsight_config(api_url, api_key, bank_id)
    if api_url:
        try:
            await memory.ensure_bank()
        except Exception as exc:
            print(f"[brain] couldn't reach hindsight server {api_url!r}: {exc!r}")
    print(f"[brain] saved hindsight config (bank {bank_id!r})")
    await _broadcast(protocol.hindsight_config(api_url, api_key, bank_id))
    lessons.invalidate()
    await _broadcast_lessons_state()


def _handle_set_debug_active(websocket: websockets.ServerConnection, data: dict) -> None:
    """Adds/removes this one connection from _DEBUG_CONNECTIONS -- purely
    local bookkeeping, no reply needed (the Renderer already knows its
    own toggle state; it's Brain's future debug_event sends that need to
    know, not this client).
    """
    if bool(data.get("active")):
        _DEBUG_CONNECTIONS.add(websocket)
    else:
        _DEBUG_CONNECTIONS.discard(websocket)


async def _handle_restart_brain(websocket: websockets.ServerConnection) -> None:
    """Restarts this entire Python process -- not a graceful reload of
    individual pieces, a full fresh start: config.yaml is re-read and
    every engine/soul/profile/harness is rebuilt from disk exactly as
    main() does on a normal launch. In-memory conversation history is
    gone, same as any other restart.

    Relaunches the exact command that launched this process
    (sys.executable + sys.argv) rather than just exiting and hoping
    something relaunches it -- this project has no supervisor process
    (see setup.sh/.bat, both just run `uv run main.py` directly), so a
    plain exit would leave Brain dead until someone manually started it
    again. Works whether Brain was started via `uv run main.py` or the
    venv's python.exe directly, since sys.executable/sys.argv reflect the
    real invocation either way.

    POSIX gets a true in-place os.execv (same PID, no gap where two
    Brains could both be trying to bind the port). Windows does NOT --
    confirmed live, two separate real bugs found by actually testing this
    on Windows rather than assuming either approach would just work:

    1. os.execv there is emulated via the C runtime's _execv, which
       reconstructs its own command line without quoting the executable
       path, so a path containing a space (this project's own path, under
       a "Claude Code" directory, is exactly such a path) gets torn apart
       at the space and the relaunch fails outright ("can't open file
       Code/glitch/brain/.venv/Scripts/python.exe" -- everything before
       the first space silently dropped). subprocess.Popen quotes
       arguments correctly and doesn't have this bug.
    2. A process tree running inside a Windows Job Object with "kill all
       processes when the job closes" semantics (e.g. a sandboxed dev
       environment) can bring the freshly-spawned child down along with
       an *externally force-killed* parent unless the child explicitly
       requests CREATE_BREAKAWAY_FROM_JOB -- harmless to include even
       when there's no such job to break away from. This does NOT affect
       the actual restart path below, though: a parent that exits
       *normally* (this function's own os._exit(0), not an external
       kill) does not cascade-kill its children via the job either way --
       confirmed live by letting a spawned child run to completion well
       after its parent's own normal exit.
    3. DETACHED_PROCESS (tried first, since it's the textbook flag for
       "fully independent background process") left the *parent* hung
       indefinitely before ever reaching os._exit(0) below -- confirmed
       live, most likely a handle-inheritance interaction with a process
       that has no console of its own. Explicitly redirecting
       stdin/stdout/stderr to DEVNULL gets the same practical outcome
       (the new process doesn't inherit this one's stdio) without it.

    A real (small) gap exists either way between the old process exiting
    and the new one listening -- the Renderer's reconnect loop
    (brain_client.js's `connect()`) already tolerates a much longer gap
    than this on every ordinary restart, so this isn't a new failure mode
    for it to handle.

    Every connected Renderer just sees this as an ordinary disconnect --
    their own reconnect loop already handles that unconditionally, and
    the fresh process's own `ready` handshake naturally re-syncs
    everything once it's back up. Never returns (the process is replaced
    or exited before this coroutine's caller would resume).
    """
    await _debug_log(websocket, "brain", "restarting Brain process...")
    print("[brain] restart requested -- restarting process now")
    if sys.platform == "win32":
        subprocess.Popen(
            [sys.executable, *sys.argv],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB,
            # See this function's own docstring point 3 for why these are
            # explicit rather than just passing DETACHED_PROCESS.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        os._exit(0)
    else:
        os.execv(sys.executable, [sys.executable, *sys.argv])


def _handle_load_profile(data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        content = profiles.load_profile(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load profile {name!r}: {exc!r}")
        return
    if profiles.read_roleplay_active():
        # isinstance guard: see _handle_delete_profile's own comment.
        if isinstance(brain.llm, LocalLLM):
            brain.llm.set_persona(content)
        print(f"[brain] loaded profile {name!r}")
    else:
        # Still recorded in rp_user.md above -- just not applied to the LLM
        # while role-play is toggled off (see _handle_set_roleplay_active).
        print(f"[brain] selected profile {name!r} (role-play is off, not applied)")


def _handle_set_roleplay_active(data: dict, brain: Brain) -> None:
    active = bool(data.get("active"))
    profiles.set_roleplay_active(active)
    if active:
        # think is always sent by the Renderer's role-play toggle (see
        # brain_client.js's _setRoleplayActive) -- the `is not None` check
        # is just defensive in case some future caller omits it, in which
        # case leave whatever LLM engine is already active alone rather
        # than assuming a switch was wanted.
        think = data.get("think")
        if think is not None:
            _switch_to_roleplay_engine(bool(think), brain)
        content = profiles.read_active_profile()
        # isinstance guard: see _handle_delete_profile's own comment --
        # here specifically, _switch_to_roleplay_engine leaves brain.llm
        # untouched (possibly still a HarnessLLM) if it couldn't find a
        # configured ROLEPLAY_LLM_ENGINE_NAME to switch to.
        if isinstance(brain.llm, LocalLLM):
            brain.llm.set_soul(_effective_soul())
            brain.llm.set_persona(content)
        print(f"[brain] role-play activated{' with the selected profile' if content else ' (no profile selected yet)'}")
    else:
        # Mirrors the "on" path: re-enables thinking on ROLEPLAY_LLM_ENGINE_NAME
        # and switches to it, so turning role-play off always lands back on
        # full reasoning -- the fast/no-think mode is specifically an RP
        # thing, not something that should quietly stay on afterward. This
        # used to be the actual source of empty replies outside RP (a model
        # can burn its entire reply-token budget on reasoning and never
        # reach real content) -- fixed at the source now, not by avoiding
        # think=true: see llm/client.py's MAX_REPLY_TOKENS_THINKING, a
        # wider budget OllamaLLM applies specifically when think is on.
        # Same graceful no-op as the "on" path if that engine isn't
        # configured.
        _switch_to_roleplay_engine(True, brain)
        # isinstance guard: same reasoning as the "on" branch above.
        if isinstance(brain.llm, LocalLLM):
            brain.llm.set_soul(_effective_soul())  # role-play flag is already off above, so this is her main soul
            brain.llm.set_persona("")
        print("[brain] role-play deactivated -- back to her main soul, thinking re-enabled")


def _switch_to_roleplay_engine(think: bool, brain: Brain) -> None:
    """Updates the saved ROLEPLAY_LLM_ENGINE_NAME LLM engine's think flag
    (preserving its other fields -- endpoint/model/api_key/provider) and
    switches to it. This is what the Renderer's "role-play requires Ollama"
    confirm modal actually promises: a reasoning-heavy persona needs a
    backend that can genuinely turn thinking off (see OllamaLLM's
    docstring for why LM Studio's best-effort budget cap isn't that).

    Silently no-ops, leaving whatever LLM engine was already active alone,
    if no engine named ROLEPLAY_LLM_ENGINE_NAME exists -- the confirm modal
    already told the user Ollama is required before this was ever called,
    so a missing engine here means they back out of actually setting one
    up, not something worth breaking the whole role-play toggle over.
    """
    try:
        engine = llm_engines.read_engine(ROLEPLAY_LLM_ENGINE_NAME)
    except (ValueError, OSError) as exc:
        print(f"[brain] role-play wanted the {ROLEPLAY_LLM_ENGINE_NAME!r} LLM engine but couldn't read it: {exc!r}")
        return
    llm_engines.save_engine(
        ROLEPLAY_LLM_ENGINE_NAME,
        engine["endpoint"],
        engine.get("model", ""),
        engine.get("api_key", ""),
        provider=engine.get("provider", "openai"),
        think=think,
    )
    try:
        brain.llm = _build_llm(ROLEPLAY_LLM_ENGINE_NAME)
    except Exception as exc:
        print(f"[brain] couldn't switch to {ROLEPLAY_LLM_ENGINE_NAME!r} for role-play: {exc!r}")
        return
    llm_engines.set_active_engine_name(ROLEPLAY_LLM_ENGINE_NAME)
    print(f"[brain] role-play switched LLM engine to {ROLEPLAY_LLM_ENGINE_NAME!r} (think={think})")


async def _handle_user_audio(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    audio_b64 = data.get("audio_b64") or ""
    if not audio_b64:
        await websocket.send(json.dumps(protocol.no_reply()))
        return
    start = time.monotonic()
    try:
        text = await asyncio.to_thread(_transcribe, brain.stt, audio_b64, data.get("mime_type", ""))
    except Exception as exc:
        await _debug_log(websocket, "stt", f"STT failed: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] STT failed: {exc!r}")
        # Without this, a failed transcription (e.g. a blank/silent voice
        # message, or the STT engine erroring outright) left the Renderer
        # stuck "awaiting a reply" forever -- Send/camera/desktop/mic all
        # grayed out with nothing actually happening. Confirmed live.
        await websocket.send(json.dumps(protocol.no_reply()))
        return
    await _debug_log(websocket, "stt", "transcription ok", (time.monotonic() - start) * 1000)
    print(f"[brain] user_audio transcribed: {text!r}")
    if text.strip():
        await websocket.send(json.dumps(protocol.user_transcript(text)))
    await _reply_to(websocket, text, brain)


async def _handle_regenerate_last(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The Renderer's "resend" controls (per-bubble retry, History panel's
    Resend Last) -- re-answers the same prompt without leaving the stale
    reply (and a duplicated question right after it) sitting in context,
    which is what plainly resending the same text as a brand new user_text
    would do and reads as "he's just repeating himself" rather than a
    clean second attempt.

    Only LocalLLM/OllamaLLM actually have history to pop (see
    pop_last_exchange's own docstring) -- a HarnessLLM manages its own
    memory externally with no local equivalent, so this falls back to
    just answering whatever text the Renderer sent (its own best
    recollection of the last prompt), same as an ordinary user_text, for
    that case and for NoneLLM.
    """
    text = data.get("text", "")
    if isinstance(brain.llm, LocalLLM):
        popped_text = brain.llm.pop_last_exchange()
        if popped_text is not None:
            text = popped_text
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


async def _reply_to(
    websocket: websockets.ServerConnection,
    text: str,
    brain: Brain,
    image_b64: str | None = None,
    image_mime: str = "image/jpeg",
) -> None:
    text = text.strip()
    if not text and not image_b64:
        # A blank voice message (silence transcribes to "") or an empty
        # user_text is the most common way here -- without telling the
        # Renderer, it stays "awaiting a reply" forever with every button
        # grayed out and nothing actually coming. Confirmed live.
        await websocket.send(json.dumps(protocol.no_reply()))
        return
    print(f"[brain] user said: {text!r}" + (" (+ image)" if image_b64 else ""))

    # isinstance guard: same reasoning as every other brain.llm-touching
    # call in this file -- HarnessLLM has no set_memory at all, and it
    # manages its own memory externally anyway (see memory.py's own
    # docstring). recall_for_prompt is provider-agnostic (memory.py's own
    # read_provider() decides local-vs-hindsight) -- for hindsight this is
    # recalled fresh for THIS message every turn, not just primed once at
    # LLM-build time, so different questions actually surface different
    # relevant memories instead of one static block; for local it's just
    # the one flat block, same as before. Never lets a lookup failure
    # break the reply itself -- a turn with no memory applied is still a
    # perfectly good reply.
    #
    # Gated on role-play being OFF, same as _maybe_retain_memory's own
    # gate below -- the user was explicit that RP should pause memory
    # entirely, not just stop learning new things during it: she
    # shouldn't be drawing on real-user facts while in character either.
    # No text (an image-only turn) leaves whatever a previous turn set
    # alone rather than clearing it -- there's nothing to recall against,
    # but that's not the same as "pause memory", so this only clears when
    # there actually was a message and memory was skipped for it.
    if text and isinstance(brain.llm, LocalLLM):
        if memory.read_memory_active() and not profiles.read_roleplay_active():
            recall_start = time.monotonic()
            try:
                relevant = await memory.recall_for_prompt(text)
            except Exception as exc:
                await _debug_log(websocket, "memory", f"recall failed: {exc!r}", (time.monotonic() - recall_start) * 1000)
            else:
                brain.llm.set_memory(relevant)
                await _debug_log(websocket, "memory", "recall ok", (time.monotonic() - recall_start) * 1000)
        else:
            # Role-play active, or memory turned off -- clears whatever a
            # previous turn set so it can't linger into this one (e.g.
            # role-play just got turned on with a stale recall still
            # sitting in the system prompt from right before).
            brain.llm.set_memory("")

    if isinstance(brain.llm, LocalLLM):
        brain.llm.set_user_info(_effective_user_info())

    # Learned behavior rules (brain/lessons.py) -- like memory, off during
    # role-play and cleared rather than skipped so a stale block can't linger
    # into an in-character turn. Never lets a lookup failure break the reply.
    if isinstance(brain.llm, LocalLLM):
        if lessons.read_active() and not profiles.read_roleplay_active():
            try:
                brain.llm.set_lessons(await asyncio.wait_for(lessons.prompt_block(), timeout=LESSON_PROMPT_TIMEOUT_SEC))
            except Exception as exc:
                await _debug_log(websocket, "lessons", f"couldn't load lessons: {exc!r}")
                brain.llm.set_lessons("")
        else:
            brain.llm.set_lessons("")

    llm_start = time.monotonic()
    try:
        reply_text, mood = await asyncio.to_thread(
            brain.llm.reply, text, image_b64, image_mime, web_search.read_active()
        )
    except Exception as exc:
        # No Brain -> Renderer error message type exists yet (protocol.md's
        # `error` is Renderer -> Brain only) -- surfacing this as speak_text
        # is a deliberate, minimal stand-in rather than adding a new message
        # type just for this. Revisit if/when that actually gets in the way.
        await _debug_log(websocket, "llm", f"LLM call failed: {exc!r}", (time.monotonic() - llm_start) * 1000)
        print(f"[brain] LLM call failed: {exc!r}")
        await websocket.send(json.dumps(protocol.speak_text(f"(couldn't reach the LLM: {exc})")))
        return
    # Never logs reply_text itself -- timing/outcome only, per the
    # Debugging feature's whole point (connection/timing/errors, not
    # conversation content).
    await _debug_log(websocket, "llm", "LLM reply received", (time.monotonic() - llm_start) * 1000)

    if not reply_text.strip():
        # A real, confirmed failure mode (not hypothetical): the
        # configured reasoning model can finish and return successfully
        # -- no exception, no token-limit truncation -- with `content`
        # still empty (everything it produced was reasoning_content, or
        # just the mood tag with nothing else). Left unhandled, this used
        # to fall through to speak_text with "" (a blank chat-history
        # bubble the user just sees as silence) and then a TTS call that
        # fails outright with "Input contains no speakable text" -- two
        # confusing symptoms for what's really one cause. Treating it the
        # same as _reply_to's own empty-input guard above is both more
        # honest and skips a TTS call that could never succeed anyway.
        await _debug_log(websocket, "llm", "LLM returned an empty reply", (time.monotonic() - llm_start) * 1000)
        await websocket.send(json.dumps(protocol.no_reply()))
        return

    print(f"[brain] mood: {mood}")
    # Sent before speak_text/speak_audio so her face is already changing by
    # the time she starts talking, not lagging a beat behind. Sent even for
    # "neutral" -- the Renderer treats that as "fade every mood expression
    # back to 0", which is exactly right after a mood-carrying reply.
    await websocket.send(json.dumps(protocol.set_expression(mood, 1.0)))
    await websocket.send(json.dumps(protocol.speak_text(reply_text)))

    # Fire-and-forget: must never slow down or affect the reply the user
    # already has. Runs regardless of whether voice/TTS succeeds below --
    # it only needs the text of what was actually said.
    # A turn where she searched the web has the results woven into her
    # reply, and a turn with a camera/screen image has her describing that
    # frame -- neither is a fact about the user, just world facts or a
    # moment. Only the user's own side is remembered for those, so memory
    # doesn't fill with headlines and "the keyboard has blue keys" (and she
    # can't repeat a bad search result back later as if it were a memory).
    used_web_search = bool(getattr(brain.llm, "last_reply_used_web_search", False))
    omit_reply = used_web_search or bool(image_b64)
    asyncio.create_task(_maybe_retain_memory(websocket, text, "" if omit_reply else reply_text, brain))

    if not voice_settings.read_voice_active():
        await _debug_log(websocket, "tts", "voice is off -- skipping synthesis")
        return

    if isinstance(brain.tts, NoneTTS):
        await _debug_log(websocket, "tts", "no speech engine configured -- skipping synthesis")
        return

    tts_start = time.monotonic()
    try:
        wav_bytes, frames = await asyncio.to_thread(brain.tts.synthesize, reply_text)
    except Exception as exc:
        await _debug_log(websocket, "tts", f"TTS call failed: {exc!r}", (time.monotonic() - tts_start) * 1000)
        print(f"[brain] TTS failed: {exc!r}")
        return
    await _debug_log(websocket, "tts", "TTS synthesis ok", (time.monotonic() - tts_start) * 1000)

    audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
    await websocket.send(json.dumps(protocol.speak_audio(audio_b64, brain.tts.SAMPLE_RATE)))
    await websocket.send(json.dumps(protocol.viseme_stream(frames)))


async def _maybe_retain_memory(websocket: websockets.ServerConnection, user_text: str, reply_text: str, brain: Brain) -> None:
    """Glitch's own native memory (brain/memory.py) -- entirely separate
    from anything Hermes does with its own memory. Gated on three things:
    not the harness (isinstance check, not a duck-typed call -- HarnessLLM
    has no set_memory at all, on purpose, and manages its own memory
    externally anyway), the feature's own on/off toggle, and role-play
    being OFF -- the user was explicit that in-character role-play content
    must never be captured as fact about them, and skipping retention
    entirely during role-play is the simplest way to guarantee that
    rather than trying to classify fiction-vs-real-signal reliably.

    Branches on the active provider (memory.py's own read_provider()):
    "hindsight" hands the raw exchange straight to Hindsight's own
    retain(), which decides server-side what's worth keeping and doesn't
    hand back the specific fact synchronously, so no memory_learned gets
    sent for that path. "local" restores the original flat-file
    behavior -- one extra lightweight LLM call
    (LocalLLM.maybe_extract_memory) judges whether there's exactly one
    new durable fact, and memory_learned only fires when something
    genuinely new was added (never for a duplicate/no-op).

    Wrapped in try/except throughout: a failure here must never surface to
    the user or affect anything else, it's a pure background nice-to-have.
    """
    if not isinstance(brain.llm, LocalLLM) or not memory.read_memory_active() or profiles.read_roleplay_active():
        return
    if not user_text.strip() and not reply_text.strip():
        return  # an image-only turn with nothing the user said -- nothing left worth keeping
    start = time.monotonic()
    if memory.read_provider() == memory.HINDSIGHT_PROVIDER:
        try:
            await memory.retain_exchange(user_text, reply_text)  # reply_text is "" for a web-search turn, see _reply_to
        except Exception as exc:
            await _debug_log(websocket, "memory", f"retain failed: {exc!r}", (time.monotonic() - start) * 1000)
            return
        await _debug_log(websocket, "memory", "retained", (time.monotonic() - start) * 1000)
        return

    try:
        fact = await asyncio.to_thread(
            brain.llm.maybe_extract_memory,
            user_text,
            reply_text or "(reply omitted -- it described search results or an image)",
            memory.read_local_entries(),
        )
    except Exception as exc:
        await _debug_log(websocket, "memory", f"extraction failed: {exc!r}", (time.monotonic() - start) * 1000)
        return
    if not fact:
        await _debug_log(websocket, "memory", "nothing new to remember", (time.monotonic() - start) * 1000)
        return
    # Never logs the fact itself -- category/timing only, same reasoning
    # as every other _debug_log call in this file (never conversation
    # content, and a remembered fact about the user is exactly that).
    if not memory.add_local_entry(fact):
        # Exact-duplicate re-add -- nothing actually changed, so no
        # memory_learned notification either (would be a false "learned
        # something new" for a fact she already had).
        await _debug_log(websocket, "memory", "fact already known", (time.monotonic() - start) * 1000)
        return
    # Re-check isinstance here rather than trusting the guard at the top of
    # this function -- brain.llm is a plain shared attribute (see Brain's
    # own docstring on why there's no per-connection state), and the
    # asyncio.to_thread call above this awaited long enough for another
    # connection's set_harness_active to reassign it out from under this
    # task in the meantime. HarnessLLM has no set_memory at all.
    if isinstance(brain.llm, LocalLLM):
        brain.llm.set_memory(memory.read_local_block())
    await _debug_log(websocket, "memory", "learned something new", (time.monotonic() - start) * 1000)
    await websocket.send(json.dumps(protocol.memory_learned(fact)))


async def main() -> None:
    config = load_config()
    brain_cfg = config["brain"]
    host = brain_cfg.get("host", "localhost")
    port = brain_cfg["port"]
    auth_token = brain_cfg.get("auth_token")
    if host not in ("localhost", "127.0.0.1") and not auth_token:
        print(
            f"[brain] WARNING: listening on {host} (reachable from other devices), but no "
            "brain.auth_token is set in config.yaml -- anyone who can reach this port can "
            "fully control Glitch (chat as you, rewrite her persona, fill your disk with "
            "uploaded avatars). Set brain.auth_token and the matching "
            "renderer/.env VITE_BRAIN_AUTH_TOKEN to close this off."
        )

    # Optional -- a fresh install with nothing here yet is a real, expected
    # state now (see llm_engines.NONE_NAME's own docstring), not something
    # to require upfront. _build_llm falls back to NoneLLM when this has
    # no endpoint, rather than main() crashing on a missing config key the
    # way it used to (llm_cfg["endpoint"] was required before this).
    llm_cfg = brain_cfg.get("llm") or {}
    _DEFAULT_LLM_CONFIG.update(endpoint=llm_cfg.get("endpoint"), model=llm_cfg.get("model"), api_key=llm_cfg.get("api_key"))

    # Optional, same graceful-degradation reasoning as llm above -- with
    # nothing configured (neither a saved hindsight_config.json nor this
    # seed), memory.py's hindsight_* functions all stay no-ops rather than
    # main() requiring this upfront, and the "local" provider (nothing to
    # set up) is what's actually used regardless. The saved-via-the-app
    # config always wins once it exists; config.yaml's own block is only a
    # one-time seed for a machine that's never had one saved yet -- same
    # pattern as harness's own saved-list migration just below. Seeding
    # also writes the seed straight into the saved file (not just into
    # the live client), so the Settings panel's Memory Server fields show
    # real values on first connect instead of looking unconfigured when
    # it's actually working.
    had_saved_provider = memory.has_saved_provider()
    hindsight_cfg = memory.read_hindsight_config() or brain_cfg.get("hindsight") or {}
    if hindsight_cfg.get("api_url"):
        memory.save_hindsight_config(
            hindsight_cfg["api_url"], hindsight_cfg.get("api_key") or "", hindsight_cfg.get("bank_id") or "glitch-native"
        )
        await memory.ensure_bank()
        # Also a one-time seed: nothing explicitly chosen yet (no Memory
        # backend dropdown pick saved), but hindsight is configured, so
        # default to actually using it rather than silently leaving what
        # was just set up unused in favor of "local". Skipped once
        # memory_provider.txt exists -- from then on the dropdown's own
        # choice always wins, same as harness's own saved-list migration.
        if not had_saved_provider:
            memory.set_provider(memory.HINDSIGHT_PROVIDER)

    # Optional, same graceful-degradation reasoning as hindsight above --
    # config.yaml-only (not Settings-managed the way memory's Hindsight
    # connection is), since this is just a SearXNG base URL, not a
    # multi-field connection with its own secret worth a saved-file
    # round trip. The Settings panel only gets the on/off toggle.
    web_search_cfg = brain_cfg.get("web_search") or {}
    if web_search_cfg.get("searxng_url"):
        web_search.configure(web_search_cfg["searxng_url"], web_search_cfg.get("trusted_domains") or [])

    _HARNESS_CONFIGS.update(brain_cfg.get("harness") or {})
    # One-time migration: config.yaml's brain.harness block used to be the
    # only way to configure a harness (a small, fixed, code-defined list).
    # Harnesses are open-ended and user-managed via the settings panel now,
    # same as LLM/TTS engines -- if nothing's been saved that way yet, seed
    # the saved-harness list from whatever's in config.yaml so an existing
    # setup (e.g. Hermes) isn't silently dropped by this change. Runs at
    # most once in practice: after the first real save through the app,
    # harness.list_harnesses() is never empty again, so this is skipped on
    # every later restart.
    if not harness.list_harnesses():
        for key, harness_cfg in _HARNESS_CONFIGS.items():
            if not harness_cfg.get("endpoint"):
                continue
            migrated_name = key.capitalize()
            harness.save_harness(
                migrated_name, harness_cfg["endpoint"], harness_cfg.get("model") or "", harness_cfg.get("api_key") or ""
            )
            print(f"[brain] migrated config.yaml's brain.harness.{key} to a saved harness named {migrated_name!r}")

    # A harness connection (brain/harness.py) persists across restarts the
    # same as a saved profile/soul/engine -- but its config.yaml block
    # might be gone or edited since, so this falls back to her own LLM
    # engine rather than assuming it's still good.
    active_harness_name = harness.read_active_harness()
    harness_llm = _build_harness_llm(active_harness_name) if active_harness_name else None
    if harness_llm is not None:
        llm = harness_llm
        print(f"[brain] primed with previously active harness {active_harness_name!r}")
    else:
        if active_harness_name:
            harness.set_active_harness("")
        # _build_llm also primes the fresh instance with whatever profile/
        # soul was last active (brain/profiles.py's user.md / brain/souls.py's
        # soul.md persist across restarts) rather than starting every
        # restart back at no persona, silently losing it.
        llm = _build_llm(llm_engines.read_active_engine_name())

    tts = _build_tts(tts_engines.read_active_engine_name())
    print("[brain] loading STT (faster-whisper)...")
    stt = FasterWhisperSTT()
    brain = Brain(llm=llm, tts=tts, stt=stt)

    # 20MB was enough for base64 TTS/STT audio, but VRM avatar files
    # (avatars.py) routinely exceed that once base64-encoded -- the
    # shipped default alone is ~15MB raw, ~20MB encoded. 100MB gives
    # real headroom for larger imported avatars.
    #
    # ping_timeout defaults to 20s -- confirmed live via a real debug log:
    # a hung local LLM backend (connected but never responding) left
    # _handle_message's `async for` loop stuck inside one long `await`
    # (llm.reply's blocking call, run via asyncio.to_thread) for longer
    # than that, and the library's own automatic keepalive ping/pong gave
    # up and force-closed the connection with 1011 "keepalive ping
    # timeout" -- well before llm.client.REQUEST_TIMEOUT_SEC's own 120s
    # timeout ever got a chance to fire and surface a proper error to the
    # user. Set comfortably above REQUEST_TIMEOUT_SEC so a slow-or-hung
    # LLM call gets to time out on its own terms instead of the transport
    # silently dying underneath it first.
    print(f"[brain] listening on ws://{host}:{port}")
    asyncio.create_task(_health_check_loop())
    async with websockets.serve(
        lambda ws: handle_renderer(ws, brain, auth_token),
        host,
        port,
        max_size=100 * 1024 * 1024,
        ping_timeout=REQUEST_TIMEOUT_SEC + 30,
    ):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
