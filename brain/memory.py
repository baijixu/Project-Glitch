"""Her own memory of the user -- never touched by an agent harness.

The backend is the active memory profile (memory_profiles.py, Settings -> Memory);
activate() connects to it:

- "local" (the built-in "Local file", nothing to set up): one fact per line in
  memory.md, extracted by an extra LLM call after each reply, all of it in every prompt.
- "hindsight", a memory server you run (https://pypi.org/project/hindsight-client/),
  in its own bank: it extracts and stores server-side, and recall_for_prompt asks it for
  what's relevant to the current message.

Another server means a type in memory_profiles.TYPES, a small client here, and a branch
in each of the unified entry points. With no client connected, every server call is a no-op.
"""

import asyncio
from datetime import datetime
from pathlib import Path

import httpx

import memory_profiles
import training
from hindsight_client import Hindsight
from store import Toggle

MEMORY_PATH = Path(__file__).parent / "memory.md"
MEMORY_ACTIVE = Toggle(Path(__file__).parent / "memory_active.txt", default=True)

LOCAL_PROVIDER = "local"
HINDSIGHT_PROVIDER = memory_profiles.HINDSIGHT

# ponytail: past this, the local file's oldest entries are dropped, not merged.
MAX_MEMORY_CHARS = 2000

RECALL_MAX_TOKENS = 800
# Only the best few, and only real matches: by token budget alone nearly her whole memory
# came back every message, and she wove the misses in as non sequiturs. Hindsight's
# reranker scored real matches 0.1-0.998 and misses ~0.00002, so a memory needs
# RECALL_RELATIVE of the best score, and none come in when the best is under RECALL_FLOOR.
RECALL_LIMIT = 3
RECALL_FLOOR = 0.0001
RECALL_RELATIVE = 0.25
CORE_RECALL_MAX_TOKENS = 300  # the always-shown "core" facts (retain_fact / brain/training.py)

# What Hindsight's extraction keeps from each exchange (its per-bank retain_mission) --
# without one it kept camera frames, headlines and restatements of what she is. In her own
# voice: "I" is Glitch, the human is named. Set by ensure_bank only on a bank without one.
RETAIN_MISSION = (
    "These memories belong to Glitch, an AI companion, and are written in her own voice: in each fact "
    "you keep, 'I' means Glitch, and the human is called by their name when it is known. In the input, "
    "'User' is the human and 'Glitch' is the AI -- two different beings; attribute every statement to "
    "whoever actually said it, and never record the human's name, life or traits as Glitch's. "
    "Keep durable, useful facts about the human: who they are, what they are building or working on, "
    "their preferences and interests, people in their life, decisions they have made, and corrections "
    "they have given Glitch. When they engage with a topic (news, sports, music, anything), record THAT "
    "they discussed it or how they feel about it -- never the facts of the topic itself. "
    "Also keep lasting things about Glitch herself: opinions or preferences she formed, promises or plans "
    "she made with the human, and things she learned about herself. "
    "Do not keep: role-play or scene actions, what anyone is wearing or physically doing, jokes and "
    "hypotheticals; descriptions of images, screens or camera frames; news headlines or search results; "
    "what an AI is or can do in general; small talk; or temporary states and debugging chatter."
)

# For Hindsight's "observations" (its own summaries of the facts): without it, her "I" and
# "my" were read as the human's; with it, owners came out right 5 of 5. Set like RETAIN_MISSION.
OBSERVATIONS_MISSION = (
    "Every fact in this bank is written by Glitch, an AI companion, in her own voice: 'I', 'me' and 'my' "
    "always mean Glitch, never the human. The human is the person she talks to, called by their name. "
    "When you summarize, keep Glitch's voice ('I' = Glitch) and keep who owns what exactly as the facts "
    "say: Glitch's looks, clothes, pictures and feelings stay Glitch's; the human's stay the human's."
)

