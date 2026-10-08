"""Settings -> LLM, Voice and Harness: building her LLM and speech engine
from the saved ones, switching between them (role-play included), and the
editors for each.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import websockets

import conversation
import harness
import health
import hub
import kokoro_voices
import llm_engines
import persona
import profiles
import protocol
import sampling
import tts_engines
from hub import Brain
from llm import ChatBackend, HarnessLLM, LocalLLM, NoneLLM, OllamaLLM, list_models, list_ollama_models
from voice import NoneTTS, RemoteTTS

# config.yaml's brain.llm block, captured once at startup (main()) -- kept
# reachable here (not just a local in main()) so build_llm can rebuild
# llm_engines.NONE_NAME on a live load_llm_engine switch too, the same way
# main.py's startup does, without needing config.yaml re-read from disk.
# Optional now (brain.llm itself, and every key inside it) -- an empty
# dict here (nothing in config.yaml at all) means build_llm falls back to
# NoneLLM rather than crashing on a missing endpoint, see its own comment.
DEFAULT_LLM_CONFIG: dict = {}


# ============================================================================
# Building her LLM and speech engine
# ============================================================================

def build_llm(name: str) -> ChatBackend:
    """LocalLLM (or OllamaLLM, see below) built from config.yaml's brain.llm
    block (DEFAULT_LLM_CONFIG) for llm_engines.NONE_NAME, or a saved
    engine's endpoint/model/api_key otherwise. Always re-primes the fresh
    instance with whatever soul/profile is currently active -- persona/
    soul state lives on the instance itself (llm.py), not
    externally, so a new instance (main.py's startup, or a live
    load_llm_engine switch) would otherwise silently drop who Glitch
    currently is. Falls back to NONE_NAME if the named engine's saved
    config can't be read.

    Returns a NoneLLM specifically when NONE_NAME resolves to no endpoint
    at all -- config.yaml's brain.llm block is optional now, so a fresh
    install with nothing configured there and no saved engine chosen yet
    is a real, expected state, not something to crash on.
    """
    if name == llm_engines.NONE_NAME:
        config = DEFAULT_LLM_CONFIG
    else:
        try:
            engine = llm_engines.read_engine(name)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't load LLM engine {name!r}, falling back to {llm_engines.NONE_NAME!r}: {exc!r}")
            name = llm_engines.NONE_NAME
            config = DEFAULT_LLM_CONFIG
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
        return NoneLLM()
    print(f"[brain] using LLM engine {name!r} at {config['endpoint']!r}")
    # The conversation is saved on every change, so a restart or an engine
    # switch picks it back up (restore_history below) instead of wiping it.
    connection = {
        "endpoint": config["endpoint"],
        "model": config.get("model"),
        "api_key": config.get("api_key"),
        "on_history_change": lambda history: conversation.save_state(history, persona.conversation_mode()),
    }
    # See OllamaLLM's own docstring for why this distinction matters:
    # Ollama's OpenAI-compatible endpoint silently ignores `think`, only
    # its native API (what OllamaLLM talks to) actually honors it.
    if config.get("provider") == "ollama":
        # During role-play on its own engine, the confirm dialog's think
        # choice wins over the engine's saved one (see switch_to_roleplay_engine).
        session = llm_engines.read_roleplay_session()
        if profiles.read_roleplay_active() and session.get("engine") == name:
            think = bool(session.get("think"))
        else:
            think = bool(config.get("think", False))
        llm = OllamaLLM(**connection, think=think)
    else:
        llm = LocalLLM(**connection)

    # Her main soul always applies unless role-play is on with an RP soul
    # selected (see persona.effective_soul); the RP profile only applies during
    # role-play. update_*, not set_*: those start a fresh conversation, which
    # would save an empty one over the real one restored just after. No memory
    # priming here -- reply.reply_to recalls what's relevant to each message.
    llm.update_soul(persona.effective_soul())
    if profiles.read_roleplay_active():
        llm.update_persona(profiles.read_active_profile())
    llm.restore_history(conversation.load_state(persona.conversation_mode()))
    return llm


def build_harness_llm(name: str) -> HarnessLLM | None:
    """A HarnessLLM for the named saved harness (brain/harness.py), or None
    if it's unknown/unreadable or has no endpoint saved -- callers fall
    back to build_llm in that case, same "don't leave Brain with nothing
    to talk to" reasoning as build_llm's own fallback when a saved LLM
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
    # Continue the same conversation across messages (and Brain restarts) --
    # see HarnessLLM.__init__ for how each harness is told which one.
    if not config.get("api_key"):
        print(f"[brain] harness {name!r} has no API key -- Hermes needs one to continue a conversation")
    return HarnessLLM(
        endpoint=config["endpoint"],
        model=config.get("model"),
        api_key=config.get("api_key"),
        session_id=harness.session_id(name),
        name=name,
        new_session=lambda: harness.new_session_id(name),
    )


