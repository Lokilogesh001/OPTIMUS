"""core/application_intelligence/registry.py — Application Registry.

Maintains the authoritative in-memory graph of all discovered, registered,
and currently running desktop applications with TTL caching, fast alias resolution,
file extension routing, and process integrity level checks.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import win32gui
import win32con

from core.application_intelligence.desktop_lock import DesktopInteractionLock
from core.application_intelligence.discovery import (
    discover_all_installed_applications,
    discover_running_processes_and_windows,
    resolve_file_association,
)
from core.application_intelligence.types import (
    AppCategory,
    AppDefinition,
    AppInteractionTier,
)
from core.windows_runtime.client import get_windows_runtime_client

logger = logging.getLogger("optimus.application_intelligence.registry")


class ApplicationRegistry:
    """Thread-safe, TTL-cached registry of desktop applications."""

    _instance: ApplicationRegistry | None = None
    _lock = threading.RLock()

    STATIC_INVENTORY_TTL = 60.0    # 60s cache for installed apps (reconciles LOW-1)
    RUNNING_PROCESS_TTL = 2.0      # 2s cache for running processes/windows

    def __init__(self, cache_file: Path | str | None = None) -> None:
        if cache_file:
            self.cache_file = Path(cache_file)
        else:
            self.cache_file = Path.cwd() / "config" / "application_inventory.json"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        self._apps: dict[str, AppDefinition] = {}
        self._alias_index: dict[str, str] = {}
        self._last_static_refresh: float = 0.0
        self._last_process_refresh: float = 0.0
        self._load_cache()

    @classmethod
    def instance(cls, cache_file: Path | str | None = None) -> ApplicationRegistry:
        """Global thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(cache_file=cache_file)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Testing hook to reset singleton."""
        with cls._lock:
            cls._instance = None

    def _load_cache(self) -> None:
        """Load cached application definitions from disk."""
        if self.cache_file.is_file():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                for item in data.get("applications", []):
                    app = AppDefinition.from_dict(item)
                    self._register_internal(app)
                logger.debug("Loaded %d applications from cache", len(self._apps))
            except Exception as ex:
                logger.warning("Failed loading application cache: %s", ex)

    def _save_cache(self) -> None:
        """Persist application definitions to disk."""
        try:
            payload = {
                "saved_at": time.time(),
                "applications": [a.to_dict() for a in self._apps.values()],
            }
            self.cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as ex:
            logger.warning("Failed saving application cache: %s", ex)

    def _register_internal(self, app: AppDefinition) -> None:
        """Register application into in-memory dictionaries and alias indices."""
        self._apps[app.app_id] = app

        # Index primary name
        self._alias_index[app.name.lower()] = app.app_id

        # Index aliases
        for alias in app.aliases:
            self._alias_index[alias.lower().strip()] = app.app_id

        # Index binary stem if available
        if app.executable_path:
            stem = Path(app.executable_path).stem.lower()
            self._alias_index[stem] = app.app_id

    def refresh(self, force: bool = False) -> None:
        """Refresh installed application inventory and running process states."""
        now = time.time()
        with self._lock:
            # 1. Static Inventory Refresh (with 60s TTL)
            if force or (now - self._last_static_refresh > self.STATIC_INVENTORY_TTL) or not self._apps:
                discovered = discover_all_installed_applications()
                for app in discovered.values():
                    self._register_internal(app)
                self._last_static_refresh = now
                self._save_cache()

            # 2. Dynamic Process Refresh (with 2s TTL)
            if force or (now - self._last_process_refresh > self.RUNNING_PROCESS_TTL):
                self._refresh_running_processes()
                self._last_process_refresh = now

    def _refresh_running_processes(self) -> None:
        """Update is_running, main_hwnd, pids, and integrity_level on registered apps."""
        running_procs = discover_running_processes_and_windows()

        # Reset running states
        for app in self._apps.values():
            app.is_running = False
            app.main_hwnd = None
            app.pids = []

        # Correlate running processes
        for proc in running_procs:
            p_name = proc["name"].lower()
            stem = Path(p_name).stem
            app_id = self._alias_index.get(stem) or self._alias_index.get(p_name)

            if app_id and app_id in self._apps:
                app = self._apps[app_id]
                app.is_running = True
                app.pids.append(proc["pid"])
                if proc["main_hwnd"] and not app.main_hwnd:
                    app.main_hwnd = proc["main_hwnd"]
                app.integrity_level = proc["integrity_level"]

    def find_app(self, query: str) -> AppDefinition | None:
        """Find an application by ID, name, alias, or executable name."""
        self.refresh()
        clean = query.strip().lower()

        with self._lock:
            # 1. Exact app_id lookup
            if clean in self._apps:
                return self._apps[clean]

            # 2. Alias index lookup
            if clean in self._alias_index:
                target_id = self._alias_index[clean]
                return self._apps.get(target_id)

            # 3. Substring matching in aliases and names
            for app in self._apps.values():
                if clean in app.name.lower():
                    return app
                if any(clean in a for a in app.aliases):
                    return app
                if app.executable_path and clean in Path(app.executable_path).name.lower():
                    return app

        return None

    def find_by_extension(self, extension: str) -> list[AppDefinition]:
        """Find registered applications that support a given file extension."""
        self.refresh()
        clean_ext = extension.lower()
        if not clean_ext.startswith("."):
            clean_ext = f".{clean_ext}"

        matched_apps: list[AppDefinition] = []
        with self._lock:
            # 1. Check registered associated extensions
            for app in self._apps.values():
                if clean_ext in [e.lower() for e in app.associated_extensions]:
                    matched_apps.append(app)

            # 2. Check Windows file associations
            associated_exes = resolve_file_association(clean_ext)
            for exe_name in associated_exes:
                stem = Path(exe_name).stem.lower()
                app = self.find_app(stem)
                if app and app not in matched_apps:
                    matched_apps.append(app)

        return matched_apps

    def list_all_apps(self) -> list[AppDefinition]:
        """Return all discovered applications."""
        self.refresh()
        with self._lock:
            return list(self._apps.values())

    def list_running_apps(self) -> list[AppDefinition]:
        """Return all currently running applications with window handles."""
        self.refresh()
        with self._lock:
            return [a for a in self._apps.values() if a.is_running]

    def launch_or_activate(
        self,
        query: str,
        args: list[str] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Launch an application or activate its existing window with DesktopInteractionLock.

        Reconciles CRIT-4 (desktop concurrency) and HIGH-6 (UIPI integrity checks).
        """
        app = self.find_app(query)
        if not app:
            return {
                "success": False,
                "error": f"Application '{query}' is not discovered or registered on this system.",
                "app": None,
            }

        with DesktopInteractionLock(owner="app_launch_or_activate", timeout=timeout):
            # Check UIPI Elevation barrier (reconciles HIGH-6)
            if app.is_running and app.integrity_level == "High":
                logger.warning("Target application %s is running with High Integrity (Admin)", app.name)

            # 1. If already running with a main window, activate and bring to foreground
            if app.is_running and app.main_hwnd:
                try:
                    hwnd = app.main_hwnd
                    if win32gui.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    else:
                        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.3)
                    return {
                        "success": True,
                        "action": "activated",
                        "app": app.to_dict(),
                        "hwnd": hwnd,
                        "message": f"Activated running instance of '{app.name}' (HWND: {hwnd})",
                    }
                except Exception as ex:
                    logger.debug("SetForegroundWindow failed: %s, falling back to launch", ex)

            # 2. Launch new instance via WindowsRuntimeClient
            client = get_windows_runtime_client()
            target_exe = app.executable_path or app.name
            launch_res = client.launch_application(target_exe, args=args or [])

            if launch_res.get("status") != "failed":
                # Wait briefly for process/window to appear
                time.sleep(0.8)
                self.refresh(force=True)
                updated_app = self.find_app(app.app_id) or app
                return {
                    "success": True,
                    "action": "launched",
                    "app": updated_app.to_dict(),
                    "pid": launch_res.get("pid"),
                    "message": f"Launched '{app.name}' successfully",
                }
            else:
                return {
                    "success": False,
                    "error": launch_res.get("error", f"Failed to launch '{app.name}'"),
                    "app": app.to_dict(),
                }
