"""main._reply_to end to end with a fake LLM: blank input, LLM failures, memory
gating (role-play, search/image turns), and what gets set on the prompt each turn.
"""

import asyncio
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import FakeBrain, FakeLLM, FakeWS, main


class ReplyPath(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.retained = []
        self.roleplay = False
        self.memory_on = True

        async def retain_exchange(user, reply):
            self.retained.append((user, reply))

        async def recall(query):
            return f"- recalled for {query!r}"

        for target, name, value in [
            (main, "_effective_user_info", lambda: "USER INFO"),
            (main.profiles, "read_roleplay_active", lambda: self.roleplay),
            (main.memory, "read_memory_active", lambda: self.memory_on),
            (main.memory, "read_provider", lambda: main.memory.HINDSIGHT_PROVIDER),
            (main.memory, "retain_exchange", retain_exchange),
            (main.memory, "recall_for_prompt", recall),
            (main.lessons, "read_active", lambda: False),
            (main.web_search, "read_active", lambda: False),
            (main.voice_settings, "read_voice_active", lambda: False),
            (main.curiosity, "read_active", lambda: False),
            (main.training, "read_active", lambda: False),
        ]:
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.ws, self.brain = FakeWS(), FakeBrain()

    async def say(self, text, **kwargs):
        await main._reply_to(self.ws, text, self.brain, **kwargs)
        await asyncio.sleep(0.05)

    async def test_normal_turn_speaks_sets_expression_and_retains(self):
        await self.say("hello")
        types_ = self.ws.sent_types()
        self.assertEqual(types_[:2], ["set_expression", "speak_text"])  # face changes before she speaks
        self.assertEqual(self.retained, [("hello", "Nice.")])

    async def test_blank_input_tells_the_renderer_instead_of_hanging(self):
        for blank in ("", "   "):
            self.ws.sent.clear()
            await self.say(blank)
            self.assertEqual(self.ws.sent_types(), ["no_reply"])
        self.assertEqual(self.brain.llm.reply_calls, [])

    async def test_an_empty_llm_reply_is_no_reply_not_a_blank_bubble(self):
        self.brain.llm.replies = ["   "]
        await self.say("hello")
        self.assertEqual(self.ws.sent_types(), ["no_reply"])
        self.assertEqual(self.retained, [])

    async def test_llm_failure_is_reported_and_nothing_is_retained(self):
        self.brain.llm.raise_on_reply = RuntimeError("model crashed")
        await self.say("hello")
        self.assertEqual(self.ws.sent_types(), ["speak_text"])
        self.assertIn("couldn't reach the LLM", self.ws.sent[0]["text"])
        self.assertEqual(self.retained, [])

    async def test_search_turn_retains_only_the_users_side(self):
        self.brain.llm.last_reply_used_web_search = True
        await self.say("what's the news")
        self.assertEqual(self.retained, [("what's the news", "")])

    async def test_image_turn_retains_only_the_users_side(self):
        await self.say("what do you see", image_b64="AAAA")
        self.assertEqual(self.retained, [("what do you see", "")])

    async def test_roleplay_pauses_memory_recall_and_retention(self):
        self.roleplay = True
        self.brain.llm._memory = "STALE RECALL"
        await self.say("in character")
        self.assertEqual(self.retained, [])
        self.assertEqual(self.brain.llm.memory_seen[-1], "")  # stale recall cleared, not lingering into the scene

    async def test_memory_off_retains_nothing_and_clears_recall(self):
        self.memory_on = False
        await self.say("hello")
        self.assertEqual(self.retained, [])
        self.assertEqual(self.brain.llm.memory_seen[-1], "")

    async def test_recall_is_per_message_and_a_failure_doesnt_block_the_reply(self):
        await self.say("first")
        self.assertIn("first", self.brain.llm.memory_seen[-1])

        async def broken(_q):
            raise RuntimeError("hindsight unreachable")

        with mock.patch.object(main.memory, "recall_for_prompt", broken):
            await self.say("second")
        self.assertEqual(self.ws.sent_types()[-2:], ["set_expression", "speak_text"])

    async def test_user_info_is_set_on_the_prompt_every_turn(self):
        await self.say("hello")
        self.assertEqual(self.brain.llm._user_info, "USER INFO")


if __name__ == "__main__":
    unittest.main()
