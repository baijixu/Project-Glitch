"""Shared name-sanitizing helper for profiles.py/souls.py/avatars.py -- all
three store a user-supplied name (arriving over the WS connection as plain
user input, never implicitly trusted just because it's this project's own
Renderer on the other end) as a filename on disk, so all three need the
exact same allow-list treatment.
"""


def sanitize_name(name: str, *, kind: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    if not cleaned:
        raise ValueError(f"{kind} name {name!r} has no usable characters")
    return cleaned
