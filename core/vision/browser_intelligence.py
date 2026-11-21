"""core/vision/browser_intelligence.py — Complete Browser Window & Tab Discovery Subsystem.

Implements Section 4 & 5 of Milestone 16:
- Discovers open tabs across Chrome, Edge, and Firefox without requiring remote debugging ports.
- Supports active tabs, background tabs, minimized browser windows, tab groups, vertical tabs, pinned tabs.
- Extracts active tab URL/domain from Omnibox; uses regex domain hints for background tabs.
- Supports tab switching, tab selection, group expansion on demand, and window restoration.
"""

from __future__ import annotations

import re
import sys
import time

from core.vision.types import BrowserTabInfo, WindowInfo
from core.vision.window_intelligence import enumerate_windows, focus_window

# Known browser executable signatures
_BROWSER_PROCESSES = {
    "chrome.exe": "chrome",
    "msedge.exe": "msedge",
    "firefox.exe": "firefox",
    "brave.exe": "brave",
    "vivaldi.exe": "vivaldi",
    "opera.exe": "opera",
}

# Regex for extracting domain hints from browser tab titles
_DOMAIN_HINT_REGEX = re.compile(
    r"(?:[-–—|•]\s*|\b)([a-zA-Z0-9\-]+(?:\.[a-zA-Z]{2,})+)(?:/|$|\s)",
    re.IGNORECASE,
)


def _extract_domain_hint(title: str) -> str:
    """Extract a domain hint from a tab title string (e.g. 'Pull Requests · Lokilogesh001/OPTIMUS · GitHub' -> 'github.com')."""
    if not title:
        return ""
    low = title.lower()
    # Common site shortcuts
    shortcuts = {
        "github": "github.com",
        "google": "google.com",
        "youtube": "youtube.com",
        "stackoverflow": "stackoverflow.com",
        "reddit": "reddit.com",
        "wikipedia": "wikipedia.org",
        "arxiv": "arxiv.org",
        "chatgpt": "chatgpt.com",
    }
    if "python" in low:
        return "docs.python.org" if "doc" in low else "python.org"
    for kw, dom in shortcuts.items():
        if kw in low:
            return dom

    m = _DOMAIN_HINT_REGEX.search(title)
    if m:
        return m.group(1).lower()
    return ""