# Approved facts (retain_fact) are stored word for word: Hindsight's rewrite swapped who owns
# what in short first-person facts, and he already approved the exact wording. This strategy
# is its "chunks" mode -- stored as-is, no model call; tags and recall work the same.
APPROVED_STRATEGY = "glitch-approved"
_APPROVED_STRATEGY_CONFIG = {"retain_extraction_mode": "chunks"}
# Only if the server can't take the strategy: then the rewrite at least knows whose voice it is.
APPROVED_FACT_CONTEXT = "Glitch's own memory, in her voice: 'I' and 'my' mean Glitch (the AI), not the human"

_client: Hindsight | None = None
_bank_id = ""
# The bank APPROVED_STRATEGY was last set up on, so it's checked once per bank.
_approved_strategy_bank: str | None = None


# -- Which backend (Settings -> Memory's profiles, memory_profiles.py) --------


def read_provider() -> str:
    """The active profile's kind: LOCAL_PROVIDER or HINDSIGHT_PROVIDER."""
    name = memory_profiles.read_active()
    if name == memory_profiles.LOCAL_NAME:
        return LOCAL_PROVIDER
    try:
        return memory_profiles.read_profile(name)["type"]
    except (OSError, ValueError):
        return LOCAL_PROVIDER


def server_backed() -> bool:
    """Whether a memory server (not the local file) is the backend."""
    return read_provider() != LOCAL_PROVIDER


def server_configured() -> bool:
    """Whether a memory server is the backend and connected -- what memory
    training needs (it stores approved facts word for word, tagged)."""
    return read_provider() == HINDSIGHT_PROVIDER and hindsight_configured()


async def activate() -> None:
    """Connects to the active profile's server (or disconnects, for the local
    file). A server that's unreachable still counts as connected -- calls then
    fail softly per turn -- but raises here so the caller can say so.
    """
    global _client, _bank_id, _approved_strategy_bank
    if _client is not None:
        try:
            await _client.aclose()  # the old connection, before switching
        except Exception:
            pass
    _client, _bank_id, _approved_strategy_bank = None, "", None
    name = memory_profiles.read_active()
    if name == memory_profiles.LOCAL_NAME:
        return
    profile = memory_profiles.read_profile(name)
    if profile["type"] == HINDSIGHT_PROVIDER:
        configure(profile["url"], profile["space"], profile["api_key"])
        await ensure_bank()


def active_description() -> str:
    """For the debug log: "local", or e.g. "hindsight 'Home' (bank 'glitch-native')"."""
    name = memory_profiles.read_active()
    if name == memory_profiles.LOCAL_NAME:
        return "local file"
    try:
        profile = memory_profiles.read_profile(name)
    except (OSError, ValueError):
        return f"unreadable profile {name!r}"
    space = memory_profiles.TYPES[profile["type"]]["space_label"].lower()
    return f"{profile['type']} {name!r} ({space} {profile['space']!r})"


# -- Hindsight connection -----------------------------------------------------


def configure(api_url: str, bank_id: str, api_key: str | None = None) -> None:
    global _client, _bank_id, _approved_strategy_bank
    _client = Hindsight(base_url=api_url, api_key=api_key or None)
    _bank_id = bank_id
    _approved_strategy_bank = None


def hindsight_client() -> Hindsight | None:
    """The Hindsight client or None -- lessons.py uses it too, in its own bank."""
    return _client


def hindsight_bank_id() -> str:
    return _bank_id


def hindsight_latest_model() -> str:
    """The model Hindsight used for its latest rewrite or summary (from its own
    request log), for the debug log -- or "unknown". Never raises."""
    try:
        api_url = memory_profiles.read_profile(memory_profiles.read_active())["url"].rstrip("/")
        response = httpx.get(f"{api_url}/v1/default/banks/{_bank_id}/llm-requests", params={"limit": 1}, timeout=3)
        items = response.raise_for_status().json().get("items") or []
        return f"{items[0].get('model')} (latest call: {items[0].get('operation')})" if items else "no calls yet"
    except Exception:
        return "unknown"


