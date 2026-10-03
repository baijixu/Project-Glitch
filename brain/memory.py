"""Glitch's own native memory of who the user is -- entirely separate
from, and never touched by, an agent harness (see harness.py's own
docstring on how the harness bypasses this module completely).

Which backend is in use is the active memory profile (memory_profiles.py,
Settings -> Memory); activate() connects to it. Kinds of backend:

- "local" (the built-in "Local file" -- nothing to set up): one fact per
  line in memory.md, extracted by a lightweight extra LLM call after every
  reply (see learning.maybe_retain_memory), and re-injected as one static
  block every turn regardless of what's being asked.
- a memory server someone runs themselves -- "hindsight"
  (https://pypi.org/project/hindsight-client/) or "mem0"
  (https://docs.mem0.ai/open-source/features/rest-api), each in its own
  bank / user id, kept separate from whatever memory a harness might use.
  Extraction and retrieval both happen server-side: retain_exchange() just
  hands it the raw exchange and lets it decide what's worth keeping, and
  recall_for_prompt() asks it for whatever's relevant to the CURRENT
  message instead of one static block every turn.

Adding another server means: a type in memory_profiles.TYPES, a small client
here (like Mem0Client), and a branch in each entry point under "Unified
entry points". Every server function below is a no-op/returns ""/[] if no
client is connected -- same graceful-degradation pattern as a missing
brain.llm block, not a crash.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx

import memory_profiles
import training
from hindsight_client import Hindsight

MEMORY_PATH = Path(__file__).parent / "memory.md"
MEMORY_ACTIVE_PATH = Path(__file__).parent / "memory_active.txt"
# Before memory profiles: which backend was picked, and the one Hindsight
# connection. Only read now, to turn them into a profile once (see migrate()).
PROVIDER_PATH = Path(__file__).parent / "memory_provider.txt"
HINDSIGHT_CONFIG_PATH = Path(__file__).parent / "hindsight_config.json"

LOCAL_PROVIDER = "local"
HINDSIGHT_PROVIDER = memory_profiles.HINDSIGHT
MEM0_PROVIDER = memory_profiles.MEM0
# The name migrate() gives the Hindsight connection saved before profiles.
MIGRATED_HINDSIGHT_NAME = "Hindsight"

# Over this many chars, the local provider's oldest entries are just
# dropped (FIFO) rather than intelligently merged -- a deliberate
# simplification, worth revisiting only if it actually becomes a problem.
MAX_MEMORY_CHARS = 2000

# Keeps a hindsight recall roughly in the same ballpark as the local
# provider's own MAX_MEMORY_CHARS cap -- plenty for what's actually
# relevant to one message, not a dump of the whole bank.
RECALL_MAX_TOKENS = 800
# The token budget alone didn't make recall selective: her short approved facts
# fit 34 of 40 into it, so every message got nearly her whole memory (and the
# user's surgery and pups came up whatever he asked). Results come ranked, so
# only the top few are kept.
RECALL_LIMIT = 8
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

_MISSION_V2_ROLES = (
    _MISSION_V2
    + " In what you are given, 'User' is the human and 'Glitch' is the AI assistant, two different "
    "beings. Attribute every statement to whoever actually said it: never record something the "
    "assistant said, did, wore or pretended as a fact about the user, and never record the user's "
    "name, life or traits as the assistant's."
)

# Her memories are hers, written in her own voice: "I" is Glitch, the human is named. That
# also lets durable things about herself (opinions, promises) be kept, which the earlier
# missions excluded outright -- scene/role-play actions and clothing still never are.
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

# Hindsight also writes "observations": its own summaries of the stored facts,
# made by its model (a 4B one on this setup) and recalled alongside them. With
# no instruction for that step it read her "I"/"my" as the human's -- a word-
# for-word "green feels like home because of my hair" came back as "the user
# associates green with home due to their hair", and "I helped Sam" as "Sam
# helped". With this set, owners came out right in 5 of 5 runs on throwaway
# banks. Set by ensure_bank() the same way as RETAIN_MISSION.
OBSERVATIONS_MISSION = (
    "Every fact in this bank is written by Glitch, an AI companion, in her own voice: 'I', 'me' and 'my' "
    "always mean Glitch, never the human. The human is the person she talks to, called by their name. "
    "When you summarize, keep Glitch's voice ('I' = Glitch) and keep who owns what exactly as the facts "
    "say: Glitch's looks, clothes, pictures and feelings stay Glitch's; the human's stay the human's."
)

# Facts approved in memory training (retain_fact) are stored word for word.
# Normally Hindsight rewrites whatever it's given with its own model, and for
# these short first-person facts that rewrite swapped owners: approved "Sam
# likes the jacket in my winter picture" was stored as "Sam prefers that
# jacket", "green feels like home because of my hair" as the user's hair, and
# names got tagged "(user)" or "user's friend". Rewording the context didn't
# fix it (tried on throwaway banks; it also made the rewrite fail outright
# about a third of the time). The user already approved the exact wording, so
# there's nothing left to extract: this named strategy uses Hindsight's
# "chunks" mode, which stores the text as-is and never calls a model -- tags,
# recall and the core-only recall all work the same (checked live).
APPROVED_STRATEGY = "glitch-approved"
_APPROVED_STRATEGY_CONFIG = {"retain_extraction_mode": "chunks"}
# Only used if the server can't take the strategy (an older Hindsight), when the
# fact goes through the normal rewrite -- then at least say whose voice it is.
APPROVED_FACT_CONTEXT = "Glitch's own memory, in her voice: 'I' and 'my' mean Glitch (the AI), not the human"

# Earlier versions of the default above. A bank still carrying one of these
# was never customized, so ensure_bank() upgrades it to the current default --
# anything else there was written by hand and is left alone.
_PREVIOUS_DEFAULT_MISSIONS = (_MISSION_V1, _MISSION_V2, _MISSION_V2_ROLES)

_client: Hindsight | None = None
_bank_id = ""
_mem0: "Mem0Client | None" = None
# The bank APPROVED_STRATEGY was last set up on (see _ensure_approved_strategy),
# so it's checked once per bank rather than before every approval.
_approved_strategy_bank: str | None = None


# -- Which backend (Settings -> Memory's profiles, memory_profiles.py) --------


def read_provider() -> str:
    """The active profile's kind: LOCAL_PROVIDER, HINDSIGHT_PROVIDER or MEM0_PROVIDER."""
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
    provider = read_provider()
    if provider == HINDSIGHT_PROVIDER:
        return hindsight_configured()
    return provider == MEM0_PROVIDER and _mem0 is not None


