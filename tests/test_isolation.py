"""Her main soul.md / the user's user.md must be written only by hand (or the
Settings manual editor) -- nothing about role-play souls/profiles may reach them.
"""

import unittest
from unittest import mock

from tests import helpers
from tests.helpers import FakeBrain, FakeLLM, main, profiles, souls


def read(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


class MainFilesAreIsolated(unittest.TestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        self.tmp = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        souls.write_main_soul("MAIN SOUL")
        profiles.write_main_user("MAIN USER")

    def assert_main_untouched(self):
        self.assertEqual(read(souls.SOUL_MD_PATH), "MAIN SOUL")
        self.assertEqual(read(profiles.USER_MD_PATH), "MAIN USER")

    def test_saving_loading_and_deleting_an_rp_soul_never_touch_soul_md(self):
        souls.save_soul("Pirate", "You are a pirate.", "User: hi\nYou: arr")
        self.assert_main_untouched()
        souls.load_soul("Pirate")
        self.assert_main_untouched()
        self.assertIn("You are a pirate.", read(souls.RP_SOUL_PATH))
        souls.load_soul(souls.DEFAULT_SOUL_NAME)
        self.assert_main_untouched()
        self.assertEqual(read(souls.RP_SOUL_PATH), "")
        souls.delete_soul("Pirate")
        self.assert_main_untouched()

    def test_saving_loading_and_deleting_an_rp_profile_never_touch_user_md(self):
        profiles.save_profile("Alyssa", "Alyssa is a barista.")
        self.assert_main_untouched()
        profiles.load_profile("Alyssa")
        self.assert_main_untouched()
        self.assertEqual(read(profiles.RP_USER_MD_PATH), "Alyssa is a barista.")
        profiles.delete_profile("Alyssa")
        self.assert_main_untouched()

    def test_reserved_default_names_cannot_be_saved_or_deleted(self):
        for fn in (
            lambda: souls.save_soul(souls.DEFAULT_SOUL_NAME, "x", ""),
            lambda: souls.delete_soul(souls.DEFAULT_SOUL_NAME),
            lambda: profiles.save_profile(profiles.DEFAULT_PROFILE_NAME, "x"),
            lambda: profiles.delete_profile(profiles.DEFAULT_PROFILE_NAME),
        ):
            with self.assertRaises(ValueError):
                fn()
        self.assert_main_untouched()

    def test_multiple_rp_profiles_coexist(self):
        profiles.save_profile("One", "first")
        profiles.save_profile("Two", "second")
        self.assertEqual(profiles.list_profiles(), ["One", "Two"])
        profiles.load_profile("Two")
        self.assertEqual(profiles.read_active_profile(), "second")
        self.assertEqual(profiles.read_active_profile_name(), "Two")

    def test_manual_editor_writes_main_files_only(self):
        souls.save_soul("Pirate", "pirate", "")
        souls.load_soul("Pirate")
        profiles.save_profile("Alyssa", "rp profile")
        profiles.load_profile("Alyssa")
        rp_soul, rp_user = read(souls.RP_SOUL_PATH), read(profiles.RP_USER_MD_PATH)
        brain = FakeBrain()
        main._handle_save_soul_and_user({"soul": "NEW MAIN SOUL", "user": "NEW MAIN USER"}, brain)
        self.assertEqual(read(souls.SOUL_MD_PATH), "NEW MAIN SOUL")
        self.assertEqual(read(profiles.USER_MD_PATH), "NEW MAIN USER")
        self.assertEqual(read(souls.RP_SOUL_PATH), rp_soul)
        self.assertEqual(read(profiles.RP_USER_MD_PATH), rp_user)


class EffectiveSoulAndUserInfo(unittest.TestCase):
    def setUp(self):
        cm = helpers.isolated_state()
        self.tmp = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        souls.write_main_soul("MAIN SOUL")
        profiles.write_main_user("MAIN USER")
        souls.save_soul("Pirate", "PIRATE SOUL", "")
        souls.load_soul("Pirate")

    def test_soul_is_main_unless_roleplay_is_on_with_an_rp_soul(self):
        profiles.set_roleplay_active(False)
        self.assertEqual(main._effective_soul(), "MAIN SOUL")
        profiles.set_roleplay_active(True)
        self.assertIn("PIRATE SOUL", main._effective_soul())

    def test_roleplay_with_no_rp_soul_falls_back_to_main(self):
        souls.load_soul(souls.DEFAULT_SOUL_NAME)
        profiles.set_roleplay_active(True)
        self.assertEqual(main._effective_soul(), "MAIN SOUL")

    def test_user_info_is_main_user_md_and_pauses_during_roleplay(self):
        profiles.set_roleplay_active(False)
        self.assertEqual(main._effective_user_info(), "MAIN USER")
        profiles.set_roleplay_active(True)
        self.assertEqual(main._effective_user_info(), "")

    def test_user_info_is_reread_every_call_and_capped(self):
        profiles.set_roleplay_active(False)
        profiles.write_main_user("edited in an editor")
        self.assertEqual(main._effective_user_info(), "edited in an editor")
        profiles.write_main_user("x" * (main.MAX_USER_INFO_CHARS + 500))
        self.assertEqual(len(main._effective_user_info()), main.MAX_USER_INFO_CHARS)

    def test_missing_files_are_empty_not_errors(self):
        souls.SOUL_MD_PATH.unlink()
        profiles.USER_MD_PATH.unlink()
        profiles.set_roleplay_active(False)
        self.assertEqual(main._effective_soul(), "")
        self.assertEqual(main._effective_user_info(), "")


if __name__ == "__main__":
    unittest.main()
