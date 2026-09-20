"""Back up everything Glitch keeps locally that git doesn't track.

Her souls, your user.md, the RP files, notes, lessons, avatars, voices, config
and toggles are all gitignored, so a lost drive or a bad edit would take them
with it. This copies every gitignored file (except build/cache junk) into a
dated folder beside the repo:

    python tools/backup_local_state.py                 # -> ../glitch-backups/glitch-backup-YYYYmmdd-HHMMSS/
    python tools/backup_local_state.py --dest E:\\backups --keep 20
    python tools/backup_local_state.py --force         # even if nothing changed

Stdlib only -- run it with any Python 3, no venv needed. A backup identical to
the newest one is skipped, so it's safe to run on a schedule. Only the newest
--keep backups are kept.

The backup contains secrets (config.yaml's auth token, the Hindsight API key)
and your conversations' memory exports. Keep it somewhere private, and never
inside a folder that is pushed anywhere.

To restore: copy the files back over the repo, keeping the same relative paths
(e.g. copy <backup>/brain/soul.md to <repo>/brain/soul.md), then restart Brain.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO.parent / "glitch-backups"
BACKUP_PREFIX = "glitch-backup-"
MANIFEST = "MANIFEST.json"

# Never worth backing up: reinstallable, regenerable or just noise.
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", "dist", ".git", ".pytest_cache"}
SKIP_NAMES = {"restart.log", "_kokoro_openapi.json"}
SKIP_SUFFIXES = {".pyc"}


def ignored_files() -> list[Path]:
    """Every file git ignores (so isn't in the repo), relative to the repo root."""
    out = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode("utf-8", errors="replace")
    files = []
    for raw in filter(None, out.split("\0")):
        path = Path(raw)
        if SKIP_DIRS & set(path.parts) or path.name in SKIP_NAMES or path.suffix in SKIP_SUFFIXES:
            continue
        if (REPO / path).is_file():
            files.append(path)
    return sorted(files)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_backups(dest: Path) -> list[Path]:
    return sorted(p for p in dest.glob(BACKUP_PREFIX + "*") if p.is_dir()) if dest.exists() else []


def backup(dest: Path, keep: int, force: bool) -> Path | None:
    files = ignored_files()
    if not files:
        print("nothing to back up -- no ignored local files found")
        return None
    manifest = {str(p).replace("\\", "/"): sha256(REPO / p) for p in files}

    previous = existing_backups(dest)
    if previous and not force:
        try:
            if json.loads((previous[-1] / MANIFEST).read_text(encoding="utf-8")) == manifest:
                print(f"no changes since {previous[-1].name} -- skipped (use --force to back up anyway)")
                return None
        except (OSError, ValueError):
            pass  # unreadable manifest on the old backup: just take a new one

    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    target = dest / f"{BACKUP_PREFIX}{stamp}"
    for attempt in range(2, 1000):  # two backups in the same second must not collide
        if not target.exists():
            break
        target = dest / f"{BACKUP_PREFIX}{stamp}-{attempt}"
    target.mkdir(parents=True)
    try:
        for path in files:
            out = target / path
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / path, out)
        # Verify what was written, not what was intended.
        for rel, digest in manifest.items():
            if sha256(target / rel) != digest:
                raise RuntimeError(f"copy of {rel} doesn't match the original")
        (target / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)  # never leave a half-written backup looking like a good one
        raise

    size = sum((target / rel).stat().st_size for rel in manifest)
    print(f"backed up {len(files)} files ({size / 1_048_576:.1f} MB) -> {target}")

    old = existing_backups(dest)[:-keep] if keep > 0 else []
    for stale in old:
        shutil.rmtree(stale, ignore_errors=True)
    if old:
        print(f"removed {len(old)} older backup(s), keeping the newest {keep}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"backup folder (default: {DEFAULT_DEST})")
    parser.add_argument("--keep", type=int, default=10, help="how many backups to keep (default 10; 0 = keep all)")
    parser.add_argument("--force", action="store_true", help="back up even if nothing changed")
    args = parser.parse_args()
    dest = args.dest.resolve()
    if REPO in dest.parents or dest == REPO:
        print(f"refusing: {dest} is inside the repo -- a backup with secrets in it could get committed", file=sys.stderr)
        return 2
    backup(dest, args.keep, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
