"""core/application_intelligence/adapters/media.py — Media & Spotify Application Adapter.

Implements end-to-end media tasks (launch/focus -> search -> select -> play -> verify)
respecting explicit Desktop vs Web intent, SharedActionScheduler coordination,
and deterministic playback verification (Windows Audio Session / window title change).
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from typing import Any, Callable

from core.application_intelligence.adapters.base import BaseApplicationAdapter
from core.application_intelligence.types import (
    ActionContract,
    AppDefinition,
    AppInteractionTier,
    ExecutionEvidence,
    ExecutionVerdict,
)
from core.orchestrator.scheduler import ResourceType, SharedActionScheduler

logger = logging.getLogger("optimus.application_intelligence.adapters.media")


class MediaAdapter(BaseApplicationAdapter):
    """Universal Media / Spotify adapter supporting Desktop, Web, and playback verification."""

    def __init__(
        self,
        app_def: AppDefinition | None = None,
        *,
        ui_driver: Callable[[str, str, dict[str, Any]], tuple[bool, str, dict[str, Any]]] | None = None,
        playback_verifier: Callable[[str], tuple[bool, str]] | None = None,
        spotify_cli_path: str | None = None,
        cli_runner: Callable[[list[str]], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(app_def)
        self._ui_driver = ui_driver
        self._playback_verifier = playback_verifier
        self._spotify_cli_path = spotify_cli_path
        self._cli_runner = cli_runner

    @staticmethod
    def _clean_track_query(query: str) -> str:
        """Strip conversational filler tokens when retrying a catalog search."""
        cleaned = re.sub(
            r"\b(?:the|song|songs|track|tracks|music|audio|from|movie|film|album|by|on\s+spotify|please)\b",
            " ",
            query,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", cleaned).strip()

    def _find_spotify_cli(self) -> str | None:
        if self._spotify_cli_path:
            return self._spotify_cli_path
        try:
            from core.application_intelligence.registry import ApplicationRegistry
            app = ApplicationRegistry.instance().find_app("spotify")
            if app and app.executable_path and os.path.basename(app.executable_path).lower() == "spotify_cli.exe":
                if os.path.exists(app.executable_path):
                    return app.executable_path
        except Exception:
            pass
        which_cli = shutil.which("spotify_cli") or shutil.which("spotify_cli.exe")
        if which_cli:
            return which_cli
        pattern = r"C:\Program Files\WindowsApps\SpotifyAB.SpotifyMusic*\spotify_cli.exe"
        matches = sorted(glob.glob(pattern))
        for m in reversed(matches):
            if os.path.exists(m):
                return m
        return None

    def discover(self) -> bool:
        if self._find_spotify_cli():
            return True
        from core.application_intelligence.registry import ApplicationRegistry
        app = ApplicationRegistry.instance().find_app("spotify")
        return bool(app)

    def connect(self) -> bool:
        """Launch or activate Spotify desktop window."""
        if self._ui_driver is not None or self._cli_runner is not None:
            self._is_connected = True
            return True
        if self._find_spotify_cli():
            res = self._run_spotify_cli(["open"], timeout=8.0)
            self._is_connected = bool(res.get("ok"))
            if self._is_connected:
                return True
        from core.application_intelligence.registry import ApplicationRegistry
        res = ApplicationRegistry.instance().launch_or_activate("spotify")
        self._is_connected = bool(res.get("success"))
        return self._is_connected

    def _run_spotify_cli(self, args: list[str], timeout: float = 12.0) -> dict[str, Any]:
        if self._cli_runner is not None:
            return self._cli_runner(args)
        cli = self._find_spotify_cli()
        if not cli:
            return {"ok": False, "returncode": -1, "data": None, "stdout": "", "error": "spotify_cli not found"}
        try:
            proc = subprocess.run(
                [cli, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            parsed = None
            if out.startswith("{") or out.startswith("["):
                try:
                    parsed = json.loads(out)
                except Exception:
                    parsed = None
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "data": parsed,
                "stdout": out,
                "error": err or (None if proc.returncode == 0 else (out or f"CLI exited {proc.returncode}")),
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "returncode": -1, "data": None, "stdout": "", "error": "Spotify CLI timed out"}
        except Exception as exc:
            return {"ok": False, "returncode": -1, "data": None, "stdout": "", "error": str(exc)}

    def verify_audio_playback(self, target_app: str = "spotify") -> tuple[bool, str]:
        """Deterministically inspect Spotify CLI now-playing, AudioSession, or window state."""
        if "spotify" in target_app.lower() and (self._cli_runner is not None or self._find_spotify_cli()):
            np = self._run_spotify_cli(["now-playing", "--format", "json"], timeout=6.0)
            data = np.get("data") if isinstance(np, dict) else None
            if isinstance(data, dict):
                cp = data.get("currently_playing") or {}
                if isinstance(cp, dict) and cp.get("is_playing"):
                    desc = str(cp.get("description") or cp.get("uri") or "Spotify track")
                    return True, f"Verified active Spotify playback via now-playing: '{desc}'."
                if self._cli_runner is not None or "currently_playing" in data:
                    return False, "Spotify now-playing reports playback is not active."
            if self._cli_runner is not None:
                return False, "Spotify CLI now-playing could not confirm active playback."

        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation  # type: ignore
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                if s.Process and target_app.lower() in (s.Process.name() or "").lower():
                    try:
                        meter = s._ctl.QueryInterface(IAudioMeterInformation)
                        peak = meter.GetPeakValue()
                        if peak and peak > 0.0005:
                            return True, f"Verified active audio peak ({peak:.4f}) on '{s.Process.name()}'."
                    except Exception:
                        pass
                    if getattr(s, "State", None) == 1:  # AudioSessionStateActive
                        return True, f"Verified active Windows AudioSession for '{s.Process.name()}'."
        except Exception:
            pass

        try:
            from core.vision.window_intelligence import enumerate_windows
            for win in enumerate_windows(include_minimized=True):
                if target_app.lower() in win.app_name.lower() or target_app.lower() in win.title.lower():
                    t = win.title.strip()
                    if " - " in t and t.lower() not in ("spotify", "spotify free", "spotify premium"):
                        return True, f"Verified playing track via Spotify window title: '{t}'."
        except Exception:
            pass

        return False, "Could not confirm active audio playback via Spotify now-playing, AudioSession, or window title."

    def execute(self, contract: ActionContract) -> ExecutionEvidence:
        """Execute an end-to-end media contract with playback verification."""
        op = contract.operation.lower().strip()
        params = contract.parameters or {}
        query = str(params.get("query") or params.get("track") or params.get("song") or "").strip()
        mode = str(params.get("mode") or ("web" if "web" in contract.target_app.lower() else "desktop")).lower()

        if op in ("search_and_play", "play_track", "play_song", "play", "play_spotify_strict"):
            if not query:
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="failed",
                    verdict=ExecutionVerdict.FAILED,
                    tier_used=AppInteractionTier.TIER_2_ADAPTER,
                    error_message="VALIDATION_ERROR: Missing song or track query to play.",
                )

            # Respect explicit Web intent vs Desktop intent
            if mode == "web":
                url = f"https://open.spotify.com/search/{urllib.parse.quote(query)}"
                if self._ui_driver is not None:
                    ok, msg, extra = self._ui_driver("web", query, params)
                    tier = AppInteractionTier.TIER_4_BROWSER_DOM
                else:
                    from core.application_intelligence.adapters.browser import BrowserAdapter
                    ev = BrowserAdapter().execute(ActionContract(
                        action_id=f"{contract.action_id}_web",
                        target_app="browser",
                        operation="open_url",
                        parameters={"url": url},
                    ))
                    ok = ev.is_success
                    msg = f"Opened Spotify Web search for '{query}' (playback requires browser interaction verification)."
                    extra = {"url": url, "intermediate_only": True}
                    tier = AppInteractionTier.TIER_4_BROWSER_DOM

                verified, v_msg = (
                    self._playback_verifier(query) if self._playback_verifier
                    else (False, "Spotify Web page opened; audio playback unverified.")
                )
                if ok and verified:
                    return ExecutionEvidence(
                        action_id=contract.action_id,
                        status="done",
                        verdict=ExecutionVerdict.COMPLETED_VERIFIED,
                        tier_used=tier,
                        facts=[msg, v_msg],
                        evidence_data={"query": query, "mode": "web", "playing": True, **extra},
                    )
                if ok:
                    return ExecutionEvidence(
                        action_id=contract.action_id,
                        status="unverified",
                        verdict=ExecutionVerdict.ACTION_EXECUTED_UNVERIFIED,
                        tier_used=tier,
                        facts=[msg],
                        evidence_data={"query": query, "mode": "web", "playing": False, **extra},
                        error_message=v_msg,
                    )
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="failed",
                    verdict=ExecutionVerdict.FAILED,
                    tier_used=tier,
                    error_message=msg,
                )

            # Desktop Spotify Execution (coordinated by SharedActionScheduler)
            def _desktop_play() -> tuple[bool, str, dict[str, Any]]:
                if self._ui_driver is not None:
                    return self._ui_driver("desktop", query, params)

                use_cli = (
                    self._cli_runner is not None
                    or (self._find_spotify_cli() is not None and os.environ.get("OPTIMUS_DISABLE_SPOTIFY_CLI") != "1")
                )
                if use_cli:
                    st = self._run_spotify_cli(["status", "--format", "json"], timeout=8.0)
                    st_data = st.get("data") if isinstance(st, dict) else None
                    is_running = bool(isinstance(st_data, dict) and st_data.get("running"))
                    if not is_running:
                        self._run_spotify_cli(["open"], timeout=10.0)
                        if self._cli_runner is None:
                            time.sleep(0.8)
                        st = self._run_spotify_cli(["status", "--format", "json"], timeout=8.0)
                        st_data = st.get("data") if isinstance(st, dict) else None
                        is_running = bool(isinstance(st_data, dict) and st_data.get("running"))

                    if not st.get("ok") and not is_running:
                        return False, st.get("error") or "Spotify desktop app is unavailable.", {
                            "app": "spotify",
                            "error_code": "spotify_unavailable",
                            "fallback_used": "none",
                        }
                    if isinstance(st_data, dict) and st_data.get("logged_in") is False:
                        return False, "Spotify desktop app is not logged in.", {
                            "app": "spotify",
                            "error_code": "spotify_unavailable",
                            "fallback_used": "none",
                        }

                    # Search catalog via Spotify CLI (retry once with cleaned query if filler words prevented match)
                    search_queries = [query]
                    cleaned_q = self._clean_track_query(query)
                    if cleaned_q and cleaned_q.lower() != query.lower():
                        search_queries.append(cleaned_q)

                    tracks: list[dict[str, Any]] = []
                    used_query = query
                    for sq in search_queries:
                        sr = self._run_spotify_cli(["search", sq, "--type", "track", "--limit", "5", "--format", "json"], timeout=12.0)
                        if not sr.get("ok"):
                            return False, sr.get("error") or f"Spotify search rejected query '{sq}'.", {
                                "app": "spotify",
                                "error_code": "command_rejected",
                                "fallback_used": "none",
                            }
                        s_data = sr.get("data") if isinstance(sr.get("data"), dict) else {}
                        tracks = list((s_data or {}).get("tracks") or [])
                        if tracks:
                            used_query = sq
                            break

                    if not tracks:
                        return False, f"No matching track found on Spotify for '{query}'.", {
                            "app": "spotify",
                            "error_code": "track_not_found",
                            "tracks": [],
                            "fallback_used": "none",
                        }

                    chosen = tracks[0]
                    track_uri = str(chosen.get("uri") or "")
                    track_name = str(chosen.get("name") or query)
                    artists = chosen.get("artists") or []
                    artist_str = ", ".join(str(a) for a in artists) if isinstance(artists, list) else str(artists)
                    if not track_uri:
                        return False, f"Spotify search result for '{query}' missing playable URI.", {
                            "app": "spotify",
                            "error_code": "command_rejected",
                            "fallback_used": "none",
                        }

                    pl = self._run_spotify_cli(["play", track_uri, "--format", "json"], timeout=10.0)
                    if not pl.get("ok"):
                        return False, pl.get("error") or f"Spotify rejected play command for '{track_uri}'.", {
                            "app": "spotify",
                            "uri": track_uri,
                            "track": track_name,
                            "error_code": "command_rejected",
                            "fallback_used": "none",
                        }
                    if self._cli_runner is None:
                        time.sleep(0.5)
                    return True, f"Started Spotify track '{track_name}' ({artist_str}) [{track_uri}].", {
                        "app": "spotify",
                        "cli_used": True,
                        "uri": track_uri,
                        "track": track_name,
                        "artists": artists,
                        "search_query_used": used_query,
                        "candidates": [{"uri": t.get("uri"), "name": t.get("name"), "artists": t.get("artists")} for t in tracks[:5]],
                        "fallback_used": "none",
                    }

                from core.application_intelligence.registry import ApplicationRegistry
                reg = ApplicationRegistry.instance()
                app_def = reg.find_app("spotify")
                if not app_def:
                    return False, "Spotify desktop application is not installed/discovered.", {
                        "installed": False,
                        "error_code": "spotify_unavailable",
                        "fallback_used": "none",
                    }

                launch_res = reg.launch_or_activate("spotify")
                if not launch_res.get("success"):
                    return False, launch_res.get("error", "Failed to launch Spotify desktop."), {
                        **launch_res,
                        "error_code": "spotify_unavailable",
                        "fallback_used": "none",
                    }

                # Search & play inside Spotify Desktop via keyboard shortcuts (Ctrl+L search, Enter, play)
                try:
                    import pyautogui
                    time.sleep(0.8)
                    pyautogui.hotkey("ctrl", "l")
                    time.sleep(0.25)
                    pyautogui.write(query, interval=0.02)
                    pyautogui.press("enter")
                    time.sleep(1.2)
                    pyautogui.press("enter")
                    return True, f"Searched and triggered playback for '{query}' in Spotify Desktop.", {
                        "app": "spotify",
                        "fallback_used": "none",
                    }
                except Exception as exc:
                    return False, f"Spotify Desktop UI automation error: {exc}", {
                        "app": "spotify",
                        "error_code": "command_rejected",
                        "fallback_used": "none",
                    }

            try:
                ok, msg, extra = SharedActionScheduler.instance().run_coordinated(
                    [ResourceType.ACTIVE_WINDOW, ResourceType.KEYBOARD_MOUSE, ResourceType.AUDIO_CAPTURE_PLAYBACK],
                    _desktop_play,
                    task_id=contract.action_id,
                    priority=4,
                    timeout=15.0,
                )
            except Exception as exc:
                ok, msg, extra = False, str(exc), {}

            tier_used = AppInteractionTier.TIER_1_NATIVE_API if extra.get("cli_used") else AppInteractionTier.TIER_3_UI_AUTOMATION

            if not ok:
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="failed",
                    verdict=ExecutionVerdict.FAILED,
                    tier_used=tier_used,
                    evidence_data={"query": query, "mode": "desktop", **extra},
                    error_message=msg,
                )

            # Deterministic verification step: verify playback actually started
            verified, v_msg = (
                self._playback_verifier(query)
                if self._playback_verifier
                else self.verify_audio_playback("spotify")
            )
            if verified:
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="done",
                    verdict=ExecutionVerdict.COMPLETED_VERIFIED,
                    tier_used=tier_used,
                    facts=[msg, v_msg],
                    evidence_data={"query": query, "mode": "desktop", "playing": True, **extra},
                )
            return ExecutionEvidence(
                action_id=contract.action_id,
                status="unverified",
                verdict=ExecutionVerdict.ACTION_EXECUTED_UNVERIFIED,
                tier_used=tier_used,
                facts=[msg],
                evidence_data={"query": query, "mode": "desktop", "playing": False, "error_code": "playback_unverified", **extra},
                error_message=f"Spotify search executed, but playback could not be verified: {v_msg}",
            )

        if op in ("pause", "resume", "previous_track"):
            cli_cmd = {"pause": ["pause", "--format", "json"], "resume": ["resume", "--format", "json"], "previous_track": ["previous", "--format", "json"]}[op]
            if self._cli_runner is not None or (self._find_spotify_cli() is not None and os.environ.get("OPTIMUS_DISABLE_SPOTIFY_CLI") != "1"):
                res = self._run_spotify_cli(cli_cmd, timeout=8.0)
                if not res.get("ok"):
                    return ExecutionEvidence(
                        action_id=contract.action_id,
                        status="failed",
                        verdict=ExecutionVerdict.FAILED,
                        tier_used=AppInteractionTier.TIER_1_NATIVE_API,
                        error_message=res.get("error") or f"Spotify {op} command failed",
                        evidence_data={"error_code": "command_rejected", "fallback_used": "none"},
                    )
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="done",
                    verdict=ExecutionVerdict.COMPLETED_VERIFIED,
                    tier_used=AppInteractionTier.TIER_1_NATIVE_API,
                    facts=[f"Spotify '{op}' executed cleanly via Spotify CLI."],
                    evidence_data={"operation": op, "fallback_used": "none"},
                )
            media_key = {"pause": "playpause", "resume": "playpause", "previous_track": "prevtrack"}[op]
            try:
                import pyautogui
                pyautogui.press(media_key)
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="done",
                    verdict=ExecutionVerdict.COMPLETED_VERIFIED,
                    tier_used=AppInteractionTier.TIER_3_UI_AUTOMATION,
                    facts=[f"Triggered media key '{media_key}' for '{op}'."],
                    evidence_data={"operation": op, "fallback_used": "none"},
                )
            except Exception as exc:
                return ExecutionEvidence(
                    action_id=contract.action_id,
                    status="failed",
                    verdict=ExecutionVerdict.FAILED,
                    tier_used=AppInteractionTier.TIER_3_UI_AUTOMATION,
                    error_message=f"Spotify {op} UI fallback error: {exc}",
                    evidence_data={"error_code": "command_rejected", "fallback_used": "none"},
                )

        if op == "next_track":
            def _next_track() -> tuple[bool, str, dict[str, Any]]:
                if self._ui_driver is not None:
                    return self._ui_driver("desktop_next", query, params)
                try:
                    import pyautogui
                    pyautogui.press("nexttrack")
                    time.sleep(0.4)
                    return True, "Triggered next track media command.", {"app": "spotify"}
                except Exception as exc:
                    return False, f"Failed to trigger next track: {exc}", {}

            try:
                ok, msg, extra = SharedActionScheduler.instance().run_coordinated(
                    [ResourceType.ACTIVE_WINDOW, ResourceType.KEYBOARD_MOUSE, ResourceType.AUDIO_CAPTURE_PLAYBACK],
                    _next_track,
                    task_id=contract.action_id,
                    priority=4,
                    timeout=10.0,
                )
            except Exception as exc:
                ok, msg, extra = False, str(exc), {}

            verified, v_msg = (
                self._playback_verifier(query)
                if self._playback_verifier
                else self.verify_audio_playback("spotify")
            )
            return ExecutionEvidence(
                action_id=contract.action_id,
                status="done" if (ok and verified) else ("unverified" if ok else "failed"),
                verdict=(
                    ExecutionVerdict.COMPLETED_VERIFIED if (ok and verified)
                    else (ExecutionVerdict.ACTION_EXECUTED_UNVERIFIED if ok else ExecutionVerdict.FAILED)
                ),
                tier_used=AppInteractionTier.TIER_3_UI_AUTOMATION,
                facts=[msg, v_msg],
                evidence_data={"playing": verified, **extra},
                error_message=None if (ok and verified) else v_msg,
            )

        if op == "verify_playback":
            verified, v_msg = (
                self._playback_verifier(query)
                if self._playback_verifier
                else self.verify_audio_playback("spotify")
            )
            return ExecutionEvidence(
                action_id=contract.action_id,
                status="done" if verified else "unverified",
                verdict=ExecutionVerdict.COMPLETED_VERIFIED if verified else ExecutionVerdict.ACTION_EXECUTED_UNVERIFIED,
                tier_used=AppInteractionTier.TIER_1_NATIVE_API,
                facts=[v_msg],
                evidence_data={"playing": verified},
                error_message=None if verified else v_msg,
            )

        return ExecutionEvidence(
            action_id=contract.action_id,
            status="failed",
            verdict=ExecutionVerdict.FAILED,
            tier_used=AppInteractionTier.TIER_2_ADAPTER,
            error_message=f"Unsupported media operation: '{op}'",
        )
