#web_search.py
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core import outcome
from core.search_validator import (
    format_search_evidence,
    parse_and_validate_publication_date,
    validate_source_url,
)

logger = logging.getLogger("optimus.actions.web_search")


class WebSearchError(RuntimeError):
    """Typed error raised when a search provider fails with a classified cause."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


def classify_search_error(exc: Exception) -> tuple[str, str]:
    """Map an exception into a concise user-safe (category, reason) pair."""
    if isinstance(exc, WebSearchError):
        return exc.category, exc.message

    msg = str(exc or "").strip() or type(exc).__name__
    low = msg.lower()

    if isinstance(exc, TimeoutError) or any(tok in low for tok in ("timeout", "timed out", "deadline_exceeded")):
        return "timeout", f"Search request timed out: {msg[:160]}"
    if isinstance(exc, json.JSONDecodeError) or any(tok in low for tok in ("json", "parse", "decode")):
        return "parsing_failure", f"Search response parsing failed: {msg[:160]}"
    if any(tok in low for tok in ("401", "403", "api key", "api_key_invalid", "unauthorized", "forbidden", "authentication")):
        return "auth_failure", f"Search provider authentication failed: {msg[:160]}"
    if isinstance(exc, (AttributeError, IndexError, KeyError, TypeError, ValueError)):
        return "invalid_response", f"Search provider returned a malformed response: {msg[:160]}"
    if isinstance(exc, (_QuotaCooldown, ConnectionError, OSError)) or any(
        tok in low for tok in ("429", "503", "quota", "unavailable", "connection", "unreachable", "dns", "network", "ladder failed")
    ):
        return "provider_unavailable", f"Search provider unavailable: {msg[:160]}"
    return "unexpected_error", f"Unexpected search error ({type(exc).__name__}): {msg[:160]}"

# ── Gemini grounding quota circuit breaker ────────────────────────────────────
# The google_search grounding tool has its own small quota, separate from plain
# generation.  Once it is spent every call returns 429 — so retrying it at the
# top of every search only adds a dead round-trip before the DDG fallback runs.
# After a quota error, skip Gemini entirely for a cooldown period.
_QUOTA_COOLDOWN_SEC  = 900          # 15 minutes
_quota_blocked_until = 0.0
_quota_lock          = threading.Lock()


def _gemini_available() -> bool:
    with _quota_lock:
        return time.monotonic() >= _quota_blocked_until


def _note_gemini_error(exc: Exception) -> None:
    """Trip the breaker when the error is a quota / rate-limit rejection."""
    global _quota_blocked_until
    msg = str(exc)
    if any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "every Gemini model on the ladder failed")) or "quota" in msg.lower():
        with _quota_lock:
            already = time.monotonic() < _quota_blocked_until
            _quota_blocked_until = time.monotonic() + _QUOTA_COOLDOWN_SEC
        if not already:
            print(
                "[WebSearch] Gemini grounding quota exhausted — skipping it for "
                f"{_QUOTA_COOLDOWN_SEC // 60} min and serving results from DDG."
            )


class _QuotaCooldown(RuntimeError):
    """Raised instead of calling Gemini while the quota breaker is open."""


def _log_gemini_failure(context: str, exc: Exception) -> None:
    """Log a Gemini failure — silently when it is just the expected cooldown."""
    if isinstance(exc, _QuotaCooldown):
        return          # announced once when the breaker tripped; not a warning
    print(f"[WebSearch] \u26a0\ufe0f {context} failed ({exc}) — using DDG instead")


def _run_bounded(fn, timeout: float, label: str = "task"):
    """Run fn() in a daemon thread; return its result, or None if it overruns."""
    box = [None]

    def _run():
        try:
            box[0] = fn()
        except Exception as e:
            _log_gemini_failure(label, e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        print(f"[WebSearch] {label} exceeded {timeout:.0f}s — moving on")
    return box[0]

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _gemini_search(query: str) -> str:
    if not _gemini_available():
        raise _QuotaCooldown("Gemini grounding is in quota cooldown")

    from core import gemini

    # Grounded search reads a live page, so it gets a longer deadline than the
    # default — but it still HAS one, and it still walks the fallback ladder.
    try:
        response = gemini.call(query, tier=gemini.SEARCH,
                               config={"tools": [{"google_search": {}}]},
                               timeout_ms=30_000)
        if response is None:
            raise WebSearchError("provider_unavailable", "Every Gemini search model failed or is unavailable")
    except Exception as e:
        _note_gemini_error(e)
        raise

    try:
        candidates = getattr(response, "candidates", None)
        if not candidates:
            raise WebSearchError("invalid_response", "Gemini response contained no candidates")
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None)
        if not isinstance(parts, (list, tuple)):
            raise WebSearchError("invalid_response", "Gemini response contained invalid content parts")
        text = ""
        for part in parts:
            part_txt = getattr(part, "text", None)
            if part_txt:
                text += str(part_txt)
    except WebSearchError:
        raise
    except Exception as e:
        raise WebSearchError("parsing_failure", f"Failed parsing Gemini search response: {e}") from e

    text = text.strip()
    if not text:
        raise WebSearchError("invalid_response", "Gemini returned an empty response")
    return text


def _get_ddgs():
    """
    Returns the DDGS class.  The package was renamed duckduckgo-search -> ddgs;
    the legacy package's endpoints are now rejected by DuckDuckGo (news() gets a
    403 Ratelimit, text() silently returns zero results), so warn loudly if we
    end up on it instead of failing in silence.
    """
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        from duckduckgo_search import DDGS
        print(
            "[WebSearch] ⚠️ Using the deprecated 'duckduckgo-search' package — "
            "DuckDuckGo blocks its endpoints, so every search will come back "
            "empty.  Fix with:  pip install -U ddgs"
        )
        return DDGS


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    DDGS = _get_ddgs()
    results = []
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            with DDGS() as ddgs:
                raw_iter = ddgs.text(query, max_results=max_results)
                if raw_iter is None:
                    raise WebSearchError("invalid_response", "DDG returned null result stream")
                for r in raw_iter:
                    if not isinstance(r, dict):
                        raise WebSearchError("invalid_response", f"DDG returned non-dict item: {type(r).__name__}")
                    results.append({
                        "title":   r.get("title",  ""),
                        "snippet": r.get("body",   ""),
                        "url":     r.get("href",   ""),
                        "date":    r.get("date"),
                    })
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            logger.warning("DDG text() attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(0.5)
                continue
    if last_exc is not None and not results:
        cat, msg = classify_search_error(last_exc)
        raise WebSearchError(cat, f"Retry exhausted ({msg})") from last_exc
    try:
        return format_search_evidence(results)
    except Exception as e:
        raise WebSearchError("parsing_failure", f"Failed formatting search evidence: {e}") from e


def _ddg_news(query: str, max_results: int = 8) -> list[dict]:
    """DDG news search — returns actual articles, not website homepages."""
    DDGS = _get_ddgs()
    results = []
    news_exc: Exception | None = None
    try:
        with DDGS() as ddgs:
            raw_iter = ddgs.news(query, max_results=max_results)
            if raw_iter is None:
                raise WebSearchError("invalid_response", "DDG news returned null stream")
            for r in raw_iter:
                if not isinstance(r, dict):
                    raise WebSearchError("invalid_response", f"DDG news returned non-dict item: {type(r).__name__}")
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("url",    ""),
                    "source":  r.get("source", ""),
                    "date":    r.get("date"),
                })
    except Exception as e:
        news_exc = e
        logger.warning("DDG news() failed (%s) — falling back to text search", e)
    # Also covers the legacy-package case, where news() returns an empty list
    # instead of raising.
    if not results:
        try:
            return _ddg_search(query, max_results=max_results)
        except Exception as fallback_exc:
            raise fallback_exc from news_exc
    try:
        return format_search_evidence(results)
    except Exception as e:
        raise WebSearchError("parsing_failure", f"Failed formatting news evidence: {e}") from e


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):
            date_prefix = f"[{r['publication_date'][:10]}] " if r.get("publication_date") else ""
            lines.append(f"{i}. {date_prefix}{r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url") and r.get("verified_url", True):
            lines.append(f"   Source: {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_news(query: str, results: list[dict]) -> str:
    if not results:
        return f"No news found for: {query}"

    lines = [f"Latest news: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        if not title:
            continue
        date_str = f"[{r['publication_date'][:10]}] " if r.get("publication_date") else ("[Undated] " if r.get("undated") else "")
        src = f"[{r['source']}] " if r.get("source") else ""
        lines.append(f"{i}. {date_str}{src}{title}".strip())
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:140]}")
        if r.get("url") and r.get("verified_url", True):
            lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


# ── Briefing helper ────────────────────────────────────────────────────────────

def _gemini_headlines(n: int = 5) -> tuple[list[str], str]:
    """
    Fetches current headlines via Gemini grounded search.
    Optimised for speed: minimal prompt + strict token cap.
    Returns (headline_list, raw_text_for_display).
    """
    import re

    from core import gemini

    response = gemini.call(
        f"Current world news: {n} headlines. Numbered list, titles only.",
        tier=gemini.SEARCH,
        config={"tools": [{"google_search": {}}]},
        timeout_ms=30_000,
    )
    if response is None:
        return [], ""

    raw = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            raw += part.text

    headlines = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Only accept lines that begin with a number — skips preamble/closing sentences
        if not re.match(r'^[\d]+[.\)\-]', line):
            continue
        clean = re.sub(r'^[\d]+[.\)\-]\s*', '', line)
        clean = re.sub(r'^\*+\s*',          '', clean).strip()
        if clean and len(clean) > 10:
            headlines.append(clean)

    return headlines[:n], raw.strip()


# ── Modes ──────────────────────────────────────────────────────────────────────

def _search(query: str) -> str:
    """Default search — Gemini grounded, DDG fallback."""
    try:
        return _gemini_search(query)
    except Exception as e:
        _log_gemini_failure("Gemini search", e)
        results = _ddg_search(query)
        return _format_ddg(query, results)


def _news(query: str) -> str:
    """
    DDG first, Gemini as backup.

    The old version raced both backends in parallel and kept the first answer.
    That burned one google_search grounding call on *every* news request —
    including the startup briefing — even when DDG had already won the race.
    Grounding has a small quota, so it ran dry after a handful of launches and
    then 429'd for everything else (research/compare), which are the modes that
    actually need a synthesised answer.

    DDG news returns in well under a second and gives raw headlines, which is
    exactly what the briefing wants, so it goes first and Gemini is only touched
    when DDG comes back empty.
    """
    gemini_query = f"latest news today: {query}" if query else "top world news today"
    ddg_query    = query if query else "world news today"

    def _ddg_attempt() -> str:
        return _format_news(ddg_query, _ddg_news(ddg_query, max_results=8))

    ddg_err: Exception | None = None
    try:
        text = _ddg_attempt()
    except Exception as exc:
        ddg_err = exc
        text = None
    if text and len(text) > 60 and not text.startswith("No news found"):
        return text

    gem_err: Exception | None = None
    try:
        text = _gemini_search(gemini_query)
    except Exception as exc:
        gem_err = exc
        _log_gemini_failure("Gemini news", exc)
        text = None
    if text and len(text) > 60:
        return text

    if ddg_err is not None and gem_err is not None:
        raise gem_err

    return f"No news found for: {query}"


def _research(query: str) -> str:
    """
    Deep dive — asks Gemini for a comprehensive answer with context.
    Falls back to a wider DDG fetch.
    """
    research_query = (
        f"Comprehensive, detailed explanation of: {query}. "
        "Include background context, key facts, current state, and important nuances."
    )
    try:
        return _gemini_search(research_query)
    except Exception as e:
        _log_gemini_failure("Gemini research", e)
        results = _ddg_search(query, max_results=10)
        return _format_ddg(query, results)


def _price(query: str) -> str:
    """Product price lookup — searches for current market prices."""
    price_query = f"current price of {query} — how much does it cost today"
    try:
        return _gemini_search(price_query)
    except Exception as e:
        _log_gemini_failure("Gemini price", e)
        results = _ddg_search(f"{query} price buy", max_results=6)
        return _format_ddg(query, results)


def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data."
    )
    try:
        return _gemini_search(query)
    except Exception as e:
        _log_gemini_failure("Gemini compare", e)

    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
            if r.get("url"):
                lines.append(f"    {r['url']}")
    return "\n".join(lines)


# ── Public entry point ─────────────────────────────────────────────────────────


def web_search_structured(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> dict[str, Any]:
    """Execute web search and return a structured result covering every outcome path."""
    params = parameters or {}
    query = str(params.get("query") or "").strip()
    mode = str(params.get("mode") or "search").lower().strip()
    items = params.get("items") or []
    aspect = str(params.get("aspect") or "general").strip() or "general"

    if not query and not items:
        reason = "Search query or items required"
        return {
            "status": "failed",
            "outcome_status": "invalid_request",
            "reason": reason,
            "output": outcome.failed("web_search", reason),
        }

    if items and mode not in ("compare",):
        mode = "compare"

    query_label = query or ", ".join(str(x) for x in items)
    if player:
        print(f"[WebSearch] {mode}: {query_label}")

    print(f"[WebSearch] 🔍 mode={mode!r}  query={query!r}")

    try:
        if mode == "compare" and items:
            found = _compare(items, aspect)
        elif mode == "news":
            found = _news(query)
        elif mode == "research":
            found = _research(query)
        elif mode == "price":
            found = _price(query)
        else:
            found = _search(query)
    except Exception as e:
        category, reason = classify_search_error(e)
        logger.exception("WebSearch backend failure [%s] for query=%r", category, query_label)
        print(f"[WebSearch] ❌ Backend failed [{category}]: {reason}")
        return {
            "status": "failed",
            "outcome_status": category,
            "reason": reason,
            "output": outcome.failed("web_search", reason, error_category=category, query=query_label),
        }

    if not isinstance(found, str):
        reason = f"Search returned unexpected payload type ({type(found).__name__})"
        return {
            "status": "failed",
            "outcome_status": "invalid_response",
            "reason": reason,
            "output": outcome.failed("web_search", reason, error_category="invalid_response", query=query_label),
        }

    stripped = found.strip()
    if not stripped or stripped.startswith(("No results found for:", "No news found for:")):
        empty_msg = stripped or f"No results found for: {query_label}"
        return {
            "status": "done",
            "outcome_status": "empty_results",
            "reason": empty_msg,
            "output": outcome.untrusted("web", empty_msg),
        }

    # Wrap external web results as untrusted data (anti prompt-injection boundary).
    return {
        "status": "done",
        "outcome_status": "success_with_results",
        "reason": None,
        "output": outcome.untrusted("web", found),
    }


def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    res = web_search_structured(
        parameters,
        response=response,
        player=player,
        session_memory=session_memory,
    )
    return str(res["output"])


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "web_search",
    "description": "Searches the web. Use for ANY question about current facts, events, prices, or topics — always prefer this over guessing. Modes: 'search' (default), 'news' (latest headlines on a topic), 'research' (deep comprehensive answer), 'price' (product cost lookup), 'compare' (side-by-side comparison of items).",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "Search query or topic"
            },
            "mode": {
                "type": "STRING",
                "description": "search | news | research | price | compare"
            },
            "items": {
                "type": "ARRAY",
                "items": {
                    "type": "STRING"
                },
                "description": "Items to compare (compare mode)"
            },
            "aspect": {
                "type": "STRING",
                "description": "Comparison aspect: price | specs | reviews | features"
            }
        },
        "required": [
            "query"
        ]
    },
    "handler": web_search,
}