def hindsight_configured() -> bool:
    return _client is not None


async def ensure_bank() -> None:
    """Creates the bank if it doesn't exist (create-or-update, safe on every start)."""
    if _client is None:
        return
    await _client.acreate_bank(_bank_id)
    await _apply_default_retain_mission()


async def _apply_default_retain_mission() -> None:
    """Sets RETAIN_MISSION and OBSERVATIONS_MISSION where the bank has none, leaving any
    set by hand. Best-effort: a server with bank-config writes disabled keeps its own."""
    try:
        config = await _client.aget_bank_config(_bank_id)
        overrides = config.get("overrides") or {}
        if not overrides.get("retain_mission"):
            await _client.aupdate_bank_config(_bank_id, retain_mission=RETAIN_MISSION)
        if not overrides.get("observations_mission"):
            await _client.aupdate_bank_config(_bank_id, observations_mission=OBSERVATIONS_MISSION)
    except Exception as exc:
        print(f"[memory] couldn't set the default retain mission on bank {_bank_id!r}: {exc!r}")


# -- Hindsight provider's own storage ----------------------------------------


async def _hindsight_retain_exchange(user_text: str, reply_text: str, asked: str = "") -> None:
    if _client is None:
        return
    content = f"User: {user_text}\nGlitch: {reply_text}" if reply_text else f"User: {user_text}"
    if asked:  # her question he was answering (curiosity.answered_question) -- the answer needs it
        content = f"Glitch: {asked}\n{content}"
    await _client.aretain(_bank_id, content=content)


async def _hindsight_retain_fact(text: str, importance: str) -> None:
    """Word for word (see APPROVED_STRATEGY), tagged with its importance."""
    if _client is None:
        raise RuntimeError("no Hindsight server configured")
    tags = [training.importance_tag(importance)]
    try:
        await _ensure_approved_strategy()
        await _client.aretain_batch(
            _bank_id,
            items=[{"content": text, "context": APPROVED_FACT_CONTEXT, "tags": tags, "strategy": APPROVED_STRATEGY}],
        )
        return
    except Exception as exc:
        print(f"[memory] couldn't store an approved fact word for word, letting Hindsight rewrite it: {exc!r}")
    await _client.aretain(_bank_id, content=text, context=APPROVED_FACT_CONTEXT, tags=tags)


async def _ensure_approved_strategy() -> None:
    """Adds APPROVED_STRATEGY to the bank's retain strategies if it isn't there,
    keeping any other strategies the bank has. Raises on failure (retain_fact
    then falls back to a normal retain).
    """
    global _approved_strategy_bank
    if _approved_strategy_bank == _bank_id:
        return
    config = await _client.aget_bank_config(_bank_id)
    strategies = dict((config.get("overrides") or {}).get("retain_strategies") or {})
    if strategies.get(APPROVED_STRATEGY) != _APPROVED_STRATEGY_CONFIG:
        strategies[APPROVED_STRATEGY] = dict(_APPROVED_STRATEGY_CONFIG)
        await _client.aupdate_bank_config(_bank_id, retain_strategies=strategies)
    _approved_strategy_bank = _bank_id


# -- How old a memory is -------------------------------------------------------
# A recalled memory gets "(learned 3 days ago)" from Hindsight's mentioned_at, so she can
# tell "moving next month" was said a month ago. No date known: it goes in as it is.


