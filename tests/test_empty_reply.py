"""When thinking runs out of budget: fall back to an answer without thinking, and
never store a blank turn from her. Also: editing her soul keeps the conversation."""

import json
import types
import unittest

from tests import helpers
from tests.helpers import FakeBrain, main, profiles, souls
from tests.test_no_thinking import _response, make_llm
import conversation


class Scripted:
    """Completions that answer from a script: thinking calls get `thinking`,
    no-thinking calls get `no_thinking`."""

    def __init__(self, thinking, no_thinking):
        self.thinking, self.no_thinking, self.calls = thinking, no_thinking, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _response(self.no_thinking if kwargs.get("reasoning_effort") == "none" else self.thinking)


class Fallback(unittest.TestCase):
    def test_an_empty_thinking_reply_is_answered_again_without_thinking(self):
        fake = Scripted(thinking="", no_thinking="Yeah, just testing things. [mood: relaxed]")
        llm = make_llm(fake)
        reply, mood = llm.reply("close, you said hey josh")
        self.assertEqual((reply, mood), ("Yeah, just testing things.", "relaxed"))
        self.assertTrue(llm.last_reply_fell_back)
        self.assertEqual([c.get("reasoning_effort") for c in fake.calls], [None, "none"])
        self.assertEqual(llm._history[-1], {"role": "assistant", "content": "Yeah, just testing things."})

    def test_a_normal_reply_thinks_and_does_not_fall_back(self):
        fake = Scripted(thinking="Hey you. [mood: happy]", no_thinking="unused")
        llm = make_llm(fake)
        self.assertEqual(llm.reply("hi")[0], "Hey you.")
        self.assertFalse(llm.last_reply_fell_back)
        self.assertEqual(len(fake.calls), 1)

    def test_still_nothing_stores_no_blank_turn(self):
        llm = make_llm(Scripted(thinking="", no_thinking="[mood: neutral]"))
        saved = []
        llm._on_history_change = lambda h: saved.append(list(h))
        self.assertEqual(llm.reply("hello")[0], "")
        self.assertEqual(llm._history, [{"role": "user", "content": "hello"}])  # their turn stays, no empty reply
        self.assertEqual(saved[-1], llm._history)


class BlankTurnsAreNotRestored(unittest.TestCase):
    def test_load_drops_blank_messages(self):
        with helpers.isolated_state():
            conversation.STATE_PATH.write_text(json.dumps({"mode": "main", "messages": [
                {"role": "assistant", "content": ""},
                {"role": "user", "content": "What is your favorite color?"},
                {"role": "assistant", "content": "   "},
            ]}), encoding="utf-8")
            self.assertEqual(conversation.load_state(conversation.MAIN), [{"role": "user", "content": "What is your favorite color?"}])


class EditingHerSoulKeepsTheConversation(unittest.TestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        profiles.set_roleplay_active(False)

    def test_the_settings_editor_updates_her_soul_without_wiping_the_chat(self):
        brain = FakeBrain(make_llm(Scripted("ok", "ok")))
        brain.llm._history[:] = [{"role": "user", "content": "remember this"}, {"role": "assistant", "content": "got it"}]
        main._handle_save_soul_and_user({"soul": "EDITED SOUL", "user": "about me"}, brain)
        self.assertEqual(brain.llm._soul, "EDITED SOUL")
        self.assertEqual(len(brain.llm._history), 2)

    def test_switching_to_a_different_soul_still_starts_fresh(self):
        llm = make_llm(Scripted("ok", "ok"))
        llm._on_history_change = None
        llm._history[:] = [{"role": "user", "content": "x"}]
        llm.set_soul("SOMEONE ELSE")
        self.assertEqual(llm._history, [])


if __name__ == "__main__":
    unittest.main()
