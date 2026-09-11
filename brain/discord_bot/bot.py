"""Discord integration (SPEC.md section 5): text chat, plus voice-channel
presence (voice.py) now that text was verified working first.

Text chat replies in exactly three cases:
  - a DM from the configured allowed_user_id (everyone else's DMs are
    ignored -- a raw Discord bot otherwise replies to anyone who finds it)
  - any message in one of the configured allowed_channel_ids, from anyone
  - a message anywhere else that @mentions the bot, from anyone

Voice works differently on purpose: she auto-joins whichever voice
channel allowed_user_id enters (following if they switch channels), but
once in, anyone in the channel can wake her by saying "glitch" -- gating
by user identity doesn't carry over the same way for voice, see voice.py.
Leaving is command-only ("!leave", allowed_user_id only), no auto-leave.

Every conversation -- each DM thread, each channel, each guild's voice
session -- gets its own LocalLLM instance and history, completely
separate from whatever's live on the Renderer's screen: a Discord message
never changes what's showing on her face there, and vice versa (an
explicit choice, not an oversight).
"""

import asyncio
import tempfile
import threading
import time
import wave
from pathlib import Path

import discord
from discord.ext import voice_recv

from llm import LocalLLM
# brain/voice/ (STT+TTS classes) vs this package's own sibling voice.py
# (voice-channel sink/wake-word logic) -- same name, different modules;
# the absolute vs. relative import below is what tells them apart.
from voice import FasterWhisperSTT, KokoroTTS

from .voice import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, UtteranceSink, extract_wake_query

# Discord's hard per-message character cap -- a long reply needs to be
# split into multiple sends rather than silently truncated or rejected.
DISCORD_MESSAGE_LIMIT = 2000

LEAVE_COMMAND = "!leave"


