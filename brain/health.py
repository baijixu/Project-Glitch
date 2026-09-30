"""The status lights for saved harnesses and speech engines: a TCP check of
each one's endpoint every ENDPOINT_HEALTH_CHECK_INTERVAL_SEC, sent to every
device when it changes.
"""

import asyncio
import socket
import urllib.parse

import websockets

import harness
import hub
import protocol
import tts_engines

ENDPOINT_HEALTH_CHECK_INTERVAL_SEC = 20


# Short and TCP-only on purpose -- this only needs to tell "something's
# listening" from "nothing is", not exercise a full request. A slow-but-up
# harness/engine shouldn't read as down just because a real API call
# would take longer than this.
ENDPOINT_HEALTH_CHECK_TIMEOUT_SEC = 3


# name -> last-known reachability, per harness.list_harnesses() -- the
# indicator light's source of truth for "reachable" (harness_health's
# `reachable`), separate from harness_state's `active`. Populated by
# health_check_loop's very first pass at startup; send_harness_health
# falls back to an on-demand check only in the brief window before that
# first pass completes. LAST_TTS_REACHABLE is the same thing for saved
# speech engines (tts_health) -- two dicts, not one keyed by kind, since a
# harness and a speech engine could coincidentally share a name.
LAST_HARNESS_REACHABLE: dict[str, bool | None] = {}
LAST_TTS_REACHABLE: dict[str, bool | None] = {}


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


async def send_harness_health(websocket: websockets.ServerConnection) -> None:
    """Sends harness_health for every known harness to this one connection
    -- used on handshake.handle_ready so a freshly-opened tab doesn't have to wait
    for health_check_loop's next tick to paint the indicator light.
    Echoes the loop's own cached value; only does a fresh on-demand check
    in the brief startup window before that loop's first pass has run.
    """
    for name in harness.list_harnesses():
        reachable = LAST_HARNESS_REACHABLE[name] if name in LAST_HARNESS_REACHABLE else await _check_harness_health(name)
        await hub.send(websocket, protocol.harness_health(name, reachable))


async def send_tts_health(websocket: websockets.ServerConnection) -> None:
    """Same as send_harness_health, for saved speech engines -- never
    sent (and never checked) for tts_engines.NONE_NAME, since it has no
    network endpoint to check at all; _renderTtsLight (Renderer-side)
    already treats "no data for this name" as grey for exactly this
    reason.
    """
    for name in tts_engines.list_engines():
        reachable = LAST_TTS_REACHABLE[name] if name in LAST_TTS_REACHABLE else await _check_tts_health(name)
        await hub.send(websocket, protocol.tts_health(name, reachable))


async def _check_harness_health(name: str) -> bool | None:
    """None if `name` has no endpoint configured (its saved harness file
    is unreadable, or has no endpoint set) -- distinct from a real
    reachability check finding it down, so the indicator light can tell
    "nothing set up" from "set up but unreachable" apart (see
    protocol.py's harness_health). Shared by send_harness_health and
    health_check_loop so that distinction is only written once.
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


async def health_check_loop() -> None:
    """Runs forever, checking every known harness's and saved speech
    engine's reachability on an interval and broadcasting harness_health/
    tts_health to every currently-connected Renderer whenever either
    changes -- a single global task (unlike server.ping_loop, which is
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
            if LAST_HARNESS_REACHABLE.get(name) != reachable:
                LAST_HARNESS_REACHABLE[name] = reachable
                await hub.broadcast(protocol.harness_health(name, reachable))
        for name in tts_engines.list_engines():
            reachable = await _check_tts_health(name)
            if LAST_TTS_REACHABLE.get(name) != reachable:
                LAST_TTS_REACHABLE[name] = reachable
                await hub.broadcast(protocol.tts_health(name, reachable))
        await asyncio.sleep(ENDPOINT_HEALTH_CHECK_INTERVAL_SEC)
