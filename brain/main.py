"""Brain entry point -- hosts the WebSocket server the Renderer connects to. A message from the Renderer (the chat box, the mic, a
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
     background (hub.spawn), while TTS speaks it sentence by sentence (speak_audio).

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
  heartbeat.py .. her mind wandering while he's away, until a topic lands
  library.py .... the books he gives her, read in Heartbeat's quiet time
  friends.py .... her friends' visits
  journal.py .... her nightly diary (and upkeep.py: her weekly memory upkeep; reflection.py: her weekly look back)
  health.py ..... status lights for harnesses and speech engines
  debugging.py .. the debug log and Restart Brain

There's ONE Brain and ONE conversation shared by every connected device (see
hub.Brain), and brain.llm can be any ChatBackend (llm.py) -- her own
LocalLLM/OllamaLLM, a HarnessLLM or a NoneLLM. Every one takes the same calls;
features that are hers alone (memory, lessons, curiosity, her chat log) check
brain.llm.owns_conversation.
"""

import sys

# Her replies have emoji, which Windows' default console codepage can't encode -- a
# print() of one would crash the connection handler.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio

import debugging
import engines
import friends
import harness
import health
import heartbeat
import hub
import journal
import library  # noqa: F401 -- registers its message handlers
import learning
import llm_engines
import mind  # noqa: F401 -- registers the 🧠 window's message handlers
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

    # An unreachable memory server mustn't stop Brain starting: memory calls fail softly per turn.
    if problem := await learning.activate_memory():
        print(f"[brain] WARNING: {problem} Starting without it.")

    # Optional SearXNG URL, config.yaml only; Settings just has the on/off toggle.
    web_search_cfg = brain_cfg.get("web_search") or {}
    if web_search_cfg.get("searxng_url"):
        web_search.configure(web_search_cfg["searxng_url"], web_search_cfg.get("trusted_domains") or [])

    # The harness active before a restart, unless it's gone or broken since -- then her own LLM.
    active_harness_name = harness.read_active_harness()
    harness_llm = engines.build_harness_llm(active_harness_name) if active_harness_name else None
    if harness_llm is not None:
        llm = harness_llm
        print(f"[brain] primed with previously active harness {active_harness_name!r}")
    else:
        if active_harness_name:
            harness.set_active_harness("")
        llm = engines.build_llm(llm_engines.read_active_engine_name())

    tts = engines.build_tts(tts_engines.read_active_engine_name())
    print("[brain] loading STT (faster-whisper)...")
    stt = FasterWhisperSTT()
    brain = Brain(llm=llm, tts=tts, stt=stt)

    # max_size: an imported VRM avatar is ~20 MB once base64-encoded, so 100 MB leaves room.
    # ping_timeout: above the LLM's own timeout, so a hung model call times out with a
    # proper error instead of the keepalive closing the connection first.
    print(f"[brain] listening on ws://{host}:{port}")
    hub.spawn(health.health_check_loop())
    hub.spawn(reach_out.reach_out_loop(brain))
    reach_out.listen_for_agents(brain)
    hub.spawn(journal.journal_loop(brain))
    hub.spawn(heartbeat.heartbeat_loop(brain))
    hub.spawn(friends.visit_loop(brain))
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
