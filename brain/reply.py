"""The reply pipeline: a message (typed, spoken, or a resend) -> her prompt
parts for this turn -> the LLM -> expression, text and voice back to the
device, then the context meter, memory and curiosity in the background.
"""

import asyncio
import base64
import tempfile
import time
from pathlib import Path

import websockets

import conversation
import curiosity
import hub
import learning
import lessons
import memory
import persona
import profiles
import protocol
import reach_out
import sampling
import training
import voice_settings
import web_search
from hub import Brain
from voice import FasterWhisperSTT, NoneTTS

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


@hub.handles(protocol.SET_VOICE_ACTIVE)
async def _set_voice_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    voice_settings.set_voice_active(bool(data.get("active")))
    await hub.broadcast(protocol.voice_state(voice_settings.read_voice_active()))


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
        # Without this, a failed transcription (e.g. a blank/silent voice
        # message, or the STT engine erroring outright) left the Renderer
        # stuck "awaiting a reply" forever -- Send/camera/desktop/mic all
        # grayed out with nothing actually happening. Confirmed live.
        await hub.send(websocket, protocol.no_reply())
        return
    await hub.debug_log(websocket, "stt", "transcription ok", (time.monotonic() - start) * 1000)
    print(f"[brain] user_audio transcribed: {text!r}")
    if text.strip():
        await hub.send(websocket, protocol.user_transcript(text))
    await reply_to(websocket, text, brain)


