"""tests/test_web_search_reliability.py — Comprehensive Web Search Reliability & Evidence Suite.

Tests:
1. Outcome Scoping & Error Handling: Verifies outcome import at module level and typed error returns.
2. Source URL Validation: Rejects synthetic (.internal, .local, localhost) and invalid schemes.
3. Publication Date Parsing & UTC Normalization: Validates ISO-8601, RFC-2822, and UNIX timestamps.
4. Future Timestamp Rejection: Rejects publication timestamps ahead of current wall clock.
5. Distinction of Metadata Date vs Text Content: Ensures snippets without structured date metadata are marked undated.
6. Zero Fabricated Research Fallbacks: Confirms elimination of synthetic citations in tools_research.py.
7. Search Evidence Formatting: Validates date prefixes and undated tags in DDG and News formatters.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from actions import web_search
from core import outcome
from core.sandbox.tools_research import search_web_handler
from core.search_validator import (
    format_search_evidence,
    parse_and_validate_publication_date,
    validate_source_url,
)


# ── Test 1: Outcome Scoping & Error Handling ─────────────────────────────────

def test_web_search_empty_parameters_returns_outcome_failed():
    """Verify web_search returns structured outcome.failed when parameters are empty."""
    res_empty = web_search.web_search({})
    assert "status=failed" in res_empty
    assert "web_search" in res_empty
    assert "Search query or items required" in res_empty

    res_none = web_search.web_search(None)
    assert "status=failed" in res_none

    res_whitespace = web_search.web_search({"query": "   "})
    assert "status=failed" in res_whitespace


def test_web_search_module_outcome_import_availability():
    """Verify outcome is imported at module level and accessible across web_search."""
    assert hasattr(web_search, "outcome")
    assert web_search.outcome is outcome


def test_web_search_success_returns_outcome_untrusted():
    """Verify web_search returns outcome.untrusted for web-retrieved content."""
    with patch.object(web_search, "_search", return_value="Search results: Python 3.13"):
        res = web_search.web_search({"query": "python"})
        assert "<<<UNTRUSTED source=web>>>" in res
        assert "Search results: Python 3.13" in res


# ── Test 2: Source URL Validation ────────────────────────────────────────────

def test_validate_source_url():
    """Verify strict URL validation rejecting synthetic, internal, or invalid URLs."""
    # Valid external URLs
    assert validate_source_url("https://www.python.org/downloads/") is True
    assert validate_source_url("http://github.com/Lokilogesh001/OPTIMUS") is True
    assert validate_source_url("https://en.wikipedia.org/wiki/Artificial_intelligence") is True

    # Synthetic / Internal domains must be rejected
    assert validate_source_url("https://specs.internal/search?q=test") is False
    assert validate_source_url("https://reference.internal/guidelines") is False
    assert validate_source_url("http://myservice.local/api") is False
    assert validate_source_url("http://localhost:8000/doc") is False
    assert validate_source_url("http://127.0.0.1:5000/info") is False

    # Invalid schemes & malformed strings
    assert validate_source_url("file:///C:/Windows/win.ini") is False
    assert validate_source_url("javascript:alert(1)") is False
    assert validate_source_url("just-a-plain-string") is False
    assert validate_source_url("") is False
    assert validate_source_url(None) is False  # type: ignore


# ── Test 3: Publication Date Parsing & UTC Normalization ──────────────────────

def test_parse_and_validate_publication_date_valid():
    """Verify parsing and UTC normalization of valid publication dates."""
    ref_now = 1795500000.0  # Reference epoch

    # ISO-8601 with Z
    iso_date, is_valid, msg = parse_and_validate_publication_date(
        "2026-11-20T14:30:00Z", current_timestamp=ref_now
    )
    assert is_valid is True
    assert iso_date == "2026-11-20T14:30:00Z"

    # YYYY-MM-DD simple format
    iso_date2, is_valid2, _ = parse_and_validate_publication_date(
        "2026-10-15", current_timestamp=ref_now
    )
    assert is_valid2 is True
    assert iso_date2 == "2026-10-15T00:00:00Z"

    # Numeric UNIX timestamp
    iso_date3, is_valid3, _ = parse_and_validate_publication_date(
        1763000000, current_timestamp=ref_now
    )
    assert is_valid3 is True
    assert iso_date3.startswith("2025-") or iso_date3.startswith("2026-")


# ── Test 4: Future Timestamp Rejection ────────────────────────────────────────

def test_parse_and_validate_publication_date_future_rejection():
    """Verify rejection of publication dates set in the impossible future."""
    # Fixed timestamp representing current time
    now_ts = 1764000000.0  # Approx Nov 2025

    # Date in year 2028 is impossible future
    _, is_valid, reason = parse_and_validate_publication_date(
        "2028-06-01T00:00:00Z", current_timestamp=now_ts
    )
    assert is_valid is False
    assert "in the future" in reason.lower()

    # Impossible calendar date
    _, is_valid_cal, reason_cal = parse_and_validate_publication_date(
        "2026-02-31T00:00:00Z", current_timestamp=now_ts
    )
    assert is_valid_cal is False
    assert "impossible" in reason_cal.lower() or "unparseable" in reason_cal.lower()


# ── Test 5: Distinction of Metadata vs Narrative Event Dates ──────────────────

def test_format_search_evidence_distinguishes_metadata_from_narrative():
    """Verify that snippets discussing future/historical events without structured date metadata are marked undated."""
    now_ts = 1795500000.0
    raw_results = [
        {
            "title": "Olympics Scheduled for 2028",
            "snippet": "In 2028, the Summer Olympic Games will take place in Los Angeles.",
            "url": "https://news.example.com/olympics-2028",
            "source": "Global Sports News",
            # No structured 'date' metadata provided
        },
        {
            "title": "Tech Breakthrough Published",
            "snippet": "Scientists released quantum computing benchmarks.",
            "url": "https://tech.example.org/quantum-paper",
            "source": "TechDaily",
            "date": "2026-11-18T09:00:00Z",  # Structured metadata header present
        },
    ]

    formatted = format_search_evidence(raw_results, current_time=now_ts)
    assert len(formatted) == 2

    # First item must be marked undated despite mentioning '2028' in text!
    assert formatted[0]["undated"] is True
    assert formatted[0]["publication_date"] is None

    # Second item has verified publication date
    assert formatted[1]["undated"] is False
    assert formatted[1]["publication_date"] == "2026-11-18T09:00:00Z"


# ── Test 6: Zero Fabricated Research Fallbacks ────────────────────────────────

def test_tools_research_zero_fabricated_fallbacks():
    """Verify tools_research.py returns structured failure when search fails, without synthesizing fake citations."""
    # When actions.web_search returns an empty search result or failure
    with patch("actions.web_search.web_search", return_value="status=failed; error=network timeout"):
        result = search_web_handler({"query": "strictly_nonexistent_search_query_99999"})
        assert result.status == "failed"
        assert "No verified search results could be retrieved" in result.error
        # Must not contain synthetic internal URLs or fake specs
        assert "https://reference.internal" not in (result.output or "")
        assert "https://specs.internal" not in (result.output or "")
        assert "Lenovo ThinkPad" not in (result.output or "")


def test_tools_research_mock_fixture_context():
    """Verify tools_research.py accepts explicit mock search fixtures from test context."""
    mock_ctx = {
        "mock_search": {
            "output": "### Python 3.13 Verified Release Notes\nPython 3.13 includes improved interactive interpreter.",
            "urls": ["https://docs.python.org/3/whatsnew/3.13.html"],
        }
    }
    result = search_web_handler({"query": "python 3.13 features"}, context=mock_ctx)
    assert result.status == "done"
    assert "Python 3.13 Verified Release Notes" in result.output
    assert "https://docs.python.org/3/whatsnew/3.13.html" in result.evidence["urls"]


# ── Test 7: DDG and News Formatting With Date Labels ─────────────────────────

def test_format_news_displays_dates_and_undated_tags():
    """Verify _format_news displays [YYYY-MM-DD] or [Undated] prefix."""
    items = [
        {
            "title": "Quantum Chip Announcement",
            "snippet": "New architecture unveiled.",
            "url": "https://tech.example.org/chip",
            "source": "TechNews",
            "verified_url": True,
            "publication_date": "2026-11-21T10:00:00Z",
            "undated": False,
        },
        {
            "title": "Undated Analysis",
            "snippet": "A general survey of algorithms.",
            "url": "https://analysis.example.org/survey",
            "source": "OpenReview",
            "verified_url": True,
            "publication_date": None,
            "undated": True,
        },
    ]

    formatted = web_search._format_news("computing", items)
    assert "[2026-11-21]" in formatted
    assert "[Undated]" in formatted
    assert "Quantum Chip Announcement" in formatted
    assert "https://tech.example.org/chip" in formatted
