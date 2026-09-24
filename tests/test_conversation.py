"""Her conversation survives a Brain restart (conversation.json), and every exchange
goes into a timestamped daily log (chat_logs/)."""

import asyncio
import json
import unittest
from datetime import datetime
from unittest import mock

from tests import helpers
from tests.helpers import FakeBrain, FakeLLM, FakeWS, main, profiles, souls
import conversation
from llm.client import MAX_HISTORY_MESSAGES


class SavedState(unittest.TestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_round_trip(self):
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey Josh"}]
        conversation.save_state(history, conversation.MAIN)
        self.assertEqual(conversation.load_state(conversation.MAIN), history)

    def test_pictures_are_stored_as_a_note_not_image_data(self):
        history = [{"role": "user", "content": [
            {"type": "text", "text": "look at this"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "A" * 5000}},
        ]}]
        conversation.save_state(history, conversation.MAIN)
        raw = conversation.STATE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("base64", raw)
        self.assertEqual(conversation.load_state(conversation.MAIN), [{"role": "user", "content": "look at this [picture]"}])

    def test_only_restored_into_the_same_mode(self):
        conversation.save_state([{"role": "user", "content": "in the scene"}], conversation.ROLEPLAY)
        self.assertEqual(conversation.load_state(conversation.MAIN), [])
        self.assertEqual(len(conversation.load_state(conversation.ROLEPLAY)), 1)

    def test_missing_or_corrupt_file_is_empty(self):
        self.assertEqual(conversation.load_state(conversation.MAIN), [])
        conversation.STATE_PATH.write_text("{nope", encoding="utf-8")
        self.assertEqual(conversation.load_state(conversation.MAIN), [])
        conversation.STATE_PATH.write_text(json.dumps({"mode": "main", "messages": [{"role": "system", "content": "x"}, 5]}), encoding="utf-8")
        self.assertEqual(conversation.load_state(conversation.MAIN), [])  # junk entries dropped


class LLMSavesOnEveryChange(unittest.TestCase):
    def setUp(self):
        self.llm = FakeLLM.__new__(FakeLLM)
        FakeLLM.__init__(self.llm)
        self.saved = []
        self.llm._on_history_change = lambda history: self.saved.append(list(history))

    def test_reply_pop_and_fresh_starts_all_save(self):
        from llm.client import LocalLLM

        self.llm._history[:] = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        LocalLLM.pop_last_exchange(self.llm)
        self.assertEqual(self.saved[-1], [])
        self.llm._history.append({"role": "user", "content": "x"})
        LocalLLM.set_soul(self.llm, "new soul")  # a new soul starts a fresh conversation
        self.assertEqual(self.saved[-1], [])

    def test_the_real_reply_saves_the_finished_exchange(self):
        from tests.test_no_thinking import FakeCompletions, make_llm

        llm = make_llm(FakeCompletions())
        saved = []
        llm._on_history_change = lambda history: saved.append(list(history))
        llm.reply("hello")
        self.assertEqual([m["role"] for m in saved[-1]], ["user", "assistant"])

    def test_a_failing_save_never_breaks_anything(self):
        from llm.client import LocalLLM

        def boom(_history):
            raise OSError("disk full")

        self.llm._on_history_change = boom
        self.llm._history[:] = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        self.assertEqual(LocalLLM.pop_last_exchange(self.llm), "a")

    def test_restore_is_capped(self):
        from llm.client import LocalLLM

        LocalLLM.restore_history(self.llm, [{"role": "user", "content": str(i)} for i in range(MAX_HISTORY_MESSAGES + 10)])
        self.assertEqual(len(self.llm._history), MAX_HISTORY_MESSAGES)
        self.assertEqual(self.llm._history[-1]["content"], str(MAX_HISTORY_MESSAGES + 9))


class RestartPicksUpWhereSheLeftOff(unittest.TestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        souls.write_main_soul("MAIN SOUL")
        profiles.set_roleplay_active(False)
        p = mock.patch.object(main.llm_engines, "read_engine", lambda name: {"endpoint": "http://127.0.0.1:9/v1", "model": "m"})
        p.start()
        self.addCleanup(p.stop)
        self.earlier = [{"role": "user", "content": "remember the song?"}, {"role": "assistant", "content": "Saturday, yes!"}]

    def test_a_fresh_brain_restores_the_saved_conversation_and_keeps_the_file(self):
        conversation.save_state(self.earlier, conversation.MAIN)
        llm = main._build_llm("Some Engine")
        self.assertEqual(llm._history, self.earlier)
        self.assertEqual(llm._soul, "MAIN SOUL")
        self.assertEqual(conversation.load_state(conversation.MAIN), self.earlier)  # not wiped by set_soul during the build

    def test_a_roleplay_conversation_is_not_restored_into_normal_chat(self):
        conversation.save_state(self.earlier, conversation.ROLEPLAY)
        self.assertEqual(main._build_llm("Some Engine")._history, [])

    def test_changes_after_the_build_are_saved(self):
        llm = main._build_llm("Some Engine")
        llm._history.append({"role": "user", "content": "new"})
        llm._history_changed()
        self.assertEqual(conversation.load_state(conversation.MAIN), [{"role": "user", "content": "new"}])


class ChatLog(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_format_and_one_file_per_day(self):
        conversation.log_exchange("hi", "hey you", roleplay=False, now=datetime(2026, 9, 24, 3, 18, 5))
        conversation.log_exchange("look", "cute cat", roleplay=True, picture=True, now=datetime(2026, 9, 24, 3, 19, 0))
        conversation.log_exchange("morning", "morning!", roleplay=False, now=datetime(2026, 9, 25, 8, 0, 0))
        day1 = (conversation.LOG_DIR / "2026-09-24.md").read_text(encoding="utf-8")
        self.assertTrue(day1.startswith("# Chat log -- Thursday 24 September 2026"))
        self.assertEqual(day1.count("# Chat log"), 1)  # header only once
        self.assertIn("**03:18:05** You: hi", day1)
        self.assertIn("**03:18:05** Glitch: hey you", day1)
        self.assertIn("**03:19:00** You (role-play): look [picture]", day1)
        self.assertTrue((conversation.LOG_DIR / "2026-09-25.md").exists())

    async def test_every_delivered_reply_is_logged(self):
        for target, name, value in [
            (main, "_effective_user_info", lambda: ""),
            (main.memory, "read_memory_active", lambda: False),
            (main.lessons, "read_active", lambda: False),
            (main.web_search, "read_active", lambda: False),
            (main.voice_settings, "read_voice_active", lambda: False),
            (main.curiosity, "read_active", lambda: False),
        ]:
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        profiles.set_roleplay_active(False)
        ws, brain = FakeWS(), FakeBrain(FakeLLM(replies=["Hi Josh!", "   "]))
        await main._reply_to(ws, "hello", brain)
        await main._reply_to(ws, "anyone there?", brain)  # an empty reply is never delivered, so not logged
        await asyncio.sleep(0.05)
        log = next(conversation.LOG_DIR.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("You: hello", log)
        self.assertIn("Glitch: Hi Josh!", log)
        self.assertNotIn("anyone there?", log)


if __name__ == "__main__":
    unittest.main()
