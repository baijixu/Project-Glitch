"""Settings -> LLM, Voice and Harness: building her LLM and speech engine
from the saved ones, switching between them (role-play included), and the
editors for each.
"""

import asyncio
import json
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
from voice import NoneTTS, RemoteTTS

# config.yaml's brain.llm block, captured once at startup (main()) -- kept
# reachable here (not just a local in main()) so build_llm can rebuild
# llm_engines.NONE_NAME on a live load_llm_engine switch too, the same way
# main()'s own startup does, without needing config.yaml re-read from disk.
# Optional now (brain.llm itself, and every key inside it) -- an empty
# dict here (nothing in config.yaml at all) means build_llm falls back to
# NoneLLM rather than crashing on a missing endpoint, see its own comment.
DEFAULT_LLM_CONFIG: dict = {}


async def send_harness_state(websocket: websockets.ServerConnection) -> None:
    """Sends harness_state to one device (a newly connected one, handshake.handle_ready).
    After a change, every device gets it instead: hub.broadcast(_harness_state_message()).
    """
    await websocket.send(json.dumps(_harness_state_message()))


def _harness_state_message() -> dict:
    """The current harness_state -- reads harness.py's own saved
    active-harness file and saved-harness list fresh each call (not a
    cached value) so it's always accurate regardless of what just changed
    it. Shared by every handler that can change whether/which harness is
    active or the saved list itself (handshake.handle_ready,
    handle_set_harness_active, handle_save_harness,
    handle_delete_harness), so all of them stay in sync by construction
    instead of by copy-pasted agreement.
    """
    active_harness_name = harness.read_active_harness()
    # While active, the selected one is by definition the active one --
    # only fall back to the separately-persisted "last selected" record
    # while inactive, see protocol.py's harness_state docstring.
    selected_harness_name = active_harness_name or harness.read_selected_harness_name()
    return protocol.harness_state(
        bool(active_harness_name),
        active_harness_name,
        selected_harness_name,
        harness.list_harnesses(),
    )


