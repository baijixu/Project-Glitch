"""Settings -> Role-play, Soul, Avatar and Background: saved role-play profiles and
souls, her main soul.md/user.md, notes, avatars, backgrounds and chat logs, and the
role-play toggle.
"""

import base64

import websockets

import avatars
import backgrounds
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


def profiles_message() -> dict:
    return protocol.profiles(profiles.list_profiles(), profiles.read_active_profile_name())


def souls_message() -> dict:
    return protocol.souls(souls.list_souls(), souls.read_active_soul_name())


# Conversation starters for the "Talk about ..." hint besides her own loves.
GENERAL_TOPICS = [
    "goals", "wishes", "dreams", "relationships", "hobbies", "friendship", "the future", "childhood memories",
    "travel", "favorite movies", "books worth rereading", "fears", "what makes you happy", "regrets",
    "something new you learned", "a perfect day", "places you'd love to see", "food", "the weekend", "family",
]


def topics_message() -> dict:
    """The chat box's "Talk about ..." hints: her main soul's loves -- minus the user
    himself ("Josh, definitely Josh"), he's not a topic to suggest to him -- and
    GENERAL_TOPICS."""
    name = persona.user_name().lower()
    loves = [t for t in souls.topics(souls.read_main_soul()) if not name or name not in t.lower()]
    return protocol.topics(loves + GENERAL_TOPICS)


@hub.handles(protocol.SAVE_PROFILE)
async def _save_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
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
    await hub.broadcast(profiles_message())


