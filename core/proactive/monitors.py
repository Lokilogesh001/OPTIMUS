"""core/proactive/monitors.py — Controlled Event and Condition Monitoring Subsystem.

Implements Phase 3 of Milestone 10: Event and Condition Monitoring.
- Evaluates system vitals (CPU, RAM, Disk), service status (Ollama), files, and mission states.
- Applies edge-triggered deduplication (ON_EDGE) to prevent alert flooding.
- Persists user-defined monitors in `memory/monitors/<id>.json`.
- Dispatches notifications via NotificationManager without aggressive polling.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from core.proactive.notifications import get_notification_manager
from core.proactive.types import (
    ConditionMonitorRecord,
    MonitorStatus,
    RecommendationPriority,
)

logger = logging.getLogger("optimus.proactive.monitors")


class ConditionMonitorEngine:
    """Evaluates configured monitors and alerts when state conditions are breached."""

    def __init__(
        self,
        storage_dir: Path | str | None = None,
        notification_manager: Any | None = None,
    ) -> None:
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path.cwd() / "memory" / "monitors"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.notification_manager = notification_manager or get_notification_manager()
        self._monitors: dict[str, ConditionMonitorRecord] = {}
        self._previous_states: dict[str, bool] = {}  # Tracks edge transitions
        self._lock = threading.RLock()
        self._running = False
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._load_all()
        self._ensure_default_monitors()

    def _load_all(self) -> None:
        with self._lock:
            for file_path in self.storage_dir.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    rec = ConditionMonitorRecord.from_dict(data)
                    self._monitors[rec.monitor_id] = rec
                except Exception as ex:
                    logger.warning("Failed to load monitor file %s: %s", file_path, ex)

    def _persist(self, rec: ConditionMonitorRecord) -> None:
        target_path = self.storage_dir / f"{rec.monitor_id}.json"
        temp_path = self.storage_dir / f"{rec.monitor_id}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
                f.flush()
            if target_path.exists():
                target_path.unlink()
            temp_path.rename(target_path)
        except Exception as ex:
            logger.error("Failed to persist monitor %s: %s", rec.monitor_id, ex)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def _ensure_default_monitors(self) -> None:
        """Register baseline system monitors if none exist."""
        with self._lock:
            if not self._monitors:
                # 1. Disk usage monitor
                self.add_monitor(
                    name="High Disk Usage Alert",
                    description="Alert when disk usage exceeds 90%",
                    condition_type="metric",
                    target="disk",
                    operator=">=",
                    threshold=90.0,
                    evaluation_interval=60.0,
                )
                # 2. Ollama service health monitor
                self.add_monitor(
                    name="Ollama Service Monitor",
                    description="Check local Ollama AI daemon availability",
                    condition_type="service",
                    target="ollama",
                    operator="==",
                    threshold="down",
                    evaluation_interval=120.0,
                )

    def add_monitor(
        self,
        name: str,
        condition_type: str,
        target: str,
        operator: str = ">=",
        threshold: Any = 90.0,
        evaluation_interval: float = 30.0,
        description: str = "",
        policy: str = "ON_EDGE",
    ) -> ConditionMonitorRecord:
        with self._lock:
            rec = ConditionMonitorRecord(
                name=name,
                description=description,
                condition_type=condition_type,
                target=target,
                operator=operator,
                threshold=threshold,
                evaluation_interval=evaluation_interval,
                policy=policy,
            )
            self._monitors[rec.monitor_id] = rec
            self._persist(rec)
            return rec

    def pause_monitor(self, monitor_id: str) -> bool:
        with self._lock:
            rec = self._monitors.get(monitor_id)
            if rec and rec.status == MonitorStatus.ACTIVE:
                rec.status = MonitorStatus.PAUSED
                rec.enabled = False
                self._persist(rec)
                return True
            return False

    def resume_monitor(self, monitor_id: str) -> bool:
        with self._lock:
            rec = self._monitors.get(monitor_id)
            if rec and rec.status == MonitorStatus.PAUSED:
                rec.status = MonitorStatus.ACTIVE
                rec.enabled = True
                self._persist(rec)
                return True
            return False

    def delete_monitor(self, monitor_id: str) -> bool:
        with self._lock:
            rec = self._monitors.pop(monitor_id, None)
            if rec:
                p = self.storage_dir / f"{monitor_id}.json"
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
                self._previous_states.pop(monitor_id, None)
                return True
            return False

    def get_monitor(self, monitor_id: str) -> ConditionMonitorRecord | None:
        with self._lock:
            return self._monitors.get(monitor_id)

    def list_monitors(self) -> list[ConditionMonitorRecord]:
        with self._lock:
            return sorted(self._monitors.values(), key=lambda m: m.name)

    # ── Evaluation Engine ────────────────────────────────────────────────────

    def get_system_vitals(self) -> dict[str, float]:
        """Query real-time system metrics (CPU, RAM, Disk)."""
        import psutil
        try:
            cpu = float(psutil.cpu_percent(interval=None))
        except Exception:
            cpu = 0.0
        try:
            ram = float(psutil.virtual_memory().percent)
        except Exception:
            ram = 0.0
        try:
            disk_path = "C:\\" if os.name == "nt" else "/"
            disk = float(psutil.disk_usage(disk_path).percent)
        except Exception:
            disk = 0.0
        return {"cpu_percent": cpu, "ram_percent": ram, "disk_percent": disk}

    def _evaluate_metric(self, target: str) -> float:
        vitals = self.get_system_vitals()
        t = target.lower()
        if "ram" in t or "memory" in t:
            return vitals.get("ram_percent", 0.0)
        elif "cpu" in t:
            return vitals.get("cpu_percent", 0.0)
        elif "disk" in t or "storage" in t:
            return vitals.get("disk_percent", 0.0)
        return 0.0

    def _evaluate_service(self, service_name: str) -> str:
        if service_name.lower() == "ollama":
            try:
                req = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "OptimusMonitor/1.0"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        return "up"
            except Exception:
                return "down"
        return "unknown"

    def _check_condition(self, rec: ConditionMonitorRecord) -> tuple[bool, str]:
        """Check if condition currently holds true."""
        try:
            if rec.condition_type == "metric":
                val = self._evaluate_metric(rec.target)
                thresh = float(rec.threshold)
                if rec.operator == ">=" and val >= thresh:
                    return True, f"{rec.target.upper()} usage is {val:.1f}%, exceeding threshold {thresh:.1f}%"
                elif rec.operator == "<=" and val <= thresh:
                    return True, f"{rec.target.upper()} usage is {val:.1f}%, below threshold {thresh:.1f}%"
                return False, f"{rec.target.upper()} is {val:.1f}% (Normal)"

            elif rec.condition_type == "service":
                status = self._evaluate_service(rec.target)
                if str(rec.threshold).lower() == status.lower():
                    return True, f"Service '{rec.target}' state is '{status}' (Breach condition met)"
                return False, f"Service '{rec.target}' is '{status}'"

            elif rec.condition_type == "file":
                p = Path(rec.target)
                if rec.operator == "exists" and p.exists():
                    return True, f"File or path '{p}' exists"
                elif rec.operator == "missing" and not p.exists():
                    return True, f"Required file '{p}' is missing"
                return False, f"Path condition for '{p}' normal"

        except Exception as ex:
            return False, f"Evaluation error: {ex}"

        return False, "Condition not met"

    def evaluate_monitor(self, rec: ConditionMonitorRecord) -> bool:
        """Evaluate a single monitor record and deliver notification if triggered."""
        if not rec.enabled or rec.status == MonitorStatus.PAUSED:
            return False

        breached, detail = self._check_condition(rec)
        now = time.time()
        rec.last_evaluated_at = now

        prev_breached = self._previous_states.get(rec.monitor_id, False)
        self._previous_states[rec.monitor_id] = breached

        should_notify = False
        if rec.policy == "ON_EDGE":
            # Only trigger on positive transition (False -> True)
            if breached and not prev_breached:
                should_notify = True
        else:
            # Continuous: notify if cooldown passed (at least 5 min)
            if breached and (rec.last_triggered_at is None or (now - rec.last_triggered_at) > 300.0):
                should_notify = True

        if should_notify:
            rec.last_triggered_at = now
            rec.trigger_count += 1
            rec.status = MonitorStatus.TRIGGERED

            self.notification_manager.add_notification(
                title=f"Alert: {rec.name}",
                message=detail,
                category="ALERT",
                priority=RecommendationPriority.HIGH,
                source=f"monitor:{rec.monitor_id}",
                evidence={"monitor_id": rec.monitor_id, "detail": detail, "threshold": rec.threshold},
            )
            self._persist(rec)
            return True

        self._persist(rec)
        return False

    def evaluate_all(self, force: bool = False) -> int:
        """Evaluate all active monitors whose interval has elapsed (or force evaluation)."""
        now = time.time()
        triggered_count = 0
        with self._lock:
            for rec in list(self._monitors.values()):
                if rec.enabled and rec.status != MonitorStatus.PAUSED:
                    elapsed = now - (rec.last_evaluated_at or 0.0)
                    if force or elapsed >= rec.evaluation_interval:
                        if self.evaluate_monitor(rec):
                            triggered_count += 1
        return triggered_count

    def create_monitor(
        self,
        name: str,
        condition_type: str,
        target: str,
        operator: str = ">=",
        threshold: Any = 90.0,
        evaluation_interval: float = 30.0,
        description: str = "",
        policy: str = "ON_EDGE",
    ) -> ConditionMonitorRecord:
        """Convenience alias for add_monitor."""
        return self.add_monitor(
            name=name,
            condition_type=condition_type,
            target=target,
            operator=operator,
            threshold=threshold,
            evaluation_interval=evaluation_interval,
            description=description,
            policy=policy,
        )

    def evaluate_monitors(self, force: bool = True) -> int:
        """Convenience alias for evaluate_all with force=True for on-demand checks."""
        return self.evaluate_all(force=force)

    def start_monitoring_loop(self, poll_interval_s: float = 10.0) -> None:
        """Start background monitor evaluation daemon."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

            def _loop():
                while not self._stop_event.is_set():
                    try:
                        self.evaluate_all()
                    except Exception as ex:
                        logger.error("Error in monitor evaluation loop: %s", ex)
                    self._stop_event.wait(poll_interval_s)

            self._worker_thread = threading.Thread(target=_loop, daemon=True, name="OptimusMonitor")
            self._worker_thread.start()

    def stop_monitoring_loop(self) -> None:
        """Stop background monitor daemon cleanly."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=2.0)
            self._worker_thread = None


_GLOBAL_MONITORS: ConditionMonitorEngine | None = None


def get_condition_monitor_engine() -> ConditionMonitorEngine:
    global _GLOBAL_MONITORS
    if _GLOBAL_MONITORS is None:
        _GLOBAL_MONITORS = ConditionMonitorEngine()
    return _GLOBAL_MONITORS
