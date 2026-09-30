"""The two kinds of saved setting Brain keeps next to its code:

* NamedStore -- saved items of one kind, one JSON file per name (LLM engines,
  speech engines, harnesses, memory profiles). The name comes from the
  Renderer, so it's sanitized (names.py) before it becomes a filename, and a
  reserved name ("None") can be neither saved nor deleted -- the Renderer
  always offers it itself.
* Choice -- one picked name in a text file (the active engine, the selected
  harness...), read back as `default` when nothing has been picked.
"""

import json
from pathlib import Path

from names import sanitize_name


class NamedStore:
    def __init__(self, directory: Path, kind: str, reserved: str | None = None) -> None:
        self.dir = directory
        self.kind = kind  # for error messages: "LLM engine", "speech engine"...
        self.reserved = reserved

    def path(self, name: str) -> Path:
        return self.dir / f"{sanitize_name(name, kind=self.kind)}.json"

    def names(self) -> list[str]:
        self.dir.mkdir(exist_ok=True)
        return sorted(p.stem for p in self.dir.glob("*.json"))

    def read(self, name: str) -> dict:
        """Raises ValueError for a name with nothing usable in it, OSError for one that isn't saved."""
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def write(self, name: str, data: dict) -> str:
        """Creates or overwrites `name`; returns the (sanitized) name it was saved under."""
        path = self.path(name)
        if path.stem == self.reserved:
            raise ValueError(f"{self.reserved!r} is reserved and can't be used as a {self.kind} name")
        self.dir.mkdir(exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path.stem

    def update(self, name: str, **fields) -> None:
        """Changes some fields of an already-saved item, keeping the rest."""
        self.write(name, {**self.read(name), **fields})

    def delete(self, name: str) -> None:
        if name == self.reserved:
            raise ValueError(f"{self.reserved!r} is reserved and can't be deleted")
        self.path(name).unlink()


class Choice:
    def __init__(self, path: Path, default: str = "") -> None:
        self.path = path
        self.default = default

    def read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip() or self.default
        except OSError:
            return self.default

    def write(self, name: str) -> None:
        self.path.write_text(name, encoding="utf-8")
