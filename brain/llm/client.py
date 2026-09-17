"""Local LLM clients -- spec section 5's "own the conversation loop end-to-
end". LocalLLM talks to any OpenAI-compatible endpoint (LM Studio,
llama-server, etc.); OllamaLLM shares all of LocalLLM's persona/soul/memory/
history logic via inheritance but talks to Ollama's native /api/chat
instead, the only way to get a real, honored `think: false` against a
reasoning model (see OllamaLLM's own docstring). NoneLLM is the
placeholder used when nothing's configured at all (see its own docstring).
No further real-backend provider abstraction beyond LocalLLM/OllamaLLM --
matches this build's own minimalism, add a third only if/when one's
actually needed.

Conversation history here is in-memory only, lost on restart -- this is
ordinary within-session continuity (every chat needs *some* form of this to
hold a conversation at all), not the persistent cross-session memory/recall
system SPEC.md section 2 explicitly excludes.
"""

import re

import httpx
from openai import OpenAI

# Mirrors this VRM's actual expression presets (confirmed via
# vrm.expressionManager.expressionMap in the live Renderer -- not guessed):
# happy/angry/sad/relaxed/surprised are the mood ones; neutral means "none
# of the above", not a settable expression Brain ever sends.
VALID_MOODS = {"happy", "angry", "sad", "relaxed", "surprised", "neutral"}

# Who Glitch is by default, replaced (not appended to) by an active
# custom soul (brain/souls.py) -- MOOD_TAG_INSTRUCTION below stays fixed
# underneath either one, since the Renderer's expression system depends
# on it regardless of which personality is currently active.
DEFAULT_PERSONALITY = "You are Glitch, a friendly and curious AI companion. Keep replies conversational and fairly short."

MOOD_TAG_INSTRUCTION = (
    "End every reply, on its own at the very end, with exactly one mood tag "
    "chosen from: [mood: neutral] [mood: happy] [mood: sad] [mood: angry] [mood: surprised] "
    "[mood: relaxed] -- pick whichever best matches the emotional tone of what you just said."
)

# Same "stays fixed underneath any soul/persona" role as MOOD_TAG_INSTRUCTION
# above -- a plain nudge toward admitting uncertainty rather than a hard
# guarantee; how well it's actually followed depends on the model behind
# whichever LLM engine is active. Deliberately scoped to factual claims,
# not opinions/suggestions/casual chat, so this doesn't make her generally
# hedgy about things that were never a knowledge question in the first place.
HONESTY_INSTRUCTION = (
    "If you're not actually confident about a specific fact, date, name, or detail -- "
    "something you could genuinely be wrong about -- just say you don't know or aren't sure, "
    "plainly, instead of guessing or making something up. This doesn't apply to opinions, "
    "suggestions, or ordinary conversation, only real factual claims."
)

_MOOD_TAG = re.compile(r"\[mood:\s*(\w+)\]\s*$", re.IGNORECASE)


# No timeout on the OpenAI client's own default is unbounded enough to be
# indistinguishable from a true hang -- confirmed live: a local LLM server
# that answers /v1/models instantly can still leave /v1/chat/completions
# connected-but-silent forever (a stuck generation thread, not a network
# failure), and without this the request just sits in asyncio.to_thread
# with nothing ever sent back to the Renderer -- "she's not responding"
# with no error, not even a slow one. 120s is chosen above the ~90s a
# legitimately slow reasoning-model reply has been seen to take (see
# MAX_HISTORY_MESSAGES's comment) so this only fires for a genuine hang,
# not a merely slow local model.
REQUEST_TIMEOUT_SEC = 120

