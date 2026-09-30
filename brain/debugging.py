"""Settings -> Debugging: the per-device debug log (a setup snapshot when it's
turned on, console problems copied into it) and Restart Brain.
"""

import asyncio
import collections
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import websockets

import curiosity
import hub
import lessons
import llm_engines
import memory
import profiles
import protocol
import sampling
import training
import tts_engines
import voice_settings
import web_search
from hub import Brain
from llm import HarnessLLM, LocalLLM, OllamaLLM

# Where a restarted Brain's own output goes (it has no console to print to once it's
# been relaunched detached). Truncated on each restart, so it only ever holds the
# latest run -- and a replacement that dies at startup leaves its error here
# instead of vanishing.
RESTART_LOG_PATH = Path(__file__).parent / "restart.log"


# Console lines worth a debug-log entry: failures and warnings, from any module
# ("[memory] couldn't ...", "[llm] ... failed", "WARNING: ..."). Everything else
# stays console-only -- including "[brain] user said: ..." and her mood lines,
# which carry conversation content.
_CONSOLE_PROBLEM = re.compile(r"couldn't|could not|failed|error|warning|timed out|refused|unreachable", re.I)
_CONSOLE_PREFIX = re.compile(r"^\[(\w+)\]\s*")


# Console lines that quote what was said -- never copied, whatever words they contain.
_CONSOLE_CONTENT_MARKERS = ("user said", "mood:", "reaching out with")


# The latest problems, kept whether or not a device is debugging: many happen
# at startup (Hindsight unreachable, a bad engine), before any device connects,
# and would otherwise never reach a log. The setup snapshot lists them.
_RECENT_PROBLEMS: collections.deque = collections.deque(maxlen=30)


class ConsoleToDebugLog:
    """Wraps sys.stdout so a problem printed anywhere in Brain also reaches the
    debug log (hub.debug_broadcast). Before this, a failed memory save, a failed
    lesson or a failed reach-out only showed in Brain's console window, and a
    downloaded debug log said nothing about it. Prints from worker threads are
    handed to the event loop, since sending is loop-only.
    """

    def __init__(self, stream, loop: asyncio.AbstractEventLoop) -> None:
        self._stream = stream
        self._loop = loop
        self._partial = ""

    def write(self, text: str) -> int:
        written = self._stream.write(text)
        self._partial += text
        *lines, self._partial = self._partial.split("\n")
        for line in lines:
            if _CONSOLE_PROBLEM.search(line) and not any(m in line for m in _CONSOLE_CONTENT_MARKERS):
                match = _CONSOLE_PREFIX.match(line)
                category = match.group(1) if match else "brain"
                message = line[match.end():] if match else line
                _RECENT_PROBLEMS.append((time.time(), category, message.strip()))
                if not hub.DEBUG_CONNECTIONS:
                    continue
                try:
                    self._loop.call_soon_threadsafe(
                        lambda c=category, m=message: hub.spawn(hub.debug_broadcast(c, f"console: {m.strip()}"))
                    )
                except RuntimeError:
                    pass  # the loop is closing
        return written

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _brain_version() -> str:
    """The git commit Brain is running, for the debug log's snapshot -- "unknown"
    outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent, capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


_BRAIN_VERSION = _brain_version()
_BRAIN_STARTED_AT = time.time()


@hub.handles(protocol.SET_DEBUG_ACTIVE)
async def _set_debug_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Adds/removes this one connection from hub.DEBUG_CONNECTIONS. Turning it on
    also sends a snapshot of how Brain is set up (_debug_snapshot), so a
    downloaded log says which engine, model, settings and features were in play
    without anyone having to ask.
    """
    if bool(data.get("active")):
        hub.DEBUG_CONNECTIONS.add(websocket)
        for line in await _debug_snapshot(brain, websocket):
            await hub.debug_log(websocket, "setup", line)
    else:
        hub.DEBUG_CONNECTIONS.discard(websocket)


