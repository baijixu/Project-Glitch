"""The reply pipeline: a message (typed, spoken, or a resend) -> her prompt
parts for this turn -> the LLM -> expression, text and voice back to the
device, then the context meter, memory and curiosity in the background.
"""

import asyncio
import base64
import re
import tempfile
import time
from pathlib import Path

import websockets

import conversation
import curiosity
import friends
import heartbeat
import hub
import learning
import lessons
import library
import memory
import persona
import pictures
import profiles
import protocol
import reach_out
import sampling
import web_search
from hub import Brain
from llm import Reply, Sentences, face
from voice import VOICE_ACTIVE, FasterWhisperSTT, NoneTTS

# Browsers' MediaRecorder doesn't produce WAV -- map its common mime types
# to a file extension so faster-whisper's decoder gets a useful hint.
AUDIO_EXTENSION_BY_MIME = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".mp4",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}
LESSON_PROMPT_TIMEOUT_SEC = 5
# A first sentence's opening clause, voiced on its own (see reply_to's `say`): 15+ characters
# up to a comma, with 15+ after it -- shorter and the split costs more than it saves.
_FIRST_CLAUSE = re.compile(r"(.{15,}?,)\s+(.{15,})", re.DOTALL)


@hub.handles(protocol.SET_VOICE_ACTIVE)
async def _set_voice_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    VOICE_ACTIVE.write(bool(data.get("active")))
    await hub.broadcast(protocol.voice_state(VOICE_ACTIVE.read()))


@hub.handles(protocol.SET_PRIVATE_ACTIVE)
async def _set_private_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    conversation.PRIVATE.write(bool(data.get("active")))
    await hub.broadcast(protocol.private_state(conversation.PRIVATE.read()))


@hub.handles(protocol.SET_WEB_SEARCH_ACTIVE)
async def _set_web_search_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    web_search.set_active(bool(data.get("active")))
    await hub.broadcast(protocol.web_search_state(web_search.read_active()))