# Ollama's own default (5m) unloads a model from memory after that long
# idle, so the next request pays to load it back in -- confirmed live: a
# ~25 minute gap between messages was enough to trigger this, and
# reloading this size of model took long enough to blow straight past
# REQUEST_TIMEOUT_SEC and surface as a plain timeout with no indication
# why. "-1" tells Ollama to never unload it once loaded, trading the
# memory it holds at rest for never eating a multi-minute cold-load in
# the middle of a conversation. Only meaningful for OllamaLLM -- LM
# Studio and other OpenAI-compatible engines have no equivalent knob this
# code controls.
# A number, not a duration string -- confirmed live: Ollama parses this as
# a Go duration and a bare "-1" with no unit suffix 400s ("time: missing
# unit in duration"). -1 as a JSON number is its own documented special
# case for "never unload", distinct from a duration string entirely.
OLLAMA_KEEP_ALIVE = -1


def list_models(endpoint: str, api_key: str | None = None) -> list[str]:
    """The model IDs an OpenAI-compatible endpoint currently has loaded
    (GET /v1/models) -- lets the LLM-engine editor offer real choices
    instead of the user having to already know (or guess) the exact
    string the endpoint expects, which is exactly what produced a
    confirmed live bug: an empty/guessed model field serializing to a
    literal JSON null (see LocalLLM.__init__) that crashed one user's LM
    Studio server outright instead of defaulting gracefully.
    """
    # A short timeout here, not REQUEST_TIMEOUT_SEC -- this only ever lists
    # already-loaded models, which should never legitimately take long, so
    # there's no reason to make the settings UI wait as long as a real
    # generation is allowed to.
    client = OpenAI(base_url=endpoint, api_key=api_key or "not-needed", timeout=15)
    return sorted(model.id for model in client.models.list())


def list_ollama_models(endpoint: str, api_key: str | None = None) -> list[str]:
    """Same purpose as list_models, but against Ollama's native GET
    /api/tags instead of the OpenAI-compatible GET /v1/models -- endpoint
    here is Ollama's own base URL (no /v1), matching OllamaLLM. api_key is
    accepted for symmetry with list_models' signature (the settings UI
    calls whichever one matches the engine's provider without needing to
    know its parameters differ) but unused -- Ollama's own API has no
    concept of one.
    """
    with httpx.Client(base_url=endpoint.rstrip("/"), timeout=15) as client:
        response = client.get("/api/tags")
        response.raise_for_status()
        models = response.json().get("models") or []
    names = {m.get("model") or m.get("name") for m in models}
    return sorted(name for name in names if name)


# Not a memory system (SPEC.md section 2 excludes that) -- just a sane
# bound on how much history gets resent every turn. Found by hitting it
# directly: a long test session let this grow unbounded and each call got
# progressively slower re-processing a ever-larger context on a local
# model, to the point one reply took 90+s and looked hung. Trimming keeps
# every turn's latency roughly flat instead of degrading over a session.
MAX_HISTORY_MESSAGES = 20  # ~10 user/assistant exchanges

# Hard cap on how many tokens a single reply is allowed to generate.
# Found via a real incident: asking her to reply in exactly five words
# instead produced a reply that ran past 6,000 tokens and never stopped
# looking "hung" -- chat.completions.create had no limit of its own, and
# the model didn't reliably emit its own stop token.
#
# Root cause (confirmed via a direct API probe, not guessed): this
# session's configured model is a reasoning/"thinking" model -- it writes
# its internal monologue to the OpenAI response's separate
# `reasoning_content` field and leaves `content` (the only field this
# file reads) empty until it's done thinking, however long that takes.
# For a trivial "say hi in five words" it was still visibly going in
# circles recounting word counts past 6,000 tokens with `content` still
# empty. A low cap (e.g. 400) bounds the hang but then tends to cut the
# response off *during* the thinking phase, before any real content --
# trading a hang for a silently empty reply, not actually fixing anything.
# 2000 gives this kind of model realistic room to finish thinking and
# still produce an answer for an ordinary conversational turn, at the
# cost of a slower worst-case reply than a non-reasoning model would need
# -- if replies are still coming back empty/slow, the real fix is a
# non-reasoning/instruct model for this slot, not raising this further.
MAX_REPLY_TOKENS = 2000

