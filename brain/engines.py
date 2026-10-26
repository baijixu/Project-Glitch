"""Settings -> LLM, Voice and Harness: building her LLM and speech engine
from the saved ones, switching between them (role-play included), and the
editors for each.
"""

import asyncio
import time

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
from saved_items import Kind, register
from voice import NoneTTS, RemoteTTS


# ============================================================================
# Building her LLM and speech engine
# ============================================================================

def build_llm(name: str) -> ChatBackend:
    """A LocalLLM or OllamaLLM from a saved engine, given the active soul and profile and
    the saved conversation. A NoneLLM for NONE_NAME or an engine that can't be read.
    """
    try:
        config = {} if name == llm_engines.NONE_NAME else llm_engines.read_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load LLM engine {name!r}: {exc!r}")
        config = {}
    if not config.get("endpoint"):
        print(f"[brain] no LLM engine configured ({name!r} has no endpoint) -- add one in Settings under LLM")
        return NoneLLM()
    print(f"[brain] using LLM engine {name!r} at {config['endpoint']!r}")
    # The conversation is saved on every change, so a restart or an engine
    # switch picks it back up (restore_history below) instead of wiping it.
    connection = {
        "endpoint": config["endpoint"],
        "model": config.get("model") or None,
        "api_key": config.get("api_key") or None,
        "on_history_change": lambda history: conversation.save_state(history, persona.conversation_mode()),
    }
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

    # update_*, not set_*: those start a fresh conversation, which would save an empty one
    # over the one restored just after.
    llm.update_soul(persona.effective_soul())
    if profiles.read_roleplay_active():
        llm.update_persona(profiles.read_active_profile())
    llm.restore_history(conversation.load_state(persona.conversation_mode()))
    return llm


def build_harness_llm(name: str) -> HarnessLLM | None:
    """A HarnessLLM for the named saved harness, or None if it can't be read (callers then
    fall back to build_llm)."""
    try:
        config = harness.read_harness(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't activate unknown/unreadable harness {name!r}: {exc!r}")
        return None
    if not config.get("endpoint"):
        print(f"[brain] couldn't activate harness {name!r}: no endpoint saved")
        return None
    print(f"[brain] using harness {name!r} at {config['endpoint']!r}")
    # The same harness conversation across messages and restarts.
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
    """A RemoteTTS for a saved engine; NoneTTS for NONE_NAME or one that can't be read."""
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
    """The current harness_state: the active harness, or the last one picked while none is."""
    active = harness.read_active_harness()
    return protocol.harness_state(bool(active), active, active or harness.read_selected_harness_name(), harness.list_harnesses())


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


for _kind in (
    Kind(
        label="speech engine",
        messages=(protocol.SAVE_TTS_ENGINE, protocol.GET_TTS_ENGINE, protocol.DELETE_TTS_ENGINE),
        fields={"endpoint": "", "api_key": "", "voice": "", "voices_dir": "", "model": ""}, required="endpoint",
        save=tts_engines.save_engine, read=tts_engines.read_engine, delete=tts_engines.delete_engine,
        content=protocol.tts_engine_content, changed=tts_engines_message,
        deleted=_tts_engine_deleted, reachability=health.LAST_TTS_REACHABLE,
    ),
    Kind(
        label="LLM engine",
        messages=(protocol.SAVE_LLM_ENGINE, protocol.GET_LLM_ENGINE, protocol.DELETE_LLM_ENGINE),
        fields={"endpoint": "", "model": "", "api_key": "", "provider": "openai", "think": False}, required="endpoint",
        save=llm_engines.save_engine, read=llm_engines.read_engine, delete=llm_engines.delete_engine,
        content=protocol.llm_engine_content, changed=llm_engines_message, deleted=_llm_engine_deleted,
    ),
    Kind(
        label="harness",
        messages=(protocol.SAVE_HARNESS, protocol.GET_HARNESS, protocol.DELETE_HARNESS),
        fields={"endpoint": "", "model": "", "api_key": ""}, required="endpoint",
        save=harness.save_harness, read=harness.read_harness, delete=harness.delete_harness,
        content=protocol.harness_content, changed=harness_state_message,
        deleted=_harness_deleted, reachability=health.LAST_HARNESS_REACHABLE,
    ),
):
    register(_kind)


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
    """A saved engine's custom voices, its default, and whether it can make more (a
    voices_dir). Empty for NONE_NAME or an unknown name, not an error."""
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
    """A saved speech engine's default voice -- right away if it's the active one. voice=""
    is the picker's "Default" (RemoteTTS.DEFAULT_VOICE)."""
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
    """Blends voices on the engine's own server (`spec` like "voice1(2)+voice2(1)") and makes
    the result its default voice. Needs a voices_dir; a refusal answers with the reason."""
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
    """The endpoint's loaded models for the LLM-engine editor. `endpoint` is echoed back so
    an answer for an endpoint since changed in the editor can be told apart."""
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
    """Always answers with the true harness_state, so a toggle that failed flips back."""
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
    """Moves the dropdown's pick while no harness is active -- no connection attempt."""
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
    """Switches her to the role-play engine, remembering where she was and the think choice
    (Ollama engines only). Stays put if the role-play engine can't be built.
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
    """Puts her back on the engine she had before role-play switched it, if it did."""
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