def build_tts(name: str) -> RemoteTTS | NoneTTS:
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
    tts = _remote_tts(engine)
    # Her voice shouldn't go with the PC it runs on: the first other saved engine
    # stands in while this one fails (RemoteTTS.synthesize).
    for other in tts_engines.list_engines():
        if other == name:
            continue
        try:
            tts.fallback = _remote_tts(tts_engines.read_engine(other))
        except (ValueError, OSError):
            continue
        print(f"[brain] speech fallback: {other!r}")
        break
    return tts


def _remote_tts(engine: dict) -> RemoteTTS:
    return RemoteTTS(
        engine["endpoint"],
        engine.get("api_key") or None,
        engine.get("voice") or RemoteTTS.DEFAULT_VOICE,
        engine.get("model") or RemoteTTS.DEFAULT_MODEL,
    )


# ============================================================================
# The saved connections: speech engines, LLM engines, harnesses
# ============================================================================

def tts_engines_message() -> dict:
    return protocol.tts_engines(tts_engines.list_engines(), tts_engines.read_active_engine_name())


def llm_engines_message() -> dict:
    return protocol.llm_engines(llm_engines.list_engines(), llm_engines.read_active_engine_name())


def harness_state_message() -> dict:
    """The current harness_state, read fresh from harness.py each time. While a
    harness is active the selected one is by definition the active one -- only
    fall back to the separately-kept "last selected" while inactive (see
    protocol.py's harness_state docstring).
    """
    active = harness.read_active_harness()
    return protocol.harness_state(bool(active), active, active or harness.read_selected_harness_name(), harness.list_harnesses())


@dataclass(frozen=True)
class _Kind:
    """One kind of saved connection the Settings panel edits (save / get / delete)."""

    label: str  # for the console: "speech engine"
    messages: tuple[str, str, str]  # its save, get and delete message types
    fields: dict  # each editable field -> its default ("", or "openai"/False for an LLM engine)
    save: Callable[..., None]
    read: Callable[[str], dict]
    delete: Callable[[str], None]
    content: Callable[..., dict]  # the protocol message answering get
    changed: Callable[[], dict]  # what every device gets after a save or delete
    reachability: dict | None = None  # health's status-light cache, stale after an edit
    # What deleting the one in use means for Brain (see the functions below).
    deleted: Callable[[str, Brain], Awaitable[None]] | None = None


async def _tts_engine_deleted(name: str, brain: Brain) -> None:
    """Don't leave brain.tts pointed at an engine config that no longer exists."""
    if tts_engines.read_active_engine_name() != name:
        return
    try:
        brain.tts = await asyncio.to_thread(build_tts, tts_engines.NONE_NAME)
    except Exception as exc:
        print(f"[brain] couldn't fall back to {tts_engines.NONE_NAME!r} after deleting {name!r}: {exc!r}")
    else:
        tts_engines.set_active_engine_name(tts_engines.NONE_NAME)
        print(f"[brain] active speech engine was deleted -- reset to {tts_engines.NONE_NAME!r}")