# OllamaLLM-only, and only when think=true (role-play deliberately runs
# with think=false specifically to avoid needing this at all -- see
# OllamaLLM's own docstring). num_predict is one shared budget covering
# both the thinking tokens and the actual reply for a single completion,
# so 2000 is routinely not enough room for a model to both finish
# reasoning and still answer -- confirmed live, replies came back empty
# after ~2000 tokens of pure thinking. This is deliberately a second,
# larger constant rather than raising MAX_REPLY_TOKENS itself, which
# would slow down every other reply (LM Studio, think=false) for a
# problem only the think=true path actually has.
MAX_REPLY_TOKENS_THINKING = 8000

# A short phrase at most -- this call only ever needs to return "NONE" or
# one compact fact, never a real reply, so this is deliberately far below
# MAX_REPLY_TOKENS. Keeps this background call cheap and fast regardless of
# how the main conversational reply is behaving.
MAX_MEMORY_EXTRACT_TOKENS = 200

_MEMORY_EXTRACT_SYSTEM_PROMPT = (
    "You maintain a short list of durable facts about the user across conversations for an AI "
    "companion. Given the latest exchange below, decide whether it reveals ONE new fact worth "
    "remembering long-term: who the user is (name, role), stable preferences, ongoing life "
    "details, or stable personality traits.\n\n"
    "SKIP: anything already in the existing list, trivial/obvious statements, one-off requests, "
    "opinions about this chat itself, or temporary states (mood right now, what they're doing "
    "this exact minute).\n\n"
    "Reply with ONLY the new fact as one short phrase (e.g. \"prefers dark mode\", \"has a cat "
    "named Pixel\"), or the single word NONE if nothing new and durable came up. Never invent "
    "facts not actually stated or clearly implied."
)


def _reply_content(message) -> str:
    """Returns the model's actual reply text -- `content` only, deliberately
    never `reasoning_content`.

    Some reasoning/"thinking" models (e.g. Qwen3 served via LM Studio) put
    everything in a non-standard `reasoning_content` field and leave
    `content` empty if generation's token budget runs out before the model
    transitions from thinking to answering -- see MAX_REPLY_TOKENS's own
    comment. It's tempting to fall back to `reasoning_content` when that
    happens so *something* comes back instead of silence, but confirmed
    live that this is worse: it dumps a whole raw scratchpad -- rambling,
    unfinished, never meant to be read as dialogue -- into the chat bubble
    and straight into TTS. A real reasoning/thoughts panel (SillyTavern's
    approach: reasoning and reply always shown in separate UI regions,
    reasoning never substituting as the reply) would be a legitimate
    feature; silently promoting scratch thoughts to "what she said" is not
    that, it's worse than the empty-reply case it's trying to avoid.
    main.py's _reply_to already treats an empty result as "nothing to
    say" and sends no_reply -- the per-model reasoning-token cap (set
    server-side, e.g. in LM Studio) is what actually bounds how often that
    happens, not this function.
    """
    return (message.content or "").strip()


def _extract_mood(text: str) -> tuple[str, str]:
    """Returns (mood, text_with_tag_removed). Falls back to "neutral" if the
    model forgot the tag or used something outside VALID_MOODS, rather than
    erroring -- a missing/malformed tag shouldn't break the reply.
    """
    match = _MOOD_TAG.search(text)
    if not match:
        return "neutral", text.strip()
    mood = match.group(1).lower()
    if mood not in VALID_MOODS:
        mood = "neutral"
    return mood, _MOOD_TAG.sub("", text).strip()