async def handle_save_tts_engine(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    endpoint = data.get("endpoint", "")
    api_key = data.get("api_key", "")
    voice = data.get("voice", "")
    voices_dir = data.get("voices_dir", "")
    model = data.get("model", "")
    if not name.strip() or not endpoint.strip() or hub.fields_too_long(name, endpoint, api_key, voice, voices_dir, model):
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
    health.LAST_TTS_REACHABLE.pop(name, None)
    await hub.broadcast(protocol.tts_engines(tts_engines.list_engines(), tts_engines.read_active_engine_name()))


async def handle_get_tts_engine(websocket: websockets.ServerConnection, data: dict) -> None:
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


async def handle_load_tts_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    if not name.strip():
        return
    start = time.monotonic()
    try:
        # Off the event loop on principle, same as build_llm's own
        # to_thread call -- neither engine actually blocks meaningfully
        # right now, but a future one might. Caught broadly (not just
        # ValueError/OSError, which build_tts already handles internally)
        # since a real client/library failure here could raise nearly
        # anything -- same reasoning as reply.reply_to's broad guard around
        # brain.llm.reply/brain.tts.synthesize.
        brain.tts = await asyncio.to_thread(build_tts, name)
    except Exception as exc:
        await hub.debug_log(websocket, "tts", f"couldn't switch to speech engine {name!r}: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] couldn't switch to speech engine {name!r}: {exc!r}")
        return
    tts_engines.set_active_engine_name(name)
    await hub.broadcast(protocol.tts_engines(tts_engines.list_engines(), name))
    await hub.debug_log(websocket, "tts", f"switched speech engine to {name!r}", (time.monotonic() - start) * 1000)
    print(f"[brain] switched speech engine to {name!r}")


async def handle_delete_tts_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        tts_engines.delete_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete speech engine {name!r}: {exc!r}")
        return
    print(f"[brain] deleted speech engine {name!r}")
    health.LAST_TTS_REACHABLE.pop(name, None)
    # Same reasoning as characters.handle_delete_profile/characters.handle_delete_soul: don't
    # leave brain.tts pointed at an engine config that no longer exists.
    if tts_engines.read_active_engine_name() == name:
        try:
            brain.tts = await asyncio.to_thread(build_tts, tts_engines.NONE_NAME)
        except Exception as exc:
            print(f"[brain] couldn't fall back to {tts_engines.NONE_NAME!r} after deleting {name!r}: {exc!r}")
        else:
            tts_engines.set_active_engine_name(tts_engines.NONE_NAME)
            print(f"[brain] active speech engine was deleted -- reset to {tts_engines.NONE_NAME!r}")
    await hub.broadcast(protocol.tts_engines(tts_engines.list_engines(), tts_engines.read_active_engine_name()))


async def send_tts_voices(websocket: websockets.ServerConnection, name: str) -> None:
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


async def handle_get_tts_voices(websocket: websockets.ServerConnection, data: dict) -> None:
    await send_tts_voices(websocket, data.get("name", ""))


async def handle_set_tts_voice(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Changes a saved speech engine's default voice -- reuses build_tts
    the same way handle_load_tts_engine does, so if this engine happens
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
            brain.tts = await asyncio.to_thread(build_tts, name)
        except Exception as exc:
            print(f"[brain] couldn't apply new voice for speech engine {name!r}: {exc!r}")
    print(f"[brain] set speech engine {name!r} voice to {voice!r}")
    await send_tts_voices(websocket, name)


async def handle_combine_kokoro_voice(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Asks the named engine's own server to blend existing voices by
    weight (kokoro_voices.py's combine_voices, `spec` in Kokoro's own
    "voice1(2)+voice2(1)" syntax) and makes the result that engine's new
    default voice -- see handle_set_tts_voice for why that's safe to do
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
        await websocket.send(json.dumps(protocol.tts_voices(name, engine.get("custom_voices", []), engine.get("voice", ""), True, error)))
        return
    tts_engines.add_custom_voice(name, voice_id)
    print(f"[brain] created blended voice {voice_id!r} ({spec}) for speech engine {name!r}")
    await handle_set_tts_voice(websocket, {"name": name, "voice": voice_id}, brain)


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
    return RemoteTTS(
        engine["endpoint"],
        engine.get("api_key") or None,
        engine.get("voice") or RemoteTTS.DEFAULT_VOICE,
        engine.get("model") or RemoteTTS.DEFAULT_MODEL,
    )


async def handle_save_llm_engine(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    endpoint = data.get("endpoint", "")
    model = data.get("model", "")
    api_key = data.get("api_key", "")
    provider = data.get("provider") or "openai"
    think = bool(data.get("think", False))
    if not name.strip() or not endpoint.strip() or hub.fields_too_long(name, endpoint, model, api_key, provider):
        return
    try:
        llm_engines.save_engine(name, endpoint, model, api_key, provider=provider, think=think)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save LLM engine {name!r}: {exc!r}")
        return
    print(f"[brain] saved LLM engine {name!r}")
    await hub.broadcast(protocol.llm_engines(llm_engines.list_engines(), llm_engines.read_active_engine_name()))


async def handle_get_llm_engine(websocket: websockets.ServerConnection, data: dict) -> None:
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


async def handle_get_llm_models(websocket: websockets.ServerConnection, data: dict) -> None:
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
        await hub.debug_log(websocket, "llm", f"couldn't list models at {endpoint!r}: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] couldn't list models at {endpoint!r}: {exc!r}")
        models = []
    else:
        await hub.debug_log(websocket, "llm", f"listed {len(models)} model(s) at {endpoint!r}", (time.monotonic() - start) * 1000)
    await websocket.send(json.dumps(protocol.llm_models(endpoint, models)))


async def handle_load_llm_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    if not name.strip():
        return
    try:
        # Building a LocalLLM is cheap (an OpenAI client constructor + a
        # couple small local file reads) -- no real model-load work
        # happens here, so this stays sync rather than needing
        # asyncio.to_thread the way handle_load_tts_engine does.
        brain.llm = build_llm(name)
    except Exception as exc:
        await hub.debug_log(websocket, "llm", f"couldn't switch to LLM engine {name!r}: {exc!r}")
        print(f"[brain] couldn't switch to LLM engine {name!r}: {exc!r}")
        return
    llm_engines.set_active_engine_name(name)
    await hub.broadcast(protocol.llm_engines(llm_engines.list_engines(), name))
    await hub.debug_log(websocket, "llm", f"switched LLM engine to {name!r}")
    print(f"[brain] switched LLM engine to {name!r}")


async def handle_delete_llm_engine(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        llm_engines.delete_engine(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete LLM engine {name!r}: {exc!r}")
        return
    print(f"[brain] deleted LLM engine {name!r}")
    # Same reasoning as handle_delete_tts_engine: don't leave brain.llm
    # pointed at an engine config that no longer exists.
    if llm_engines.read_active_engine_name() == name:
        try:
            brain.llm = build_llm(llm_engines.NONE_NAME)
        except Exception as exc:
            print(f"[brain] couldn't fall back to {llm_engines.NONE_NAME!r} after deleting {name!r}: {exc!r}")
        else:
            llm_engines.set_active_engine_name(llm_engines.NONE_NAME)
            print(f"[brain] active LLM engine was deleted -- reset to {llm_engines.NONE_NAME!r}")
    await hub.broadcast(protocol.llm_engines(llm_engines.list_engines(), llm_engines.read_active_engine_name()))


def build_llm(name: str) -> ChatBackend:
    """LocalLLM (or OllamaLLM, see below) built from config.yaml's brain.llm
    block (DEFAULT_LLM_CONFIG) for llm_engines.NONE_NAME, or a saved
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


async def handle_set_harness_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Always replies with the true post-attempt harness_state, success or
    failure -- this used to be silent (no reply at all), which meant the
    Renderer's own optimistic toggle just stayed wrong forever if
    build_harness_llm returned None (unknown/unconfigured harness). A
    reply the Renderer can resync from closes that gap.
    """
    active = bool(data.get("active"))
    name = data.get("name", "")
    if active:
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

    await hub.broadcast(_harness_state_message())


async def handle_select_harness(websocket: websockets.ServerConnection, data: dict) -> None:
    """Just moves the Renderer's dropdown pick -- no connection attempt,
    no switch key needed (that's still only enforced by
    handle_set_harness_active). Only meaningful while inactive (the
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
    await hub.broadcast(_harness_state_message())


async def handle_save_harness(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    endpoint = data.get("endpoint", "")
    model = data.get("model", "")
    api_key = data.get("api_key", "")
    if not name.strip() or not endpoint.strip() or hub.fields_too_long(name, endpoint, model, api_key):
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
    health.LAST_HARNESS_REACHABLE.pop(name, None)
    await hub.broadcast(_harness_state_message())


async def handle_get_harness(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    try:
        h = harness.read_harness(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read harness {name!r}: {exc!r}")
        return
    await websocket.send(
        json.dumps(protocol.harness_content(name, h["endpoint"], h.get("model", ""), h.get("api_key", "")))
    )


async def handle_delete_harness(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        harness.delete_harness(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete harness {name!r}: {exc!r}")
        return
    print(f"[brain] deleted harness {name!r}")
    health.LAST_HARNESS_REACHABLE.pop(name, None)
    # Same reasoning as deleting the active LLM/TTS engine -- don't leave
    # Brain plugged into (or configured to reconnect to) a harness that no
    # longer exists.
    if harness.read_active_harness() == name:
        harness.set_active_harness("")
        brain.llm = build_llm(llm_engines.read_active_engine_name())
        print(f"[brain] active harness {name!r} was deleted -- disconnected, restored her own profile/soul/LLM engine")
    # Same reasoning, for the separately-persisted dropdown pick -- don't
    # leave it pointing at a harness that no longer exists.
    if harness.read_selected_harness_name() == name:
        harness.set_selected_harness_name("")
    await hub.broadcast(_harness_state_message())


def sampling_state_message(error: str = "") -> dict:
    return protocol.sampling_state(
        sampling.read_active_name(), sampling.list_profiles(), list(sampling.BUILTIN_NAMES), error
    )


async def handle_sampling_message(websocket: websockets.ServerConnection, msg_type: str, data: dict) -> None:
    """Pick, save or delete a sampling profile (Settings -> LLM). A refused
    change (a bad number, a built-in name) goes back only to the device that
    asked, with the reason; a real change goes to every device.
    """
    name = data.get("name")
    try:
        if not isinstance(name, str):
            raise ValueError("a sampling profile needs a name")
        if msg_type == protocol.SET_SAMPLING_PROFILE:
            sampling.set_active(name)
        elif msg_type == protocol.SAVE_SAMPLING_PROFILE:
            values = data.get("values")
            sampling.save_profile(name, values if isinstance(values, dict) else {})
        else:
            sampling.delete_profile(name)
    except (ValueError, OSError) as exc:
        await websocket.send(json.dumps(sampling_state_message(str(exc))))
        return
    print(f"[brain] sampling profile now {sampling.read_active_name()!r}: {sampling.active_values()}")
    await hub.broadcast(sampling_state_message())


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
