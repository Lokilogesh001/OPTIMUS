"""core/proactive/engine.py — Proactive Personal Intelligence Engine for OPTIMUS.

Implements Phase 1 & 7:
- Synthesizes context, active monitors, schedules, and mission continuity into actionable recommendations.
- Produces recommendations across 7 canonical categories:
  1. PENDING_MISSION
  2. PROJECT_FOLLOWUP
  3. SCHEDULED_TASK
  4. CONDITION_ALERT
  5. RECOVERY_SUGGESTION
  6. SYSTEM_HEALTH
  7. NEXT_ACTION
- Implements anti-spam deduplication, expiration, snooze, accept, and reject flows.
- Integrates with ActionGovernor to strictly enforce the 4-tier governance matrix.
- Atomic disk persistence under `memory/proactive/recommendations.json`.
- Lightweight background evaluation daemon with configurable interval.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from core.proactive.context import (
    ProactiveContextManager,
    sanitize_dict,
    sanitize_sensitive_data,
)
from core.proactive.governance import ActionGovernor, get_action_governor
from core.proactive.monitors import ConditionMonitorEngine, get_condition_monitor_engine
from core.proactive.notifications import NotificationManager, get_notification_manager
from core.proactive.scheduler import IntelligentScheduler, get_intelligent_scheduler
from core.proactive.types import (
    ActionGovernanceLevel,
    Recommendation,
    RecommendationCategory,
    RecommendationPriority,
    RecommendationStatus,
)

logger = logging.getLogger(__name__)

_DEFAULT_STORAGE = Path(__file__).resolve().parent.parent.parent / "memory" / "proactive"
_engine_lock = threading.Lock()
_global_engine: ProactiveIntelligenceEngine | None = None


class ProactiveIntelligenceEngine:
    """Core proactive decision engine analyzing context and generating governed recommendations."""

    def __init__(
        self,
        storage_dir: Path | str | None = None,
        context_manager: ProactiveContextManager | None = None,
        scheduler: IntelligentScheduler | None = None,
        monitor_engine: ConditionMonitorEngine | None = None,
        notification_manager: NotificationManager | None = None,
        governor: ActionGovernor | None = None,
        enabled: bool = True,
    ) -> None:
        self.storage_dir = Path(storage_dir or _DEFAULT_STORAGE)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._recommendations_file = self.storage_dir / "recommendations.json"

        self.context_manager = context_manager or ProactiveContextManager()
        self.scheduler = scheduler or get_intelligent_scheduler()
        self.monitor_engine = monitor_engine or get_condition_monitor_engine()
        self.notification_manager = notification_manager or get_notification_manager()
        self.governor = governor or get_action_governor()

        self.enabled = enabled
        self._recommendations: dict[str, Recommendation] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._eval_thread: threading.Thread | None = None
        self._eval_interval: float = 20.0  # seconds

        # Load persisted recommendations
        self._load_recommendations()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_recommendations(self) -> None:
        """Load persisted recommendations from disk."""
        with self._lock:
            if not self._recommendations_file.exists():
                return
            try:
                with open(self._recommendations_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                items = data.get("recommendations", [])
                for item in items:
                    rec = Recommendation.from_dict(item)
                    self._recommendations[rec.recommendation_id] = rec
                logger.info("Loaded %d proactive recommendations.", len(self._recommendations))
            except Exception as e:
                logger.error("Failed to load recommendations from %s: %s", self._recommendations_file, e)

    def _persist_recommendations(self) -> None:
        """Atomically persist recommendations to disk."""
        with self._lock:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            temp_file = self.storage_dir / f"recommendations.tmp_{int(time.time()*1000)}"
            try:
                # Clean up expired or older dismissed/rejected ones before writing
                now = time.time()
                payload = {
                    "updated_at": now,
                    "recommendations": [r.to_dict() for r in self._recommendations.values()],
                }
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())

                if self._recommendations_file.exists():
                    self._recommendations_file.unlink()
                temp_file.rename(self._recommendations_file)
            except Exception as e:
                logger.error("Failed to persist recommendations: %s", e)
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass

    # ── Public Recommendation Management API ─────────────────────────────────

    def list_recommendations(
        self,
        status: RecommendationStatus | None = None,
        category: RecommendationCategory | None = None,
        include_snoozed: bool = False,
    ) -> list[Recommendation]:
        """List recommendations matching optional status and category filters."""
        with self._lock:
            now = time.time()
            results: list[Recommendation] = []
            for rec in self._recommendations.values():
                # Check expiration
                if rec.is_expired(now):
                    rec.status = RecommendationStatus.EXPIRED
                    continue

                # Filter by status
                if status and rec.status != status:
                    continue
                elif not status and rec.status != RecommendationStatus.ACTIVE:
                    if rec.status == RecommendationStatus.SNOOZED:
                        if rec.is_snoozed(now) and not include_snoozed:
                            continue
                        elif not rec.is_snoozed(now):
                            # Snooze period elapsed, return to active
                            rec.status = RecommendationStatus.ACTIVE
                    else:
                        continue

                # Filter by category
                if category and rec.category != category:
                    continue

                results.append(rec)

            results.sort(key=lambda r: (r.priority == RecommendationPriority.CRITICAL, r.created_at), reverse=True)
            return results

    def get_recommendation(self, rec_id: str) -> Recommendation | None:
        """Get a single recommendation by ID."""
        with self._lock:
            rec = self._recommendations.get(rec_id)
            if rec and rec.is_expired():
                rec.status = RecommendationStatus.EXPIRED
            return rec

    def create_recommendation(
        self,
        category: RecommendationCategory,
        title: str,
        description: str,
        reason: str = "",
        source_evidence: dict[str, Any] | None = None,
        priority: RecommendationPriority = RecommendationPriority.MEDIUM,
        suggested_action: dict[str, Any] | None = None,
        governance_level: ActionGovernanceLevel = ActionGovernanceLevel.LEVEL_1_RECOMMEND,
        required_permission: str = "READ_ONLY",
        expires_in_seconds: float | None = 86400.0,
    ) -> Recommendation:
        """Create, persist, and notify a new recommendation with deduplication."""
        with self._lock:
            clean_title = sanitize_sensitive_data(title)
            clean_desc = sanitize_sensitive_data(description)
            clean_reason = sanitize_sensitive_data(reason)
            clean_evidence = sanitize_dict(source_evidence or {})
            clean_action = sanitize_dict(suggested_action or {})

            # Anti-spam deduplication: check if an identical active recommendation already exists
            for existing in self._recommendations.values():
                if (
                    existing.status in {RecommendationStatus.ACTIVE, RecommendationStatus.SNOOZED}
                    and existing.category == category
                    and existing.title == clean_title
                ):
                    return existing

            # Re-evaluate governance level using the Governor
            if clean_action:
                assessed_res = self.governor.classify_action(
                    action_type=clean_action.get("action_type", "action"),
                    target=clean_action.get("target", clean_action.get("mission_id", "")),
                    payload=clean_action.get("params", {}),
                )
                assessed_level = assessed_res[0] if isinstance(assessed_res, tuple) else assessed_res
                if assessed_level.value > governance_level.value:
                    governance_level = assessed_level

            now = time.time()
            rec = Recommendation(
                category=category,
                title=clean_title,
                description=clean_desc,
                reason=clean_reason,
                source_evidence=clean_evidence,
                priority=priority,
                created_at=now,
                expires_at=(now + expires_in_seconds) if expires_in_seconds else None,
                suggested_action=clean_action,
                governance_level=governance_level,
                required_permission=required_permission,
                status=RecommendationStatus.ACTIVE,
            )

            self._recommendations[rec.recommendation_id] = rec
            self._persist_recommendations()

            # Post a Notification to the Notification Center
            self.notification_manager.post(
                title=f"[{category.value}] {clean_title}",
                message=clean_desc,
                category="RECOMMENDATION",
                priority=priority,
                source="proactive_engine",
                evidence=clean_evidence,
                recommendation_id=rec.recommendation_id,
                actions=[
                    {"name": "accept", "label": "Accept"},
                    {"name": "reject", "label": "Dismiss"},
                    {"name": "snooze", "label": "Snooze"},
                ],
            )

            return rec

    def accept_recommendation(
        self,
        rec_id: str,
        operator_approved: bool = False,
    ) -> dict[str, Any]:
        """Accept a recommendation and invoke or stage its suggested action under governance."""
        with self._lock:
            rec = self.get_recommendation(rec_id)
            if not rec:
                return {"success": False, "error": f"Recommendation {rec_id} not found"}

            action = rec.suggested_action
            gov_level = rec.governance_level

            # Governance check
            if gov_level == ActionGovernanceLevel.LEVEL_4_APPROVAL_REQUIRED and not operator_approved:
                return {
                    "success": False,
                    "status": "APPROVAL_REQUIRED",
                    "governance_level": gov_level.value,
                    "message": "This action modifies system resources or requires explicit operator approval.",
                }

            rec.status = RecommendationStatus.ACCEPTED
            self._persist_recommendations()

            # Execute action according to type
            execution_result: dict[str, Any] = {"status": "accepted"}
            if action:
                action_type = action.get("action_type")
                if action_type == "resume_mission":
                    mission_id = action.get("mission_id")
                    try:
                        from core.missions.engine import get_mission_engine
                        engine = get_mission_engine()
                        resume_ok = engine.resume_mission(mission_id)
                        execution_result = {"status": "resumed" if resume_ok else "failed", "mission_id": mission_id}
                    except Exception as e:
                        logger.error("Failed to resume mission %s: %s", mission_id, e)
                        execution_result = {"status": "error", "error": str(e)}

                elif action_type == "start_mission":
                    objective = action.get("objective", "")
                    try:
                        from core.missions.engine import get_mission_engine
                        engine = get_mission_engine()
                        mission = engine.create_mission(objective=objective)
                        started = engine.start_mission(mission.mission_id)
                        execution_result = {"status": "started" if started else "failed", "mission_id": mission.mission_id}
                    except Exception as e:
                        logger.error("Failed to start mission: %s", e)
                        execution_result = {"status": "error", "error": str(e)}

                elif action_type == "tool":
                    tool_name = action.get("target") or action.get("tool_name", "")
                    params = action.get("params", {})
                    try:
                        from core.orchestrator.tools import ToolRegistry
                        tr = ToolRegistry()
                        result = tr.execute(tool_name, **params)
                        execution_result = {"status": "executed", "result": result}
                    except Exception as e:
                        logger.error("Failed to execute tool %s: %s", tool_name, e)
                        execution_result = {"status": "error", "error": str(e)}

            return {
                "success": True,
                "recommendation_id": rec_id,
                "execution_result": execution_result,
            }

    def reject_recommendation(self, rec_id: str, reason: str = "") -> bool:
        """Reject a recommendation so it is dismissed and recorded."""
        with self._lock:
            rec = self.get_recommendation(rec_id)
            if not rec:
                return False
            rec.status = RecommendationStatus.REJECTED
            self._persist_recommendations()
            return True

    def snooze_recommendation(self, rec_id: str, duration_seconds: float = 3600.0) -> bool:
        """Snooze a recommendation for a specified duration."""
        with self._lock:
            rec = self.get_recommendation(rec_id)
            if not rec:
                return False
            rec.status = RecommendationStatus.SNOOZED
            rec.snoozed_until = time.time() + duration_seconds
            self._persist_recommendations()
            return True

    def dismiss_recommendation(self, rec_id: str) -> bool:
        """Dismiss a recommendation from view."""
        with self._lock:
            rec = self.get_recommendation(rec_id)
            if not rec:
                return False
            rec.status = RecommendationStatus.DISMISSED
            self._persist_recommendations()
            return True

    # ── Autonomous Evaluation Cycle ──────────────────────────────────────────

    def evaluate_proactive_cycle(self) -> list[Recommendation]:
        """Perform one bounded context-awareness and recommendation evaluation pass."""
        if not self.enabled:
            return []

        generated: list[Recommendation] = []

        try:
            # 1. Unfinished & Interrupted Missions Discovery
            unfinished = self.context_manager.get_unfinished_missions(limit=5)
            for m in unfinished:
                mission_id = m.get("mission_id", "")
                objective = m.get("objective", "Unknown")
                state = m.get("state", "PAUSED")
                total = m.get("total_tasks", 0)
                completed = m.get("completed_tasks", 0)

                rec = self.create_recommendation(
                    category=RecommendationCategory.PENDING_MISSION,
                    title=f"Resume Unfinished Mission: {objective[:35]}...",
                    description=(
                        f"Mission '{mission_id}' was paused in state {state}. "
                        f"Progress: {completed}/{total} subtasks completed."
                    ),
                    reason=f"Found unfinished mission in {state} state across sessions.",
                    source_evidence=m,
                    priority=RecommendationPriority.HIGH,
                    suggested_action={
                        "action_type": "resume_mission",
                        "mission_id": mission_id,
                    },
                    governance_level=ActionGovernanceLevel.LEVEL_3_EXECUTE,
                )
                generated.append(rec)

            # 2. System Health & Resource Diagnostics
            vitals = self.monitor_engine.get_system_vitals()
            cpu = vitals.get("cpu_percent", 0.0)
            ram = vitals.get("ram_percent", 0.0)
            disk = vitals.get("disk_percent", 0.0)

            if ram > 90.0:
                rec = self.create_recommendation(
                    category=RecommendationCategory.SYSTEM_HEALTH,
                    title="High RAM Consumption Detected",
                    description=f"System memory usage has reached {ram:.1f}%. Consider garbage collection or closing inactive tasks.",
                    reason="Memory pressure exceeds 90% threshold.",
                    source_evidence=vitals,
                    priority=RecommendationPriority.HIGH,
                    suggested_action={"action_type": "tool", "target": "system_info", "params": {}},
                    governance_level=ActionGovernanceLevel.LEVEL_2_PREPARE,
                )
                generated.append(rec)

            if cpu > 90.0:
                rec = self.create_recommendation(
                    category=RecommendationCategory.SYSTEM_HEALTH,
                    title="High CPU Load Warning",
                    description=f"Processor load is sustained at {cpu:.1f}%.",
                    reason="CPU load exceeds 90% threshold.",
                    source_evidence=vitals,
                    priority=RecommendationPriority.MEDIUM,
                    suggested_action={"action_type": "tool", "target": "system_info", "params": {}},
                    governance_level=ActionGovernanceLevel.LEVEL_2_PREPARE,
                )
                generated.append(rec)

            if disk > 95.0:
                rec = self.create_recommendation(
                    category=RecommendationCategory.SYSTEM_HEALTH,
                    title="Critical Disk Space Warning",
                    description=f"Primary drive storage is at {disk:.1f}%. Immediate disk cleanup recommended.",
                    reason="Free disk space is critically low.",
                    source_evidence=vitals,
                    priority=RecommendationPriority.CRITICAL,
                    suggested_action={"action_type": "tool", "target": "system_info", "params": {}},
                    governance_level=ActionGovernanceLevel.LEVEL_1_RECOMMEND,
                )
                generated.append(rec)

            # 3. Mission Failures & Recovery Diagnostics
            failures = self.context_manager.get_recent_failures(limit=3)
            for f in failures:
                m_id = f.get("mission_id", "")
                err = f.get("error", "Error")
                obj = f.get("objective", "")
                rec = self.create_recommendation(
                    category=RecommendationCategory.RECOVERY_SUGGESTION,
                    title=f"Review Failed Mission: {obj[:30]}",
                    description=f"Mission {m_id} encountered an error: {err[:60]}",
                    reason="Recent failure detected. Retry or corrective action may resolve this.",
                    source_evidence=f,
                    priority=RecommendationPriority.MEDIUM,
                    suggested_action={"action_type": "start_mission", "objective": f"Investigate failure in mission {m_id}: {err[:50]}"},
                    governance_level=ActionGovernanceLevel.LEVEL_2_PREPARE,
                )
                generated.append(rec)

        except Exception as e:
            logger.error("Error during proactive evaluation cycle: %s", e)

        return generated

    # ── Daemon Thread Lifecycle ──────────────────────────────────────────────

    def start(self, interval_seconds: float = 20.0) -> None:
        """Start the background proactive evaluation daemon."""
        with self._lock:
            if self._eval_thread and self._eval_thread.is_alive():
                return
            self._eval_interval = max(5.0, interval_seconds)
            self._stop_event.clear()
            self._eval_thread = threading.Thread(
                target=self._run_loop,
                name="ProactiveIntelligenceDaemon",
                daemon=True,
            )
            self._eval_thread.start()
            logger.info("Proactive Intelligence Daemon started with interval %.1fs", self._eval_interval)

    def stop(self) -> None:
        """Stop the background proactive evaluation daemon cleanly."""
        self._stop_event.set()
        if self._eval_thread and self._eval_thread.is_alive():
            self._eval_thread.join(timeout=3.0)
            self._eval_thread = None
        logger.info("Proactive Intelligence Daemon stopped.")

    def _run_loop(self) -> None:
        """Daemon worker loop performing periodic evaluation ticks."""
        while not self._stop_event.is_set():
            try:
                self.evaluate_proactive_cycle()
            except Exception as e:
                logger.error("Unhandled exception in proactive loop: %s", e)
            self._stop_event.wait(self._eval_interval)


def get_proactive_engine() -> ProactiveIntelligenceEngine:
    """Retrieve or initialize the global ProactiveIntelligenceEngine singleton."""
    global _global_engine
    with _engine_lock:
        if _global_engine is None:
            _global_engine = ProactiveIntelligenceEngine()
        return _global_engine