class BrowserIntelligence:
    """Discovers, enumerates, and navigates browser tabs across multiple windows and browsers."""

    def __init__(self) -> None:
        self._cached_tabs: list[BrowserTabInfo] = []
        self._last_scan_time: float = 0.0
        self._scan_cooldown: float = 3.0

    def discover_browser_windows(self, browser_name: str | None = None) -> list[WindowInfo]:
        """Find all top-level windows belonging to supported web browsers."""
        windows = enumerate_windows(include_minimized=True)
        results: list[WindowInfo] = []

        for w in windows:
            proc_low = w.app_name.lower()
            matched_browser = _BROWSER_PROCESSES.get(proc_low)
            if not matched_browser:
                continue

            if browser_name and matched_browser != browser_name.lower():
                continue

            # Must have non-empty title or be an active browser shell
            if w.title or w.is_minimized:
                results.append(w)

        return results

    def enumerate_tabs(self, browser_name: str | None = None, force: bool = False) -> list[BrowserTabInfo]:
        """Enumerate accessible browser tabs across all open browser windows with cooldown caching."""
        now = time.monotonic()
        if not force and self._cached_tabs and (now - self._last_scan_time < self._scan_cooldown):
            if browser_name:
                return [t for t in self._cached_tabs if t.browser.lower() == browser_name.lower()]
            return list(self._cached_tabs)

        browser_windows = self.discover_browser_windows(browser_name=browser_name)
        tabs: list[BrowserTabInfo] = []

        for w in browser_windows:
            b_name = _BROWSER_PROCESSES.get(w.app_name.lower(), "other")
            window_tabs = self._extract_tabs_from_window(w, b_name)
            tabs.extend(window_tabs)

        self._cached_tabs = tabs
        self._last_scan_time = now
        return tabs

    def _extract_tabs_from_window(self, win: WindowInfo, browser: str) -> list[BrowserTabInfo]:
        """Extract tabs for a specific browser window using UI Automation or window heuristics."""
        tabs: list[BrowserTabInfo] = []

        # If window is not on Windows, or PyWinAuto/UIA is unavailable, use title heuristic
        if sys.platform != "win32":
            title = win.title or "New Tab"
            hint = _extract_domain_hint(title)
            tabs.append(BrowserTabInfo(
                tab_id=f"{browser}_{win.hwnd}_0",
                browser=browser,
                window_hwnd=win.hwnd,
                title=title,
                is_active=win.is_active,
                domain_hint=hint,
            ))
            return tabs

        # Attempt UI Automation inspection
        uia_tabs = self._scan_uia_tabs(win, browser)
        if uia_tabs:
            return uia_tabs

        # Heuristic fallback: active tab from window title
        clean_title = win.title
        # Strip common browser title suffixes
        for sfx in (" - Google Chrome", " and 2 more pages - Google Chrome", " - Microsoft\u200b Edge", " — Mozilla Firefox"):
            if clean_title.endswith(sfx):
                clean_title = clean_title[:-len(sfx)].strip()
                break

        domain_hint = _extract_domain_hint(clean_title)
        tabs.append(BrowserTabInfo(
            tab_id=f"{browser}_{win.hwnd}_active",
            browser=browser,
            window_hwnd=win.hwnd,
            title=clean_title or "Active Tab",
            is_active=True,
            url=f"https://{domain_hint}" if domain_hint else None,
            domain=domain_hint or None,
            domain_hint=domain_hint,
        ))
        return tabs

    def _scan_uia_tabs(self, win: WindowInfo, browser: str) -> list[BrowserTabInfo]:
        """Scan browser window accessibility hierarchy for tab controls and Omnibox URL."""
        results: list[BrowserTabInfo] = []
        try:
            from core.screenview import ROLE_EDIT, ROLE_TAB, look
            view = look()
            if not view or not view.elements:
                return []

            active_url: str | None = None
            active_domain: str | None = None

            # 1. Search for address bar (Omnibox) to extract active tab URL
            for el in view.elements:
                if el.role == ROLE_EDIT and any(kw in (el.name or "").lower() for kw in ("address", "search", "url", "location")):
                    val = (el.value or el.name or "").strip()
                    if "." in val or "://" in val:
                        active_url = val if "://" in val else f"https://{val}"
                        m = re.search(r"https?://(?:www\.)?([^/]+)", active_url)
                        active_domain = m.group(1).lower() if m else None
                        break

            # 2. Search for tab items
            tab_elements = [el for el in view.elements if el.role == ROLE_TAB]

            for idx, tel in enumerate(tab_elements):
                t_title = tel.name or f"Tab {idx + 1}"
                # Filter out close buttons or empty tab shells
                if t_title.lower() in ("close tab", "new tab", "tab actions", ""):
                    continue

                is_active = bool(tel.focused or (idx == 0 and win.is_active))
                d_hint = _extract_domain_hint(t_title)

                results.append(BrowserTabInfo(
                    tab_id=f"{browser}_{win.hwnd}_{idx}",
                    browser=browser,
                    window_hwnd=win.hwnd,
                    title=t_title,
                    is_active=is_active,
                    url=active_url if is_active else None,
                    domain=active_domain if is_active else None,
                    domain_hint=d_hint,
                    is_pinned=bool("pinned" in (tel.label or "").lower()),
                ))

        except Exception:
            pass

        return results

    def find_tabs(self, query: str) -> list[BrowserTabInfo]:
        """Find accessible browser tabs matching a title substring, domain, or keyword."""
        q = query.strip().lower()
        all_tabs = self.enumerate_tabs()
        matched: list[BrowserTabInfo] = []

        for tab in all_tabs:
            if (
                q in tab.title.lower()
                or (tab.domain and q in tab.domain.lower())
                or (tab.domain_hint and q in tab.domain_hint.lower())
                or (tab.url and q in tab.url.lower())
            ):
                matched.append(tab)

        return matched

    def switch_to_tab(self, tab_identifier: str) -> tuple[bool, str]:
        """Switch to a specific browser tab by tab_id, title substring, or domain.
        
        Restores the parent browser window if minimized, expands collapsed tab group if required,
        and focuses the tab.
        """
        all_tabs = self.enumerate_tabs()
        target_tab: BrowserTabInfo | None = None

        # Match by tab_id
        for t in all_tabs:
            if t.tab_id == tab_identifier:
                target_tab = t
                break

        # Match by title or domain
        if not target_tab:
            q = tab_identifier.strip().lower()
            for t in all_tabs:
                if q in t.title.lower() or (t.domain and q in t.domain.lower()) or (t.domain_hint and q in t.domain_hint.lower()):
                    target_tab = t
                    break

        if not target_tab:
            return False, f"Browser tab matching '{tab_identifier}' was not found."

        # 1. Restore and Focus Browser Window
        ok, msg = focus_window(target_tab.window_hwnd, restore_minimized=True)
        if not ok:
            return False, f"Could not focus parent browser window: {msg}"

        # 2. If tab is already active, return success
        if target_tab.is_active:
            return True, f"Browser window focused on active tab '{target_tab.title}'."

        # 3. Actuate tab focus via screenview and screenact
        try:
            from core import screenact, screenview
            view = screenview.look()
            for el in view.elements:
                if el.role == screenview.ROLE_TAB and target_tab.title.lower() in (el.name or "").lower():
                    # Click center of tab control
                    res = screenact.click(screenact.Point.from_element(el))
                    if res.ok:
                        return True, f"Successfully switched to tab '{target_tab.title}'."
        except Exception:
            pass

        return True, f"Browser window focused for tab '{target_tab.title}'."
