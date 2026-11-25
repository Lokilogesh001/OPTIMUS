"""core/search_validator.py — Web Search Result, URL, and Date Validation Engine.

Provides:
- Strict RFC-compliant URL validation rejecting internal or synthetic schemes.
- Structured publication date extraction, UTC normalization, and wall-clock bounds verification.
- Algorithmic separation of article publication metadata from narrative event dates in text snippets.
- Explicit labeling of undated or unverified information.
"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any
from urllib.parse import urlparse

# Rejection pattern for synthetic, local, or private internal networks
_INVALID_HOST_PATTERN = re.compile(
    r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|.*\.local|.*\.internal|.*\.lan|.*\.test|.*\.example)$",
    re.IGNORECASE,
)

# Common ISO-8601 date patterns
_ISO_DATE_PATTERN = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-]\d{2}:?\d{2}))?)?$"
)


def validate_source_url(url: str) -> bool:
    """Validate that a URL is well-formed, uses http/https, and is not a synthetic/internal lead."""
    if not url or not isinstance(url, str):
        return False

    clean_url = url.strip()
    if len(clean_url) < 8 or len(clean_url) > 2048:
        return False

    try:
        parsed = urlparse(clean_url)
        if parsed.scheme.lower() not in ("http", "https"):
            return False

        hostname = (parsed.hostname or "").lower()
        if not hostname or "." not in hostname:
            return False

        if _INVALID_HOST_PATTERN.match(hostname):
            return False

        return True
    except Exception:
        return False


def parse_and_validate_publication_date(
    date_raw: Any,
    current_timestamp: float | None = None,
) -> tuple[str | None, bool, str]:
    """Parse, UTC-normalize, and validate a publication date extracted from structured metadata.

    Returns:
        (normalized_iso_str, is_valid, reason)
    """
    if date_raw is None or date_raw == "":
        return None, False, "No publication date metadata provided."

    now_ts = current_timestamp if current_timestamp is not None else time.time()
    now_utc = datetime.datetime.fromtimestamp(now_ts, tz=datetime.timezone.utc)
    max_allowed_future = now_utc + datetime.timedelta(days=1)  # 24h clock skew allowance

    parsed_dt: datetime.datetime | None = None

    if isinstance(date_raw, (int, float)):
        # UNIX timestamp
        try:
            parsed_dt = datetime.datetime.fromtimestamp(float(date_raw), tz=datetime.timezone.utc)
        except (ValueError, OSError, OverflowError) as e:
            return None, False, f"Invalid numeric timestamp: {e}"

    elif isinstance(date_raw, datetime.datetime):
        if date_raw.tzinfo is None:
            parsed_dt = date_raw.replace(tzinfo=datetime.timezone.utc)
        else:
            parsed_dt = date_raw.astimezone(datetime.timezone.utc)

    elif isinstance(date_raw, datetime.date):
        parsed_dt = datetime.datetime(
            date_raw.year, date_raw.month, date_raw.day, tzinfo=datetime.timezone.utc
        )

    elif isinstance(date_raw, str):
        cleaned = date_raw.strip()
        # 1. Try standard ISO parsing
        try:
            # Handle trailing 'Z'
            iso_str = cleaned.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                parsed_dt = dt.replace(tzinfo=datetime.timezone.utc)
            else:
                parsed_dt = dt.astimezone(datetime.timezone.utc)
        except ValueError:
            pass

        # 2. Try RFC-2822 / HTTP date format
        if parsed_dt is None:
            from email.utils import parsedate_to_datetime
            try:
                dt = parsedate_to_datetime(cleaned)
                if dt.tzinfo is None:
                    parsed_dt = dt.replace(tzinfo=datetime.timezone.utc)
                else:
                    parsed_dt = dt.astimezone(datetime.timezone.utc)
            except Exception:
                pass

        # 3. Try custom regex match for YYYY-MM-DD
        if parsed_dt is None:
            match = _ISO_DATE_PATTERN.match(cleaned)
            if match:
                try:
                    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                    hour = int(match.group(4)) if match.group(4) else 0
                    minute = int(match.group(5)) if match.group(5) else 0
                    second = int(match.group(6)) if match.group(6) else 0
                    parsed_dt = datetime.datetime(
                        year, month, day, hour, minute, second, tzinfo=datetime.timezone.utc
                    )
                except ValueError as ve:
                    return None, False, f"Impossible calendar date: {ve}"

    if parsed_dt is None:
        return None, False, f"Unparseable date metadata format: '{date_raw}'"

    # Validate against impossible year boundaries
    if parsed_dt.year < 1990:
        return None, False, f"Publication date year {parsed_dt.year} precedes modern web indexing standard."

    # Validate against future timestamps (reject impossible future dates)
    if parsed_dt > max_allowed_future:
        return None, False, f"Publication date {parsed_dt.isoformat()} is in the future beyond allowed clock skew."

    normalized_iso = parsed_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return normalized_iso, True, "Valid publication date."


def format_search_evidence(
    items: list[dict[str, Any]],
    current_time: float | None = None,
) -> list[dict[str, Any]]:
    """Process, validate, and enrich a list of raw search results.

    Enforces:
    - URL validation (discarding synthetic or malicious links).
    - Structured publication date extraction from metadata headers only (never body text).
    - Explicit tagging of undated snippets.
    """
    validated_results: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        raw_url = item.get("url") or item.get("href") or ""
        is_url_valid = validate_source_url(raw_url)

        # Extract structured date metadata header (e.g., DuckDuckGo 'date', HTTP 'Last-Modified', meta)
        raw_date = item.get("date") or item.get("published_date") or item.get("pub_date")
        iso_date, date_valid, date_reason = parse_and_validate_publication_date(
            raw_date, current_timestamp=current_time
        )

        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or item.get("body") or "").strip()
        source = str(item.get("source") or "").strip()

        enriched = {
            "title": title,
            "snippet": snippet,
            "url": raw_url if is_url_valid else "",
            "source": source,
            "verified_url": is_url_valid,
            "publication_date": iso_date if date_valid else None,
            "undated": not date_valid,
            "date_validation_reason": date_reason,
        }
        validated_results.append(enriched)

    return validated_results