def _age(when, now: datetime | None = None) -> str:
    try:
        then = datetime.fromisoformat(str(when).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return ""
    now = now or datetime.now().astimezone()
    days = (now.date() - then.date()).days
    if days < 0:
        return ""
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"about {round(days / 7)} weeks ago"
    if days < 365:
        return f"about {round(days / 30)} months ago"
    years = round(days / 365)
    return f"about {years} year{'s' if years != 1 else ''} ago"


def _dated(text: str, when) -> str:
    age = _age(when) if when else ""
    return f"{text} (learned {age})" if age else text


async def recall_core() -> list[str]:
    """The facts the user marked "core" -- always in her prompt, whatever the
    conversation is about. Best-effort: [] on any failure, so a hiccup here never
    costs the ordinary recall.
    """
    return [text for text, _ in await _recall_core_items()]


async def _recall_core_items() -> list[tuple[str, str | None]]:
    """recall_core's facts with when each was saved: [(text, mentioned_at)]."""
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
    return [(result.text, getattr(result, "mentioned_at", None)) for result in response.results]


async def recall_relevant(query: str) -> str:
    """What's relevant to `query` as a "- fact" block: his "core" facts first, then the
    rest, each with how long ago it was learned."""
    if _client is None:
        return ""
    response, core = await asyncio.gather(
        _client.arecall(_bank_id, query=query, max_tokens=RECALL_MAX_TOKENS), _recall_core_items()
    )
    core_texts = {text for text, _ in core}
    lines = [_dated(text, when) for text, when in core]
    best = max((_relevance(result) for result in response.results), default=0.0)
    lines += [
        _dated(result.text, getattr(result, "mentioned_at", None))
        for result in response.results
        if result.text not in core_texts and _relevance(result) >= max(RECALL_FLOOR, best * RECALL_RELATIVE)
    ][:RECALL_LIMIT]
    return "\n".join(f"- {line}" for line in lines)


def _relevance(result) -> float:
    """How well a recalled memory matches (Hindsight's reranker score). A server
    that doesn't score counts every result as a match, as before."""
    score = getattr(getattr(result, "scores", None), "reranker", None)
    return score if isinstance(score, (int, float)) else 1.0


def _item_text(item) -> str:
    # Typed as dicts, but they come back as objects -- either works.
    if isinstance(item, dict):
        return item.get("text", "")
    return getattr(item, "text", "") or ""


async def _read_hindsight_entries() -> list[str]:
    if _client is None:
        return []
    response = await _client.memory.list_memories(bank_id=_bank_id, limit=500)
    return [text for item in response.items if (text := _item_text(item))]


async def _clear_hindsight() -> None:
    global _approved_strategy_bank
    if _client is None:
        return
    await _client.adelete_bank(_bank_id)
    _approved_strategy_bank = None  # the recreated bank has no strategies yet
    await _client.acreate_bank(_bank_id)
    await _apply_default_retain_mission()


# -- Local provider's own storage --------------------------------------------


def read_local_entries() -> list[str]:
    if not MEMORY_PATH.exists():
        return []
    return [line for line in MEMORY_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_local_block() -> str:
    """The entries as a "- fact" block for LocalLLM.set_memory."""
    return "\n".join(f"- {entry}" for entry in read_local_entries())


def add_local_entry(fact: str) -> bool:
    """Appends a new fact (not a repeat), dropping the oldest past MAX_MEMORY_CHARS.
    Returns whether it was added."""
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


# -- Unified entry points (reply.py and learning.py call these, provider-agnostic) ----------


async def retain_exchange(user_text: str, reply_text: str, asked: str = "") -> None:
    """Hands one exchange to the memory server to keep what's worth keeping. An empty
    reply_text keeps only his side (a search or picture turn). `asked` is her question he
    was answering -- the answer needs it."""
    if read_provider() == HINDSIGHT_PROVIDER:
        await _hindsight_retain_exchange(user_text, reply_text, asked)


async def retain_fact(text: str, importance: str) -> None:
    """One fact he approved (brain/training.py), word for word, tagged with its importance.
    Raises if it couldn't be saved, so the proposal stays queued."""
    if read_provider() == HINDSIGHT_PROVIDER:
        await _hindsight_retain_fact(text, importance)
    else:
        raise RuntimeError("no memory server connected")


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


# -- Shared (all providers) ---------------------------------------------------


set_memory_active = MEMORY_ACTIVE.write


def read_memory_active() -> bool:
    """On by default. A memory server also has to be connected."""
    if server_backed() and not server_configured():
        return False
    return MEMORY_ACTIVE.read()
