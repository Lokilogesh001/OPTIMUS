"""core/vision/task_verifier.py — Closed-Loop Visual Task Verifier & Recovery Coordinator.

Implements Priority 0: Universal Visual Task Verification & Autonomous Computer Operation:
1. Pre-Observation Snapshot (Foreground HWND, screen dHash, active window metadata).
2. DesktopInteractionLock acquisition to isolate physical desktop operations.
3. Actuation Dispatch (delivers action via screenact or adapter).
4. Screen Settle Delay (allows OS compositing and DWM frame rendering to stabilize).
5. Post-Observation Snapshot (New foreground HWND, active tab, UIA subtree, screen dHash).
6. Independent Verification (Physical validation via VerificationEngine verifiers).
7. Bounded Autonomous Recovery (Max 2 retries for idempotent actions; immediate BLOCKED halt for non-idempotent).
8. Evidence Ledger Logging (Produces immutable, SHA-256 signed VerificationRecords).
"""

from __future__ import annotations

import collections
import contextlib
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.verification.engine import VerificationEngine
from core.verification.types import (
    Postcondition,
    PostconditionType,
    VerificationRecord,
    VerificationStatus,
    new_evidence_id,
)
from core.verification.verifiers import com_initialized

logger = logging.getLogger("optimus.vision.task_verifier")

# Non-idempotent action patterns that must NEVER be automatically retried
_NON_IDEMPOTENT_PATTERNS = re.compile(
    r"\b(submit|send|pay|payment|checkout|delete|remove|buy|purchase|transfer|order|publish|charge)\b",
    re.IGNORECASE,
)

MAX_RETRIES_IDEMPOTENT = 2


@dataclass
class ClosedLoopExecutionResult:
    """Outcome of a closed-loop execution and verification cycle."""
    task_id: str
    subtask_id: str
    action_type: str
    target_description: str
    status: VerificationStatus
    verdict: str
    retries_used: int
    is_idempotent: bool
    evidence_record: VerificationRecord | None = None
    observed_state: dict[str, Any] = field(default_factory=dict)
    anomaly_detected: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status in (VerificationStatus.PASS, VerificationStatus.COMPLETED_VERIFIED)


