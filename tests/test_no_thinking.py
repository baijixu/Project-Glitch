"""Background JSON calls (memory/question/lesson proposals) ask the model to skip its
thinking pass; her actual replies never do. A server that rejects the parameter
gets the call again without it.
"""

import types
import unittest

import httpx
from openai import BadRequestError

from tests import helpers  # noqa: F401
from llm.client import LocalLLM


def _response(text):
    message = types.SimpleNamespace(content=text, tool_calls=None, model_dump=lambda **kw: {"role": "assistant", "content": text})
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class FakeCompletions:
    def __init__(self, reject_reasoning_effort=False):
        self.calls = []
        self.reject = reject_reasoning_effort

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject and "reasoning_effort" in kwargs:
            request = httpx.Request("POST", "http://x/v1/chat/completions")
            raise BadRequestError("unknown parameter", response=httpx.Response(400, request=request), body=None)
        return _response('{"fact": "I promised Josh I\'d help with his song."}')


def make_llm(completions):
    llm = LocalLLM.__new__(LocalLLM)
    for attr in ("_soul", "_persona", "_memory", "_lessons", "_curiosity", "_user_info"):
        setattr(llm, attr, "")
    llm._history, llm._model, llm._reply_generation = [], "m", 0
    llm.last_reply_used_web_search = False
    llm._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
    return llm


class NoThinking(unittest.TestCase):
    def test_background_calls_ask_to_skip_thinking(self):
        fake = FakeCompletions()
        llm = make_llm(fake)
        llm.propose_memory("u", "r", "")
        llm.propose_question("u", "r", "", [])
        llm.propose_lesson("u", "r", "up", "note", [], [])
        self.assertEqual([c.get("reasoning_effort") for c in fake.calls], ["none", "none", "none"])

    def test_her_replies_never_skip_thinking(self):
        fake = FakeCompletions()
        llm = make_llm(fake)
        llm.reply("hello")
        self.assertNotIn("reasoning_effort", fake.calls[-1])

    def test_a_server_that_rejects_it_is_retried_without_and_never_sent_it_again(self):
        fake = FakeCompletions(reject_reasoning_effort=True)
        llm = make_llm(fake)
        self.assertIn("I promised Josh", llm.propose_memory("u", "r", ""))
        llm.propose_memory("u", "r", "")
        self.assertEqual(["reasoning_effort" in c for c in fake.calls], [True, False, False])


class ReplyLimits(unittest.TestCase):
    """She thinks before every reply (the user's choice), so the limits must leave room for it."""

    def test_a_reply_gets_room_to_think(self):
        fake = FakeCompletions()
        make_llm(fake).reply("hello")
        self.assertGreaterEqual(fake.calls[-1]["max_tokens"], 8000)

    def test_clients_wait_long_enough_and_never_silently_retry(self):
        from llm.client import REQUEST_TIMEOUT_SEC, HarnessLLM

        self.assertGreaterEqual(REQUEST_TIMEOUT_SEC, 400)  # above ~8,000 tokens at ~20 tokens/s
        for llm in (LocalLLM("http://127.0.0.1:9/v1", "m"), HarnessLLM("http://127.0.0.1:9/v1", "m")):
            self.assertEqual(llm._client.max_retries, 0, type(llm).__name__)
            self.assertEqual(llm._client.timeout, REQUEST_TIMEOUT_SEC, type(llm).__name__)


if __name__ == "__main__":
    unittest.main()