async def _debug_snapshot(brain: Brain, current: websockets.ServerConnection | None = None) -> list[str]:
    """How Brain is set up right now -- settings and names only, no content.
    `current` is the device asking, marked as "This device" in the list."""
    def on(flag: bool) -> str:
        return "on" if flag else "off"

    llm = brain.llm
    lines = [f"Brain {_BRAIN_VERSION}, up {int((time.time() - _BRAIN_STARTED_AT) / 60)} min"]
    for connection in sorted(hub.RENDERER_CONNECTIONS, key=lambda c: c is not current):
        lines.append(f"{'This device' if connection is current else 'Also connected'}: {hub.device_label(connection)}")
    if isinstance(llm, HarnessLLM):
        session = (llm.session_id or "none")[:14]
        lines.append(f"LLM: harness {llm.name!r} (session {session}...)")
    elif isinstance(llm, LocalLLM):
        engine = llm_engines.read_active_engine_name()
        kind = "Ollama" if isinstance(llm, OllamaLLM) else "OpenAI-compatible"
        window = await asyncio.to_thread(llm.context_window)
        lines.append(
            f"LLM: engine {engine!r} ({kind}), model {llm.model_name or '(server default)'!r}, "
            f"context {window or 'unknown'}, keeps ~{llm.history_budget()} tokens of chat, "
            f"{llm.message_count} messages in the conversation now"
        )
        lines.append(f"Sampling profile: {sampling.read_active_name()!r} {sampling.active_values() or '(server settings)'}")
    else:
        lines.append("LLM: none configured")
    lines.append(f"Speech engine: {tts_engines.read_active_engine_name()!r}, voice {on(voice_settings.read_voice_active())}")
    provider = memory.read_provider()
    lines.append(
        f"Memory: {on(memory.read_memory_active())} ({memory.active_description()}), training {on(training.read_active())}, "
        f"lessons {on(lessons.read_active())} ({lessons.read_autonomy()})"
    )
    lines.append(
        f"Role-play {on(profiles.read_roleplay_active())} (engine {llm_engines.read_roleplay_engine() or 'keep current'!r}), "
        f"curiosity {on(curiosity.read_active())}, web search {on(web_search.read_active())}"
    )
    try:
        lesson_count = str(len(await asyncio.wait_for(lessons.active_lessons(), timeout=5))) if lessons.available() else "n/a"
    except Exception:
        lesson_count = "unknown"
    questions = curiosity.counts()
    lines.append(
        f"Waiting for review: {len(training.read_pending())} memories, {len(lessons.read_pending())} lesson changes; "
        f"active lessons: {lesson_count}; her saved questions: {questions['open']} open, {questions['done']} asked or dropped"
    )
    if isinstance(llm, LocalLLM):
        lines.append(f"Loaded on her LLM server: {await asyncio.to_thread(llm.loaded_models)}")
    if provider == memory.HINDSIGHT_PROVIDER:
        lines.append(f"Hindsight's own model (rewrites and summaries): {await asyncio.to_thread(memory.hindsight_latest_model)}")
    if _RECENT_PROBLEMS:
        lines.append(f"Problems since Brain started ({len(_RECENT_PROBLEMS)}, newest last):")
        for at, category, message in list(_RECENT_PROBLEMS)[-10:]:
            lines.append(f"  {time.strftime('%H:%M:%S', time.localtime(at))} [{category}] {message}")
    return lines


@hub.handles(protocol.DEBUG_PING)
async def _debug_ping(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The Renderer measuring its round trip to Brain while debugging."""
    await hub.send(websocket, protocol.debug_pong(data.get("ts")))


@hub.handles(protocol.RESTART_BRAIN)
async def _restart_brain(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Restarts this entire Python process: config.yaml is re-read and every
    engine/soul/profile/harness rebuilt from disk, exactly as a normal launch.
    There's no supervisor process (setup.sh/.bat just run `uv run main.py`),
    so it relaunches its own command line (sys.executable + sys.argv) rather
    than exiting and hoping something restarts it. Every Renderer just sees an
    ordinary disconnect and reconnects; its `ready` re-syncs everything.

    POSIX replaces the process in place (os.execv). Windows can't, for three
    reasons found by testing it there:

    1. Its os.execv doesn't quote the executable path, so a path with a space
       in it (like this project's) breaks the relaunch. subprocess.Popen quotes
       correctly.
    2. Inside a Job Object that kills its processes when it closes (e.g. a
       sandboxed dev environment), the child needs CREATE_BREAKAWAY_FROM_JOB to
       survive -- harmless when there's no such job.
    3. DETACHED_PROCESS left the parent hung before it could exit; explicit
       std handles get the same independence without that.

    The old process exits right after, and the new one retries its port until
    the old one lets go (server.serve_when_free). Never returns.
    """
    await hub.debug_log(websocket, "brain", "restarting Brain process...")
    print("[brain] restart requested -- restarting process now")
    if sys.platform == "win32":
        # The replacement's output goes to RESTART_LOG_PATH, not DEVNULL: it has
        # no console of its own, and a replacement that crashes at startup used
        # to just vanish, looking like Brain "killed itself and never came
        # back". Still explicit std handles (not DETACHED_PROCESS) -- see this
        # function's own docstring point 3.
        restart_log = open(RESTART_LOG_PATH, "w", encoding="utf-8", buffering=1)
        restart_log.write(f"[restart] relaunching {sys.executable} {' '.join(sys.argv)}\n")
        subprocess.Popen(
            [sys.executable, *sys.argv],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_BREAKAWAY_FROM_JOB,
            stdin=subprocess.DEVNULL,
            stdout=restart_log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},  # so the log fills as it happens, not in one lump at exit
        )
        os._exit(0)
    else:
        os.execv(sys.executable, [sys.executable, *sys.argv])
