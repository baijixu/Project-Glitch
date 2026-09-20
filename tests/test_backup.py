"""tools/backup_local_state.py against a throwaway git repo."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "backup_local_state.py"
spec = importlib.util.spec_from_file_location("backup_local_state", SCRIPT)
backup_local_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_local_state)


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class BackupScript(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo, self.dest = root / "repo", root / "backups"
        (self.repo / "brain" / "souls").mkdir(parents=True)
        (self.repo / "node_modules" / "pkg").mkdir(parents=True)
        (self.repo / "brain" / "__pycache__").mkdir(parents=True)
        git(self.repo, "init", "-q")
        (self.repo / ".gitignore").write_text(
            "brain/soul.md\nbrain/user.md\nbrain/souls/\nconfig.yaml\nbrain/restart.log\nnode_modules/\n__pycache__/\n", encoding="utf-8"
        )
        (self.repo / "brain" / "tracked.py").write_text("print('tracked')", encoding="utf-8")
        for rel, text in {
            "brain/soul.md": "her soul",
            "brain/user.md": "about me",
            "brain/souls/Pirate.md": "arr",
            "config.yaml": "auth_token: secret",
            "brain/restart.log": "noise",
            "node_modules/pkg/index.js": "junk",
            "brain/__pycache__/x.pyc": "junk",
        }.items():
            (self.repo / rel).write_text(text, encoding="utf-8")
        git(self.repo, "add", ".gitignore", "brain/tracked.py")
        p = mock.patch.object(backup_local_state, "REPO", self.repo.resolve())
        p.start()
        self.addCleanup(p.stop)

    def backups(self):
        return sorted(p for p in self.dest.glob("glitch-backup-*") if p.is_dir())

    def test_copies_ignored_local_files_and_nothing_else(self):
        target = backup_local_state.backup(self.dest, keep=10, force=False)
        for rel, text in {"brain/soul.md": "her soul", "brain/user.md": "about me", "brain/souls/Pirate.md": "arr", "config.yaml": "auth_token: secret"}.items():
            self.assertEqual((target / rel).read_text(encoding="utf-8"), text, rel)
        for absent in ("brain/tracked.py", "brain/restart.log", "node_modules", "brain/__pycache__"):
            self.assertFalse((target / absent).exists(), absent)  # tracked by git, or junk
        manifest = json.loads((target / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest), ["brain/soul.md", "brain/souls/Pirate.md", "brain/user.md", "config.yaml"])

    def test_an_unchanged_repo_is_skipped_and_a_change_is_not(self):
        self.assertIsNotNone(backup_local_state.backup(self.dest, 10, False))
        self.assertIsNone(backup_local_state.backup(self.dest, 10, False))
        self.assertEqual(len(self.backups()), 1)
        (self.repo / "brain" / "soul.md").write_text("her soul, rewritten", encoding="utf-8")
        newer = backup_local_state.backup(self.dest, 10, False)
        self.assertIsNotNone(newer)
        self.assertEqual((newer / "brain" / "soul.md").read_text(encoding="utf-8"), "her soul, rewritten")
        self.assertEqual(len(self.backups()), 2)

    def test_force_backs_up_even_when_nothing_changed(self):
        backup_local_state.backup(self.dest, 10, False)
        with mock.patch.object(backup_local_state, "datetime") as fake_dt:
            fake_dt.now.return_value = __import__("datetime").datetime(2030, 1, 1, 0, 0, 0)
            self.assertIsNotNone(backup_local_state.backup(self.dest, 10, True))
        self.assertEqual(len(self.backups()), 2)

    def test_only_the_newest_backups_are_kept(self):
        for i in range(4):
            (self.repo / "brain" / "user.md").write_text(f"version {i}", encoding="utf-8")
            with mock.patch.object(backup_local_state, "datetime") as fake_dt:
                fake_dt.now.return_value = __import__("datetime").datetime(2030, 1, 1, 0, 0, i)
                backup_local_state.backup(self.dest, keep=2, force=False)
        kept = self.backups()
        self.assertEqual(len(kept), 2)
        self.assertEqual((kept[-1] / "brain" / "user.md").read_text(encoding="utf-8"), "version 3")

    def test_a_failed_copy_leaves_no_half_written_backup(self):
        with mock.patch.object(backup_local_state.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                backup_local_state.backup(self.dest, 10, False)
        self.assertEqual(self.backups(), [])

    def test_a_corrupted_copy_is_detected_and_discarded(self):
        real_copy = backup_local_state.shutil.copy2

        def corrupting_copy(src, dst, *a, **kw):
            real_copy(src, dst, *a, **kw)
            Path(dst).write_text("garbage", encoding="utf-8")

        with mock.patch.object(backup_local_state.shutil, "copy2", corrupting_copy):
            with self.assertRaises(RuntimeError):
                backup_local_state.backup(self.dest, 10, False)
        self.assertEqual(self.backups(), [])

    def test_refuses_to_write_inside_the_repo(self):
        with mock.patch("sys.argv", ["backup", "--dest", str(self.repo / "inside")]):
            self.assertEqual(backup_local_state.main(), 2)
        self.assertFalse((self.repo / "inside").exists())


if __name__ == "__main__":
    unittest.main()
