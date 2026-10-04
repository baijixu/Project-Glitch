"""Brain entry point -- hosts the WebSocket server the Renderer connects to
(spec section 5). A message from the Renderer (the chat box, the mic, a
Settings control) comes in over one WebSocket per device; replies go back the
same way, and anything that's shared state goes to every device.

Run with:
    uv run main.py

How a reply flows
-----------------
user_text / user_audio (-> STT) / regenerate_last
  -> server.handle_renderer starts it as its own task, queued behind any
     reply already running (one at a time across every device)
  -> reply.reply_to: recall memories, set user.md, lessons and curiosity on
     the LLM, then brain.llm.reply (llm.py) in a worker thread
  -> set_expression + speak_text back to the device, the chat log, the context
     meter, then memory saving / training proposals / curiosity in the
     background (hub.spawn), then TTS -> speak_audio + viseme_stream.

Where things live
-----------------
  hub.py ........ the one Brain, connected devices, broadcast, debug log
  server.py ..... connections, login and lockout, routing each message
  handshake.py .. everything a newly connected device needs
  reply.py ...... the reply pipeline, voice input, resend, clear chat
  persona.py .... which soul, user info and conversation apply right now
  engines.py .... LLM / speech engines and harnesses: building and switching
  characters.py . role-play profiles and souls, soul.md/user.md, avatars, logs
  learning.py ... lessons, memory saving and training, memory server profiles
  reach_out.py .. curiosity: her reaching out after an hour, its countdown
  health.py ..... status lights for harnesses and speech engines
  debugging.py .. the debug log and Restart Brain

There's ONE Brain and ONE conversation shared by every connected device (see
hub.Brain), and brain.llm can be any ChatBackend (llm.py) -- her own
LocalLLM/OllamaLLM, a HarnessLLM or a NoneLLM. Every one takes the same calls;
features that are hers alone (memory, lessons, curiosity, her chat log) check
brain.llm.owns_conversation.
"""

import sys

# The LLM is prompted to be "friendly and conversational" and routinely
# replies with emoji -- Windows' default console codepage (cp1252) can't
# encode those, so any print() touching raw LLM text would crash the whole
# connection handler (confirmed: a debug print of the raw reply took down a
# live request with UnicodeEncodeError on a party-popper emoji). Reconfigure
# to UTF-8 with replacement so logging never crashes on content the LLM is
# explicitly encouraged to produce.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio

import debugging
import engines
import harness
import health
import hub
import journal
import learning
import llm_engines
import reach_out
import server
import tts_engines
import web_search
from config import load_config
from hub import Brain
from llm import REQUEST_TIMEOUT_SEC
from voice import FasterWhisperSTT


async def main() -> None:
    sys.stdout = debugging.ConsoleToDebugLog(sys.stdout, asyncio.get_running_loop())
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
    # to require upfront. engines.build_llm falls back to NoneLLM when this has
    # no endpoint, rather than main() crashing on a missing config key the
    # way it used to (llm_cfg["endpoint"] was required before this).
    llm_cfg = brain_cfg.get("llm") or {}
    engines.DEFAULT_LLM_CONFIG.update(endpoint=llm_cfg.get("endpoint"), model=llm_cfg.get("model"), api_key=llm_cfg.get("api_key"))

    # Her memory backend: the active memory profile (Settings -> Memory,
    # memory_profiles.py), or the built-in local file. An unreachable server
    # (off, or on a machine that's asleep) must not stop Brain from starting:
    # memory calls fail softly per turn instead.
    if problem := await learning.activate_memory():
        print(f"[brain] WARNING: {problem} Starting without it.")

    # Optional, same graceful-degradation reasoning as hindsight above --
    # config.yaml-only (not Settings-managed the way memory's Hindsight
    # connection is), since this is just a SearXNG base URL, not a
    # multi-field connection with its own secret worth a saved-file
    # round trip. The Settings panel only gets the on/off toggle.
    web_search_cfg = brain_cfg.get("web_search") or {}
    if web_search_cfg.get("searxng_url"):
        web_search.configure(web_search_cfg["searxng_url"], web_search_cfg.get("trusted_domains") or [])

    # A harness connection (brain/harness.py) persists across restarts the
    # same as a saved profile/soul/engine -- but the saved harness might be
    # gone or edited since, so this falls back to her own LLM
    # engine rather than assuming it's still good.
    active_harness_name = harness.read_active_harness()
    harness_llm = engines.build_harness_llm(active_harness_name) if active_harness_name else None
    if harness_llm is not None:
        llm = harness_llm
        print(f"[brain] primed with previously active harness {active_harness_name!r}")
    else:
        if active_harness_name:
            harness.set_active_harness("")
        # engines.build_llm also primes the fresh instance with whatever profile/
        # soul was last active (brain/profiles.py's user.md / brain/souls.py's
        # soul.md persist across restarts) rather than starting every
        # restart back at no persona, silently losing it.
        llm = engines.build_llm(llm_engines.read_active_engine_name())

    tts = engines.build_tts(tts_engines.read_active_engine_name())
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
    # server.handle_message's `async for` loop stuck inside one long `await`
    # (llm.reply's blocking call, run via asyncio.to_thread) for longer
    # than that, and the library's own automatic keepalive ping/pong gave
    # up and force-closed the connection with 1011 "keepalive ping
    # timeout" -- well before llm.REQUEST_TIMEOUT_SEC's own
    # timeout ever got a chance to fire and surface a proper error to the
    # user. Set comfortably above REQUEST_TIMEOUT_SEC so a slow-or-hung
    # LLM call gets to time out on its own terms instead of the transport
    # silently dying underneath it first.
    print(f"[brain] listening on ws://{host}:{port}")
    hub.spawn(health.health_check_loop())
    hub.spawn(reach_out.reach_out_loop(brain))
    hub.spawn(journal.journal_loop(brain))
    listener = await server.serve_when_free(
        lambda ws: server.handle_renderer(ws, brain, auth_token),
        host,
        port,
        max_size=100 * 1024 * 1024,
        ping_timeout=REQUEST_TIMEOUT_SEC + 30,
    )
    async with listener:
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
