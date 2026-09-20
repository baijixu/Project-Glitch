import json
import unittest

from tests import helpers  # noqa: F401  (puts brain/ on sys.path)
import lessons

LESSONS = [{"id": "L1", "content": "keep replies short"}, {"id": "L2", "content": "don't mention sports"}]
CANDIDATES = [{"content": "ask before changing topics"}]


def parse(obj, note=True, lessons_=LESSONS, candidates=CANDIDATES):
    raw = obj if isinstance(obj, str) else json.dumps(obj)
    return lessons.parse_distillation(raw, lessons_, candidates, has_note=note)


class ParseDistillation(unittest.TestCase):
    def test_unusable_answers_are_dropped(self):
        for raw in ("", None, "no json here", "{broken", "[1, 2]", '{"action": "none"}', '{"action": "explode"}', '{"action": 5}'):
            self.assertIsNone(lessons.parse_distillation(raw, LESSONS, CANDIDATES), raw)

    def test_tolerates_code_fences_and_prose_around_the_json(self):
        raw = 'Here you go:\n```json\n{"action": "create", "content": "Be concise."}\n```\nHope that helps!'
        result = lessons.parse_distillation(raw, LESSONS, CANDIDATES)
        self.assertEqual((result["action"], result["content"]), ("create", "Be concise."))

    def test_create_needs_content(self):
        self.assertIsNone(parse({"action": "create", "content": ""}))
        self.assertIsNone(parse({"action": "create", "content": None}))
        self.assertEqual(parse({"action": "create", "content": "Be concise."})["content"], "Be concise.")

    def test_null_fields_become_empty_strings_not_the_word_none(self):
        result = parse({"action": "create", "content": "Be concise.", "name": None, "reason": None})
        self.assertEqual((result["name"], result["reason"]), ("", ""))

    def test_targets_are_one_based_indexes_mapped_to_real_ids(self):
        result = parse({"action": "strengthen", "target": 2})
        self.assertEqual((result["target_id"], result["target_name"]), ("L2", "don't mention sports"))
        for bad in (0, 3, -1, "x", None):
            self.assertIsNone(parse({"action": "weaken", "target": bad}), bad)

    def test_confirm_maps_to_a_candidate(self):
        result = parse({"action": "confirm", "target": 1})
        self.assertEqual(result["content"], "ask before changing topics")
        self.assertIsNone(parse({"action": "confirm", "target": 2}))

    def test_revise_needs_content_and_a_target(self):
        self.assertIsNone(parse({"action": "revise", "target": 1, "content": ""}))
        self.assertEqual(parse({"action": "revise", "target": 1, "content": "Keep it under two sentences."})["target_id"], "L1")

    def test_a_bare_rating_can_only_strengthen_or_weaken(self):
        # Regression: a bare thumbs-up once created a bogus "multi-topic news" lesson.
        for action in ("create", "confirm", "revise", "retire"):  # spelled out: don't trust the constant under test
            self.assertIsNone(parse({"action": action, "target": 1, "content": "New rule."}, note=False), action)
        self.assertIsNotNone(parse({"action": "strengthen", "target": 1}, note=False))
        self.assertIsNotNone(parse({"action": "weaken", "target": 1}, note=False))

    def test_overlong_fields_are_trimmed(self):
        result = parse({"action": "create", "content": "x" * 1000, "name": "n" * 500})
        self.assertLessEqual(len(result["content"]), lessons.MAX_CONTENT_CHARS)
        self.assertLessEqual(len(result["name"]), lessons.MAX_NAME_CHARS)

    def test_autonomy_setting_validates(self):
        with helpers.isolated_state():
            self.assertEqual(lessons.read_autonomy(), lessons.ASK)  # safest default
            lessons.set_autonomy(lessons.SMALL)
            self.assertEqual(lessons.read_autonomy(), lessons.SMALL)
            with self.assertRaises(ValueError):
                lessons.set_autonomy("reckless")


if __name__ == "__main__":
    unittest.main()
