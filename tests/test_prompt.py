"""How LocalLLM assembles a request. The system prompt holds only what rarely
changes, so the model can keep its cached work on it and the conversation after
it; the per-turn parts (memories, curiosity, the clock) ride on the newest user
message for that request only.
"""

import unittest
from datetime import datetime
from unittest import mock

from tests import helpers  # noqa: F401
from llm import client
from llm.client import (
    DEFAULT_PERSONALITY,
    HONESTY_INSTRUCTION,
    IDENTITY_INSTRUCTION,
    MOOD_TAG_INSTRUCTION,
    TURN_NOTES_HEADER,
    LocalLLM,
    _current_time_line,
)


def make(history=None, **parts):
    llm = LocalLLM.__new__(LocalLLM)
    for attr in ("_soul", "_persona", "_memory", "_lessons", "_curiosity", "_user_info"):
        setattr(llm, attr, parts.get(attr.lstrip("_"), ""))
    llm._history = list(history or [{"role": "user", "content": "hi"}])
    return llm


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = "TIMELINE-1"
        p = mock.patch.object(client, "_current_time_line", lambda now=None: self.clock)
        p.start()
        self.addCleanup(p.stop)


class SystemPrompt(Base):
    def test_order(self):
        prompt = make(soul="SOUL", user_info="USERINFO", lessons="LESSONS", persona="PERSONA")._system_prompt()
        wanted = ["SOUL", IDENTITY_INSTRUCTION, MOOD_TAG_INSTRUCTION, HONESTY_INSTRUCTION, "USERINFO", "LESSONS", "PERSONA"]
        positions = [prompt.index(s) for s in wanted]
        self.assertEqual(positions, sorted(positions))

    def test_default_personality_when_there_is_no_soul(self):
        self.assertTrue(make()._system_prompt().startswith(DEFAULT_PERSONALITY))

    def test_identity_line_is_present_with_or_without_a_soul_or_persona(self):
        for parts in ({}, {"soul": "anything"}, {"soul": "anything", "persona": "a role"}):
            self.assertIn(IDENTITY_INSTRUCTION, make(**parts)._system_prompt(), parts)

    def test_the_system_prompt_does_not_change_between_turns(self):
        # The whole point: memory, curiosity and the clock change every turn, and must not
        # touch the system prompt (or the model rereads the entire conversation each turn).
        llm = make(soul="SOUL", lessons="LESSONS", memory="- cat is Pixel", curiosity="ASK ONE")
        before = llm._system_prompt()
        llm._memory, llm._curiosity, self.clock = "- dog is Biscuit", "", "TIMELINE-2"
        self.assertEqual(llm._system_prompt(), before)
        for per_turn in ("Pixel", "Biscuit", "ASK ONE", "TIMELINE"):
            self.assertNotIn(per_turn, before)


class TurnNotes(Base):
    def test_notes_ride_on_the_newest_user_message_only(self):
        history = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}, {"role": "user", "content": "now"}]
        llm = make(history, soul="SOUL", memory="- cat is Pixel", curiosity="ASK ONE")
        messages = llm._request_messages()
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1:3], history[:2])  # earlier turns sent exactly as they were
        last = messages[-1]["content"]
        self.assertTrue(last.startswith("<notes>\n" + TURN_NOTES_HEADER))
        for part in ("cat is Pixel", "ASK ONE", "TIMELINE-1"):
            self.assertIn(part, last)
        self.assertTrue(last.endswith("</notes>\n\nnow"))

    def test_history_itself_is_never_changed(self):
        llm = make([{"role": "user", "content": "now"}], memory="- cat is Pixel")
        llm._request_messages()
        self.assertEqual(llm._history, [{"role": "user", "content": "now"}])

    def test_picture_turns_get_the_notes_on_their_text_part(self):
        image = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
        llm = make([{"role": "user", "content": [{"type": "text", "text": "look"}, image]}], memory="- cat is Pixel")
        content = llm._request_messages()[-1]["content"]
        self.assertTrue(content[0]["text"].startswith("<notes>"))
        self.assertTrue(content[0]["text"].endswith("look"))
        self.assertEqual(content[1], image)

    def test_roleplay_has_no_clock_and_empty_notes_add_nothing(self):
        llm = make([{"role": "user", "content": "in the scene"}], persona="PERSONA")
        self.assertEqual(llm._turn_notes(), "")
        self.assertEqual(llm._request_messages()[-1]["content"], "in the scene")


class TimeLine(unittest.TestCase):
    def test_names_the_date_and_time(self):
        line = _current_time_line(datetime(2026, 9, 20, 14, 5))
        self.assertIn("2026", line)
        self.assertIn("September", line)


if __name__ == "__main__":
    unittest.main()
