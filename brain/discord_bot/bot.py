"""Discord text-chat integration (SPEC.md section 5). Text only for now --
full voice-channel presence (the bot joining a call to do live STT/TTS) is
a separate, deliberately deferred phase: discord.py can't receive voice
audio without an extra library, and that's real added scope worth its own
build-and-verify pass rather than bundling in here.

Replies in exactly three cases:
  - a DM from the configured allowed_user_id (everyone else's DMs are
    ignored -- a raw Discord bot otherwise replies to anyone who finds it)
  - any message in one of the configured allowed_channel_ids, from anyone
  - a message anywhere else that @mentions the bot, from anyone

Each DM thread / channel gets its own LocalLLM instance and history,
completely separate from whatever's live on the Renderer's screen -- a
Discord message never changes what's showing on her face there, and vice
versa (this was an explicit choice, not an oversight).
"""

import asyncio

import discord

from llm import LocalLLM

# Discord's hard per-message character cap -- a long reply needs to be
# split into multiple sends rather than silently truncated or rejected.
DISCORD_MESSAGE_LIMIT = 2000


class DiscordBrain(discord.Client):
    def __init__(
        self,
        *,
        llm_cfg: dict,
        allowed_user_id: int | None,
        allowed_channel_ids: set[int],
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._llm_cfg = llm_cfg
        self._allowed_user_id = allowed_user_id
        self._allowed_channel_ids = allowed_channel_ids
        # Keyed by "dm:<user_id>" or "channel:<channel_id>" -- lazily
        # created per conversation the first time it's actually used.
        self._conversations: dict[str, LocalLLM] = {}

    async def on_ready(self) -> None:
        print(f"[discord] logged in as {self.user}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.id == self.user.id:
            return

        is_dm = message.guild is None
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


def build_discord_client(llm_cfg: dict, allowed_user_id: int | None, allowed_channel_ids: list[int]) -> DiscordBrain:
    intents = discord.Intents.default()
    intents.message_content = True  # required to see message text at all
    return DiscordBrain(
        llm_cfg=llm_cfg,
        allowed_user_id=allowed_user_id,
        allowed_channel_ids=set(allowed_channel_ids),
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
