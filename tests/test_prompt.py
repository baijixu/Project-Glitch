"""How LocalLLM assembles her system prompt -- order matters (see _system_prompt)."""

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
    LocalLLM,
    _current_time_line,
)


def make(**parts):
    llm = LocalLLM.__new__(LocalLLM)
    for attr in ("_soul", "_persona", "_memory", "_lessons", "_curiosity", "_user_info"):
        setattr(llm, attr, parts.get(attr.lstrip("_"), ""))
    return llm


class SystemPrompt(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(client, "_current_time_line", lambda now=None: "TIMELINE")
        p.start()
        self.addCleanup(p.stop)

    def test_full_order(self):
        prompt = make(soul="SOUL", user_info="USERINFO", lessons="LESSONS", curiosity="CURIOSITY", memory="MEMORY", persona="PERSONA")._system_prompt()
        wanted = ["SOUL", IDENTITY_INSTRUCTION, MOOD_TAG_INSTRUCTION, HONESTY_INSTRUCTION, "USERINFO", "LESSONS", "CURIOSITY", "MEMORY", "PERSONA"]
        positions = [prompt.index(s) for s in wanted]
        self.assertEqual(positions, sorted(positions))

    def test_default_personality_when_there_is_no_soul(self):
        self.assertTrue(make()._system_prompt().startswith(DEFAULT_PERSONALITY))

    def test_identity_line_is_present_with_or_without_a_soul_or_persona(self):
        for parts in ({}, {"soul": "anything"}, {"soul": "anything", "persona": "a role"}):
            self.assertIn(IDENTITY_INSTRUCTION, make(**parts)._system_prompt(), parts)

    def test_optional_blocks_are_omitted_when_empty(self):
        prompt = make(soul="SOUL")._system_prompt()
        for absent in ("About the user", "How this user wants you to behave", "What you remember", "role-playing"):
            self.assertNotIn(absent, prompt)

    def test_current_time_is_last_and_absent_during_roleplay(self):
        normal = make(soul="SOUL", memory="MEMORY")._system_prompt()
        self.assertTrue(normal.endswith("TIMELINE"))
        self.assertGreater(normal.index("TIMELINE"), normal.index("MEMORY"))
        self.assertNotIn("TIMELINE", make(soul="SOUL", persona="PERSONA")._system_prompt())


class TimeLine(unittest.TestCase):
    def test_names_the_date_and_time(self):
        line = _current_time_line(datetime(2026, 9, 20, 14, 5))
        self.assertIn("2026", line)
        self.assertIn("September", line)


if __name__ == "__main__":
    unittest.main()