async def _llm_engine_deleted(name: str, brain: Brain) -> None:
    """Don't leave brain.llm pointed at an engine config that no longer exists."""
    if llm_engines.read_active_engine_name() != name:
        return
    try:
        brain.llm = build_llm(llm_engines.NONE_NAME)
    except Exception as exc:
        print(f"[brain] couldn't fall back to {llm_engines.NONE_NAME!r} after deleting {name!r}: {exc!r}")
    else:
        llm_engines.set_active_engine_name(llm_engines.NONE_NAME)
        print(f"[brain] active LLM engine was deleted -- reset to {llm_engines.NONE_NAME!r}")


async def _harness_deleted(name: str, brain: Brain) -> None:
    """Don't leave Brain plugged into (or set to reconnect to, or showing as
    picked) a harness that no longer exists."""
    if harness.read_active_harness() == name:
        harness.set_active_harness("")
        brain.llm = build_llm(llm_engines.read_active_engine_name())
        print(f"[brain] active harness {name!r} was deleted -- disconnected, restored her own profile/soul/LLM engine")
    if harness.read_selected_harness_name() == name:
        harness.set_selected_harness_name("")


_KINDS = (
    _Kind(
        "speech engine",
        (protocol.SAVE_TTS_ENGINE, protocol.GET_TTS_ENGINE, protocol.DELETE_TTS_ENGINE),
        {"endpoint": "", "api_key": "", "voice": "", "voices_dir": "", "model": ""},
        tts_engines.save_engine, tts_engines.read_engine, tts_engines.delete_engine,
        protocol.tts_engine_content, tts_engines_message, health.LAST_TTS_REACHABLE, _tts_engine_deleted,
    ),
    _Kind(
        "LLM engine",
        (protocol.SAVE_LLM_ENGINE, protocol.GET_LLM_ENGINE, protocol.DELETE_LLM_ENGINE),
        {"endpoint": "", "model": "", "api_key": "", "provider": "openai", "think": False},
        llm_engines.save_engine, llm_engines.read_engine, llm_engines.delete_engine,
        protocol.llm_engine_content, llm_engines_message, None, _llm_engine_deleted,
    ),
    _Kind(
        "harness",
        (protocol.SAVE_HARNESS, protocol.GET_HARNESS, protocol.DELETE_HARNESS),
        {"endpoint": "", "model": "", "api_key": ""},
        harness.save_harness, harness.read_harness, harness.delete_harness,
        protocol.harness_content, harness_state_message, health.LAST_HARNESS_REACHABLE, _harness_deleted,
    ),
)