class LocalLLM:
    def __init__(self, endpoint: str, model: str | None, api_key: str | None = None) -> None:
        self._client = OpenAI(base_url=endpoint, api_key=api_key or "not-needed", timeout=REQUEST_TIMEOUT_SEC)
        # "" not None: the openai client's `model` param has no omit-if-
        # absent sentinel (unlike e.g. audio.speech.create's `speed`) --
        # passing None serializes to a literal JSON `null` in the request
        # body, not a dropped field. Confirmed live: LM Studio's server
        # crashed on that with "Cannot read properties of null (reading
        # 'toLowerCase')" instead of defaulting to its loaded model the
        # way config.yaml's own `model: null` comment promises. An empty
        # string is a normal, harmless value for a server to see instead.
        self._model = model or ""
        self._history: list[dict] = []
        self._persona = ""
        self._soul = ""
        self._memory = ""

    def _complete(self, messages: list[dict], max_tokens: int) -> str:
        """Runs one chat completion and returns just the reply text (see
        _reply_content -- content only, never reasoning_content). Split out
        from reply()/maybe_extract_memory() specifically so OllamaLLM can
        override this one method (different wire protocol -- Ollama's
        native /api/chat, not an OpenAI-compatible endpoint) while
        inheriting everything else (history, system prompt, mood tag
        handling) unchanged.
        """
        response = self._client.chat.completions.create(model=self._model, messages=messages, max_tokens=max_tokens)
        return _reply_content(response.choices[0].message)

    def set_persona(self, persona_md: str) -> None:
        """Sets the active role-play profile (freeform markdown -- character
        and scenario combined, not separate fields; see brain/profiles.py),
        folded into the system prompt for every reply from here on. Resets
        conversation history -- continuing an old exchange under a brand
        new role-play premise would just be incoherent, so a persona
        change starts the conversation fresh.
        """
        self._persona = persona_md.strip()
        self._history.clear()

    def set_soul(self, soul_md: str) -> None:
        """Sets the active soul (who Glitch is + example dialogue,
        combined; see brain/souls.py) -- replaces DEFAULT_PERSONALITY
        rather than adding to it, since a soul redefines who she is
        rather than layering onto the default. Resets conversation
        history for the same reason set_persona does: continuing an old
        exchange as a different character would be incoherent.
        """
        self._soul = soul_md.strip()
        self._history.clear()

    def set_memory(self, memory_block: str) -> None:
        """Sets what Glitch remembers about the real user (brain/memory.py --
        her own native memory, never touched by the Hermes harness). Unlike
        set_persona/set_soul, this deliberately does NOT clear _history: a
        newly-learned fact is additive context picked up mid-conversation,
        not an identity change, and nuking the conversation every time a
        fact gets extracted would defeat the point of extracting it live.
        """
        self._memory = memory_block.strip()

    def _system_prompt(self) -> str:
        personality = self._soul or DEFAULT_PERSONALITY
        parts = [personality, MOOD_TAG_INSTRUCTION, HONESTY_INSTRUCTION]
        if self._memory:
            # Placed before the persona block -- this describes the real
            # user underneath whatever pretend scenario is currently
            # layered on top, not something a role-play toggle should hide.
            parts.append(f"What you remember about the user from past conversations:\n{self._memory}")
        if self._persona:
            parts.append(
                f"You are role-playing with the user under this profile:\n{self._persona}\n\n"
                "Stay in character and play out this scenario naturally as the conversation continues."
            )
        return "\n\n".join(parts)

    def reply(self, user_text: str, image_b64: str | None = None, image_mime: str = "image/jpeg") -> tuple[str, str]:
        """Returns (reply_text, mood) -- reply_text has the mood tag
        already stripped out (never shown/spoken), mood is one of
        VALID_MOODS.

        image_b64, when given (a camera/desktop snapshot -- see main.py's
        _reply_to), turns this turn's content into the standard OpenAI
        multimodal list instead of a plain string, so any vision-capable
        model behind this endpoint sees it. No "does this engine support
        vision" flag exists -- an endpoint/model that can't handle images
        is left to fail exactly the way a bad model string already does
        (caught by _reply_to's broad except, surfaced as a speak_text
        stand-in), rather than adding a second way to configure the same
        failure mode.
        """
        content: str | list[dict] = user_text
        if image_b64:
            content = [
                {"type": "text", "text": user_text or "What do you see?"},
                {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}},
            ]
        self._history.append({"role": "user", "content": content})
        del self._history[:-MAX_HISTORY_MESSAGES]
        messages = [{"role": "system", "content": self._system_prompt()}, *self._history]
        raw_reply = self._complete(messages, MAX_REPLY_TOKENS)
        mood, reply_text = _extract_mood(raw_reply)
        # Stored cleaned, not with the tag -- keeps the tag from cluttering
        # future turns' context for no benefit (the system prompt alone is
        # enough to keep the model tagging consistently turn to turn).
        self._history.append({"role": "assistant", "content": reply_text})
        return reply_text, mood

    def pop_last_exchange(self) -> str | None:
        """Removes the most recent turn from history and returns the user
        text it was for, or None if there's nothing sensible to pop
        (empty history).

        Handles two shapes at the tail of history: a complete
        user-then-assistant pair (the ordinary case -- an empty or
        nonsensical reply still gets appended as a real, if unsatisfying,
        assistant turn, see main.py's _reply_to), and a dangling lone user
        turn with no assistant after it (reply() appends the user turn
        *before* calling _complete, so a request that raises -- a
        connection error, say -- leaves exactly this shape). Popping just
        the dangling turn in that second case matters just as much as
        popping the pair in the first: leaving it in place would mean the
        next reply() call appends a second, back-to-back user turn on top
        of it with no assistant in between, which is the same "he's just
        repeating himself" problem this method exists to avoid in the
        first place.

        Used by main.py's regenerate_last: popping first means the
        follow-up reply() call this feeds into starts from the exact same
        state as the original attempt, rather than piling another user
        turn on top of a stale one -- which is what simply resending the
        same text as a brand new message would do.

        An image attached to the popped turn is not recoverable here (its
        content becomes a list, not a plain string) -- only the text part
        is returned, same limitation the Renderer's own retry button
        already accepts by hiding itself on an image message entirely.
        """
        if not self._history:
            return None
        if self._history[-1].get("role") == "assistant":
            if len(self._history) < 2 or self._history[-2].get("role") != "user":
                return None
            user_message = self._history.pop(-2)
            self._history.pop()  # the assistant turn that followed it
        elif self._history[-1].get("role") == "user":
            user_message = self._history.pop()
        else:
            return None
        content = user_message.get("content")
        if isinstance(content, list):
            return "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
        return content

    def maybe_extract_memory(self, user_text: str, reply_text: str, existing_entries: list[str]) -> str | None:
        """One extra lightweight chat.completions.create call, entirely
        separate from self._history/self._system_prompt -- looks at a
        single already-completed exchange and returns a new durable fact
        as a short phrase, or None if nothing new/durable came up.
        Deliberately not a method on HarnessLLM: main.py only ever calls
        this after confirming brain.llm is a LocalLLM, so Hermes's own
        memory (see brain/harness.py) is never touched by this at all.

        Reuses self._client/self._model -- same endpoint the real
        conversation already uses, no separate engine config needed.
        Existing entries are included so the model can judge novelty
        instead of re-proposing something already remembered.
        """
        existing_block = "\n".join(f"- {e}" for e in existing_entries) or "(none yet)"
        messages = [
            {"role": "system", "content": _MEMORY_EXTRACT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Existing remembered facts:\n{existing_block}\n\n"
                    f"Latest exchange:\nUser: {user_text}\nAssistant: {reply_text}"
                ),
            },
        ]
        result = self._complete(messages, MAX_MEMORY_EXTRACT_TOKENS)
        if not result or result.upper().startswith("NONE"):
            return None
        return result.strip("\"'")


