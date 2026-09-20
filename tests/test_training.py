import asyncio
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import FakeBrain, FakeWS, main, training


class TrainingQueue(unittest.TestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_off_by_default_and_toggles(self):
        self.assertFalse(training.read_active())
        training.set_active(True)
        self.assertTrue(training.read_active())
        training.set_active(False)
        self.assertFalse(training.read_active())

    def test_parse_fact_is_defensive(self):
        parse = training.parse_fact
        self.assertEqual(parse('{"fact": "The user is moving after Sept 22."}'), "The user is moving after Sept 22.")
        self.assertEqual(parse('Sure!\n```json\n{"fact": "The user plays guitar."}\n```'), "The user plays guitar.")
        for bad in ('{"fact": null}', '{"fact": ""}', "nope", "", None, '{"fact": "' + "x" * 400 + '"}', '{"fact": 5}'):
            self.assertIsNone(parse(bad), bad)

    def test_importance_tag_falls_back_to_normal(self):
        self.assertEqual(training.importance_tag("core"), "importance:core")
        self.assertEqual(training.importance_tag("minor"), "importance:minor")
        self.assertEqual(training.importance_tag("bogus"), "importance:normal")

    def test_queue_add_dedupe_remove(self):
        item = training.add_pending("The user plays guitar.", "i play guitar")
        self.assertIsNotNone(item)
        self.assertIsNone(training.add_pending("the user plays guitar", "again"))  # duplicate
        self.assertEqual([p["fact"] for p in training.read_pending()], ["The user plays guitar."])
        self.assertTrue(training.remove_pending(item["id"]))
        self.assertFalse(training.remove_pending(item["id"]))

    def test_queue_is_capped_and_corrupt_file_is_empty(self):
        for i in range(training.MAX_PENDING + 5):
            training.add_pending(f"unique fact number {i} about topic{i}x")
        self.assertEqual(len(training.read_pending()), training.MAX_PENDING)
        training.QUEUE_PATH.write_text("{bad", encoding="utf-8")
        self.assertEqual(training.read_pending(), [])


class TrainingThroughBrain(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.retained_exchanges, self.retained_facts, self.broadcasts = [], [], []

        async def retain_exchange(u, r):
            self.retained_exchanges.append((u, r))

        async def retain_fact(text, importance):
            if text == "BOOM":
                raise RuntimeError("hindsight down")
            self.retained_facts.append((text, importance))

        async def recall(_q):
            return ""

        async def broadcast(message):
            self.broadcasts.append(message)

        for target, name, value in [
            (main, "_effective_user_info", lambda: ""),
            (main, "_broadcast", broadcast),
            (main.profiles, "read_roleplay_active", lambda: False),
            (main.memory, "read_memory_active", lambda: True),
            (main.memory, "read_provider", lambda: main.memory.HINDSIGHT_PROVIDER),
            (main.memory, "hindsight_configured", lambda: True),
            (main.memory, "retain_exchange", retain_exchange),
            (main.memory, "retain_fact", retain_fact),
            (main.memory, "recall_for_prompt", recall),
            (main.lessons, "read_active", lambda: False),
            (main.web_search, "read_active", lambda: False),
            (main.voice_settings, "read_voice_active", lambda: False),
            (main.curiosity, "read_active", lambda: False),
        ]:
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        training.set_active(True)
        self.ws, self.brain = FakeWS(), FakeBrain()

    async def _say(self, text):
        await main._reply_to(self.ws, text, self.brain)
        await asyncio.sleep(0.05)

    async def test_training_on_queues_instead_of_retaining(self):
        self.brain.llm.memory_raw = '{"fact": "The user is moving into a new house after Sept 22."}'
        await self._say("I'm moving into a new house next week")
        self.assertEqual(self.retained_exchanges, [])  # nothing reaches Hindsight on its own
        pending = training.read_pending()
        self.assertEqual(len(pending), 1)
        self.assertTrue(pending[0]["source"].startswith("I'm moving"))
        state = self.broadcasts[-1]
        self.assertEqual((state["type"], state["available"], state["active"], len(state["pending"])), ("training_state", True, True, 1))

    async def test_pending_facts_are_passed_as_known_so_they_arent_reproposed(self):
        training.add_pending("The user is moving into a new house.", "x")
        await self._say("yeah")
        self.assertIn("moving into a new house", self.brain.llm.memory_known_seen[-1])

    async def test_nothing_worth_keeping_queues_nothing(self):
        await self._say("hello there")
        self.assertEqual(training.read_pending(), [])

    async def test_approve_with_edited_text_and_importance(self):
        item = training.add_pending("The user is moving.", "x")
        await main._handle_resolve_memory_proposal(
            {"id": item["id"], "approve": True, "fact": "The user is moving houses on Sept 23.", "importance": "core"}
        )
        self.assertEqual(self.retained_facts, [("The user is moving houses on Sept 23.", "core")])
        self.assertEqual(training.read_pending(), [])
        self.assertEqual(self.broadcasts[-1]["error"], "")

    async def test_failed_save_keeps_it_queued_and_says_why(self):
        item = training.add_pending("BOOM", "x")
        await main._handle_resolve_memory_proposal({"id": item["id"], "approve": True, "fact": "BOOM", "importance": "normal"})
        self.assertEqual(len(training.read_pending()), 1)
        self.assertIn("still in the list", self.broadcasts[-1]["error"])

    async def test_reject_and_unknown_id_and_overlong_edit(self):
        item = training.add_pending("A fact.", "x")
        await main._handle_resolve_memory_proposal({"id": item["id"], "approve": False})
        self.assertEqual(training.read_pending(), [])
        self.assertEqual(self.broadcasts[-1]["error"], "")

        await main._handle_resolve_memory_proposal({"id": "nope", "approve": False})
        self.assertIn("no longer", self.broadcasts[-1]["error"])

        item = training.add_pending("Another fact.", "x")
        await main._handle_resolve_memory_proposal({"id": item["id"], "approve": True, "fact": "y" * 400, "importance": "normal"})
        self.assertEqual(len(training.read_pending()), 1)
        self.assertIn("characters", self.broadcasts[-1]["error"])
        self.assertEqual(self.retained_facts, [])

    async def test_training_off_uses_the_ordinary_retain_path(self):
        training.set_active(False)
        await self._say("I like tea")
        self.assertEqual(self.retained_exchanges, [("I like tea", "Nice.")])
        self.assertEqual(training.read_pending(), [])

    async def test_search_and_image_turns_retain_only_the_users_side(self):
        training.set_active(False)
        self.brain.llm.last_reply_used_web_search = True
        await self._say("what's the news")
        self.assertEqual(self.retained_exchanges[-1], ("what's the news", ""))


if __name__ == "__main__":
    unittest.main()
