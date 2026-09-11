"""Discord integration (SPEC.md section 5): text chat, plus join-and-speak
voice-channel presence.

Text chat replies in exactly three cases:
  - a DM from the configured allowed_user_id (everyone else's DMs are
    ignored -- a raw Discord bot otherwise replies to anyone who finds it)
  - any message in one of the configured allowed_channel_ids, from anyone
  - a message anywhere else that @mentions the bot, from anyone

Voice is join-and-speak only, not listen-and-respond: she auto-joins
whichever voice channel allowed_user_id enters (following if they switch
channels) and speaks her text-chat replies out loud while connected, but
does not transcribe or react to anything said in the channel. Live
wake-word listening was attempted and deliberately dropped after hitting
a real, currently-unresolved upstream limitation: Discord's voice
channels are now DAVE (E2EE) encrypted by default, and
discord-ext-voice-recv -- the only community library that lets
discord.py receive voice audio at all -- cannot decrypt DAVE packets yet
(open feature request: github.com/imayhaveborkedit/discord-ext-voice-recv
issue #64, no ETA). Every packet it tried to receive Opus-decoded to
garbage ("corrupted stream"), which also appeared to destabilize the
voice session for other real people in the channel -- confirmed live,
not a guess. Sending audio doesn't touch that broken code path at all
(discord.py's own native, DAVE-aware VoiceClient handles it), so
speaking is unaffected; only the extra `discord-ext-voice-recv`
dependency and its receive machinery were removed. Leaving is
command-only ("!leave", allowed_user_id only), no auto-leave.

Every conversation -- each DM thread, each channel -- gets its own
LocalLLM instance and history, completely separate from whatever's live
on the Renderer's screen: a Discord message never changes what's showing
on her face there, and vice versa (an explicit choice, not an oversight).
"""

import asyncio
import tempfile
from pathlib import Path

import discord
import discord.opus

from llm import LocalLLM
from voice import KokoroTTS

# discord.py doesn't reliably auto-load libopus on import (confirmed:
# discord.opus.is_loaded() was False here despite the bundled Windows DLL
# sitting right there in the package) -- without it, encoding audio for
# playback fails, not just receiving it. _load_default() is "private"
# (underscore-prefixed) but it's the only part of discord.py that
# already knows how to find the right library on every platform: the
# bundled DLL on Windows, or a system-installed libopus via
# ctypes.util.find_library on macOS/Linux (which needs actually
# installing there, e.g. `apt install libopus0` -- see setup.sh).
if not discord.opus.is_loaded():
    try:
        discord.opus._load_default()
    except Exception as exc:
        print(f"[discord] warning: couldn't load libopus ({exc!r}) -- voice playback will fail")

# Discord's hard per-message character cap -- a long reply needs to be
# split into multiple sends rather than silently truncated or rejected.
DISCORD_MESSAGE_LIMIT = 2000

LEAVE_COMMAND = "!leave"

# See _connect_voice's docstring -- discord.py's default connect()
# timeout (30s) was confirmed too short live.
VOICE_CONNECT_TIMEOUT_SEC = 60.0
VOICE_CONNECT_RETRIES = 3
VOICE_RETRY_DELAY_SEC = 3.0