@hub.handles(protocol.GET_PROFILE)
async def _get_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        content = profiles.read_profile(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read profile {name!r}: {exc!r}")
        return
    await hub.send(websocket, protocol.profile_content(name, content))


@hub.handles(protocol.DELETE_PROFILE)
async def _delete_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
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
    await hub.broadcast(profiles_message())


@hub.handles(protocol.SAVE_SOUL)
async def _save_soul(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
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
    await hub.broadcast(souls_message())


@hub.handles(protocol.LOAD_SOUL)
async def load_soul(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        souls.load_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load soul {name!r}: {exc!r}")
    else:
        if profiles.read_roleplay_active():
            # effective_soul, not the loaded content: loading "Default" clears the
            # RP soul, which means "no RP soul -- use her main soul", not "no soul".
            brain.llm.set_soul(persona.effective_soul())
            print(f"[brain] loaded soul {name!r}")
        else:
            # Still recorded above (in rp_soul.md, never her main soul.md) -- just
            # not applied while role-play is off, same as load_profile, and the
            # saved scene (with the old character) isn't resumed.
            conversation.clear_state(conversation.ROLEPLAY)
            print(f"[brain] selected soul {name!r} (role-play is off, not applied)")
    await hub.broadcast(souls_message())


@hub.handles(protocol.GET_SOUL)
async def _get_soul(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        description, examples = souls.read_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't read soul {name!r}: {exc!r}")
        return
    await hub.send(websocket, protocol.soul_content(name, description, examples))


@hub.handles(protocol.GET_NOTES)
async def _get_notes(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await hub.send(websocket, protocol.notes_content(notes.read_notes()))


@hub.handles(protocol.SAVE_NOTES)
async def _save_notes(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    content = data.get("content", "")
    if not hub.fields_too_long(content):
        notes.write_notes(content)


@hub.handles(protocol.GET_SOUL_AND_USER)
async def _get_soul_and_user(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await hub.send(websocket, protocol.soul_and_user_content(souls.read_main_soul(), profiles.read_main_user()))


@hub.handles(protocol.SAVE_SOUL_AND_USER)
async def save_soul_and_user(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
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
    await hub.broadcast(topics_message())


@hub.handles(protocol.DELETE_SOUL)
async def _delete_soul(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        souls.delete_soul(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't delete soul {name!r}: {exc!r}")
        return
    print(f"[brain] deleted soul {name!r}")
    # Same reasoning as _delete_profile: don't leave rp_soul.md/the
    # LLM pointing at a soul that no longer exists.
    if souls.read_active_soul_name() == name:
        souls.load_soul(souls.DEFAULT_SOUL_NAME)
        if profiles.read_roleplay_active():
            brain.llm.set_soul(persona.effective_soul())
        print(f"[brain] active soul was deleted -- reset to {souls.DEFAULT_SOUL_NAME!r}")
    await hub.broadcast(souls_message())


@hub.handles(protocol.SAVE_AVATAR)
async def _save_avatar(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    data_b64 = data.get("data_b64", "")
    kind = data.get("kind", "vrm")
    # Only the name is length-checked here, not data_b64 (the avatar's raw
    # bytes) -- that's a real, deliberately large payload already bounded
    # by websockets.serve's own max_size in main.py, not a free-text
    # field this cap is meant for. `kind` is checked against
    # avatars.AVATAR_KINDS rather than trusted outright -- avatars.save_avatar
    # already re-validates it too (defense in depth, not redundant: this
    # check just avoids doing a base64 decode for a request that's going to
    # be rejected anyway).
    if not name.strip() or not data_b64 or hub.fields_too_long(name) or kind not in avatars.AVATAR_KINDS:
        return
    try:
        avatar_bytes = base64.b64decode(data_b64)
        saved = avatars.save_avatar(name, avatar_bytes, kind)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't save avatar {name!r}: {exc!r}")
        return
    avatars.set_active_avatar(saved)
    print(f"[brain] saved and activated avatar {saved!r} ({kind}, {len(avatar_bytes)} bytes)")
    # `renamed`: the Renderer shows the file's own name until told the one it was saved
    # under ("my.avatar" -> "myavatar"). Every device, so their lists stay in step.
    await hub.broadcast(protocol.avatars_changed(avatars.list_avatars(), renamed={"from": name, "to": saved}))


@hub.handles(protocol.LOAD_AVATAR)
async def _load_avatar(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
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
    await hub.send(websocket, protocol.avatar_data(name, data_b64, kind))


@hub.handles(protocol.RENAME_AVATAR, protocol.DELETE_AVATAR)
async def _manage_avatar(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Rename or delete a saved avatar (Settings -> Avatar's ✏️/🗑️). Every device
    gets the new list; one that was showing the avatar updates its name (rename)
    or goes back to the built-in Glitch (delete). A refused change (the built-in
    avatar, a name already taken) just re-sends the unchanged list to the asker.
    """
    name = str(data.get("name") or "")
    renaming = data["type"] == protocol.RENAME_AVATAR
    try:
        if renaming:
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
        print(f"[brain] couldn't {'rename' if renaming else 'delete'} avatar {name!r}: {exc!r}")
        await hub.send(websocket, protocol.avatars(avatars.list_avatars()))


def background_messages() -> list[dict]:
    """The background list, then the picture in use, if any: what a device needs to show it."""
    active = backgrounds.read_active()
    messages = [protocol.backgrounds(backgrounds.list_backgrounds(), active)]
    if active:
        try:
            data, kind = backgrounds.read(active)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't read background {active!r}: {exc!r}")
        else:
            messages.append(protocol.background_data(active, base64.b64encode(data).decode("ascii"), kind))
    return messages


@hub.handles(protocol.SAVE_BACKGROUND, protocol.SET_BACKGROUND, protocol.DELETE_BACKGROUND)
async def _change_background(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Settings -> Background: add a picture (it becomes the one in use), pick one ("" for
    none) or delete one. Every device then shows the same; a refused change just re-sends
    what's there to the asker. Like an avatar's bytes, data_b64 is bounded by max_size.
    """
    name = str(data.get("name") or "")
    try:
        if hub.fields_too_long(name):
            raise ValueError("that name is too long")
        if data["type"] == protocol.SAVE_BACKGROUND:
            image = base64.b64decode(str(data.get("data_b64") or ""), validate=True)
            if not image:
                raise ValueError("no image")
            name = backgrounds.save(name, image, str(data.get("kind") or ""))
            backgrounds.set_active(name)
        elif data["type"] == protocol.SET_BACKGROUND:
            backgrounds.set_active(name)
        else:
            backgrounds.delete(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't {data['type'].replace('_', ' ')} {name!r}: {exc!r}")
        for message in background_messages():
            await hub.send(websocket, message)
        return
    print(f"[brain] {data['type'].replace('_', ' ')}: {name!r}")
    for message in background_messages():
        await hub.broadcast(message)


@hub.handles(protocol.GET_CHAT_LOGS, protocol.GET_CHAT_LOG, protocol.DELETE_CHAT_LOG)
async def _chat_logs(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Settings -> Chat Logs: list a mode's days, read one, or delete one. Only
    her daily logs in brain/chat_logs/ (regular), chat_logs/roleplay/ and her journal (self/) -- the
    file is picked by mode and a plain date, never by a path from the Renderer.
    A deletion goes to every device, so their lists stay in step.
    """
    mode = data.get("mode") if data.get("mode") in (conversation.ROLEPLAY, conversation.JOURNAL) else conversation.MAIN
    day = str(data.get("date") or "")
    if data["type"] == protocol.GET_CHAT_LOGS:
        await hub.send(websocket, protocol.chat_logs(mode, conversation.list_logs(mode)))
    elif data["type"] == protocol.GET_CHAT_LOG:
        try:
            content, error = conversation.read_log(mode, day), ""
        except (ValueError, OSError) as exc:
            content, error = "", str(exc)
        await hub.send(websocket, protocol.chat_log_content(mode, day, content, error))
    else:
        try:
            conversation.delete_log(mode, day)
            print(f"[brain] deleted the {mode} chat log for {day}")
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't delete the {mode} chat log for {day}: {exc!r}")
        await hub.broadcast(protocol.chat_logs(mode, conversation.list_logs(mode)))


@hub.handles(protocol.LOAD_PROFILE)
async def load_profile(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    name = data.get("name", "")
    try:
        content = profiles.load_profile(name)
    except (ValueError, OSError) as exc:
        print(f"[brain] couldn't load profile {name!r}: {exc!r}")
    else:
        if profiles.read_roleplay_active():
            brain.llm.set_persona(content)
            print(f"[brain] loaded profile {name!r}")
        else:
            # Still recorded in rp_user.md above -- just not applied to the LLM
            # while role-play is toggled off (see set_roleplay_active). The
            # saved scene was with a different character, so it isn't resumed.
            conversation.clear_state(conversation.ROLEPLAY)
            print(f"[brain] selected profile {name!r} (role-play is off, not applied)")
    await hub.broadcast(profiles_message())


@hub.handles(protocol.SET_ROLEPLAY_ACTIVE)
async def set_roleplay_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The role-play toggle. On: if a role-play engine is set (Settings), remembers
    her current engine and switches to it, with thinking as the Renderer's confirm
    dialog chose -- otherwise she stays on her current engine -- and resumes the
    last role-play scene. Off: puts her back on the engine she had before, if role-
    play switched, and brings the normal conversation back. The two conversations
    are saved separately (brain/conversation.py), so neither wipes the other.
    Every device hears the new state and engine.
    """
    _toggle_roleplay(data, brain)
    active = profiles.read_roleplay_active()
    engine = llm_engines.read_active_engine_name()
    device = hub.DEVICE_NAMES.get(websocket, "unknown device")
    await hub.debug_broadcast("roleplay", f"role-play {'on' if active else 'off'} (from {device}), LLM engine now {engine!r}")
    await hub.broadcast(protocol.roleplay_state(active))
    await hub.broadcast(protocol.llm_engines(llm_engines.list_engines(), engine))


def _toggle_roleplay(data: dict, brain: Brain) -> None:
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