@hub.handles(protocol.CLEAR_CONVERSATION)
async def clear_conversation(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The chat history panel's Clear Chat: she starts a fresh conversation too,
    not just the panel. Her long-term memory is untouched -- only the running
    conversation goes. Every connected device clears its panel as well.
    """
    global LAST_CONTEXT_USAGE
    brain.llm.clear_history()  # with a harness: a new session there
    if brain.llm.owns_conversation:  # a harness's conversations stay out of her chat log -- see reply_to
        conversation.log_marker("New conversation (chat cleared)", mode=persona.conversation_mode())
    LAST_CONTEXT_USAGE = None
    curiosity.restart_quiet_hour(time.time())  # she may reach out again after a new random wait
    await reach_out.broadcast_curiosity_timer()
    print("[brain] conversation cleared")
    await hub.broadcast(protocol.conversation_cleared())


@hub.handles(protocol.REGENERATE_LAST)
async def regenerate_last(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The Renderer's "resend" controls (per-bubble retry, History panel's
    Resend Last) -- re-answers the same prompt without leaving the stale
    reply (and a duplicated question right after it) sitting in context,
    which is what plainly resending the same text as a brand new user_text
    would do and reads as "he's just repeating himself" rather than a
    clean second attempt.

    Only her own LLM has history to pop (see pop_last_exchange's own
    docstring) -- with a harness or NoneLLM there's nothing to pop, so this
    just answers whatever text the Renderer sent (its own best recollection
    of the last prompt), same as an ordinary user_text.
    """
    text = str(data.get("text", ""))
    edited = bool(data.get("edited"))  # the ✏️ button: answer their corrected text instead
    if edited and hub.fields_too_long(text):
        return
    popped_text = brain.llm.pop_last_exchange()
    await _drop_proposal_from(popped_text)
    if popped_text is not None and not edited:
        text = popped_text
    await reply_to(websocket, text, brain)


@hub.handles(protocol.DELETE_LAST)
async def delete_last(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The 🗑️ on the user's latest message: drops it (and her reply, if the
    Renderer showed one) from her context. Pops only when the tail matches --
    a Stop before the turn reached history leaves an older exchange there."""
    expected = "assistant" if data.get("answered") else "user"
    if brain.llm.last_role() == expected:
        await _drop_proposal_from(brain.llm.pop_last_exchange())


@hub.handles(protocol.SPEECH_INTERRUPTED)
async def speech_interrupted(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The user cut in while she was speaking (🎤 or Send): `heard` is how much of
    her reply's audio had played, 0-1. She keeps only that much of it."""
    heard = data.get("heard")
    if isinstance(heard, (int, float)):
        brain.llm.cut_last_reply(float(heard))
        await hub.debug_log(websocket, "tts", f"talked over at {heard:.0%} of her reply")


async def _drop_proposal_from(user_text: str | None) -> None:
    """A memory proposed from an exchange that was just regenerated or deleted
    goes too -- it came from a reply that's no longer in her conversation."""
    # ponytail: a proposal still being written (it runs after the reply) slips past; fine at human click speed.
    if user_text is not None and training.drop_latest_from(user_text):
        await hub.broadcast(learning.training_state_message())


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
        # A blank voice message (silence transcribes to "") or an empty
        # user_text is the most common way here -- without telling the
        # Renderer, it stays "awaiting a reply" forever with every button
        # grayed out and nothing actually coming. Confirmed live.
        await hub.send(websocket, protocol.no_reply())
        return
    print(f"[brain] user said: {text!r}" + (" (+ image)" if image_b64 else ""))
    curiosity.note_user_message(time.time())  # starts a new random wait before she may reach out (see reach_out.reach_out_loop)
    await reach_out.broadcast_curiosity_timer()

    # Her own LLM only -- a harness manages its own memory (see memory.py's
    # own docstring). recall_for_prompt is provider-agnostic (memory.py's own
    # read_provider() decides local-vs-hindsight) -- for hindsight this is
    # recalled fresh for THIS message every turn, not just primed once at
    # LLM-build time, so different questions actually surface different
    # relevant memories instead of one static block; for local it's just
    # the one flat block, same as before. Never lets a lookup failure
    # break the reply itself -- a turn with no memory applied is still a
    # perfectly good reply.
    #
    # Gated on role-play being OFF, same as learning.maybe_retain_memory's own
    # gate below -- the user was explicit that RP should pause memory
    # entirely, not just stop learning new things during it: she
    # shouldn't be drawing on real-user facts while in character either.
    # No text (an image-only turn) leaves whatever a previous turn set
    # alone rather than clearing it -- there's nothing to recall against,
    # but that's not the same as "pause memory", so this only clears when
    # there actually was a message and memory was skipped for it.
    if text and brain.llm.owns_conversation:
        if memory.read_memory_active() and not profiles.read_roleplay_active():
            recall_start = time.monotonic()
            try:
                relevant = await memory.recall_for_prompt(text)
            except Exception as exc:
                await hub.debug_log(websocket, "memory", f"recall failed: {exc!r}", (time.monotonic() - recall_start) * 1000)
            else:
                brain.llm.set_memory(relevant)
                found = sum(1 for line in relevant.splitlines() if line.startswith("- "))
                await hub.debug_log(websocket, "memory", f"recall ok: {found} memories", (time.monotonic() - recall_start) * 1000)
        else:
            # Role-play active, or memory turned off -- clears whatever a
            # previous turn set so it can't linger into this one (e.g.
            # role-play just got turned on with a stale recall still
            # sitting in the system prompt from right before).
            brain.llm.set_memory("")

    brain.llm.set_user_info(persona.effective_user_info())
    # Her sampling profile (brain/sampling.py) -- read every turn, so a switch
    # in Settings applies to the very next reply. Kept on during role-play:
    # it's how she generates, not something about the real user.
    brain.llm.set_sampling(sampling.active_values())

    # Learned behavior rules (brain/lessons.py) -- like memory, off during
    # role-play and cleared rather than skipped so a stale block can't linger
    # into an in-character turn. Never lets a lookup failure break the reply.
    if brain.llm.owns_conversation and lessons.read_active() and not profiles.read_roleplay_active():
        try:
            brain.llm.set_lessons(await asyncio.wait_for(lessons.prompt_block(), timeout=LESSON_PROMPT_TIMEOUT_SEC))
        except Exception as exc:
            await hub.debug_log(websocket, "lessons", f"couldn't load lessons: {exc!r}")
            brain.llm.set_lessons("")
    else:
        brain.llm.set_lessons("")

    # Curiosity (brain/curiosity.py) -- same shape as lessons: off during role-play,
    # cleared rather than skipped. Not tied to Hindsight; it only needs the local files.
    curious = brain.llm.owns_conversation and curiosity.read_active() and not profiles.read_roleplay_active()
    recent = brain.llm.recent_replies(curiosity.QUESTION_COOLDOWN_REPLIES)
    brain.llm.set_curiosity(curiosity.start_turn(recent) if curious else "")
    answered = curiosity.answered_question() if curious else ""  # her question this message answers, for memory
    for event in curiosity.take_events():
        await hub.debug_log(websocket, "curiosity", event)

    llm_start = time.monotonic()
    try:
        reply = await asyncio.to_thread(brain.llm.reply, text, image_b64, image_mime, web_search.read_active())
    except Exception as exc:
        # No Brain -> Renderer error message type exists yet (protocol.md's
        # `error` is Renderer -> Brain only) -- surfacing this as speak_text
        # is a deliberate, minimal stand-in rather than adding a new message
        # type just for this. Revisit if/when that actually gets in the way.
        await hub.debug_log(websocket, "llm", f"LLM call failed: {exc!r}", (time.monotonic() - llm_start) * 1000)
        print(f"[brain] LLM call failed: {exc!r}")
        await hub.send(websocket, protocol.speak_text(f"(couldn't reach the LLM: {exc})"))
        return
    # Never logs reply_text itself -- timing/outcome only, per the
    # Debugging feature's whole point (connection/timing/errors, not
    # conversation content).
    # Sizes and settings only -- how big the prompt was, how long the reply was,
    # which sampling profile -- so a slow or odd reply can be told apart.
    reply_text, mood, usage, trim = reply.text, reply.mood, reply.usage, reply.trimmed
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
        # A real, confirmed failure mode (not hypothetical): the
        # configured reasoning model can finish and return successfully
        # -- no exception, no token-limit truncation -- with `content`
        # still empty (everything it produced was reasoning_content, or
        # just the mood tag with nothing else). Left unhandled, this used
        # to fall through to speak_text with "" (a blank chat-history
        # bubble the user just sees as silence) and then a TTS call that
        # fails outright with "Input contains no speakable text" -- two
        # confusing symptoms for what's really one cause. Treating it the
        # same as reply_to's own empty-input guard above is both more
        # honest and skips a TTS call that could never succeed anyway.
        await hub.debug_log(websocket, "llm", "LLM returned an empty reply", (time.monotonic() - llm_start) * 1000)
        await hub.send(websocket, protocol.no_reply())
        return

    print(f"[brain] mood: {mood}")
    # Sent before speak_text/speak_audio so her face is already changing by
    # the time she starts talking, not lagging a beat behind. Sent even for
    # "neutral" -- the Renderer treats that as "fade every mood expression
    # back to 0", which is exactly right after a mood-carrying reply.
    await hub.send(websocket, protocol.set_expression(mood, 1.0))
    await hub.send(websocket, protocol.speak_text(reply_text))
    # Only her own conversations go in her chat log. With a harness (Hermes) in
    # control she's working under a different soul and memory -- that side keeps
    # its own session logs, and personal and professional are kept apart on
    # purpose. NoneLLM's placeholder isn't a conversation either.
    if brain.llm.owns_conversation:
        conversation.log_exchange(text, reply_text, roleplay=profiles.read_roleplay_active(), picture=bool(image_b64))
    hub.spawn(_send_context_usage(brain, usage))

    # Fire-and-forget: must never slow down or affect the reply the user
    # already has. Runs regardless of whether voice/TTS succeeds below --
    # it only needs the text of what was actually said.
    # A turn where she searched the web has the results woven into her
    # reply, and a turn with a camera/screen image has her describing that
    # frame -- neither is a fact about the user, just world facts or a
    # moment. Only the user's own side is remembered for those, so memory
    # doesn't fill with headlines and "the keyboard has blue keys" (and she
    # can't repeat a bad search result back later as if it were a memory).
    omit_reply = reply.used_web_search or bool(image_b64)
    hub.spawn(learning.maybe_retain_memory(websocket, text, "" if omit_reply else reply_text, brain, asked=answered))
    if curious:
        curiosity.end_turn(reply_text)
        for event in curiosity.take_events():
            await hub.debug_log(websocket, "curiosity", event)
        # "*" in the user's message means they're playing out an action/scene -- not a source of real questions
        if text and "*" not in text and not omit_reply and curiosity.should_propose():
            hub.spawn(reach_out.maybe_propose_question(websocket, text, reply_text, brain))

    if not voice_settings.read_voice_active():
        await hub.debug_log(websocket, "tts", "voice is off -- skipping synthesis")
        return

    if isinstance(brain.tts, NoneTTS):
        await hub.debug_log(websocket, "tts", "no speech engine configured -- skipping synthesis")
        return

    tts_start = time.monotonic()
    try:
        wav_bytes, frames = await asyncio.to_thread(brain.tts.synthesize, reply_text, mood)
    except Exception as exc:
        await hub.debug_log(websocket, "tts", f"TTS call failed: {exc!r}", (time.monotonic() - tts_start) * 1000)
        print(f"[brain] TTS failed: {exc!r}")
        return
    await hub.debug_log(websocket, "tts", "TTS synthesis ok", (time.monotonic() - tts_start) * 1000)

    audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
    await hub.send(websocket, protocol.speak_audio(audio_b64, brain.tts.SAMPLE_RATE))
    await hub.send(websocket, protocol.viseme_stream(frames))


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