def _to_ollama_message(message: dict) -> dict:
    """Translates one message's `content` from the OpenAI multimodal list
    shape (LocalLLM.reply()'s image_b64 path -- `content: [{"type": "text",
    ...}, {"type": "image_url", ...}]`) into Ollama's native /api/chat shape,
    where `content` must always be a plain string and image data goes in a
    separate `images` list of bare base64 (no `data:<mime>;base64,` prefix).
    Confirmed live: sending the OpenAI list shape straight through 400s --
    Ollama's schema rejects a non-string `content` outright. A plain-string
    `content` (every text-only turn, still the overwhelming majority) passes
    through unchanged. Applied per-message in _complete rather than at the
    point content is built, so past image turns already sitting in history
    get translated on every later call too, not just the turn that sent them.
    """
    content = message.get("content")
    if not isinstance(content, list):
        return message
    text_parts = []
    images = []
    for part in content:
        if part.get("type") == "text":
            text_parts.append(part.get("text", ""))
        elif part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
            images.append(url.split(",", 1)[1] if "," in url else url)
    translated = {**message, "content": "\n".join(text_parts)}
    if images:
        translated["images"] = images
    return translated


class OllamaLLM(LocalLLM):
    """Same persona/soul/memory/history system as LocalLLM (all inherited
    unchanged -- reply(), maybe_extract_memory(), set_persona() etc. don't
    need to know or care which backend _complete() actually talks to).
    Only __init__ and _complete differ: this talks to Ollama's *native*
    /api/chat instead of an OpenAI-compatible endpoint.

    That distinction is load-bearing, not stylistic -- confirmed via a
    direct side-by-side probe against this same model: Ollama's
    OpenAI-compatible /v1/chat/completions silently ignores `think: false`
    (reasoning still runs every time), while /api/chat honors it exactly.
    Reasoning-heavy local models otherwise hit the same "thinks herself to
    death" problem LM Studio's models do (see MAX_REPLY_TOKENS's comment)
    -- this is what makes disabling it actually reliable instead of the
    budget-cap/`/no_think`-prompt workarounds that setup needs.
    """

    def __init__(self, endpoint: str, model: str | None, api_key: str | None = None, think: bool = False) -> None:
        # No OpenAI client here on purpose -- endpoint is Ollama's own base
        # URL (e.g. http://localhost:11434), not an OpenAI-compatible /v1
        # one, so this talks to it directly over plain HTTP instead.
        self._http = httpx.Client(base_url=endpoint.rstrip("/"), timeout=REQUEST_TIMEOUT_SEC)
        self._model = model or ""
        self._think = think
        self._history: list[dict] = []
        self._persona = ""
        self._soul = ""
        self._memory = ""

    def _complete(self, messages: list[dict], max_tokens: int) -> str:
        # See MAX_REPLY_TOKENS_THINKING's own comment -- the caller passes
        # MAX_REPLY_TOKENS same as every other engine, but that's not
        # enough room once thinking is actually turned on, so this widens
        # it right here rather than needing every caller to know that.
        if self._think:
            max_tokens = max(max_tokens, MAX_REPLY_TOKENS_THINKING)
        response = self._http.post(
            "/api/chat",
            json={
                "model": self._model,
                "messages": [_to_ollama_message(m) for m in messages],
                "think": self._think,
                "stream": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
                # Ollama's own generation-parameter shape -- num_predict is
                # its equivalent of max_tokens, nested under `options`
                # rather than top-level like the OpenAI-compatible API.
                "options": {"num_predict": max_tokens},
            },
        )
        response.raise_for_status()
        message = response.json().get("message") or {}
        return (message.get("content") or "").strip()


