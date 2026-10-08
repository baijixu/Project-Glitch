"""Everything a newly connected device needs, sent in answer to its `ready`."""

import base64

import websockets

import avatars
import characters
import curiosity
import engines
import health
import journal
import hub
import learning
import llm_engines
import memory
import profiles
import protocol
import reach_out
import reply
import tts_engines
import voice_settings
import web_search
from hub import Brain


@hub.handles(protocol.READY)
async def handle_ready(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    device = data.get("device")
    if isinstance(device, str) and device.strip():
        hub.DEVICE_NAMES[websocket] = device.strip()[:hub.MAX_DEVICE_NAME_CHARS]
    print(f"[brain] renderer ready: {hub.device_label(websocket)}")
    messages = [
        characters.profiles_message(),
        characters.souls_message(),
        characters.topics_message(),
        protocol.avatars(avatars.list_avatars()),
        *characters.background_messages(),
        protocol.roleplay_state(profiles.read_roleplay_active()),
        protocol.roleplay_engine(llm_engines.read_roleplay_engine()),
        protocol.voice_state(voice_settings.read_voice_active()),
        protocol.web_search_state(web_search.read_active()),
        protocol.curiosity_state(curiosity.read_active()),
        reach_out.curiosity_timer_message(),
        engines.sampling_state_message(),
        *([reply.LAST_CONTEXT_USAGE] if reply.LAST_CONTEXT_USAGE else []),
        *([protocol.brain_status(journal.STATUS)] if journal.STATUS else []),
        learning.training_state_message(),
        protocol.memory_state(memory.read_memory_active()),
        learning.memory_profiles_message(),
    ]
    for message in messages:
        await hub.send(websocket, message)
    # Its own task -- fetching lessons is a network call to Hindsight, and an
    # unreachable server shouldn't hold up the rest of the ready handshake
    # (including the avatar_data that signals it's finished).
    hub.spawn(learning.send_lessons_state(websocket))
    await hub.send(websocket, engines.tts_engines_message())
    await engines.send_tts_voices(websocket, tts_engines.read_active_engine_name())
    await hub.send(websocket, engines.llm_engines_message())
    await hub.send(websocket, engines.harness_state_message())
    await health.send_health(websocket)
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
            await hub.send(websocket, protocol.avatar_data(active_avatar, data_b64, kind))
