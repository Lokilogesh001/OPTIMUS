"""core/vision/context_classifier.py — Tab Stack & Context Intelligence.

Implements Section 5 of Milestone 16:
- Semantic classification of browser tabs (development, research, documentation, communication, general).
- Duplicate tab detection across open browser windows.
- Task-relevance ranking and context grouping.
"""

from __future__ import annotations

import re

from core.vision.types import BrowserTabInfo

_CATEGORY_KEYWORDS = {
    "development": [
        "github", "gitlab", "bitbucket", "stackoverflow", "localhost", "127.0.0.1",
        "npm", "pypi", "crates.io", "docker", "pull request", "commit", "diff",
    ],
    "research": [
        "arxiv", "scholar.google", "wikipedia", "nature.com", "sciencedirect",
        "researchgate", "paper", "abstract", "journal", "proceedings", "pdf",
    ],
    "documentation": [
        "docs", "readthedocs", "developer.mozilla", "mdn", "devdocs", "api reference",
        "documentation", "manual", "guide", "tutorial", "learn.microsoft",
    ],
    "communication": [
        "mail", "gmail", "outlook", "slack", "discord", "teams", "whatsapp",
        "telegram", "messages", "inbox", "thread",
    ],
}


def classify_tab_semantics(tab: BrowserTabInfo) -> str:
    """Classify a tab into a semantic category based on title, domain, and URL hints."""
    combined = f"{tab.title} {tab.domain or ''} {tab.domain_hint or ''} {tab.url or ''}".lower()

    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return category

    return "general"


def detect_duplicate_tabs(tabs: list[BrowserTabInfo]) -> list[tuple[BrowserTabInfo, BrowserTabInfo]]:
    """Detect duplicate open tabs across windows (matching URL or normalized title)."""
    seen: dict[str, BrowserTabInfo] = {}
    duplicates: list[tuple[BrowserTabInfo, BrowserTabInfo]] = []

    for t in tabs:
        # Key on clean URL or normalized title
        key = (t.url or t.title).strip().lower()
        if not key or len(key) < 4:
            continue

        if key in seen:
            duplicates.append((seen[key], t))
        else:
            seen[key] = t

    return duplicates


def group_tabs_by_task(tabs: list[BrowserTabInfo]) -> dict[str, list[BrowserTabInfo]]:
    """Group tabs into functional task-oriented clusters."""
    groups: dict[str, list[BrowserTabInfo]] = {
        "development": [],
        "research": [],
        "documentation": [],
        "communication": [],
        "general": [],
    }

    for t in tabs:
        cat = classify_tab_semantics(t)
        t.semantic_category = cat
        groups[cat].append(t)

    return groups


def rank_tabs_for_task(tabs: list[BrowserTabInfo], task_goal: str) -> list[tuple[BrowserTabInfo, float]]:
    """Rank tabs by relevance to an ongoing task goal using keyword and semantic matching."""
    goal_words = set(re.findall(r"\w{3,}", task_goal.lower()))
    ranked: list[tuple[BrowserTabInfo, float]] = []

    for t in tabs:
        score = 0.0
        text = f"{t.title} {t.domain or ''} {t.domain_hint or ''}".lower()
        tab_words = set(re.findall(r"\w{3,}", text))

        intersection = goal_words.intersection(tab_words)
        score += len(intersection) * 2.0

        if t.is_active:
            score += 1.0
        if t.is_pinned:
            score += 0.5

        # Boost matching category
        cat = classify_tab_semantics(t)
        if any(cat in kw for kw in goal_words) or ("code" in goal_words and cat == "development") or ("paper" in goal_words and cat == "research"):
            score += 3.0

        ranked.append((t, score))

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked
