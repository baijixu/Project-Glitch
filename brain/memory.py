"""Glitch's own native memory of who the user is -- entirely separate
from, and never touched by, the Hermes harness (see harness.py's own
docstring on how the harness bypasses this module completely).

Two interchangeable backends, picked via set_provider()/read_provider():

- "local" (the default -- nothing to set up): one fact per line in
  memory.md, extracted by a lightweight extra LLM call after every reply
  (see main.py's _maybe_retain_memory), and re-injected as one static
  block every turn regardless of what's being asked.
- "hindsight": talks to a Hindsight server
  (https://pypi.org/project/hindsight-client/, the same semantic-memory
  service Hermes itself can use) in its own bank, kept separate from
  whatever bank a Hermes harness might use. Extraction and retrieval both
  happen server-side: retain_exchange() just hands it the raw exchange
  text and lets it decide what's worth keeping, and recall_relevant()
  asks it for whatever's actually relevant to the CURRENT message instead
  of one static block every turn. Requires a real server someone's
  actually running -- not everyone has one, which is why this isn't the
  default.

configure() primes the Hindsight client from either the Settings panel's
saved hindsight_config.json or config.yaml's one-time-seed brain.hindsight
block (see main.py). Every hindsight_* function below is a no-op/returns
""/[] if it was never called -- same graceful-degradation pattern as a
missing brain.llm block, not a crash -- but that only matters when
read_provider() is actually "hindsight"; the local functions never
touch it at all.
"""

import asyncio
import json
from pathlib import Path

import training
from hindsight_client import Hindsight

MEMORY_PATH = Path(__file__).parent / "memory.md"
MEMORY_ACTIVE_PATH = Path(__file__).parent / "memory_active.txt"
PROVIDER_PATH = Path(__file__).parent / "memory_provider.txt"
HINDSIGHT_CONFIG_PATH = Path(__file__).parent / "hindsight_config.json"

LOCAL_PROVIDER = "local"
HINDSIGHT_PROVIDER = "hindsight"

# Over this many chars, the local provider's oldest entries are just
# dropped (FIFO) rather than intelligently merged -- a deliberate
# simplification, worth revisiting only if it actually becomes a problem.
MAX_MEMORY_CHARS = 2000

# Keeps a hindsight recall roughly in the same ballpark as the local
# provider's own MAX_MEMORY_CHARS cap -- plenty for what's actually
# relevant to one message, not a dump of the whole bank.
RECALL_MAX_TOKENS = 800
CORE_RECALL_MAX_TOKENS = 300  # the always-shown "core" facts (retain_fact / brain/training.py)

# Steers what Hindsight's server-side extraction keeps from each retained
# exchange (its per-bank `retain_mission` setting). Without one it keeps
# everything -- a live bank filled up with descriptions of camera frames,
# news headlines from searches, and endless restatements of what Glitch
# herself is, none of which is a memory of the *user*. Applied by
# ensure_bank() when the bank has no mission or still has an earlier default
# of ours, so it never overwrites one someone set by hand.
_MISSION_V1 = (
    "Keep only durable, useful facts about the user: who they are, what they are building or "
    "working on, their preferences and interests, people in their life, decisions they have made, "
    "and corrections they have given the assistant. Do not keep: descriptions of images, screens "
    "or camera frames; news headlines or search results (unless the user expressed an opinion or "
    "interest in them); anything about the assistant itself, such as what it is or what it can do; "
    "small talk; or temporary states and debugging chatter."
)

_MISSION_V2 = (
    "Keep only durable, useful facts about the user: who they are, what they are building or "
    "working on, their preferences and interests, people in their life, decisions they have made, "
    "and corrections they have given the assistant. When the user engages with a topic (news, "
    "sports, music, anything), record THAT they discussed it, asked about it, or how they feel about "
    "it -- never the facts of the topic itself, such as what was announced or who won. A topic the "
    "assistant brings up that the user never engages with is not worth keeping. Do not keep: "
    "descriptions of images, screens or camera frames; news headlines or search results; anything "
    "about the assistant itself, such as what it is or what it can do; small talk; or temporary "
    "states and debugging chatter."
)

RETAIN_MISSION = (
    _MISSION_V2
    + " In what you are given, 'User' is the human and 'Glitch' is the AI assistant, two different "
    "beings. Attribute every statement to whoever actually said it: never record something the "
    "assistant said, did, wore or pretended as a fact about the user, and never record the user's "
    "name, life or traits as the assistant's."
)

# Earlier versions of the default above. A bank still carrying one of these
# was never customized, so ensure_bank() upgrades it to the current default --
# anything else there was written by hand and is left alone.
_PREVIOUS_DEFAULT_MISSIONS = (_MISSION_V1, _MISSION_V2)

