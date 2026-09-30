"""Settings -> Role-play, Soul and Avatar: saved role-play profiles and souls,
her main soul.md/user.md, notes, avatars and chat logs, and the role-play toggle.
"""

import base64
import json

import websockets

import avatars
import conversation
import engines
import hub
import llm_engines
import notes
import persona
import profiles
import protocol
import souls
from hub import Brain

async def handle_save_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    content = data.get("content", "")
    if not name.strip() or not content.strip() or hub.fields_too_long(name, content):
        return
    try:
        profiles.save_profile(name, content)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save profile {name!r}: {exc!r}")
        return
    print(f"[brain] saved profile {name!r}")
    await hub.broadcast(protocol.profiles(profiles.list_profiles(), profiles.read_active_profile_name()))


async def handle_get_profile(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    try:
        content = profiles.read_profile(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read profile {name!r}: {exc!r}")
        return
    await websocket.send(json.dumps(protocol.profile_content(name, content)))


async def handle_delete_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        profiles.delete_profile(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete profile {name!r}: {exc!r}")
        return
    print(f"[brain] deleted profile {name!r}")
    # The profile that was active just got deleted out from under it --
    # fall back to DEFAULT_PROFILE_NAME rather than leaving rp_user.md/the
    # LLM's persona pointing at a file that no longer exists.
    if profiles.read_active_profile_name() == name:
        content = profiles.load_profile(profiles.DEFAULT_PROFILE_NAME)
        if profiles.read_roleplay_active():
            brain.llm.set_persona(content)
        print(f"[brain] active profile was deleted -- reset to {profiles.DEFAULT_PROFILE_NAME!r}")
    await hub.broadcast(protocol.profiles(profiles.list_profiles(), profiles.read_active_profile_name()))


async def handle_save_soul(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    description = data.get("description", "")
    examples = data.get("examples", "")
    if not name.strip() or not description.strip() or hub.fields_too_long(name, description, examples):
        return
    try:
        souls.save_soul(name, description, examples)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save soul {name!r}: {exc!r}")
        return
    print(f"[brain] saved soul {name!r}")
    await hub.broadcast(protocol.souls(souls.list_souls(), souls.read_active_soul_name()))


def handle_load_soul(data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        content = souls.load_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load soul {name!r}: {exc!r}")
        return
    if profiles.read_roleplay_active():
        # Not `content` directly: loading "Default" clears the RP soul,
        # which means "no RP soul -- use her main soul", not "no soul".
        brain.llm.set_soul(persona.effective_soul())
        print(f"[brain] loaded soul {name!r}")
    else:
        # Still recorded above (in rp_soul.md, never her main soul.md) -- just
        # not applied while role-play is off, same as handle_load_profile, and
        # the saved scene (with the old character) isn't resumed.
        conversation.clear_state(conversation.ROLEPLAY)
        print(f"[brain] selected soul {name!r} (role-play is off, not applied)")


async def handle_get_soul(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    try:
        description, examples = souls.read_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read soul {name!r}: {exc!r}")
        return
    await websocket.send(json.dumps(protocol.soul_content(name, description, examples)))


def handle_save_notes(data: dict) -> None:
    content = data.get("content", "")
    if hub.fields_too_long(content):
        return
    notes.write_notes(content)


def handle_save_soul_and_user(data: dict, brain: Brain) -> None:
    """Manual-edit escape hatch (Settings' soul/user editor) -- writes her
    MAIN soul (soul.md) and main user.md directly, bypassing the named saved
    soul/profile system entirely. This is the only thing that ever writes
    either file (see souls.py/profiles.py); the saved role-play souls and
    profiles can't. The live LLM is re-primed to match her main soul, which
    applies whenever role-play is off (or on with no RP soul selected). The
    role-play profile is a separate file (rp_user.md) this never touches.
    With a harness active the files are still saved for whenever it's turned
    back off, just not applied to anything now.
    """
    soul_content = data.get("soul", "")
    user_content = data.get("user", "")
    if hub.fields_too_long(soul_content, user_content):
        return
    souls.write_main_soul(soul_content)
    profiles.write_main_user(user_content)
    brain.llm.update_soul(persona.effective_soul())  # an edit, not a new her: the conversation carries on
    print("[brain] soul.md/user.md updated via manual editor")


async def handle_delete_soul(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        souls.delete_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete soul {name!r}: {exc!r}")
        return
    print(f"[brain] deleted soul {name!r}")
    # Same reasoning as handle_delete_profile: don't leave rp_soul.md/the
    # LLM pointing at a soul that no longer exists.
    if souls.read_active_soul_name() == name:
        souls.load_soul(souls.DEFAULT_SOUL_NAME)
        if profiles.read_roleplay_active():
            brain.llm.set_soul(persona.effective_soul())
        print(f"[brain] active soul was deleted -- reset to {souls.DEFAULT_SOUL_NAME!r}")
    await hub.broadcast(protocol.souls(souls.list_souls(), souls.read_active_soul_name()))


async def handle_save_avatar(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    data_b64 = data.get("data_b64", "")
    kind = data.get("kind", "vrm")
    # Only the name is length-checked here, not data_b64 (the avatar's raw
    # bytes) -- that's a real, deliberately large payload already bounded
    # by websockets.serve's own max_size in main() below, not a free-text
    # field this cap is meant for. `kind` is checked against
    # avatars.AVATAR_KINDS rather than trusted outright -- avatars.save_avatar
    # already re-validates it too (defense in depth, not redundant: this
    # check just avoids doing a base64 decode for a request that's going to
    # be rejected anyway).
    if not name.strip() or not data_b64 or hub.fields_too_long(name) or kind not in avatars.AVATAR_KINDS:
        return
    try:
        avatar_bytes = base64.b64decode(data_b64)
        avatars.save_avatar(name, avatar_bytes, kind)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save avatar {name!r}: {exc!r}")
        return
    avatars.set_active_avatar(name)
    print(f"[brain] saved and activated avatar {name!r} ({kind}, {len(avatar_bytes)} bytes)")
    await websocket.send(json.dumps(protocol.avatars(avatars.list_avatars())))


async def handle_load_avatar(websocket: websockets.ServerConnection, data: dict) -> None:
    name = data.get("name", "")
    if not name.strip():
        return
    avatars.set_active_avatar(name)
    if name == avatars.DEFAULT_AVATAR_NAME:
        # Bookkeeping only -- the Renderer already knows how to load the
        # shipped default locally, no bytes to send.
        print(f"[brain] activated default avatar {name!r}")
        return
    try:
        avatar_bytes, kind = avatars.read_avatar(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load avatar {name!r}: {exc!r}")
        return
    print(f"[brain] activated avatar {name!r} ({kind}), sending {len(avatar_bytes)} bytes")
    data_b64 = base64.b64encode(avatar_bytes).decode("ascii")
    await websocket.send(json.dumps(protocol.avatar_data(name, data_b64, kind)))


async def handle_manage_avatar(websocket: websockets.ServerConnection, msg_type: str, data: dict) -> None:
    """Rename or delete a saved avatar (Settings -> Avatar's ✏️/🗑️). Every device
    gets the new list; one that was showing the avatar updates its name (rename)
    or goes back to the built-in Glitch (delete). A refused change (the built-in
    avatar, a name already taken) just re-sends the unchanged list to the asker.
    """
    name = str(data.get("name") or "")
    try:
        if msg_type == protocol.RENAME_AVATAR:
            new_name = str(data.get("new_name") or "")
            if hub.fields_too_long(new_name):
                raise ValueError("that name is too long")
            saved = avatars.rename_avatar(name, new_name)
            print(f"[brain] renamed avatar {name!r} to {saved!r}")
            await hub.broadcast(protocol.avatars_changed(avatars.list_avatars(), renamed={"from": name, "to": saved}))
        else:
            avatars.delete_avatar(name)
            print(f"[brain] deleted avatar {name!r}")
            await hub.broadcast(protocol.avatars_changed(avatars.list_avatars(), deleted=name))
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't {'rename' if msg_type == protocol.RENAME_AVATAR else 'delete'} avatar {name!r}: {exc!r}")
        await websocket.send(json.dumps(protocol.avatars(avatars.list_avatars())))


async def handle_chat_logs_message(websocket: websockets.ServerConnection, msg_type: str, data: dict) -> None:
    """Settings -> Chat Logs: list a mode's days, read one, or delete one. Only
    her daily logs in brain/chat_logs/ (regular) and chat_logs/roleplay/ -- the
    file is picked by mode and a plain date, never by a path from the Renderer.
    A deletion goes to every device, so their lists stay in step.
    """
    mode = conversation.ROLEPLAY if data.get("mode") == conversation.ROLEPLAY else conversation.MAIN
    day = str(data.get("date") or "")
    if msg_type == protocol.GET_CHAT_LOGS:
        await websocket.send(json.dumps(protocol.chat_logs(mode, conversation.list_logs(mode))))
    elif msg_type == protocol.GET_CHAT_LOG:
        try:
            content, error = conversation.read_log(mode, day), ""
        except (ValueError, OSError) as exc:
            content, error = "", str(exc)
        await websocket.send(json.dumps(protocol.chat_log_content(mode, day, content, error)))
    else:
        try:
            conversation.delete_log(mode, day)
            print(f"[brain] deleted the {mode} chat log for {day}")
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't delete the {mode} chat log for {day}: {exc!r}")
        await hub.broadcast(protocol.chat_logs(mode, conversation.list_logs(mode)))


def handle_load_profile(data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        content = profiles.load_profile(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load profile {name!r}: {exc!r}")
        return
    if profiles.read_roleplay_active():
        brain.llm.set_persona(content)
        print(f"[brain] loaded profile {name!r}")
    else:
        # Still recorded in rp_user.md above -- just not applied to the LLM
        # while role-play is toggled off (see handle_set_roleplay_active). The
        # saved scene was with a different character, so it isn't resumed.
        conversation.clear_state(conversation.ROLEPLAY)
        print(f"[brain] selected profile {name!r} (role-play is off, not applied)")


def handle_set_roleplay_active(data: dict, brain: Brain) -> None:
    """The role-play toggle. On: if a role-play engine is set (Settings), remembers
    her current engine and switches to it, with thinking as the Renderer's confirm
    dialog chose -- otherwise she stays on her current engine -- and resumes the
    last role-play scene. Off: puts her back on the engine she had before, if role-
    play switched, and brings the normal conversation back. The two conversations
    are saved separately (brain/conversation.py), so neither wipes the other.
    """
    active = bool(data.get("active"))
    was_active = profiles.read_roleplay_active()
    profiles.set_roleplay_active(active)
    if active:
        # The Renderer sends think only when a role-play engine is set (from its
        # confirm dialog). No engine set, or no think: she stays where she is.
        think = data.get("think")
        target = llm_engines.read_roleplay_engine()
        if target and think is not None:
            engines.switch_to_roleplay_engine(target, bool(think), brain, was_active)
        content = profiles.read_active_profile()
        persona.apply_conversation_mode(brain, persona=content)
        print(f"[brain] role-play activated{' with the selected profile' if content else ' (no profile selected yet)'}")
    else:
        # If role-play switched engines: back to the engine she had before.
        engines.end_roleplay_engine(brain)
        persona.apply_conversation_mode(brain, persona="")  # the flag is already off, so this is her main soul
        print("[brain] role-play deactivated -- back to her main soul and her normal conversation")
