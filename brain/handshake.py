"""Everything a newly connected device needs, sent in answer to its `ready`."""

import base64
import json

import websockets

import avatars
import curiosity
import engines
import health
import hub
import learning
import llm_engines
import memory
import profiles
import protocol
import reach_out
import reply
import souls
import tts_engines
import voice_settings
import web_search

async def handle_ready(websocket: websockets.ServerConnection, data: dict) -> None:
    device = data.get("device")
    if isinstance(device, str) and device.strip():
        hub.DEVICE_NAMES[websocket] = device.strip()[:hub.MAX_DEVICE_NAME_CHARS]
    print(f"[brain] renderer ready: {hub.device_label(websocket)}")
    await websocket.send(json.dumps(protocol.profiles(profiles.list_profiles(), profiles.read_active_profile_name())))
    await websocket.send(json.dumps(protocol.souls(souls.list_souls(), souls.read_active_soul_name())))
    await websocket.send(json.dumps(protocol.avatars(avatars.list_avatars())))
    await websocket.send(json.dumps(protocol.roleplay_state(profiles.read_roleplay_active())))
    await websocket.send(json.dumps(protocol.roleplay_engine(llm_engines.read_roleplay_engine())))
    await websocket.send(json.dumps(protocol.voice_state(voice_settings.read_voice_active())))
    await websocket.send(json.dumps(protocol.web_search_state(web_search.read_active())))
    await websocket.send(json.dumps(protocol.curiosity_state(curiosity.read_active())))
    await websocket.send(json.dumps(reach_out.curiosity_timer_message()))
    await websocket.send(json.dumps(engines.sampling_state_message()))
    if reply.LAST_CONTEXT_USAGE:
        await websocket.send(json.dumps(reply.LAST_CONTEXT_USAGE))
    await websocket.send(json.dumps(learning.training_state_message()))
    await websocket.send(json.dumps(protocol.memory_state(memory.read_memory_active())))
    await websocket.send(json.dumps(learning.memory_profiles_message()))
    # Its own task -- fetching lessons is a network call to Hindsight, and an
    # unreachable server shouldn't hold up the rest of the ready handshake
    # (including the avatar_data that signals it's finished).
    hub.spawn(learning.send_lessons_state(websocket))
    await websocket.send(json.dumps(protocol.tts_engines(tts_engines.list_engines(), tts_engines.read_active_engine_name())))
    await engines.send_tts_voices(websocket, tts_engines.read_active_engine_name())
    await websocket.send(json.dumps(protocol.llm_engines(llm_engines.list_engines(), llm_engines.read_active_engine_name())))
    await engines.send_harness_state(websocket)
    await health.send_harness_health(websocket)
    await health.send_tts_health(websocket)
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