class HarnessLLM:
    """Delegates entirely to an external agent harness (brain/harness.py;
    e.g. Hermes Agent's own OpenAI-compatible /v1/chat/completions,
    https://github.com/NousResearch/hermes-agent) instead of this app's
    own persona/soul/profile/history system. When this is active, the
    harness's own agent/session config *is* Glitch's entire personality
    -- Brain becomes a thin relay, not a second source of "who she is",
    so unlike LocalLLM this sends no system prompt and keeps no
    conversation history of its own (the harness owns that).

    Duck-types LocalLLM's reply() -> (reply_text, mood) contract exactly,
    so main.py's _reply_to needs no changes to work with either -- it
    just calls brain.llm.reply(text) without caring which one is active.
    Still runs replies through the same [mood: ...] tag convention
    (_extract_mood) so facial expressions keep working automatically if
    the harness's own Glitch persona has been set up to include the tag;
    falls back to "neutral" exactly like a Brain-side reply missing the
    tag already does otherwise.
    """

    def __init__(self, endpoint: str, model: str | None = None, api_key: str | None = None) -> None:
        self._client = OpenAI(base_url=endpoint, api_key=api_key or "not-needed", timeout=REQUEST_TIMEOUT_SEC)
        self._model = model or ""  # see LocalLLM.__init__'s comment -- None serializes to a literal JSON null

    def reply(self, user_text: str, image_b64: str | None = None, image_mime: str = "image/jpeg") -> tuple[str, str]:
        # Same MAX_REPLY_TOKENS cap as LocalLLM.reply, same reasoning -- a
        # runaway generation is exactly as much of a hang either way. If a
        # given harness turns out to legitimately need more tokens for its
        # own internal multi-step reasoning within one completion, this is
        # the first place to revisit, not something to just remove.
        content: str | list[dict] = user_text
        if image_b64:
            # Same multimodal content shape as LocalLLM.reply -- whether
            # the harness itself is vision-capable is between it and
            # whatever model it's running; this just passes the image
            # through the same way a text turn already does.
            content = [
                {"type": "text", "text": user_text or "What do you see?"},
                {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}},
            ]
        response = self._client.chat.completions.create(
            model=self._model, messages=[{"role": "user", "content": content}], max_tokens=MAX_REPLY_TOKENS
        )
        raw_reply = _reply_content(response.choices[0].message)
        # _extract_mood returns (mood, text) -- swapped here to match this
        # method's own (reply_text, mood) contract, same as LocalLLM.reply
        # does. Confirmed live: without this swap, main.py's _reply_to
        # (which unpacks `reply_text, mood = brain.llm.reply(text)`) got
        # the mood tag ("neutral") back as the spoken reply text, and the
        # real reply silently ended up in `mood` instead.
        mood, reply_text = _extract_mood(raw_reply)
        return reply_text, mood


