import asyncio
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import FakeBrain, FakeLLM, FakeWS, curiosity, main, profiles

QS = [
    "Who taught you guitar?",
    "Where do you hike near home?",
    "Has sourdough baking ever worked out?",
    "Do you play chess online?",
    "Ever thrown pottery on a wheel?",
    "Which river would you kayak first?",
]


class CuriosityModule(unittest.TestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        self.tmp = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_toggle_defaults_on_and_persists(self):
        self.assertTrue(curiosity.read_active())
        curiosity.set_active(False)
        self.assertFalse(curiosity.read_active())
        curiosity.set_active(True)
        self.assertTrue(curiosity.read_active())

    def test_parse_question_is_defensive(self):
        parse = curiosity.parse_question
        self.assertEqual(parse('{"question": "What got you into climbing?"}'), "What got you into climbing?")
        self.assertEqual(parse('Sure!\n```json\n{"question": "Do you still play guitar?"}\n```'), "Do you still play guitar?")
        for bad in ('{"question": null}', '{"question": "not a question"}', "garbage", "[1, 2]", "", None,
                    '{"question": "' + "x" * 300 + '?"}'):
            self.assertIsNone(parse(bad), bad)

    def test_off_limits_topics_are_refused(self):
        for bad in ["What's the loan for?", "What was under your boxers?", "Is the pregnancy going okay?",
                    "How much do you owe in debt?", "Does your body feel tired?", "Are you on any medication?"]:
            self.assertFalse(curiosity.add_question(bad), bad)
        self.assertEqual(curiosity.open_questions(), [])

    def test_repeats_and_reworded_repeats_are_refused(self):
        self.assertTrue(curiosity.add_question("What got you into climbing?"))
        self.assertFalse(curiosity.add_question("what got you into climbing"))
        self.assertFalse(curiosity.add_question("What got you into rock climbing originally?"))
        self.assertTrue(curiosity.add_question("Which song are you most proud of writing?"))

    def test_open_list_is_capped(self):
        for q in QS + ["Can you fold an origami crane?", "What telescope would you buy?"]:
            curiosity.add_question(q)
        self.assertEqual(len(curiosity.open_questions()), curiosity.MAX_OPEN)

    def test_closed_list_is_capped_and_open_ones_survive(self):
        curiosity.add_question(QS[0])
        for i in range(60):
            curiosity._write(curiosity._read() + [{"id": f"x{i}", "text": f"old {i}?", "status": "closed", "offers": 0}])
        state = curiosity._read()
        self.assertLessEqual(len([q for q in state if q["status"] != "open"]), curiosity.MAX_CLOSED_KEPT)
        self.assertEqual(len(curiosity.open_questions()), 1)

    def test_corrupt_file_reads_as_empty(self):
        curiosity.QUESTIONS_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(curiosity.open_questions(), [])
        self.assertEqual(curiosity.start_turn(), curiosity.GUIDANCE)

    def test_first_turn_offers_the_oldest_question(self):
        curiosity.add_question(QS[0])
        curiosity.add_question(QS[1])
        block = curiosity.start_turn()
        self.assertIn(curiosity.GUIDANCE, block)
        self.assertIn(QS[0], block)
        self.assertNotIn(QS[1], block)

    def test_unused_offer_stays_open_then_closes_unasked(self):
        curiosity.add_question(QS[0])
        for _ in range(curiosity.MAX_OFFERS_UNUSED):
            self.assertIn(QS[0], curiosity.start_turn())
            curiosity.end_turn("Sounds fun.")  # no '?' -> she didn't ask it
        self.assertEqual(curiosity.open_questions(), [])
        self.assertIn(QS[0], curiosity.all_question_texts())  # remembered so it isn't re-proposed
        self.assertFalse(curiosity.add_question(QS[0]))

    def test_asked_question_starts_cooldown_and_closes_next_turn(self):
        curiosity.add_question(QS[0])
        curiosity.add_question(QS[1])
        curiosity.start_turn()
        curiosity.end_turn("Oh nice -- who taught you?")
        self.assertEqual([q["status"] for q in curiosity._read() if q["text"] == QS[0]], ["asked"])
        for _ in range(curiosity.OFFER_EVERY_TURNS - 1):
            self.assertNotIn("you could ask", curiosity.start_turn())
            curiosity.end_turn("fine")
        self.assertNotIn("asked", [q["status"] for q in curiosity._read()])  # closed by the user's next message
        self.assertIn("you could ask", curiosity.start_turn())  # cooldown over

    def test_should_propose_pacing_and_full_list(self):
        self.assertFalse(curiosity.should_propose())
        with mock.patch.object(curiosity, "_turns_since_propose", curiosity.PROPOSE_EVERY_TURNS):
            self.assertTrue(curiosity.should_propose())
        self.assertFalse(curiosity.should_propose())  # counter was reset
        for q in QS:
            curiosity.add_question(q)
        with mock.patch.object(curiosity, "_turns_since_propose", curiosity.PROPOSE_EVERY_TURNS):
            self.assertFalse(curiosity.should_propose())  # full list -> nothing more to think up


class CuriosityInReplies(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        self.tmp = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        for target, value in [
            (main, {"_effective_user_info": lambda: "likes rock climbing"}),
            (main.memory, {"read_memory_active": lambda: False, "read_provider": lambda: "local"}),
            (main.lessons, {"read_active": lambda: False}),
            (main.web_search, {"read_active": lambda: False}),
            (main.voice_settings, {"read_voice_active": lambda: False}),
        ]:
            for name, fn in value.items():
                p = mock.patch.object(target, name, fn)
                p.start()
                self.addCleanup(p.stop)
        profiles.set_roleplay_active(False)  # unset means role-play ON, which pauses curiosity
        self.ws, self.brain = FakeWS(), FakeBrain()

    async def _say(self, text):
        await main._reply_to(self.ws, text, self.brain)
        await asyncio.sleep(0.05)  # let background tasks run

    async def test_guidance_every_turn_question_offered_then_asked(self):
        self.brain.llm.replies = ["Fine."] * curiosity.PROPOSE_EVERY_TURNS + ["Oh nice, what grade do you climb?"]
        self.brain.llm.question_raw = '{"question": "What got you started climbing?"}'
        for i in range(curiosity.PROPOSE_EVERY_TURNS):
            await self._say(f"message {i}")
        self.assertTrue(all(curiosity.GUIDANCE in c for c in self.brain.llm.curiosity_seen))
        self.assertTrue(all("you could ask" not in c for c in self.brain.llm.curiosity_seen))
        self.assertEqual(self.brain.llm.question_calls, 1)
        self.assertEqual([q["text"] for q in curiosity.open_questions()], ["What got you started climbing?"])
        await self._say("yeah")
        self.assertIn("started climbing", self.brain.llm.curiosity_seen[-1])
        self.assertEqual([q["status"] for q in curiosity._read()], ["asked"])

    async def test_off_when_toggled_off_or_in_roleplay(self):
        curiosity.set_active(False)
        await self._say("hello")
        self.assertEqual(self.brain.llm.curiosity_seen[-1], "")
        curiosity.set_active(True)
        with mock.patch.object(profiles, "read_roleplay_active", lambda: True):
            await self._say("in character")
        self.assertEqual(self.brain.llm.curiosity_seen[-1], "")

    async def test_action_messages_never_trigger_a_proposal(self):
        with mock.patch.object(curiosity, "_turns_since_propose", curiosity.PROPOSE_EVERY_TURNS):
            await self._say("*leans in close*")
        self.assertEqual(self.brain.llm.question_calls, 0)


if __name__ == "__main__":
    unittest.main()
