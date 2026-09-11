"""Local OpenAI-compatible LLM client (LM Studio, llama.cpp, etc.) -- spec
section 5's "own the conversation loop end-to-end". No provider-switching
abstraction yet -- a single local endpoint, matching this build's own
minimalism; worth revisiting only if/when a second provider is actually
needed, not preemptively.

Conversation history here is in-memory only, lost on restart -- this is
ordinary within-session continuity (every chat needs *some* form of this to
hold a conversation at all), not the persistent cross-session memory/recall
system SPEC.md section 2 explicitly excludes.
"""

import re

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

_MOOD_TAG = re.compile(r"\[mood:\s*(\w+)\]\s*$", re.IGNORECASE)

# Not a memory system (SPEC.md section 2 excludes that) -- just a sane
# bound on how much history gets resent every turn. Found by hitting it
# directly: a long test session let this grow unbounded and each call got
# progressively slower re-processing a ever-larger context on a local
# model, to the point one reply took 90+s and looked hung. Trimming keeps
# every turn's latency roughly flat instead of degrading over a session.
MAX_HISTORY_MESSAGES = 20  # ~10 user/assistant exchanges


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
        self._client = OpenAI(base_url=endpoint, api_key=api_key or "not-needed")
        self._model = model
        self._history: list[dict] = []
        self._persona = ""
        self._soul = ""

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

    def _system_prompt(self) -> str:
        personality = self._soul or DEFAULT_PERSONALITY
        parts = [personality, MOOD_TAG_INSTRUCTION]
        if self._persona:
            parts.append(
                f"You are role-playing with the user under this profile:\n{self._persona}\n\n"
                "Stay in character and play out this scenario naturally as the conversation continues."
            )
        return "\n\n".join(parts)

    def reply(self, user_text: str) -> tuple[str, str]:
        """Returns (reply_text, mood) -- reply_text has the mood tag
        already stripped out (never shown/spoken), mood is one of
        VALID_MOODS.
        """
        self._history.append({"role": "user", "content": user_text})
        del self._history[:-MAX_HISTORY_MESSAGES]
        messages = [{"role": "system", "content": self._system_prompt()}, *self._history]
        response = self._client.chat.completions.create(model=self._model, messages=messages)
        raw_reply = response.choices[0].message.content
        mood, reply_text = _extract_mood(raw_reply)
        # Stored cleaned, not with the tag -- keeps the tag from cluttering
        # future turns' context for no benefit (the system prompt alone is
        # enough to keep the model tagging consistently turn to turn).
        self._history.append({"role": "assistant", "content": reply_text})
        return reply_text, mood
