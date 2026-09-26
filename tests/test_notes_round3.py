"""Question cooldown (she stopped ending every reply with a question), Clear Chat
starting a fresh conversation, and editing your last message."""

import asyncio
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import FakeBrain, FakeLLM, FakeWS, curiosity, main, profiles
from tests.test_empty_reply import Scripted
from tests.test_no_thinking import make_llm
import conversation


class QuestionCooldown(unittest.TestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_a_recent_question_means_no_question_this_turn(self):
        curiosity.add_question("Who taught you guitar?")
        for recent in (["Do you like it?"], ["Do you like it?", "Nice."], ["Cool.", "What app is that?"]):
            self.assertEqual(curiosity.start_turn(recent), curiosity.NO_QUESTION_GUIDANCE, recent)
        self.assertEqual(curiosity.open_questions()[0]["offers"], 0)  # nothing was offered meanwhile

    def test_no_recent_question_gets_the_normal_guidance(self):
        block = curiosity.start_turn(["That's cute.", "Honestly, I love autumn."])
        self.assertTrue(block.startswith(curiosity.GUIDANCE))

    def test_only_the_last_two_replies_count(self):
        self.assertTrue(curiosity.start_turn(["Why?", "Nice.", "Cool."]).startswith(curiosity.GUIDANCE))

    def test_guidance_discourages_ending_every_reply_with_a_question(self):
        self.assertIn("should NOT end in a question", curiosity.GUIDANCE)
        self.assertIn("never something you've already asked", curiosity.GUIDANCE)


class CooldownInReplies(unittest.IsolatedAsyncioTestCase):
    async def test_her_own_recent_question_puts_the_next_turn_on_cooldown(self):
        with helpers.isolated_state():
            profiles.set_roleplay_active(False)
            patches = [
                mock.patch.object(main, "_effective_user_info", lambda: ""),
                mock.patch.object(main.memory, "read_memory_active", lambda: False),
                mock.patch.object(main.lessons, "read_active", lambda: False),
                mock.patch.object(main.web_search, "read_active", lambda: False),
                mock.patch.object(main.voice_settings, "read_voice_active", lambda: False),
            ]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            brain = FakeBrain(FakeLLM(replies=["Do you like how it turned out?", "Glad you like it."]))
            brain.llm.reply = self._record_and_answer(brain.llm)
            ws = FakeWS()
            await main._reply_to(ws, "look at this one", brain)
            await main._reply_to(ws, "yeah it's great", brain)
            await asyncio.sleep(0.05)
            self.assertTrue(brain.llm.curiosity_seen[0].startswith(curiosity.GUIDANCE))
            self.assertEqual(brain.llm.curiosity_seen[1], curiosity.NO_QUESTION_GUIDANCE)

    @staticmethod
    def _record_and_answer(llm):
        original = llm.reply

        def reply(text, *a, **kw):
            result = original(text, *a, **kw)
            llm._history += [{"role": "user", "content": text}, {"role": "assistant", "content": result[0]}]
            return result

        return reply


class ClearConversation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.broadcasts = []

        async def broadcast(message):
            self.broadcasts.append(message)

        p = mock.patch.object(main, "_broadcast", broadcast)
        p.start()
        self.addCleanup(p.stop)

    async def test_clears_her_conversation_the_saved_file_and_every_device(self):
        llm = make_llm(Scripted("ok", "ok"))
        llm._on_history_change = lambda h: conversation.save_state(h, conversation.MAIN)
        llm._history[:] = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "chat"}]
        llm._history_changed()
        await main._handle_clear_conversation(FakeBrain(llm))
        self.assertEqual(llm._history, [])
        self.assertEqual(conversation.load_state(conversation.MAIN), [])
        self.assertEqual(self.broadcasts[-1], {"type": "conversation_cleared"})
        log = next(conversation.LOG_DIR.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("New conversation (chat cleared)", log)

    async def test_a_reply_in_flight_is_not_added_to_the_new_conversation(self):
        llm = make_llm(Scripted("ok", "ok"))
        generation = llm._reply_generation
        llm.clear_history()
        self.assertNotEqual(llm._reply_generation, generation)  # reply() compares this and discards


class EditLastMessage(unittest.IsolatedAsyncioTestCase):
    async def run_regenerate(self, data):
        answered = []

        async def fake_reply_to(ws, text, brain, **kw):
            answered.append(text)

        llm = make_llm(Scripted("ok", "ok"))
        llm._history[:] = [{"role": "user", "content": "hey josh whats up just testing"}, {"role": "assistant", "content": "Old reply."}]
        with mock.patch.object(main, "_reply_to", fake_reply_to):
            await main._handle_regenerate_last(FakeWS(), data, FakeBrain(llm))
        return answered, llm._history

    async def test_an_edit_answers_the_corrected_text_and_drops_the_old_exchange(self):
        answered, history = await self.run_regenerate({"text": 'You said "hey Josh." I\'m just testing.', "edited": True})
        self.assertEqual(answered, ['You said "hey Josh." I\'m just testing.'])
        self.assertEqual(history, [])

    async def test_a_plain_resend_still_answers_the_original_text(self):
        answered, _ = await self.run_regenerate({"text": "whatever the screen had"})
        self.assertEqual(answered, ["hey josh whats up just testing"])


if __name__ == "__main__":
    unittest.main()
