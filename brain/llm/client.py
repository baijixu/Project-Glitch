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

from openai import OpenAI

SYSTEM_PROMPT = "You are Glitch, a friendly and curious AI companion. Keep replies conversational and fairly short."


class LocalLLM:
    def __init__(self, endpoint: str, model: str | None, api_key: str | None = None) -> None:
        self._client = OpenAI(base_url=endpoint, api_key=api_key or "not-needed")
        self._model = model
        self._history: list[dict] = []

    def reply(self, user_text: str) -> str:
        self._history.append({"role": "user", "content": user_text})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self._history]
        response = self._client.chat.completions.create(model=self._model, messages=messages)
        reply_text = response.choices[0].message.content
        self._history.append({"role": "assistant", "content": reply_text})
        return reply_text
