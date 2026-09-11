"""Shared WebSocket message schema -- the Python-side mirror of protocol.md
(the actual source of truth). Every message type Brain code sends or
expects to receive from the Renderer is built/validated through this
module; nothing constructs a raw message dict inline elsewhere, so
protocol.md and this file can't silently drift apart from the rest of the
codebase.
"""

from dataclasses import asdict, dataclass

# Renderer -> Brain message type strings.
PONG = "pong"
READY = "ready"
ANIMATION_FINISHED = "animation_finished"
ERROR = "error"

# Brain -> Renderer message type strings.
PING = "ping"
PLAY_ANIMATION = "play_animation"
SET_EXPRESSION = "set_expression"
VISEME_STREAM = "viseme_stream"


def ping() -> dict:
    return {"type": PING}


def play_animation(name: str, loop: bool = False) -> dict:
    return {"type": PLAY_ANIMATION, "name": name, "loop": loop}


def set_expression(name: str, weight: float) -> dict:
    return {"type": SET_EXPRESSION, "name": name, "weight": weight}


@dataclass
class VisemeFrame:
    t: float
    shape: str
    weight: float


def viseme_stream(frames: list[VisemeFrame]) -> dict:
    return {"type": VISEME_STREAM, "frames": [asdict(f) for f in frames]}
