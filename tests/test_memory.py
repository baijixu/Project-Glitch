import types
import unittest
from unittest import mock

from tests import helpers  # noqa: F401
import memory


class FakeHindsight:
    """Just the calls memory.py makes, recording what was sent."""

    def __init__(self, mission=None, config_error=None):
        self.mission = mission
        self.config_error = config_error
        self.calls = []
        self.recall_results = {"normal": [], "core": []}

    async def aget_bank_config(self, bank_id):
        if self.config_error:
            raise self.config_error
        return {"overrides": {"retain_mission": self.mission} if self.mission is not None else {}}

    async def aupdate_bank_config(self, bank_id, retain_mission=None):
        self.calls.append(("update_mission", retain_mission))
        self.mission = retain_mission

    async def acreate_bank(self, bank_id):
        self.calls.append(("create_bank", bank_id))

    async def adelete_bank(self, bank_id):
        self.calls.append(("delete_bank", bank_id))
        self.mission = None  # a recreated bank starts with no mission

    async def aretain(self, bank_id, **kwargs):
        self.calls.append(("retain", kwargs))

    async def arecall(self, bank_id, query, **kwargs):
        which = "core" if kwargs.get("tags") else "normal"
        self.calls.append(("recall", which, kwargs))
        return types.SimpleNamespace(results=[types.SimpleNamespace(text=t) for t in self.recall_results[which]])


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def use(self, fake):
        for name, value in (("_client", fake), ("_bank_id", "test-bank")):
            p = mock.patch.object(memory, name, value)
            p.start()
            self.addCleanup(p.stop)
        return fake

    async def test_mission_is_set_on_a_bank_without_one(self):
        fake = self.use(FakeHindsight(mission=None))
        await memory.ensure_bank()
        self.assertEqual(fake.mission, memory.RETAIN_MISSION)

    async def test_earlier_default_missions_are_upgraded(self):
        for old in memory._PREVIOUS_DEFAULT_MISSIONS:
            fake = self.use(FakeHindsight(mission=old))
            await memory._apply_default_retain_mission()
            self.assertEqual(fake.mission, memory.RETAIN_MISSION)

    async def test_a_hand_written_mission_is_never_overwritten(self):
        fake = self.use(FakeHindsight(mission="my own careful instructions"))
        await memory._apply_default_retain_mission()
        self.assertEqual(fake.mission, "my own careful instructions")
        self.assertNotIn("update_mission", [c[0] for c in fake.calls])

    async def test_current_mission_is_left_alone_and_config_errors_are_swallowed(self):
        fake = self.use(FakeHindsight(mission=memory.RETAIN_MISSION))
        await memory._apply_default_retain_mission()
        self.assertEqual(fake.calls, [])
        self.use(FakeHindsight(config_error=RuntimeError("config API disabled")))
        await memory._apply_default_retain_mission()  # must not raise

    async def test_mission_carries_the_role_rule(self):
        self.assertIn("'User' is the human", memory.RETAIN_MISSION)
        self.assertIn("'Glitch' is the AI", memory.RETAIN_MISSION)

    async def test_clearing_memory_restores_the_mission(self):
        # Regression: a recreated bank started with no mission until the next Brain restart.
        fake = self.use(FakeHindsight(mission=memory.RETAIN_MISSION))
        await memory._clear_hindsight()
        self.assertEqual([c[0] for c in fake.calls[:2]], ["delete_bank", "create_bank"])
        self.assertEqual(fake.mission, memory.RETAIN_MISSION)

    async def test_retain_exchange_drops_the_reply_when_empty(self):
        fake = self.use(FakeHindsight())
        await memory.retain_exchange("what's the news", "")
        await memory.retain_exchange("hi", "hello")
        contents = [c[1]["content"] for c in fake.calls if c[0] == "retain"]
        self.assertEqual(contents, ["User: what's the news", "User: hi\nGlitch: hello"])

    async def test_retain_fact_tags_its_importance(self):
        fake = self.use(FakeHindsight())
        await memory.retain_fact("The user is Josh.", "core")
        await memory.retain_fact("The user likes tea.", "bogus")
        tags = [c[1]["tags"] for c in fake.calls if c[0] == "retain"]
        self.assertEqual(tags, [["importance:core"], ["importance:normal"]])

    async def test_recall_puts_core_facts_first_without_duplicates(self):
        fake = self.use(FakeHindsight())
        fake.recall_results = {"normal": ["likes coffee", "is named Josh"], "core": ["is named Josh"]}
        block = await memory.recall_relevant("what do I drink?")
        self.assertEqual(block.splitlines(), ["- is named Josh", "- likes coffee"])

    async def test_a_core_recall_failure_never_costs_the_ordinary_recall(self):
        fake = self.use(FakeHindsight())
        fake.recall_results = {"normal": ["likes coffee"], "core": []}
        original = fake.arecall

        async def flaky(bank_id, query, **kwargs):
            if kwargs.get("tags"):
                raise RuntimeError("tag filter unsupported")
            return await original(bank_id, query, **kwargs)

        fake.arecall = flaky
        self.assertEqual(await memory.recall_relevant("q"), "- likes coffee")

    async def test_unconfigured_memory_does_nothing(self):
        with mock.patch.object(memory, "_client", None):
            self.assertEqual(await memory.recall_relevant("q"), "")
            self.assertEqual(await memory.recall_core(), [])
            await memory.retain_exchange("a", "b")  # no crash
            with self.assertRaises(RuntimeError):
                await memory.retain_fact("x", "core")  # an approval must fail loudly, not vanish


if __name__ == "__main__":
    unittest.main()
