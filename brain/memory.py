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

import json
from pathlib import Path

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


async def ensure_bank() -> None:
    """Creates the configured bank, or just updates it if it already
    exists (create_bank is documented as create-or-update, confirmed live
    as idempotent) -- safe to call on every startup/reconfigure. A no-op
    if configure() was never called.
    """
    if _client is None:
        return
    await _client.acreate_bank(_bank_id)


# -- Hindsight provider's own storage ----------------------------------------


async def retain_exchange(user_text: str, reply_text: str) -> None:
    """Fire-and-forget: hands one exchange to Hindsight to decide what, if
    anything, is worth remembering long-term.
    """
    if _client is None:
        return
    await _client.aretain(_bank_id, content=f"User: {user_text}\nGlitch: {reply_text}")


async def recall_relevant(query: str) -> str:
    """Whatever's actually relevant to `query` right now, joined into the
    same "- fact" block shape LocalLLM.set_memory already expects.
    """
    if _client is None:
        return ""
    response = await _client.arecall(_bank_id, query=query, max_tokens=RECALL_MAX_TOKENS)
    return "\n".join(f"- {result.text}" for result in response.results)


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
