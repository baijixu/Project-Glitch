"""Web search for Glitch's own LLM path (LocalLLM/OllamaLLM) -- lets her
search the web via tool-calling when she needs current information,
independent of the Hermes harness (which already has its own web/session
search built in when active, see harness.py's own docstring on how the
harness bypasses everything in this module).

Talks to a self-hosted SearXNG instance (https://docs.searxng.org/) --
reuses whichever one is configured (config.yaml's brain.web_search block,
see main.py), even one also used for something else: unlike memory.py's
Hindsight bank, there's nothing personal/stateful in a search result to
worry about blending with anything else that happens to share the same
SearXNG instance.

configure() must be called once at startup. Left uncalled, search()
always returns [] rather than crashing -- same graceful-degradation
pattern as memory.py's hindsight functions when configure() was never
called there either.
"""

from pathlib import Path

import httpx

ACTIVE_PATH = Path(__file__).parent / "web_search_active.txt"

# Plenty for a model to actually read through without bloating the
# follow-up completion call -- SearXNG itself returns far more by default.
MAX_RESULTS = 5
REQUEST_TIMEOUT_SEC = 15

_base_url: str | None = None


def configure(base_url: str) -> None:
    global _base_url
    _base_url = base_url.rstrip("/")


def set_active(active: bool) -> None:
    ACTIVE_PATH.write_text("1" if active else "0", encoding="utf-8")


def read_active() -> bool:
    """Defaults to False (off) -- unlike memory, this changes what she can
    find out and say, not just what she remembers, so it opts in rather
    than being on by default. Always False when never configured
    (configure() uncalled), regardless of this file, since there's no
    backend for the toggle to actually turn on.
    """
    if _base_url is None:
        return False
    if ACTIVE_PATH.exists():
        return ACTIVE_PATH.read_text(encoding="utf-8").strip() == "1"
    return False


def search(query: str) -> list[dict]:
    """Up to MAX_RESULTS {title, url, snippet} dicts, or [] if never
    configured or the request failed for any reason -- llm/client.py's
    tool-calling loop turns an empty list into a plain "no results"
    message for the model rather than raising, so a flaky search backend
    degrades the answer, not the whole reply.
    """
    if _base_url is None:
        return []
    try:
        response = httpx.get(f"{_base_url}/search", params={"q": query, "format": "json"}, timeout=REQUEST_TIMEOUT_SEC)
        response.raise_for_status()
        results = response.json().get("results") or []
    except Exception as exc:
        print(f"[web_search] search failed for {query!r}: {exc!r}")
        return []
    print(f"[web_search] {query!r} -> {len(results)} result(s)")
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in results[:MAX_RESULTS]
    ]