class DiscordBrain(discord.Client):
    def __init__(
        self,
        *,
        llm_cfg: dict,
        allowed_user_id: int | None,
        allowed_channel_ids: set[int],
        tts: KokoroTTS,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._llm_cfg = llm_cfg
        self._allowed_user_id = allowed_user_id
        self._allowed_channel_ids = allowed_channel_ids
        self._tts = tts
        # Keyed by "dm:<user_id>" or "channel:<channel_id>" -- lazily
        # created per conversation the first time it's actually used.
        self._conversations: dict[str, LocalLLM] = {}
        self._voice_clients: dict[int, discord.VoiceClient] = {}

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

        if existing:
            # Not cleanly connected anymore (e.g. a Discord voice-server
            # hiccup) -- tear it down explicitly instead of leaving stale
            # state behind while a fresh connection opens underneath it.
            print(f"[discord-voice] stale connection in {member.guild.name!r}, cleaning up before rejoining")
            try:
                await existing.disconnect(force=True)
            except Exception as exc:
                print(f"[discord-voice] cleanup of stale connection failed (continuing anyway): {exc!r}")
            self._voice_clients.pop(member.guild.id, None)

        vc = await self._connect_voice(after.channel)
        if vc is None:
            return  # already logged -- nothing was left half-connected to clean up

        self._voice_clients[member.guild.id] = vc
        print(f"[discord-voice] joined {after.channel.name!r}")

    async def _connect_voice(self, channel: discord.VoiceChannel):
        """Connects with retries. discord.py's own warning when this is
        slow ("Awaiting endpoint... considering raising the timeout and
        reconnecting") names exactly this fix -- confirmed live hitting
        the default 30s connect() timeout during a real voice-server
        hiccup. Each retry also clears whatever discord.py's own
        guild.voice_client registry still thinks is connected first, in
        case the previous attempt left it in a half-connected state that
        would make a plain retry fail immediately with "already
        connected."
        """
        last_exc: Exception | None = None
        for attempt in range(1, VOICE_CONNECT_RETRIES + 1):
            stale = channel.guild.voice_client
            if stale:
                try:
                    await stale.disconnect(force=True)
                except Exception:
                    pass

            print(f"[discord-voice] joining {channel.name!r} in {channel.guild.name!r} (attempt {attempt}/{VOICE_CONNECT_RETRIES})")
            try:
                return await channel.connect(timeout=VOICE_CONNECT_TIMEOUT_SEC)
            except Exception as exc:
                last_exc = exc
                print(f"[discord-voice] connect attempt {attempt} failed: {exc!r}")
                if attempt < VOICE_CONNECT_RETRIES:
                    await asyncio.sleep(VOICE_RETRY_DELAY_SEC)

        print(
            f"[discord-voice] giving up joining {channel.name!r} after {VOICE_CONNECT_RETRIES} attempts: {last_exc!r} -- "
            "this looks like a Discord-side voice-server issue rather than a bug here (discord.py's own "
            "handshake timed out waiting on Discord's servers, not on anything this code controls)."
        )
        return None

    async def _leave_voice(self, guild: discord.Guild) -> None:
        vc = self._voice_clients.pop(guild.id, None)
        if vc:
            await vc.disconnect(force=True)
            print(f"[discord-voice] left voice in {guild.name!r}")

    async def _speak_in_voice(self, guild: discord.Guild, text: str) -> None:
        vc = self._voice_clients.get(guild.id)
        if not vc or not vc.is_connected():
            # Logged even though it's a normal no-op (no active voice
            # connection for this guild) -- previously silent here, which
            # made "not connected to voice" and "connected but playback
            # silently failed" indistinguishable from the log alone.
            print(f"[discord-voice] not connected in {guild.name!r}, skipping speech")
            return
        print(f"[discord-voice] synthesizing reply for {guild.name!r}: {text[:80]!r}")
        try:
            wav_bytes, _frames = await asyncio.to_thread(self._tts.synthesize, text)
        except Exception as exc:
            print(f"[discord-voice] TTS failed: {exc!r}")
            return

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            reply_path = f.name

        def _cleanup(err) -> None:
            if err:
                print(f"[discord-voice] playback error: {err!r}")
            Path(reply_path).unlink(missing_ok=True)

        if vc.is_playing():
            vc.stop()  # a new reply takes priority over finishing the last one
        vc.play(discord.FFmpegPCMAudio(reply_path), after=_cleanup)
        print(f"[discord-voice] playing reply in {vc.channel.name!r}")

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

        print(f"[discord] ({key}) {message.author}: {text!r}")

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

        print(f"[discord] ({key}) reply: {reply_text!r}")

        for start in range(0, len(reply_text), DISCORD_MESSAGE_LIMIT):
            await message.channel.send(reply_text[start : start + DISCORD_MESSAGE_LIMIT])

        if is_dm:
            print("[discord] DM reply -- no guild to speak into, skipping voice")
        else:
            await self._speak_in_voice(message.guild, reply_text)


def build_discord_client(
    llm_cfg: dict,
    allowed_user_id: int | None,
    allowed_channel_ids: list[int],
    tts: KokoroTTS,
) -> DiscordBrain:
    intents = discord.Intents.default()
    intents.message_content = True  # required to see message text at all
    intents.voice_states = True  # required for on_voice_state_update (auto-join)
    return DiscordBrain(
        llm_cfg=llm_cfg,
        allowed_user_id=allowed_user_id,
        allowed_channel_ids=set(allowed_channel_ids),
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