def _register(kind: _Kind) -> None:
    save_type, get_type, delete_type = kind.messages

    @hub.handles(save_type)
    async def save(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
        name = data.get("name", "")
        values = {
            field: bool(data.get(field, default)) if isinstance(default, bool) else (data.get(field) or default)
            for field, default in kind.fields.items()
        }
        text = [name, *(v for v in values.values() if isinstance(v, str))]
        if not name.strip() or not values["endpoint"].strip() or hub.fields_too_long(*text):
            return
        try:
            kind.save(name, **values)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't save {kind.label} {name!r}: {exc!r}")
            return
        print(f"[brain] saved {kind.label} {name!r}")
        if kind.reachability is not None:
            kind.reachability.pop(name, None)  # the endpoint may have changed; the next check repaints it
        await hub.broadcast(kind.changed())

    @hub.handles(get_type)
    async def get(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
        """The Edit dialog asking for what's saved, api_key included."""
        name = data.get("name", "")
        try:
            saved = kind.read(name)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't read {kind.label} {name!r}: {exc!r}")
            return
        await hub.send(websocket, kind.content(name, **{field: saved.get(field, default) for field, default in kind.fields.items()}))

    @hub.handles(delete_type)
    async def delete(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
        name = data.get("name", "")
        try:
            kind.delete(name)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't delete {kind.label} {name!r}: {exc!r}")
            return
        print(f"[brain] deleted {kind.label} {name!r}")
        if kind.reachability is not None:
            kind.reachability.pop(name, None)
        await kind.deleted(name, brain)
        await hub.broadcast(kind.changed())


for _kind in _KINDS:
    _register(_kind)


# ============================================================================
# Speech engines: switching, voices
# ============================================================================

@hub.handles(protocol.LOAD_TTS_ENGINE)
async def _load_tts_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    if not name.strip():
        return
    start = time.monotonic()
    try:
        # Off the event loop on principle -- neither engine blocks meaningfully
        # right now, but a future one might. Caught broadly (not just
        # ValueError/OSError, which build_tts already handles) since a real
        # client/library failure here could raise nearly anything.
        brain.tts = await asyncio.to_thread(build_tts, name)
    except Exception as exc:
        await hub.debug_log(websocket, "tts", f"couldn't switch to speech engine {name!r}: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] couldn't switch to speech engine {name!r}: {exc!r}")
        return
    tts_engines.set_active_engine_name(name)
    await hub.broadcast(tts_engines_message())
    await hub.debug_log(websocket, "tts", f"switched speech engine to {name!r}", (time.monotonic() - start) * 1000)
    print(f"[brain] switched speech engine to {name!r}")


def _tts_voices_message(name: str, error: str = "") -> dict:
    """Which custom voices exist for this saved engine, its current default,
    and whether creating another is possible for it (voices_dir configured).
    Empty/false across the board for NONE_NAME or a name that isn't a saved
    engine, rather than an error -- picking "None" or briefly seeing a stale
    name in the dropdown shouldn't need special-casing on the Renderer side.
    """
    try:
        engine = tts_engines.read_engine(name)
    except (ValueError, OSError):
        return protocol.tts_voices(name, [], "", False, error)
    return protocol.tts_voices(name, engine.get("custom_voices", []), engine.get("voice", ""), bool(engine.get("voices_dir")), error)


async def send_tts_voices(websocket: websockets.ServerConnection, name: str) -> None:
    await hub.send(websocket, _tts_voices_message(name))


@hub.handles(protocol.GET_TTS_VOICES)
async def _get_tts_voices(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await send_tts_voices(websocket, data.get("name", ""))


@hub.handles(protocol.SET_TTS_VOICE)
async def _set_tts_voice(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Changes a saved speech engine's default voice. If it's the active
    engine, brain.tts is rebuilt with the new voice right away; otherwise only
    the saved file changes, taking effect when it's next loaded. voice="" is a
    real value here -- it clears back to RemoteTTS.DEFAULT_VOICE, the picker's
    "Default" entry.
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
            brain.tts = await asyncio.to_thread(build_tts, name)
        except Exception as exc:
            print(f"[brain] couldn't apply new voice for speech engine {name!r}: {exc!r}")
    print(f"[brain] set speech engine {name!r} voice to {voice!r}")
    await send_tts_voices(websocket, name)


@hub.handles(protocol.COMBINE_KOKORO_VOICE)
async def _combine_kokoro_voice(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Asks the named engine's own server to blend existing voices by weight
    (kokoro_voices.combine_voices, `spec` in Kokoro's "voice1(2)+voice2(1)"
    syntax) and makes the result that engine's new default voice. Refused if
    the engine has no voices_dir (nowhere to save the result). Every refusal
    answers with a tts_voices carrying the reason -- doing nothing would look
    exactly like "worked, still shows the old voice".
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
        await hub.send(websocket, _tts_voices_message(name, error))
        return
    voices_dir = engine.get("voices_dir") or ""
    if not voices_dir:
        error = f"speech engine {name!r} has no voices folder configured"
        print(f"[brain] {error} -- refusing to create blended voice")
        await hub.debug_log(websocket, "tts", f"refused blended voice: {error}")
        await send_tts_voices(websocket, name)
        return
    try:
        voice_id = await asyncio.to_thread(
            kokoro_voices.combine_voices, engine["endpoint"], engine.get("api_key") or None, spec, display_name, voices_dir
        )
    except Exception as exc:
        error = f"couldn't create blended voice {display_name!r} ({spec}): {exc}"
        print(f"[brain] {error}")
        await hub.debug_log(websocket, "tts", error)
        await hub.send(websocket, _tts_voices_message(name, error))
        return
    tts_engines.add_custom_voice(name, voice_id)
    print(f"[brain] created blended voice {voice_id!r} ({spec}) for speech engine {name!r}")
    await _set_tts_voice(websocket, {"name": name, "voice": voice_id}, brain)


# ============================================================================
# LLM engines and harnesses: switching
# ============================================================================

@hub.handles(protocol.GET_LLM_MODELS)
async def _get_llm_models(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Fills the LLM-engine editor's model field with the endpoint's actual
    loaded models (GET /v1/models, or Ollama's own /api/tags) instead of
    leaving the user to guess a string. `endpoint` is echoed back so the
    Renderer can tell this reply apart from a stale one for an endpoint the
    user has since changed in the still-open editor.
    """
    endpoint = data.get("endpoint", "")
    if not endpoint.strip():
        return
    list_fn = list_ollama_models if data.get("provider") == "ollama" else list_models
    start = time.monotonic()
    try:
        models = await asyncio.to_thread(list_fn, endpoint, data.get("api_key") or None)
    except Exception as exc:
        await hub.debug_log(websocket, "llm", f"couldn't list models at {endpoint!r}: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] couldn't list models at {endpoint!r}: {exc!r}")
        models = []
    else:
        await hub.debug_log(websocket, "llm", f"listed {len(models)} model(s) at {endpoint!r}", (time.monotonic() - start) * 1000)
    await hub.send(websocket, protocol.llm_models(endpoint, models))


@hub.handles(protocol.LOAD_LLM_ENGINE)
async def _load_llm_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    if not name.strip():
        return
    try:
        # Cheap (a client constructor and a few small file reads) -- no model
        # loading happens here, so it stays on the event loop.
        brain.llm = build_llm(name)
    except Exception as exc:
        await hub.debug_log(websocket, "llm", f"couldn't switch to LLM engine {name!r}: {exc!r}")
        print(f"[brain] couldn't switch to LLM engine {name!r}: {exc!r}")
        return
    llm_engines.set_active_engine_name(name)
    await hub.broadcast(llm_engines_message())
    await hub.debug_log(websocket, "llm", f"switched LLM engine to {name!r}")
    print(f"[brain] switched LLM engine to {name!r}")


@hub.handles(protocol.SET_HARNESS_ACTIVE)
async def _set_harness_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Always answers with the true harness_state afterwards, success or not,
    so the Renderer's optimistic toggle can resync when a harness can't be
    reached or isn't set up."""
    name = data.get("name", "")
    if data.get("active"):
        harness_llm = build_harness_llm(name)
        if harness_llm is not None:
            brain.llm = harness_llm
            harness.set_active_harness(name)
            harness.set_selected_harness_name(name)
            await hub.debug_log(websocket, "harness", f"connected to harness {name!r}")
            print(f"[brain] plugged into harness {name!r} -- her profile/soul/LLM engine are bypassed while this is active")
        else:
            await hub.debug_log(websocket, "harness", f"couldn't activate harness {name!r} (unknown or unconfigured)")
    else:
        await hub.debug_log(websocket, "harness", "disconnected from harness")
        harness.set_active_harness("")
        brain.llm = build_llm(llm_engines.read_active_engine_name())
        print("[brain] disconnected from harness -- restored her own profile/soul/LLM engine")
    await hub.broadcast(harness_state_message())


@hub.handles(protocol.SELECT_HARNESS)
async def _select_harness(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Just moves the dropdown's pick -- no connection attempt. Only meaningful
    while no harness is active (the Renderer disables the dropdown while one
    is), and lets picking None stick across a refresh."""
    name = data.get("name", "")
    harness.set_selected_harness_name("" if name == harness.NONE_NAME else name)
    await hub.broadcast(harness_state_message())


# ============================================================================
# Role-play's own engine
# ============================================================================

@hub.handles(protocol.SET_ROLEPLAY_ENGINE)
async def _set_roleplay_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    try:
        llm_engines.set_roleplay_engine(str(data.get("name") or ""))
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't set the role-play engine: {exc!r}")
    await hub.broadcast(protocol.roleplay_engine(llm_engines.read_roleplay_engine()))


def switch_to_roleplay_engine(name: str, think: bool, brain: Brain, was_active: bool) -> None:
    """Switches her to the role-play engine, recording where she was (so turning
    role-play off can put her back) and the think choice, which build_llm
    applies -- the engine's saved settings are left alone. think only takes
    effect on an Ollama-provider engine, the one kind that can reliably turn
    thinking off (see OllamaLLM's docstring). Leaves her on her current engine
    if the role-play one can't be read or built.
    """
    try:
        llm_engines.read_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] role-play wanted the {name!r} LLM engine but couldn't read it: {exc!r}")
        return
    if was_active:  # toggled on again mid-role-play: she already knows where to go back to
        previous = llm_engines.read_roleplay_session().get("previous", "")
    else:
        current = llm_engines.read_active_engine_name()
        previous = "" if current == name else current  # already on it: nothing different to go back to
    llm_engines.start_roleplay_session(previous, name, think)
    try:
        brain.llm = build_llm(name)
    except Exception as exc:
        print(f"[brain] couldn't switch to {name!r} for role-play: {exc!r}")
        return
    llm_engines.set_active_engine_name(name)
    print(f"[brain] role-play switched LLM engine to {name!r} (think={think})")


def end_roleplay_engine(brain: Brain) -> None:
    """If role-play switched engines (see switch_to_roleplay_engine): puts her
    back on the engine she was using before, or rebuilds the role-play engine
    without role-play's think choice if that's where she was all along. With no
    session -- role-play kept her current engine -- nothing changes.
    """
    session = llm_engines.end_roleplay_session()
    if not session:
        return
    previous = session.get("previous", "")
    roleplay_engine = session.get("engine", "")
    if previous and previous != llm_engines.NONE_NAME:
        try:
            llm_engines.read_engine(previous)
        except (ValueError, OSError):
            print(f"[brain] the engine used before role-play ({previous!r}) is gone -- staying where she is")
            previous = ""
    target = previous or llm_engines.read_active_engine_name()
    if not previous and target != roleplay_engine:
        return  # nothing to switch back to, and nothing to rebuild
    if harness.read_active_harness():
        llm_engines.set_active_engine_name(target)  # a harness is in charge; applied when it's turned off
        return
    try:
        brain.llm = build_llm(target)
    except Exception as exc:
        print(f"[brain] couldn't switch back to {target!r} after role-play: {exc!r}")
        return
    llm_engines.set_active_engine_name(target)
    print(f"[brain] role-play off -- back on LLM engine {target!r}")


# ============================================================================
# Sampling profiles
# ============================================================================

def sampling_state_message(error: str = "") -> dict:
    return protocol.sampling_state(
        sampling.read_active_name(), sampling.list_profiles(), list(sampling.BUILTIN_NAMES), error
    )


@hub.handles(protocol.SET_SAMPLING_PROFILE, protocol.SAVE_SAMPLING_PROFILE, protocol.DELETE_SAMPLING_PROFILE)
async def _sampling_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Pick, save or delete a sampling profile (Settings -> LLM). A refused
    change (a bad number, a built-in name) goes back only to the device that
    asked, with the reason; a real change goes to every device.
    """
    name = data.get("name")
    try:
        if not isinstance(name, str):
            raise ValueError("a sampling profile needs a name")
        if data["type"] == protocol.SET_SAMPLING_PROFILE:
            sampling.set_active(name)
        elif data["type"] == protocol.SAVE_SAMPLING_PROFILE:
            values = data.get("values")
            sampling.save_profile(name, values if isinstance(values, dict) else {})
        else:
            sampling.delete_profile(name)
    except (ValueError, OSError) as exc:
        await hub.send(websocket, sampling_state_message(str(exc)))
        return
    print(f"[brain] sampling profile now {sampling.read_active_name()!r}: {sampling.active_values()}")
    await hub.broadcast(sampling_state_message())