_client: Hindsight | None = None
_bank_id = ""


# -- Provider choice (Settings panel's Memory backend dropdown) -------------


def read_provider() -> str:
    if PROVIDER_PATH.exists() and PROVIDER_PATH.read_text(encoding="utf-8").strip() == HINDSIGHT_PROVIDER:
        return HINDSIGHT_PROVIDER
    return LOCAL_PROVIDER


def has_saved_provider() -> bool:
    """Whether the user (or a one-time config.yaml seed, see main.py) has
    ever actually chosen a provider -- distinct from read_provider(),
    which always returns a usable default even when this is False.
    """
    return PROVIDER_PATH.exists()


def set_provider(provider: str) -> None:
    PROVIDER_PATH.write_text(provider, encoding="utf-8")


# -- Hindsight connection (Settings panel's Memory Server fields) -----------


def configure(api_url: str, bank_id: str, api_key: str | None = None) -> None:
    global _client, _bank_id
    _client = Hindsight(base_url=api_url, api_key=api_key or None)
    _bank_id = bank_id


def read_hindsight_config() -> dict:
    """The saved {api_url, api_key, bank_id}, or {} if never saved --
    config.yaml's own brain.hindsight block is only a one-time seed for
    this, read directly by main.py, same pattern as a saved harness
    overriding its own config.yaml seed once one exists.
    """
    if HINDSIGHT_CONFIG_PATH.exists():
        return json.loads(HINDSIGHT_CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def save_hindsight_config(api_url: str, api_key: str, bank_id: str) -> None:
    HINDSIGHT_CONFIG_PATH.write_text(
        json.dumps({"api_url": api_url, "api_key": api_key, "bank_id": bank_id}), encoding="utf-8"
    )
    if api_url:
        configure(api_url, bank_id or "glitch-native", api_key)


def hindsight_client() -> Hindsight | None:
    """The configured Hindsight client, or None if configure() was never
    called -- for lessons.py, which talks to the same server (though in its
    own bank, see lessons._bank) rather than opening a second connection.
    """
    return _client


def hindsight_bank_id() -> str:
    return _bank_id


def hindsight_configured() -> bool:
    return _client is not None


async def ensure_bank() -> None:
    """Creates the configured bank, or just updates it if it already
    exists (create_bank is documented as create-or-update, confirmed live
    as idempotent) -- safe to call on every startup/reconfigure. A no-op
    if configure() was never called.
    """
    if _client is None:
        return
    await _client.acreate_bank(_bank_id)
    await _apply_default_retain_mission()


async def _apply_default_retain_mission() -> None:
    """Sets RETAIN_MISSION on the bank unless it already has a retain_mission
    of someone's own -- one that is neither empty, nor the current default,
    nor an earlier default of ours (those get upgraded). Best-effort: a
    server that has bank-config writes disabled
    (HINDSIGHT_API_ENABLE_BANK_CONFIG_API=false) just keeps its own extraction
    behavior -- that must never stop the bank from being usable.
    """
    try:
        config = await _client.aget_bank_config(_bank_id)
        current = (config.get("overrides") or {}).get("retain_mission")
        if current and current != RETAIN_MISSION and current not in _PREVIOUS_DEFAULT_MISSIONS:
            return  # written by hand -- not ours to change
        if current != RETAIN_MISSION:
            await _client.aupdate_bank_config(_bank_id, retain_mission=RETAIN_MISSION)
    except Exception as exc:
        print(f"[memory] couldn't set the default retain mission on bank {_bank_id!r}: {exc!r}")


# -- Hindsight provider's own storage ----------------------------------------


async def retain_exchange(user_text: str, reply_text: str) -> None:
    """Fire-and-forget: hands one exchange to Hindsight to decide what, if
    anything, is worth remembering long-term. An empty reply_text retains
    only what the user said -- main.py passes that for a turn where she
    searched the web, so the search results in her reply never get saved as
    if they were something about the user.
    """
    if _client is None:
        return
    content = f"User: {user_text}\nGlitch: {reply_text}" if reply_text else f"User: {user_text}"
    await _client.aretain(_bank_id, content=content)


async def retain_fact(text: str, importance: str) -> None:
    """Retains one fact the user reviewed and approved (brain/training.py),
    tagged with its importance so core facts can be recalled every turn.
    """
    if _client is None:
        raise RuntimeError("no Hindsight server configured")
    await _client.aretain(
        _bank_id,
        content=text,
        context="A fact the user reviewed and approved",
        tags=[training.importance_tag(importance)],
    )


async def recall_core() -> list[str]:
    """The facts the user marked "core" -- always in her prompt, whatever the
    conversation is about. Best-effort: [] on any failure, so a hiccup here never
    costs the ordinary recall.
    """
    if _client is None:
        return []
    try:
        response = await _client.arecall(
            _bank_id,
            query="the most important things to know about the user",
            max_tokens=CORE_RECALL_MAX_TOKENS,
            tags=[training.importance_tag("core")],
            tags_match="any_strict",
        )
    except Exception as exc:
        print(f"[memory] core recall failed: {exc!r}")
        return []
    return [result.text for result in response.results]


async def recall_relevant(query: str) -> str:
    """Whatever's actually relevant to `query` right now, joined into the
    same "- fact" block shape LocalLLM.set_memory already expects -- the
    user's "core" facts first (see brain/training.py), then the rest.
    """
    if _client is None:
        return ""
    response, core = await asyncio.gather(
        _client.arecall(_bank_id, query=query, max_tokens=RECALL_MAX_TOKENS), recall_core()
    )
    lines = list(core)
    lines += [result.text for result in response.results if result.text not in core]
    return "\n".join(f"- {line}" for line in lines)


def _item_text(item) -> str:
    # list_memories' response items are typed as plain dicts in the
    # OpenAPI schema, but confirmed live to actually come back as
    # MemoryUnitListItem objects -- handles either shape rather than
    # trusting one over the other.
    if isinstance(item, dict):
        return item.get("text", "")
    return getattr(item, "text", "") or ""


async def _read_hindsight_entries() -> list[str]:
    if _client is None:
        return []
    response = await _client.memory.list_memories(bank_id=_bank_id, limit=500)
    return [text for item in response.items if (text := _item_text(item))]


async def _clear_hindsight() -> None:
    if _client is None:
        return
    await _client.adelete_bank(_bank_id)
    await _client.acreate_bank(_bank_id)
    # A recreated bank starts with no retain_mission -- without this, everything
    # retained until the next Brain restart (which re-applies it via ensure_bank)
    # would be extracted with no guidance at all.
    await _apply_default_retain_mission()


# -- Local provider's own storage --------------------------------------------


def read_local_entries() -> list[str]:
    if not MEMORY_PATH.exists():
        return []
    return [line for line in MEMORY_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_local_block() -> str:
    """Joined entries for feeding straight into LocalLLM.set_memory -- ""
    (not None) when empty, since _system_prompt just skips a falsy block.
    """
    return "\n".join(f"- {entry}" for entry in read_local_entries())


def add_local_entry(fact: str) -> bool:
    """Appends a new fact, skipping an exact-duplicate re-add. Trims the
    OLDEST entries first when over MAX_MEMORY_CHARS. Returns whether
    anything was actually added -- main.py uses this to decide whether the
    memory_learned notification should fire at all.
    """
    fact = fact.strip()
    if not fact:
        return False
    entries = read_local_entries()
    if fact in entries:
        return False
    entries.append(fact)
    while entries and sum(len(e) for e in entries) > MAX_MEMORY_CHARS:
        entries.pop(0)
    MEMORY_PATH.write_text("\n".join(entries), encoding="utf-8")
    return True


def _clear_local() -> None:
    MEMORY_PATH.write_text("", encoding="utf-8")


# -- Unified entry points (main.py calls these, provider-agnostic) ----------


async def recall_for_prompt(query: str) -> str:
    """Whatever should go into LocalLLM.set_memory() this turn."""
    if read_provider() == HINDSIGHT_PROVIDER:
        return await recall_relevant(query)
    return read_local_block()


async def read_entries() -> list[str]:
    """For the Settings panel's Download Memory button."""
    if read_provider() == HINDSIGHT_PROVIDER:
        return await _read_hindsight_entries()
    return read_local_entries()


async def clear() -> None:
    """For the Settings panel's Clear Memory button."""
    if read_provider() == HINDSIGHT_PROVIDER:
        await _clear_hindsight()
    else:
        _clear_local()


# -- Shared (both providers) --------------------------------------------------


def set_memory_active(active: bool) -> None:
    MEMORY_ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_memory_active() -> bool:
    """Defaults to True (on) -- unlike Mic Always-On, this feature's whole
    point is to be on so she actually gets to know the user, so it opts in
    by default rather than requiring the user to find and flip it. The
    local provider is always "available" (nothing to configure); the
    hindsight provider additionally needs configure() to have actually
    been called (a real server address known), same graceful-degradation
    reasoning as a missing brain.llm block.
    """
    if read_provider() == HINDSIGHT_PROVIDER and _client is None:
        return False
    if MEMORY_ACTIVE_PATH.exists():
        return MEMORY_ACTIVE_PATH.read_text(encoding="utf-8").strip() != "0"
    return True
