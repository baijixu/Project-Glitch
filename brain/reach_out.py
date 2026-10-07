"""Curiosity's speaking-first half: after a quiet spell (15-60 minutes, random) she reaches out
once (_reach_out_loop), the Settings countdown to it, its Test button, and
proposing questions she might ask after a reply.
"""

import asyncio
import base64
import time

import websockets

import conversation
import curiosity
import harness
import hub
import persona
import profiles
import protocol
import sampling
import voice_settings
from hub import Brain
from voice import NoneTTS

async def maybe_propose_question(
    websocket: websockets.ServerConnection, user_text: str, reply_text: str, brain: Brain
) -> None:
    """Background: after a reply, ask the model whether there's one thing she'd like to
    know about the user (brain/curiosity.py keeps it for a later turn). Skipped for search
    and image turns, whose replies are about the world rather than the user. Never surfaces
    a failure -- a turn with no new question is the normal outcome.
    """
    start = time.monotonic()
    known = "\n".join(part for part in (persona.effective_user_info(), brain.llm.memory_block) if part)
    try:
        raw = await asyncio.to_thread(
            brain.llm.propose_question, user_text, reply_text, known, curiosity.all_question_texts()
        )
    except Exception as exc:
        await hub.debug_log(websocket, "curiosity", f"question proposal failed: {exc!r}", (time.monotonic() - start) * 1000)
        return
    question = curiosity.parse_question(raw)
    if question and curiosity.add_question(question):
        await hub.debug_log(websocket, "curiosity", "new question kept", (time.monotonic() - start) * 1000)
    elif question:
        for event in curiosity.take_events():  # why it wasn't kept (a repeat, list full...)
            await hub.debug_log(websocket, "curiosity", event, (time.monotonic() - start) * 1000)
    else:
        await hub.debug_log(websocket, "curiosity", "nothing to be curious about", (time.monotonic() - start) * 1000)


REACH_OUT_CHECK_SEC = 60  # how often the reach-out loop looks at the clock


async def reach_out_loop(brain: Brain) -> None:
    """Curiosity's third part: after a random 15-60 minutes with no message from him, she speaks
    first -- once, until he replies (brain/curiosity.py's should_reach_out). Only
    for her own LLM (not a harness), with curiosity on, role-play off, a device
    connected to hear it, and no reply already in progress.
    """
    while True:
        await asyncio.sleep(REACH_OUT_CHECK_SEC)
        try:
            await broadcast_curiosity_timer(only_if_changed=True)  # catches role-play / harness switches
            if (
                brain.llm.owns_conversation
                and curiosity.read_active()
                and not profiles.read_roleplay_active()
                and hub.RENDERER_CONNECTIONS
                and not brain.reply_lock.locked()
                and curiosity.should_reach_out(time.time())
            ):
                async with brain.reply_lock:
                    await _reach_out(brain)
        except Exception as exc:  # never let one bad attempt stop the loop
            print(f"[brain] reaching out failed: {exc!r}")


# The last curiosity_timer sent to everyone, so the loop only re-sends a change.
_LAST_CURIOSITY_TIMER: dict | None = None


def curiosity_timer_message(error: str = "") -> dict:
    if not curiosity.read_active():
        return protocol.curiosity_timer("off", None, error)
    if profiles.read_roleplay_active():
        return protocol.curiosity_timer("roleplay", None, error)
    if harness.read_active_harness():
        return protocol.curiosity_timer("harness", None, error)
    due = curiosity.reach_out_due()
    return protocol.curiosity_timer("counting" if due else "waiting", due, error)


async def broadcast_curiosity_timer(only_if_changed: bool = False) -> None:
    global _LAST_CURIOSITY_TIMER
    message = curiosity_timer_message()
    if only_if_changed and message == _LAST_CURIOSITY_TIMER:
        return
    _LAST_CURIOSITY_TIMER = message
    await hub.broadcast(message)


@hub.handles(protocol.SET_CURIOSITY_ACTIVE)
async def _set_curiosity_active(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    curiosity.set_active(bool(data.get("active")))
    await hub.broadcast(protocol.curiosity_state(curiosity.read_active()))
    await broadcast_curiosity_timer()


@hub.handles(protocol.TEST_REACH_OUT)
async def _test_reach_out(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Settings -> Curiosity's Test button: she reaches out right now, the same
    way she would after a quiet spell (a check-in -- her saved questions are kept for
    the real thing), and then waits for a reply like after any reach-out.
    """
    problem = ""
    if not brain.llm.owns_conversation:
        problem = "She can't reach out while a harness is in control, or with no LLM set up."
    elif profiles.read_roleplay_active():
        problem = "She doesn't reach out during role-play."
    elif brain.reply_lock.locked():
        problem = "She's in the middle of a reply -- try again in a moment."
    if problem:
        await hub.send(websocket, curiosity_timer_message(problem))
        return
    async with brain.reply_lock:
        await _reach_out(brain, test=True)


async def _reach_out(brain: Brain, *, test: bool = False) -> None:
    """Writes her unprompted message and delivers it like a reply -- expression,
    text and voice -- to every connected device, since there's no one device
    that asked. Logged in her chat log. Marked as done even if nothing came of
    it, so a failing model can't make her try every minute. A `test` (the
    Settings button) is a plain check-in, so it doesn't use up a saved question.
    """
    question = None if test else curiosity.question_to_reach_out_with()
    journal = conversation.read_latest_journal()
    wondering = "" if test or question else curiosity.take_diary_question(journal)  # no saved question: her diary's
    brain.llm.set_user_info(persona.effective_user_info())
    brain.llm.set_left_off(conversation.read_left_off())  # she only reaches out outside role-play
    brain.llm.set_journal(journal)
    brain.llm.set_sampling(sampling.active_values())
    brain.llm.set_curiosity("")  # the reach-out note carries its own instruction
    print("[brain] a quiet spell -- reaching out" + (f" with {question['text']!r}" if question else " with her diary's question" if wondering else ""))
    try:
        result = await asyncio.to_thread(brain.llm.reach_out, question["text"] if question else wondering or None)
        text, mood = result.text, result.mood
    finally:
        curiosity.mark_reached_out(question["id"] if question else None)
        await broadcast_curiosity_timer()
    if not text:
        await hub.debug_broadcast("curiosity", "tried to reach out after a quiet spell, but the model said nothing")
        return
    await hub.debug_broadcast(
        "curiosity",
        f"reached out {'(test from Settings)' if test else 'after a quiet spell'} "
        f"({'with a saved question' if question else 'with her diary question' if wondering else 'a check-in'}, {len(text)} chars, "
        f"to {len(hub.RENDERER_CONNECTIONS)} device(s))",
    )
    await hub.broadcast(protocol.set_expression(mood, 1.0))
    await hub.broadcast(protocol.speak_text(text, reach_out=True))
    conversation.log_reach_out(text)
    if not voice_settings.read_voice_active() or isinstance(brain.tts, NoneTTS):
        return
    try:
        wav_bytes, frames = await asyncio.to_thread(brain.tts.synthesize, text, mood)
    except Exception as exc:
        print(f"[brain] TTS failed for her reach-out: {exc!r}")
        return
    await hub.broadcast(protocol.speak_audio(base64.b64encode(wav_bytes).decode("ascii"), brain.tts.SAMPLE_RATE, text, frames))