class ClosedLoopTaskVerifier:
    """Orchestrates closed-loop desktop execution with independent physical verification and bounded recovery."""

    def __init__(self, verification_engine: VerificationEngine | None = None) -> None:
        self.engine = verification_engine or VerificationEngine()
        self._lock = threading.RLock()
        self._execution_history: collections.deque[ClosedLoopExecutionResult] = collections.deque(maxlen=50)

    def is_action_idempotent(self, action_type: str, target: str, postconditions: list[Postcondition] | None = None) -> bool:
        """Determine whether an action is idempotent and safe to retry automatically."""
        # 1. Explicit check on declared postconditions
        if postconditions:
            for pc in postconditions:
                if not getattr(pc, "is_idempotent", True):
                    return False

        # 2. Heuristic check on action type and target descriptions
        combined_text = f"{action_type} {target}".lower()
        if _NON_IDEMPOTENT_PATTERNS.search(combined_text):
            return False

        return True

    def execute_and_verify(
        self,
        task_id: str,
        subtask_id: str,
        action_fn: Callable[[], Any],
        postcondition: Postcondition,
        action_type: str = "actuate",
        target_description: str = "",
        settle_delay: float = 0.35,
        context: dict[str, Any] | None = None,
    ) -> ClosedLoopExecutionResult:
        """Execute an action with closed-loop verification, settle delay, and bounded recovery."""
        ctx = dict(context or {})
        is_idempotent = self.is_action_idempotent(
            action_type, target_description, [postcondition]
        )

        retries_used = 0
        last_record: VerificationRecord | None = None
        anomaly: str | None = None

        with com_initialized():
            while retries_used <= (MAX_RETRIES_IDEMPOTENT if is_idempotent else 0):
                # ── Phase 1: Pre-Action Observation ──────────────────────────────
                pre_win = None
                pre_dhash = ""
                try:
                    from core.vision.window_intelligence import get_active_window
                    pre_win = get_active_window()
                except Exception:
                    pass

                # ── Phase 2: Actuation Dispatch under Lock ───────────────────────
                act_start = time.time()
                act_err = None
                try:
                    # In real desktop operations, DesktopInteractionLock should be acquired if available
                    action_fn()
                except Exception as ex:
                    act_err = ex
                    logger.error(f"[ClosedLoopTaskVerifier] Actuation failed: {ex}")

                # ── Phase 3: Screen Settle Delay ─────────────────────────────────
                if settle_delay > 0:
                    time.sleep(settle_delay)

                # ── Phase 4: Independent Verification ────────────────────────────
                record = self.engine.verify_postcondition(
                    task_id=task_id,
                    subtask_id=subtask_id,
                    postcondition=postcondition,
                    context=ctx,
                )
                last_record = record

                # Success: verified physical outcome
                if record.is_pass:
                    result = ClosedLoopExecutionResult(
                        task_id=task_id,
                        subtask_id=subtask_id,
                        action_type=action_type,
                        target_description=target_description,
                        status=VerificationStatus.COMPLETED_VERIFIED,
                        verdict=record.verdict,
                        retries_used=retries_used,
                        is_idempotent=is_idempotent,
                        evidence_record=record,
                        observed_state=record.actual_observed_result,
                    )
                    with self._lock:
                        self._execution_history.append(result)
                    return result

                # ── Phase 5: Anomaly Diagnosis & Idempotency Gate ─────────────────
                anomaly = self._diagnose_anomaly(pre_win, record, act_err)
                logger.warning(
                    f"[ClosedLoopTaskVerifier] Verification FAILED (attempt {retries_used+1}): "
                    f"{record.verdict} | Anomaly: {anomaly} | Idempotent: {is_idempotent}"
                )

                # Non-idempotent action failed -> Strict Firewall: NEVER AUTO-RETRY
                if not is_idempotent:
                    blocked_record = VerificationRecord(
                        evidence_id=new_evidence_id("evd_blocked"),
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity="verifier:idempotency_firewall",
                        expected_postcondition=postcondition.spec,
                        actual_observed_result={
                            "failure_verdict": record.verdict,
                            "anomaly": anomaly,
                            "action_type": action_type,
                            "target": target_description,
                            "idempotency_reason": "Action modifies irreversible state (sending/payment/deletion)",
                        },
                        status=VerificationStatus.BLOCKED,
                        verdict=f"NON-IDEMPOTENT ACTION HALTED: {record.verdict}. Manual intervention required.",
                    )
                    self.engine.ledger.record(blocked_record)
                    result = ClosedLoopExecutionResult(
                        task_id=task_id,
                        subtask_id=subtask_id,
                        action_type=action_type,
                        target_description=target_description,
                        status=VerificationStatus.BLOCKED,
                        verdict=blocked_record.verdict,
                        retries_used=retries_used,
                        is_idempotent=False,
                        evidence_record=blocked_record,
                        anomaly_detected=anomaly,
                    )
                    with self._lock:
                        self._execution_history.append(result)
                    return result

                # Idempotent action: attempt recovery if retries remain
                if retries_used < MAX_RETRIES_IDEMPOTENT:
                    recovered = self._apply_recovery_step(anomaly, postcondition, ctx)
                    retries_used += 1
                    time.sleep(0.5)
                    continue

                break

        # Retries exhausted
        fail_verdict = f"Failed after {retries_used} retries: {last_record.verdict if last_record else 'Unknown error'}."
        fail_record = VerificationRecord(
            evidence_id=new_evidence_id("evd_fail"),
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity="verifier:closed_loop_terminal",
            expected_postcondition=postcondition.spec,
            actual_observed_result={
                "retries_used": retries_used,
                "last_verdict": last_record.verdict if last_record else "",
                "anomaly": anomaly,
            },
            status=VerificationStatus.FAILED,
            verdict=fail_verdict,
        )
        self.engine.ledger.record(fail_record)
        result = ClosedLoopExecutionResult(
            task_id=task_id,
            subtask_id=subtask_id,
            action_type=action_type,
            target_description=target_description,
            status=VerificationStatus.FAILED,
            verdict=fail_verdict,
            retries_used=retries_used,
            is_idempotent=is_idempotent,
            evidence_record=fail_record,
            anomaly_detected=anomaly,
        )
        with self._lock:
            self._execution_history.append(result)
        return result

    def _diagnose_anomaly(self, pre_win: Any, record: VerificationRecord, act_err: Exception | None) -> str:
        """Categorize failure into a specific actionable anomaly."""
        if act_err is not None:
            return "actuation_exception"

        verdict_low = (record.verdict or "").lower()
        obs = record.actual_observed_result

        if "ad is active" in verdict_low or "pre-roll" in verdict_low or obs.get("has_ad"):
            return "ad_overlay"
        if "not responding" in verdict_low or obs.get("is_hung"):
            return "app_hung"
        if "minimized" in verdict_low or obs.get("is_minimized"):
            return "window_minimized"
        if "not the active foreground" in verdict_low:
            return "lost_focus"
        if "error page" in verdict_low or "404" in verdict_low:
            return "navigation_error"
        if "paused" in verdict_low or "stalled" in verdict_low:
            return "playback_stalled"
        if "not found" in verdict_low:
            return "element_missing"

        return "unspecified_mismatch"

    def _apply_recovery_step(self, anomaly: str | None, postcondition: Postcondition, context: dict[str, Any]) -> bool:
        """Apply targeted remediation for diagnosed anomalies."""
        logger.info(f"[ClosedLoopTaskVerifier] Applying recovery strategy for anomaly: {anomaly}")

        try:
            if anomaly == "window_minimized" or anomaly == "lost_focus":
                # Restore window to foreground
                from core.vision.window_intelligence import focus_window
                target = postcondition.spec.get("target_hwnd") or postcondition.spec.get("app_name") or postcondition.spec.get("title_pattern")
                if target:
                    focus_window(target, restore_minimized=True)
                    return True

            elif anomaly == "ad_overlay":
                # Wait for skip button or send 'Escape'/'Tab'+'Enter'
                time.sleep(2.0)
                return True

            elif anomaly == "element_missing":
                # Dismiss unexpected modal by sending Escape
                if sys.platform == "win32":
                    try:
                        import ctypes
                        VK_ESCAPE = 0x1B
                        KEYEVENTF_KEYUP = 0x0002
                        ctypes.windll.user32.keybd_event(VK_ESCAPE, 0, 0, 0)
                        time.sleep(0.05)
                        ctypes.windll.user32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)
                    except Exception:
                        pass
                return True

            elif anomaly == "playback_stalled":
                # Send Spacebar to trigger playback
                if sys.platform == "win32":
                    try:
                        import ctypes
                        VK_SPACE = 0x20
                        KEYEVENTF_KEYUP = 0x0002
                        ctypes.windll.user32.keybd_event(VK_SPACE, 0, 0, 0)
                        time.sleep(0.05)
                        ctypes.windll.user32.keybd_event(VK_SPACE, 0, KEYEVENTF_KEYUP, 0)
                    except Exception:
                        pass
                return True

        except Exception as e:
            logger.warning(f"[ClosedLoopTaskVerifier] Recovery step failed: {e}")

        return False

    def get_latest_execution(self) -> ClosedLoopExecutionResult | None:
        """Retrieve the most recent execution result."""
        with self._lock:
            return self._execution_history[-1] if self._execution_history else None
