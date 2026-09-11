"""Loads the shared config.yaml (falling back to config.example.yaml if it
doesn't exist yet) -- the single source of truth for the Brain's WS server
bind address (SPEC.md section 5).
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent


def load_config() -> dict:
    real = REPO_ROOT / "config.yaml"
    example = REPO_ROOT / "config.example.yaml"
    path = real if real.exists() else example
    if path is example:
        print(f"[config] {real} not found, falling back to {example}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))
