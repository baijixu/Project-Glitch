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


# name -> last-known reachability, filled by health_check_loop's first pass at startup. Two dicts, since a
# harness and a speech engine could share a name.
LAST_HARNESS_REACHABLE: dict[str, bool | None] = {}
LAST_TTS_REACHABLE: dict[str, bool | None] = {}

# (saved names, read one's config, its status message, last-known reachability)
_KINDS = [
    (harness.list_harnesses, harness.read_harness, protocol.harness_health, LAST_HARNESS_REACHABLE),
    (tts_engines.list_engines, tts_engines.read_engine, protocol.tts_health, LAST_TTS_REACHABLE),
]


def _check_endpoint_reachable(endpoint: str) -> bool:
    """Whether *something* listens at endpoint's host:port -- enough for a status light; using it is what
    proves it works."""
    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=ENDPOINT_HEALTH_CHECK_TIMEOUT_SEC):
            return True
    except OSError:
        return False


async def send_health(websocket: websockets.ServerConnection) -> None:
    """Every status light to one newly opened device: the loop's last result, or a fresh check
    in the moment before its first pass."""
    for list_names, read, message, last in _KINDS:
        for name in list_names():
            reachable = last[name] if name in last else await _check(read, name)
            await hub.send(websocket, message(name, reachable))


async def _check(read, name: str) -> bool | None:
    """None if `name` has no endpoint set (or can't be read): "nothing set up", a different
    light from "set up but down"."""
    try:
        endpoint = read(name).get("endpoint")
    except (ValueError, OSError):
        return None
    return await asyncio.to_thread(_check_endpoint_reachable, endpoint) if endpoint else None


async def health_check_loop() -> None:
    """Checks every saved harness and speech engine now and then every interval, telling every
    device when one changes."""
    while True:
        for list_names, read, message, last in _KINDS:
            for name in list_names():
                reachable = await _check(read, name)
                if last.get(name) != reachable:
                    last[name] = reachable
                    await hub.broadcast(message(name, reachable))
        await asyncio.sleep(ENDPOINT_HEALTH_CHECK_INTERVAL_SEC)