@hub.handles(protocol.USER_TEXT)
async def _user_text(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await reply_to(
        websocket, data.get("text", ""), brain, image_b64=data.get("image_b64"), image_mime=data.get("image_mime", "image/jpeg")
    )


@hub.handles(protocol.USER_AUDIO)
async def _user_audio(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    audio_b64 = data.get("audio_b64") or ""
    if not audio_b64:
        await hub.send(websocket, protocol.no_reply())
        return
    start = time.monotonic()
    try:
        text = await asyncio.to_thread(_transcribe, brain.stt, audio_b64, data.get("mime_type", ""))
    except Exception as exc:
        await hub.debug_log(websocket, "stt", f"STT failed: {exc!r}", (time.monotonic() - start) * 1000)
        print(f"[brain] STT failed: {exc!r}")
        # Or the device waits for a reply forever, every button grayed out.
        await hub.send(websocket, protocol.no_reply())
        return
    await hub.debug_log(websocket, "stt", "transcription ok", (time.monotonic() - start) * 1000)
    print(f"[brain] user_audio transcribed: {'(private)' if conversation.PRIVATE.read() else repr(text)}")
    if text.strip():
        await hub.send(websocket, protocol.user_transcript(text))
    await reply_to(websocket, text, brain)


@hub.handles(protocol.CLEAR_CONVERSATION)
async def clear_conversation(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Clear Chat: a fresh conversation for her and every device's panel; her long-term
    memory stays. First she writes herself a note on where it left off (_write_left_off).
    """
    global LAST_CONTEXT_USAGE
    name = persona.user_name() or "User"
    # Her own conversations only: a role-play scene isn't where she and the user left off,
    # and a private chat leaves nothing behind.
    keep_note = brain.llm.owns_conversation and not profiles.read_roleplay_active() and not conversation.PRIVATE.read()
    transcript = brain.llm.transcript(name) if keep_note else ""
    brain.llm.clear_history()  # with a harness: a new session there
    if brain.llm.owns_conversation:  # a harness's conversations stay out of her chat log -- see reply_to
        conversation.log_marker("New conversation (chat cleared)", mode=persona.conversation_mode())
    LAST_CONTEXT_USAGE = None
    curiosity.restart_quiet_hour(time.time())  # she may reach out again after a new random wait
    await reach_out.broadcast_curiosity_timer()
    print("[brain] conversation cleared")
    await hub.broadcast(protocol.conversation_cleared())
    if any(line.startswith(f"{name}: ") for line in transcript.splitlines()):  # the user said something worth a note
        hub.spawn(_write_left_off(brain, transcript, name))
        learning.end_sitting(brain, quiet_sec=0)  # Clear Chat ends the sitting now


async def _write_left_off(brain: Brain, transcript: str, name: str) -> None:
    """Her note on where the cleared conversation left off, saved for her next ones
    (reply_to puts it in her prompt) and shown on every device. Holds the reply lock
    meanwhile, so her first reply in the new conversation already has it."""
    async with brain.busy("Noting where you left off..."):
        try:
            note = await asyncio.to_thread(brain.llm.write_left_off, transcript, name)
        except Exception as exc:
            print(f"[brain] couldn't write where we left off: {exc!r}")
            return
    if note:
        conversation.save_left_off(note)
        await hub.broadcast(protocol.left_off(note))


@hub.handles(protocol.REGENERATE_LAST)
async def regenerate_last(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Resend and ✏️ on his latest message: re-answers it in place, without the stale reply
    and a repeat of his message left in her context. A harness has no history to pop, so
    it just answers the text sent.
    """
    text = str(data.get("text", ""))
    edited = bool(data.get("edited"))  # the ✏️ button: answer their corrected text instead
    if edited and hub.fields_too_long(text):
        return await hub.send(websocket, protocol.no_reply())
    popped = brain.llm.pop_last_exchange()
    image_b64, image_mime = None, "image/jpeg"
    if popped is not None:
        popped_text, image_b64, image_mime = popped  # a picture goes again, edited or not
        if not edited:
            text = popped_text
    await reply_to(websocket, text, brain, image_b64=image_b64, image_mime=image_mime)


@hub.handles(protocol.DELETE_LAST)
async def delete_last(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The 🗑️ on the user's latest message: drops it (and her reply, if the
    Renderer showed one) from her context. Pops only when the tail matches --
    a Stop before the turn reached history leaves an older exchange there."""
    expected = "assistant" if data.get("answered") else "user"
    if brain.llm.last_role() == expected:
        brain.llm.pop_last_exchange()
        await _sync_other_devices(websocket, brain)


async def _sync_other_devices(websocket: websockets.ServerConnection, brain: Brain) -> None:
    """What was just said or deleted on this device reaches every other device's chat panel."""
    if (chat := brain.llm.screen_messages()) is not None:
        await hub.broadcast(protocol.chat_history(chat), skip=websocket)


@hub.handles(protocol.GET_PICTURE)
async def get_picture(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """One of her pictures for this device's chat panel -- by name, from pictures/ only."""
    name = data.get("name", "")
    mime, data_b64 = pictures.read(name) or ("", "")
    await hub.send(websocket, protocol.picture(name, mime, data_b64))


@hub.handles(protocol.DELETE_PICTURE)
async def delete_picture(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Settings -> Artwork's 🗑️: gone for good -- from her chat bubble too, which finds it missing."""
    pictures.delete(data.get("name", ""))
    await hub.broadcast(protocol.artwork(pictures.listing()))


@hub.handles(protocol.SPEECH_INTERRUPTED)
async def speech_interrupted(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The user cut in while she was speaking (🎤 or Send): `heard` is how much of
    her reply's audio had played, 0-1. She keeps only that much of it."""
    brain.speech_stopped = True  # what she hasn't voiced yet stays unsaid
    heard = data.get("heard")
    if isinstance(heard, (int, float)):
        brain.llm.cut_last_reply(float(heard))
        await hub.debug_log(websocket, "tts", f"talked over at {heard:.0%} of her reply")


def _transcribe(stt: FasterWhisperSTT, audio_b64: str, mime_type: str) -> str:
    audio_bytes = base64.b64decode(audio_b64)
    suffix = AUDIO_EXTENSION_BY_MIME.get(mime_type.split(";")[0].strip(), ".webm")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        temp_path = f.name
    try:
        return stt.transcribe(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)


async def reply_to(
    websocket: websockets.ServerConnection,
    text: str,
    brain: Brain,
    image_b64: str | None = None,
    image_mime: str = "image/jpeg",
) -> None:
    text = text.strip()
    if not text and not image_b64:
        # A blank voice message or empty text: say so, or the device waits forever.
        await hub.send(websocket, protocol.no_reply())
        return
    print(f"[brain] user said: {'(private)' if conversation.PRIVATE.read() else repr(text)}" + (" (+ image)" if image_b64 else ""))  # restart.log is on disk
    curiosity.note_user_message(time.time())  # starts a new random wait before she may reach out (see reach_out.reach_out_loop)
    await reach_out.broadcast_curiosity_timer()

    # Her memories for this message (her own LLM only; a harness has its own). Paused in
    # role-play -- no real-life facts in character. A failed lookup never breaks the reply.
    # A picture with no text leaves the previous turn's memories.
    if text and brain.llm.owns_conversation:
        if memory.read_memory_active() and not profiles.read_roleplay_active():
            recall_start = time.monotonic()
            try:
                async with hub.doing("Remembering..."):
                    relevant = await memory.recall_for_prompt(text)
            except Exception as exc:
                await hub.debug_log(websocket, "memory", f"recall failed: {exc!r}", (time.monotonic() - recall_start) * 1000)
            else:
                brain.llm.set_memory(relevant)
                found = sum(1 for line in relevant.splitlines() if line.startswith("- "))
                await hub.debug_log(websocket, "memory", f"recall ok: {found} memories", (time.monotonic() - recall_start) * 1000)
        else:
            # Cleared, so a previous turn's memories can't linger into role-play.
            brain.llm.set_memory("")

    brain.llm.set_user_info(persona.effective_user_info())
    brain.llm.set_left_off("" if profiles.read_roleplay_active() else conversation.read_left_off())
    # Her diary, her last visit's journal, the book she's reading and what she's been into.
    journals = (conversation.read_latest_journal(), friends.read_visit_journal(), library.reading_line(), heartbeat.into_lately())
    brain.llm.set_journal("" if profiles.read_roleplay_active() else "\n\n".join(j for j in journals if j))
    # Her sampling profile, read every turn (kept in role-play: it's how she writes, not who he is).
    brain.llm.set_sampling(sampling.active_values())

    # Learned behavior rules (brain/lessons.py): like memory, cleared in role-play.
    if brain.llm.owns_conversation and lessons.read_active() and not profiles.read_roleplay_active():
        try:
            brain.llm.set_lessons(await asyncio.wait_for(lessons.prompt_block(), timeout=LESSON_PROMPT_TIMEOUT_SEC))
        except Exception as exc:
            await hub.debug_log(websocket, "lessons", f"couldn't load lessons: {exc!r}")
            brain.llm.set_lessons("")
    else:
        brain.llm.set_lessons("")

    # Curiosity (brain/curiosity.py): the same.
    curious = brain.llm.owns_conversation and curiosity.read_active() and not profiles.read_roleplay_active()
    recent = brain.llm.recent_replies(curiosity.QUESTION_COOLDOWN_REPLIES)
    brain.llm.set_curiosity(curiosity.start_turn(recent) if curious else "")
    answered = curiosity.answered_question() if curious else ""  # her question this message answers, for memory
    for event in curiosity.take_events():
        await hub.debug_log(websocket, "curiosity", event)

    # Her reply is shown and spoken sentence by sentence as it's written: `say` puts each
    # one on screen and queues it for her voice, which _voice works through in order.
    brain.speech_stopped = False
    said: asyncio.Queue = asyncio.Queue()  # (sentence, mood) for her voice; None when there are no more
    voicing = asyncio.create_task(_voice(websocket, brain, said))
    sentences = Sentences()
    started = False

    async def say(sentence: str, mood: str) -> None:
        nonlocal started
        first = not started
        if first:  # her face changes as she starts talking, not a beat behind
            started = True
            await hub.send(websocket, protocol.set_expression(face(mood), 1.0))
        await hub.send(websocket, protocol.speak_text(sentence, partial=True))
        if first:
            # Her voice takes ~1 s per 25 characters to make, and her first words wait on all
            # of the first sentence: a long one is voiced in two, so she starts after a clause.
            if clause := _FIRST_CLAUSE.match(sentence):
                said.put_nowait((clause[1], mood))
                sentence = clause[2]
        said.put_nowait((sentence, mood))

    try:
        llm_start = time.monotonic()
        try:
            reply = await _stream_reply(websocket, brain, text, image_b64, image_mime, sentences, say)
        except Exception as exc:
            # ponytail: the error is shown as her reply; a real error message if that gets in the way.
            await hub.debug_log(websocket, "llm", f"LLM call failed: {exc!r}", (time.monotonic() - llm_start) * 1000)
            print(f"[brain] LLM call failed: {exc!r}")
            await hub.send(websocket, protocol.speak_text(f"(couldn't reach the LLM: {exc})"))
            return
        # The debug log gets sizes and settings, never what was said.
        reply_text, mood, usage, trim = reply.text, sentences.mood or reply.mood, reply.usage, reply.trimmed
        details = ""
        llm_ms = (time.monotonic() - llm_start) * 1000
        if brain.llm.owns_conversation:
            thinking = f", {usage['reasoning']} of them thinking" if usage.get("reasoning") else ""
            details = (
                f" ({usage.get('prompt', '?')} prompt + {usage.get('completion', '?')} reply tokens{thinking}, "
                f"{brain.llm.message_count} messages, profile {sampling.read_active_name()!r})"
            )
        await hub.debug_log(websocket, "llm", f"LLM reply received{details}", llm_ms)
        if trim:
            await hub.debug_log(
                websocket, "llm",
                f"conversation trimmed: dropped the oldest {trim['dropped']} messages, kept {trim['kept']} "
                f"(budget ~{trim['budget']} tokens)",
            )
        if llm_ms > SLOW_REPLY_MS and brain.llm.owns_conversation:
            loaded = await asyncio.to_thread(brain.llm.loaded_models)
            await hub.debug_log(websocket, "llm", f"slow reply -- loaded on her LLM server now: {loaded}")
        if reply.fell_back:
            await hub.debug_log(websocket, "llm", "ran out of thinking room -- answered again with thinking off")

        if not reply_text.strip():
            # A reasoning model can finish with nothing but thinking (or a bare mood tag).
            await hub.debug_log(websocket, "llm", "LLM returned an empty reply", (time.monotonic() - llm_start) * 1000)
            await hub.send(websocket, protocol.no_reply())
            return

        for sentence in sentences.finish(reply_text):  # the last one -- or all of it, if it didn't stream
            await say(sentence, mood)
        print(f"[brain] mood: {mood}")
        # all of it: the device's record of her reply, with any picture her agent made for it
        await hub.send(websocket, protocol.speak_text(reply_text, pictures=reply.pictures))
        if reply.pictures:
            await hub.broadcast(protocol.artwork(pictures.listing()))  # Settings -> Artwork on every device
        await _sync_other_devices(websocket, brain)
        # Only her own conversations go in her chat log: a harness keeps its own, and
        # personal and professional stay apart.
        if brain.llm.owns_conversation:
            conversation.log_exchange(text, reply_text, roleplay=profiles.read_roleplay_active(), picture=bool(image_b64))
        hub.spawn(_send_context_usage(brain, usage))

        # In the background. After a web search or a picture her reply is about the world, not
        # him, so only his side is remembered -- no headlines or "the keyboard has blue keys".
        omit_reply = reply.used_tool or bool(image_b64)
        hub.spawn(learning.maybe_retain_memory(websocket, text, "" if omit_reply else reply_text, brain, asked=answered))
        if curious:
            curiosity.end_turn(reply_text)
            for event in curiosity.take_events():
                await hub.debug_log(websocket, "curiosity", event)
            # "*" means he's acting out a scene -- not a source of real questions
            if text and "*" not in text and not omit_reply and curiosity.should_propose():
                hub.spawn(reach_out.maybe_propose_question(websocket, text, reply_text, brain))

        said.put_nowait(None)
        await voicing  # she finishes speaking before another reply can start
    finally:
        voicing.cancel()  # a no-op once she's finished; otherwise (Stop, an error) her voice stops too


async def _stream_reply(
    websocket: websockets.ServerConnection, brain: Brain, text: str, image_b64: str | None, image_mime: str, sentences: Sentences, say
) -> Reply:
    """brain.llm.reply in a worker thread, `say`ing each sentence as soon as it's written. While she's
    still thinking, the device is told how far she's got (protocol.thinking) every THINKING_EVERY tokens."""
    loop = asyncio.get_running_loop()
    pieces: asyncio.Queue = asyncio.Queue()  # the reply as it streams in (an int: thinking so far); None once it's done
    search = web_search.read_active()
    thought = 0

    def thinking() -> None:
        nonlocal thought
        thought += 1
        if thought % THINKING_EVERY == 0:
            loop.call_soon_threadsafe(pieces.put_nowait, thought)

    def answer() -> Reply:
        try:
            on_text = lambda piece: loop.call_soon_threadsafe(pieces.put_nowait, piece)  # noqa: E731
            return brain.llm.reply(text, image_b64, image_mime, search, on_text=on_text, on_thinking=thinking)
        finally:
            loop.call_soon_threadsafe(pieces.put_nowait, None)

    answering = asyncio.ensure_future(asyncio.to_thread(answer))
    while (piece := await pieces.get()) is not None:
        if isinstance(piece, int):
            await hub.send(websocket, protocol.thinking(piece))
            continue
        for sentence in sentences.feed(piece):
            await say(sentence, sentences.mood)
    return await answering


async def _voice(websocket: websockets.ServerConnection, brain: Brain, said: asyncio.Queue) -> None:
    """Her voice: each (sentence, mood) off `said` (None ends it) becomes its own
    speak_audio, which the device plays after the one before. Once she's been talked
    over (brain.speech_stopped) the rest goes unsaid."""
    while (item := await said.get()) is not None:
        sentence, mood = item
        if brain.speech_stopped or not VOICE_ACTIVE.read() or isinstance(brain.tts, NoneTTS):
            continue
        if not any(c.isalnum() for c in sentence):  # an emoji or "..." on its own -- nothing to say
            continue
        start = time.monotonic()
        try:
            wav_bytes, frames = await asyncio.to_thread(brain.tts.synthesize, sentence, mood)
        except Exception as exc:
            await hub.debug_log(websocket, "tts", f"TTS call failed: {exc!r}", (time.monotonic() - start) * 1000)
            print(f"[brain] TTS failed: {exc!r}")
            continue
        await hub.debug_log(websocket, "tts", f"TTS ok ({len(sentence)} chars)", (time.monotonic() - start) * 1000)
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
        await hub.send(websocket, protocol.speak_audio(audio_b64, brain.tts.SAMPLE_RATE, sentence, frames))


# The latest context_usage message, so a device that connects later sees the meter too.
LAST_CONTEXT_USAGE: dict | None = None


async def _send_context_usage(brain: Brain, usage: dict) -> None:
    """After a reply: how full her context is, for the Settings meter (`usage`
    is the reply's token counts). Asking the server for the context size is a
    network call (cached, see LocalLLM.context_window), so this runs on its own
    and never delays her.
    """
    global LAST_CONTEXT_USAGE
    if not brain.llm.owns_conversation or not usage:
        return
    used = (usage.get("prompt") or 0) + (usage.get("completion") or 0)
    if not used:
        return  # the server didn't report token counts
    try:
        window = await asyncio.to_thread(brain.llm.context_window)
        keep = await asyncio.to_thread(brain.llm.history_budget)  # context_window is cached by now
    except Exception:
        window = keep = None
    LAST_CONTEXT_USAGE = protocol.context_usage(used, window, brain.llm.history_tokens(), keep)
    await hub.broadcast(LAST_CONTEXT_USAGE)


SLOW_REPLY_MS = 60_000  # a reply slower than this gets a "which models are loaded" check in the debug log
THINKING_EVERY = 50  # thinking tokens between progress messages: about every 2 s at her ~23 tokens/s