class DiscordBrain(discord.Client):
    def __init__(
        self,
        *,
        llm_cfg: dict,
        allowed_user_id: int | None,
        allowed_channel_ids: set[int],
        stt: FasterWhisperSTT,
        tts: KokoroTTS,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._llm_cfg = llm_cfg
        self._allowed_user_id = allowed_user_id
        self._allowed_channel_ids = allowed_channel_ids
        self._stt = stt
        self._tts = tts
        # Keyed by "dm:<user_id>" / "channel:<channel_id>" / "voice:<guild_id>"
        # -- lazily created per conversation the first time it's used.
        self._conversations: dict[str, LocalLLM] = {}
        self._voice_clients: dict[int, voice_recv.VoiceRecvClient] = {}
        # Guards each guild's voice conversation history and playback
        # ordering -- utterances are handled on worker threads (voice.py),
        # so two people talking over each other in the same channel could
        # otherwise race on the same LocalLLM instance.
        self._voice_locks: dict[int, threading.Lock] = {}

    async def on_ready(self) -> None:
        print(f"[discord] logged in as {self.user}")

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.id != self._allowed_user_id:
            return
        if after.channel is None or before.channel == after.channel:
            return  # they left/disconnected, or this is a mute/deafen toggle -- leaving is !leave-only

        existing = self._voice_clients.get(member.guild.id)
        if existing and existing.is_connected():
            if existing.channel.id != after.channel.id:
                await existing.move_to(after.channel)
            return

        print(f"[discord-voice] joining {after.channel.name!r} in {member.guild.name!r}")
        vc = await after.channel.connect(cls=voice_recv.VoiceRecvClient)
        self._voice_clients[member.guild.id] = vc

        def on_utterance(user, pcm: bytes) -> None:
            self._on_voice_utterance(member.guild, vc, user, pcm)

        vc.listen(UtteranceSink(on_utterance))

    async def _leave_voice(self, guild: discord.Guild) -> None:
        vc = self._voice_clients.pop(guild.id, None)
        if vc:
            await vc.disconnect()
            print(f"[discord-voice] left voice in {guild.name!r}")

    def _on_voice_utterance(self, guild: discord.Guild, voice_client, user, pcm: bytes) -> None:
        lock = self._voice_locks.setdefault(guild.id, threading.Lock())
        with lock:
            wav_path = _pcm_to_wav_file(pcm)
            try:
                transcript = self._stt.transcribe(wav_path)
            except Exception as exc:
                print(f"[discord-voice] STT failed: {exc!r}")
                return
            finally:
                Path(wav_path).unlink(missing_ok=True)

            if not transcript.strip():
                return
            query = extract_wake_query(transcript)
            if query is None:
                return  # not directed at her -- ignore silently, no history stored
            print(f"[discord-voice] {user}: {transcript!r} -> {query!r}")

            key = f"voice:{guild.id}"
            llm = self._conversations.get(key)
            if llm is None:
                llm = LocalLLM(
                    endpoint=self._llm_cfg["endpoint"],
                    model=self._llm_cfg.get("model"),
                    api_key=self._llm_cfg.get("api_key"),
                )
                self._conversations[key] = llm

            try:
                reply_text, _mood = llm.reply(query)
            except Exception as exc:
                print(f"[discord-voice] LLM call failed: {exc!r}")
                return

            try:
                wav_bytes, _frames = self._tts.synthesize(reply_text)
            except Exception as exc:
                print(f"[discord-voice] TTS failed: {exc!r}")
                return

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(wav_bytes)
                reply_path = f.name

            while voice_client.is_playing():
                time.sleep(0.1)

            def _cleanup(_err) -> None:
                Path(reply_path).unlink(missing_ok=True)

            voice_client.play(discord.FFmpegPCMAudio(reply_path), after=_cleanup)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.id == self.user.id:
            return

        is_dm = message.guild is None

        # Control command, not a chat query -- checked before the normal
        # trigger-scope gating below so it works in any channel, and
        # restricted to allowed_user_id since it's a command, not a reply.
        if not is_dm and message.author.id == self._allowed_user_id and message.content.strip().lower() == LEAVE_COMMAND:
            await self._leave_voice(message.guild)
            return

        mentioned = self.user in message.mentions

        if is_dm:
            if message.author.id != self._allowed_user_id:
                return
            key = f"dm:{message.author.id}"
        elif message.channel.id in self._allowed_channel_ids:
            key = f"channel:{message.channel.id}"
        elif mentioned:
            key = f"channel:{message.channel.id}"
        else:
            return

        text = message.content
        if mentioned:
            text = text.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()
        if not text:
            return

        llm = self._conversations.get(key)
        if llm is None:
            llm = LocalLLM(
                endpoint=self._llm_cfg["endpoint"],
                model=self._llm_cfg.get("model"),
                api_key=self._llm_cfg.get("api_key"),
            )
            self._conversations[key] = llm

        async with message.channel.typing():
            try:
                # reply() also returns a mood tag (for the Renderer's
                # expression crossfade) -- meaningless here, discarded.
                reply_text, _mood = await asyncio.to_thread(llm.reply, text)
            except Exception as exc:
                print(f"[discord] LLM call failed: {exc!r}")
                reply_text = f"(couldn't reach the LLM: {exc})"

        for start in range(0, len(reply_text), DISCORD_MESSAGE_LIMIT):
            await message.channel.send(reply_text[start : start + DISCORD_MESSAGE_LIMIT])


def _pcm_to_wav_file(pcm: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm)
    return path


def build_discord_client(
    llm_cfg: dict,
    allowed_user_id: int | None,
    allowed_channel_ids: list[int],
    stt: FasterWhisperSTT,
    tts: KokoroTTS,
) -> DiscordBrain:
    intents = discord.Intents.default()
    intents.message_content = True  # required to see message text at all
    intents.voice_states = True  # required for on_voice_state_update (auto-join)
    return DiscordBrain(
        llm_cfg=llm_cfg,
        allowed_user_id=allowed_user_id,
        allowed_channel_ids=set(allowed_channel_ids),
        stt=stt,
        tts=tts,
        intents=intents,
    )


async def run_discord_bot(client: DiscordBrain, token: str) -> None:
    """Wraps client.start() so a bad token or other startup failure gets
    logged instead of silently sitting in a background task, unnoticed
    until asyncio complains about a never-retrieved exception.
    """
    try:
        await client.start(token)
    except Exception as exc:
        print(f"[discord] bot crashed: {exc!r}")
