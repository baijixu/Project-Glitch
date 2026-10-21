"""The 🧠 "Her mind" window (Renderer's mind_ui.js / growth_ui.js): what went into her last reply and
what's waiting on him -- the memories recalled for it, his review list, her wonder list, what she saved
or chose on her own, today against her caps, her soul.md's size -- and its Growth tab's charts
(metrics.growth). Each is asked for when the window opens, so it's never stale."""

from datetime import datetime

import websockets

import heartbeat
import hub
import lessons
import memory
import metrics
import notable
import protocol
import reflection
import souls
import training
import wonder
from hub import Brain

SHOWN_OWN = 8  # her latest saves without him, and her latest choices, newest first


@hub.handles(protocol.GET_MIND)
async def _get_mind(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await hub.send(websocket, mind_message(brain))


@hub.handles(protocol.ADD_WONDER, protocol.REMOVE_WONDER)
async def _wonder(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """Something he adds to what she wants to find out, or takes off."""
    if data["type"] == protocol.ADD_WONDER:
        wonder.add(str(data.get("text") or ""), "Added by him")
    else:
        wonder.remove(str(data.get("id") or ""))
    await hub.send(websocket, mind_message(brain))


_reflecting = False  # set before the task starts, so a second press can't start another


@hub.handles(protocol.REFLECT_NOW)
async def _reflect_now(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    """The button: her weekly look-back and soul revision now, in the background."""
    global _reflecting
    if _reflecting:
        return
    _reflecting = True

    async def reflect() -> None:
        global _reflecting
        try:
            await reflection.run(brain)
            hub.spawn(reflection.progress_exam())  # the button resets her week, so the night's exam would be skipped
        finally:
            _reflecting = False

    hub.spawn(reflect())


@hub.handles(protocol.GET_GROWTH)
async def _get_growth(websocket: websockets.ServerConnection, data: dict, brain: Brain) -> None:
    await hub.send(websocket, protocol.growth(metrics.growth()))


def mind_message(brain: Brain) -> dict:
    soul = souls.SOUL_MD_PATH
    text = soul.read_text(encoding="utf-8") if soul.exists() else ""
    rows = metrics.read()
    own = [{"text": r["text"], "source": r.get("source", ""), "t": r["t"]} for r in rows if r["e"] == "approved" and r.get("by") == "her"]
    chose = [{"what": r["what"], "why": r.get("reason", ""), "t": r["t"]} for r in rows if r["e"] == "activity" and "reason" in r]
    day = heartbeat.day_usage(datetime.now())
    del day["did"]
    return protocol.mind({
        "recalled": [line[2:] for line in brain.llm.memory_block.splitlines() if line.startswith("- ")],
        "pending": training.read_pending(),
        "lessons": [lessons.describe(p) for p in lessons.read_pending()],
        "wondering": wonder.read()[::-1],
        "own": own[-SHOWN_OWN:][::-1],
        "chose": chose[-SHOWN_OWN:][::-1],
        "day": {**day, "reflect": [notable.for_him(e["what"]) for e in notable.waiting()]},
        "soul": {
            "chars": len(text), "lines": len(text.splitlines()), "max": souls.SOUL_MAX_CHARS,
            "edited": datetime.fromtimestamp(soul.stat().st_mtime).isoformat(timespec="minutes") if soul.exists() else "",
        },
    })


async def daily_snapshot() -> None:
    """Once a day (metrics.snapshot_due): how big her memory bank and his review list are, for Growth."""
    try:
        size = len(await memory.list_memories()) if memory.hindsight_client() else None  # not connected: no count, not 0
    except Exception as exc:
        print(f"[metrics] counting her memories failed: {exc!r}")
        size = None
    metrics.log("bank", size=size, queue=len(training.read_pending()), lessons=len(lessons.read_pending()))