class NoneLLM:
    """Placeholder used when genuinely no LLM is configured -- a fresh
    install with nothing in config.yaml's now-optional brain.llm block and
    no saved engine chosen yet via Settings (llm_engines.py's NONE_NAME).
    Duck-types LocalLLM's reply()/set_persona()/set_soul()/set_memory()
    contract so main.py's _build_llm needs no special-casing beyond
    building this instead of a real client, and every other call site
    (main.py's _reply_to, the settings-panel handlers) keeps working
    completely unchanged.

    Deliberately not a subclass of LocalLLM -- main.py gates memory
    extraction and persona/soul priming on isinstance(brain.llm, LocalLLM)
    specifically so those never fire for a harness relay (HarnessLLM); the
    same exclusion is exactly right here too; there's nothing real to
    extract memory from or apply a persona to yet.

    reply() always returns the same honest, in-character-adjacent line
    instead of crashing or silently doing nothing -- someone freshly
    cloning this project and opening the Renderer for the first time
    should see *why* nothing's happening without reading code to find out.
    """

    def set_persona(self, persona_md: str) -> None:
        pass

    def set_soul(self, soul_md: str) -> None:
        pass

    def set_memory(self, memory_block: str) -> None:
        pass

    def reply(self, user_text: str, image_b64: str | None = None, image_mime: str = "image/jpeg") -> tuple[str, str]:
        return "(No LLM engine is configured yet -- add one in Settings, under LLM.)", "neutral"
