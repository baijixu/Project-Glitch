"""The Settings context meter: token usage recorded per reply, the model's context
size read from the server, and the message Brain sends."""

import types
import unittest
from unittest import mock

from tests import helpers  # noqa: F401
from tests.helpers import FakeBrain, FakeLLM, main
from tests.test_no_thinking import FakeCompletions, make_llm
from llm import client
from llm.client import MAX_HISTORY_MESSAGES


class UsageRecorded(unittest.TestCase):
    def test_a_reply_records_its_token_usage_and_history_size(self):
        fake = FakeCompletions()
        real_create = fake.create

        def create(**kwargs):
            response = real_create(**kwargs)
            response.usage = types.SimpleNamespace(prompt_tokens=4200, completion_tokens=35)
            return response

        fake.create = create
        llm = make_llm(fake)
        llm.reply("hello")
        self.assertEqual(llm.last_usage, {"prompt": 4200, "completion": 35, "history_messages": 2})


class FakeHTTP:
    """Stands in for httpx.Client: maps a URL suffix to (status, json)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, *a, **kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, **kw):
        self.calls.append(url)
        for suffix, (status, body) in self.routes.items():
            if url.endswith(suffix):
                return types.SimpleNamespace(status_code=status, json=lambda body=body: body)
        return types.SimpleNamespace(status_code=404, json=lambda: {})


def llm_at(model="qwen"):
    llm = client.LocalLLM.__new__(client.LocalLLM)
    llm._client = types.SimpleNamespace(base_url="http://10.0.0.1:1234/v1/")
    llm._model = model
    return llm


LMSTUDIO = {"data": [
    {"id": "other", "state": "loaded", "loaded_context_length": 8192},
    {"id": "qwen", "state": "loaded", "loaded_context_length": 65536},
    {"id": "big", "state": "not-loaded"},
]}


class ContextWindow(unittest.TestCase):
    def window(self, routes, model="qwen"):
        http = FakeHTTP(routes)
        with mock.patch.object(client.httpx, "Client", http):
            return llm_at(model).context_window(), http

    def test_lm_studio_matches_the_configured_model(self):
        window, http = self.window({"/api/v0/models": (200, LMSTUDIO)})
        self.assertEqual(window, 65536)
        self.assertEqual(http.calls, ["http://10.0.0.1:1234/api/v0/models"])  # /v1 stripped

    def test_no_model_configured_uses_the_loaded_one(self):
        window, _ = self.window({"/api/v0/models": (200, LMSTUDIO)}, model="")
        self.assertEqual(window, 8192)

    def test_llama_server_props_fallback(self):
        window, _ = self.window({"/props": (200, {"default_generation_settings": {"n_ctx": 32768}})})
        self.assertEqual(window, 32768)

    def test_unknown_server_is_none(self):
        window, _ = self.window({})
        self.assertIsNone(window)

    def test_answer_is_cached(self):
        http = FakeHTTP({"/api/v0/models": (200, LMSTUDIO)})
        llm = llm_at()
        with mock.patch.object(client.httpx, "Client", http):
            llm.context_window()
            llm.context_window()
        self.assertEqual(len(http.calls), 1)


class MeterMessage(unittest.IsolatedAsyncioTestCase):
    async def run_meter(self, usage, window):
        sent = []

        async def broadcast(message):
            sent.append(message)

        brain = FakeBrain(FakeLLM())
        brain.llm.last_usage = usage
        brain.llm.context_window = lambda: window
        with mock.patch.object(main, "_broadcast", broadcast), mock.patch.object(main, "_LAST_CONTEXT_USAGE", None):
            await main._send_context_usage(brain)
            return sent, main._LAST_CONTEXT_USAGE

    async def test_sends_used_window_and_message_counts(self):
        sent, last = await self.run_meter({"prompt": 4200, "completion": 35, "history_messages": 12}, 65536)
        self.assertEqual(sent, [{"type": "context_usage", "used": 4235, "window": 65536, "messages": 12, "max_messages": MAX_HISTORY_MESSAGES}])
        self.assertEqual(last, sent[0])  # remembered for devices that connect later

    async def test_nothing_sent_without_token_counts(self):
        sent, _ = await self.run_meter({}, 65536)
        self.assertEqual(sent, [])

    async def test_an_unknown_window_is_still_sent(self):
        sent, _ = await self.run_meter({"prompt": 100, "completion": 5, "history_messages": 2}, None)
        self.assertIsNone(sent[0]["window"])


if __name__ == "__main__":
    unittest.main()
