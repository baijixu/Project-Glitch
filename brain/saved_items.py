"""The Brain side of renderer/src/saved_items.js: every kind of thing saved here that Settings picks from a dropdown
and edits in a modal -- speech engines, LLM engines and harnesses (engines.py), role-play profiles and souls
(characters.py), friends (friends.py) and memory backends (learning.py) -- is saved, read for the editor and deleted
the same way. A kind says what's different about it (Kind); register makes its three handlers. Picking one (load_*)
is each kind's own, since what that means differs."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import websockets

import hub
from hub import Brain

# After a save or a delete: what it means for Brain, and a problem to tell the device that asked, if any.
Hook = Callable[[str, Brain], Awaitable[str | None]]


@dataclass(frozen=True, kw_only=True)
class Kind:
    label: str  # for the console: "speech engine"
    messages: tuple[str, str, str]  # its save, get and delete message types
    fields: dict  # each editable field -> its default ("", or "openai"/False for an LLM engine)
    required: str  # the field a save can't go without
    save: Callable[..., None]  # (name, **fields); raises ValueError to refuse
    read: Callable[[str], dict]  # name -> its fields
    delete: Callable[[str], None]
    content: Callable[..., dict]  # the protocol message answering get: (name, **fields)
    changed: Callable[[], dict]  # what every device gets after a save or delete
    refused: Callable[[str], dict] | None = None  # what the asking device gets when it's refused; None: the console only
    saved: Hook | None = None
    deleted: Hook | None = None  # e.g. the one in use is gone: fall back
    reachability: dict | None = None  # health's status-light cache, stale after an edit


def register(kind: Kind) -> None:
    save_type, get_type, delete_type = kind.messages

    async def refuse(websocket: websockets.ServerConnection, problem: str) -> None:
        if kind.refused:
            await hub.send(websocket, kind.refused(problem))

    async def changed(websocket: websockets.ServerConnection, name: str, hook: Hook | None, brain: Brain) -> None:
        if kind.reachability is not None:
            kind.reachability.pop(name, None)  # the endpoint may have changed; the next check repaints it
        problem = await hook(name, brain) if hook else None
        await hub.broadcast(kind.changed())
        if problem:
            await refuse(websocket, problem)

    @hub.handles(save_type)
    async def save(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
        name = data.get("name", "")
        values = {
            field: bool(data.get(field, default)) if isinstance(default, bool) else (data.get(field) or default)
            for field, default in kind.fields.items()
        }
        text = [name, *(v for v in values.values() if isinstance(v, str))]
        if not name.strip() or not values[kind.required].strip() or hub.fields_too_long(*text):
            return
        try:
            kind.save(name, **values)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't save {kind.label} {name!r}: {exc!r}")
            return await refuse(websocket, str(exc))
        print(f"[brain] saved {kind.label} {name!r}")
        await changed(websocket, name, kind.saved, brain)

    @hub.handles(get_type)
    async def get(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
        """The Edit dialog asking for what's saved, api_key included."""
        name = data.get("name", "")
        try:
            saved = kind.read(name)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't read {kind.label} {name!r}: {exc!r}")
            return await refuse(websocket, f"Couldn't open {name!r}.")
        await hub.send(websocket, kind.content(name, **{field: saved.get(field, default) for field, default in kind.fields.items()}))

    @hub.handles(delete_type)
    async def delete(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
        name = data.get("name", "")
        try:
            kind.delete(name)
        except (ValueError, OSError) as exc:
            print(f"[brain] couldn't delete {kind.label} {name!r}: {exc!r}")
            return await refuse(websocket, str(exc))
        print(f"[brain] deleted {kind.label} {name!r}")
        await changed(websocket, name, kind.deleted, brain)