def migrate(seed: dict | None = None) -> None:
    """Once, on the first start with memory profiles: the Hindsight connection
    saved before them (hindsight_config.json, or config.yaml's brain.hindsight
    `seed` on a machine that never saved one) becomes the "Hindsight" profile,
    active if Hindsight was the backend -- or if no backend was ever picked,
    so a connection someone set up isn't left unused.
    """
    if memory_profiles.has_active() or memory_profiles.list_profiles():
        return
    try:
        old = json.loads(HINDSIGHT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    old = old if isinstance(old, dict) and old.get("api_url") else (seed or {})
    if not old.get("api_url"):
        return
    name = memory_profiles.save_profile(
        MIGRATED_HINDSIGHT_NAME, HINDSIGHT_PROVIDER, old["api_url"], old.get("api_key") or "", old.get("bank_id") or ""
    )
    try:
        picked = PROVIDER_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        picked = ""
    memory_profiles.set_active(name if picked in ("", HINDSIGHT_PROVIDER) else memory_profiles.LOCAL_NAME)
    print(f"[memory] saved the Hindsight connection as the memory profile {name!r}")


async def activate() -> None:
    """Connects to the active profile's server (or disconnects, for the local
    file). A server that's unreachable still counts as connected -- calls then
    fail softly per turn -- but raises here so the caller can say so.
    """
    global _client, _bank_id, _mem0, _approved_strategy_bank
    if _client is not None:
        try:
            await _client.aclose()  # the old connection, before switching
        except Exception:
            pass
    _client, _mem0, _bank_id, _approved_strategy_bank = None, None, "", None
    name = memory_profiles.read_active()
    if name == memory_profiles.LOCAL_NAME:
        return
    profile = memory_profiles.read_profile(name)
    if profile["type"] == HINDSIGHT_PROVIDER:
        configure(profile["url"], profile["space"], profile["api_key"])
        await ensure_bank()
    elif profile["type"] == MEM0_PROVIDER:
        _mem0 = Mem0Client(profile["url"], profile["space"], profile["api_key"])
        await _mem0.check()


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
    """The configured Hindsight client, or None if configure() was never
    called -- for lessons.py, which talks to the same server (though in its
    own bank, see lessons._bank) rather than opening a second connection.
    """
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


# -- Mem0 connection ----------------------------------------------------------


# Mem0's own server (mem0/server in https://github.com/mem0ai/mem0): memories
# live under a user id (the profile's `space`), and it extracts facts from what
# it's given with its own model, like Hindsight. Approved facts go in with
# infer=False, which stores them as written.
MEM0_TIMEOUT_SEC = 30.0
MEM0_RECALL_LIMIT = 10
MEM0_LIST_LIMIT = 500


class Mem0Client:
    def __init__(self, url: str, user_id: str, api_key: str = "") -> None:
        self.url = url.rstrip("/")
        self.user_id = user_id
        self._headers = {"X-API-Key": api_key} if api_key else {}

    async def _request(self, method: str, path: str, **kwargs):
        async with httpx.AsyncClient(timeout=MEM0_TIMEOUT_SEC, headers=self._headers) as client:
            response = await client.request(method, self.url + path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    async def check(self) -> None:
        await self.list(limit=1)

    async def add(self, messages: list[dict], *, infer: bool = True, metadata: dict | None = None) -> None:
        body = {"messages": messages, "user_id": self.user_id, "infer": infer}
        if metadata:
            body["metadata"] = metadata
        await self._request("POST", "/memories", json=body)

    async def search(self, query: str, limit: int = MEM0_RECALL_LIMIT) -> list[dict]:
        body = {"query": query, "filters": {"user_id": self.user_id}, "top_k": limit}
        return _mem0_results(await self._request("POST", "/search", json=body))

    async def list(self, limit: int = MEM0_LIST_LIMIT) -> list[dict]:
        return _mem0_results(await self._request("GET", "/memories", params={"user_id": self.user_id, "top_k": limit}))

    async def delete_all(self) -> None:
        await self._request("DELETE", "/memories", params={"user_id": self.user_id})


def _mem0_results(body) -> list[dict]:
    # {"results": [...]} from current servers, a bare list from older ones.
    items = body.get("results", []) if isinstance(body, dict) else body
    return [item for item in items or [] if isinstance(item, dict) and item.get("memory")]


def _mem0_is_core(item: dict) -> bool:
    return (item.get("metadata") or {}).get("importance") == "core"


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
    nor an earlier default of ours (those get upgraded). Same for
    OBSERVATIONS_MISSION (set only when the bank has none). Best-effort: a
    server that has bank-config writes disabled
    (HINDSIGHT_API_ENABLE_BANK_CONFIG_API=false) just keeps its own extraction
    behavior -- that must never stop the bank from being usable.
    """
    try:
        config = await _client.aget_bank_config(_bank_id)
        overrides = config.get("overrides") or {}
        current = overrides.get("retain_mission")
        if not (current and current != RETAIN_MISSION and current not in _PREVIOUS_DEFAULT_MISSIONS):
            if current != RETAIN_MISSION:  # (otherwise written by hand -- not ours to change)
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
# A recalled memory gets "(learned 3 days ago)" after it, from when the server
# says it was saved (Hindsight's mentioned_at, Mem0's updated_at/created_at), so
# she can tell that "moving next month" was said a month ago. By calendar day in
# this machine's timezone. No date known (the local file, an older server): the
# memory goes in as it is.


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
    """Whatever's actually relevant to `query` right now, joined into the
    same "- fact" block shape LocalLLM.set_memory already expects -- the
    user's "core" facts first (see brain/training.py), then the rest, each with
    how long ago it was learned.
    """
    if _client is None:
        return ""
    response, core = await asyncio.gather(
        _client.arecall(_bank_id, query=query, max_tokens=RECALL_MAX_TOKENS), _recall_core_items()
    )
    core_texts = {text for text, _ in core}
    lines = [_dated(text, when) for text, when in core]
    lines += [
        _dated(result.text, getattr(result, "mentioned_at", None))
        for result in response.results[:RECALL_LIMIT]
        if result.text not in core_texts
    ]
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
    global _approved_strategy_bank
    if _client is None:
        return
    await _client.adelete_bank(_bank_id)
    _approved_strategy_bank = None  # the recreated bank has no strategies yet
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


# -- Mem0 provider's own storage ----------------------------------------------


async def _mem0_retain_exchange(user_text: str, reply_text: str, asked: str) -> None:
    messages = [{"role": "user", "content": user_text}]
    if reply_text:
        messages.append({"role": "assistant", "content": reply_text})
    if asked:
        messages.insert(0, {"role": "assistant", "content": asked})
    await _mem0.add(messages)


async def _mem0_retain_fact(text: str, importance: str) -> None:
    await _mem0.add([{"role": "user", "content": text}], infer=False, metadata={"importance": importance})


async def _mem0_recall(query: str) -> str:
    """Core facts first (every turn, whatever the topic), then what's relevant."""
    relevant, everything = await asyncio.gather(_mem0.search(query), _mem0.list())
    core = [item for item in everything if _mem0_is_core(item)]
    core_texts = {item["memory"] for item in core}
    chosen = core + [item for item in relevant if item["memory"] not in core_texts]
    return "\n".join(f"- {_dated(item['memory'], item.get('updated_at') or item.get('created_at'))}" for item in chosen)


# -- Unified entry points (reply.py and learning.py call these, provider-agnostic) ----------


async def retain_exchange(user_text: str, reply_text: str, asked: str = "") -> None:
    """Fire-and-forget: hands one exchange to the memory server to decide what,
    if anything, is worth remembering long-term. An empty reply_text retains
    only what the user said -- main.py passes that for a turn where she
    searched the web, so the search results in her reply never get saved as
    if they were something about the user. `asked` is her question he was
    answering (curiosity.answered_question) -- the answer needs it.
    """
    provider = read_provider()
    if provider == HINDSIGHT_PROVIDER:
        await _hindsight_retain_exchange(user_text, reply_text, asked)
    elif provider == MEM0_PROVIDER and _mem0 is not None:
        await _mem0_retain_exchange(user_text, reply_text, asked)


async def retain_fact(text: str, importance: str) -> None:
    """Retains one fact the user reviewed and approved (brain/training.py),
    word for word, marked with its importance so core facts can be recalled
    every turn. Raises if it couldn't be saved at all -- the caller keeps the
    proposal queued so nothing approved is lost.
    """
    provider = read_provider()
    if provider == HINDSIGHT_PROVIDER:
        await _hindsight_retain_fact(text, importance)
    elif provider == MEM0_PROVIDER and _mem0 is not None:
        await _mem0_retain_fact(text, importance)
    else:
        raise RuntimeError("no memory server connected")


async def recall_for_prompt(query: str) -> str:
    """Whatever should go into LocalLLM.set_memory() this turn."""
    provider = read_provider()
    if provider == HINDSIGHT_PROVIDER:
        return await recall_relevant(query)
    if provider == MEM0_PROVIDER:
        return await _mem0_recall(query) if _mem0 is not None else ""
    return read_local_block()


async def read_entries() -> list[str]:
    """For the Settings panel's Download Memory button."""
    provider = read_provider()
    if provider == HINDSIGHT_PROVIDER:
        return await _read_hindsight_entries()
    if provider == MEM0_PROVIDER:
        return [item["memory"] for item in await _mem0.list()] if _mem0 is not None else []
    return read_local_entries()


async def clear() -> None:
    """For the Settings panel's Clear Memory button."""
    provider = read_provider()
    if provider == HINDSIGHT_PROVIDER:
        await _clear_hindsight()
    elif provider == MEM0_PROVIDER:
        if _mem0 is not None:
            await _mem0.delete_all()
    else:
        _clear_local()


# -- Shared (both providers) --------------------------------------------------


def set_memory_active(active: bool) -> None:
    MEMORY_ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_memory_active() -> bool:
    """Defaults to True (on) -- unlike Mic Always-On, this feature's whole
    point is to be on so she actually gets to know the user, so it opts in
    by default rather than requiring the user to find and flip it. The
    local provider is always "available" (nothing to configure); a memory
    server additionally needs to be connected (activate()), same
    graceful-degradation reasoning as a missing brain.llm block.
    """
    if server_backed() and not server_configured():
        return False
    if MEMORY_ACTIVE_PATH.exists():
        return MEMORY_ACTIVE_PATH.read_text(encoding="utf-8").strip() != "0"
    return True
