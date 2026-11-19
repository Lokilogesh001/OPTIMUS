"""core/workflows/approval.py — Human Approval System & Authorization Gates for OPTIMUS.

Implements:
- Human authorization gates for destructive, sensitive, or high-risk actions.
- Explicit approval lifecycle: PENDING -> APPROVED | REJECTED | EXPIRED.
- Cryptographically verifiable audit trail.
- Strict constraint: Model output is NEVER accepted as human authorization.
"""

from __future__ import annotations

import threading
import time
import uuid

from core.workflows.types import (
    ApprovalDecision,
    ApprovalStatus,
)


class ApprovalManager:
    """Manages authorization requests, pending gates, and human approval audit logs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._approvals: dict[str, ApprovalDecision] = {}

    def request_approval(
        self,
        workflow_id: str,
        execution_id: str,
        step_id: str,
        reason: str,
        required_role: str = "admin",
        timeout_seconds: float = 3600.0,
    ) -> ApprovalDecision:
        """Create a new human authorization gate."""
        with self._lock:
            approval_id = f"appr_{uuid.uuid4().hex[:8]}"
            now = time.time()
            decision = ApprovalDecision(
                approval_id=approval_id,
                workflow_id=workflow_id,
                execution_id=execution_id,
                step_id=step_id,
                reason=reason,
                required_role=required_role,
                status=ApprovalStatus.PENDING,
                requested_at=now,
                expires_at=now + timeout_seconds,
            )
            self._approvals[approval_id] = decision
            return decision

    def approve(
        self,
        approval_id: str,
        approver: str,
        reason: str = "Approved by operator.",
    ) -> ApprovalDecision:
        """Grant human authorization for a pending approval gate."""
        with self._lock:
            # Enforce non-empty human approver identity (no automated agent approvals)
            if not approver or any(k in approver.lower() for k in ("llm", "ai", "model", "assistant", "agent")):
                raise ValueError("SECURITY_VIOLATION: Automated LLM output cannot provide human approval.")

            decision = self._approvals.get(approval_id)
            if not decision:
                raise KeyError(f"Approval request '{approval_id}' not found.")

            if decision.status == ApprovalStatus.EXPIRED or time.time() > decision.expires_at:
                decision.status = ApprovalStatus.EXPIRED
                raise TimeoutError(f"Approval request '{approval_id}' has expired.")

            if decision.status != ApprovalStatus.PENDING:
                raise ValueError(f"Approval request '{approval_id}' is already {decision.status.value}.")

            decision.status = ApprovalStatus.APPROVED
            decision.approver = approver
            decision.decision_reason = reason
            decision.resolved_at = time.time()
            return decision

    def reject(
        self,
        approval_id: str,
        approver: str,
        reason: str = "Rejected by operator.",
    ) -> ApprovalDecision:
        """Deny authorization for a pending approval gate."""
        with self._lock:
            decision = self._approvals.get(approval_id)
            if not decision:
                raise KeyError(f"Approval request '{approval_id}' not found.")

            if decision.status != ApprovalStatus.PENDING:
                raise ValueError(f"Approval request '{approval_id}' is already {decision.status.value}.")

            decision.status = ApprovalStatus.REJECTED
            decision.approver = approver
            decision.decision_reason = reason
            decision.resolved_at = time.time()
            return decision

    def get_approval(self, approval_id: str) -> ApprovalDecision | None:
        """Retrieve an approval record by identifier."""
        with self._lock:
            return self._approvals.get(approval_id)

    def create_approval_token(
        self,
        approval_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        ttl_seconds: float = 300.0,
    ) -> Any:
        """Generate a cryptographically-bound ApprovalToken for an approved request."""
        with self._lock:
            decision = self._approvals.get(approval_id)
            if not decision:
                raise KeyError(f"Approval request '{approval_id}' not found.")
            if decision.status != ApprovalStatus.APPROVED:
                raise ValueError(
                    f"Approval request '{approval_id}' is not in APPROVED status (status: {decision.status.value})."
                )
            if not decision.approver:
                raise ValueError(f"Approval request '{approval_id}' lacks valid approver identity.")

            from core.security.policy_engine import PolicyEngine
            return PolicyEngine.instance().create_approval_token(
                mission_id=decision.execution_id or decision.workflow_id,
                subtask_id=decision.step_id,
                operator_id=decision.approver,
                tool_name=tool_name,
                arguments=arguments,
                ttl_seconds=ttl_seconds,
            )

    def list_approvals(self, execution_id: str | None = None) -> list[ApprovalDecision]:
        """List all approvals, optionally filtered by execution ID."""
        with self._lock:
            if execution_id:
                return [a for a in self._approvals.values() if a.execution_id == execution_id]
            return list(self._approvals.values())

    def list_pending(self, execution_id: str | None = None) -> list[ApprovalDecision]:
        """Return currently pending approval gates."""
        with self._lock:
            now = time.time()
            pending = []
            for a in self._approvals.values():
                if a.status == ApprovalStatus.PENDING:
                    if now > a.expires_at:
                        a.status = ApprovalStatus.EXPIRED
                    elif not execution_id or a.execution_id == execution_id:
                        pending.append(a)
            return pending

    def check_expirations(self) -> list[ApprovalDecision]:
        """Evaluate pending approvals and mark expired entries."""
        with self._lock:
            now = time.time()
            expired = []
            for a in self._approvals.values():
                if a.status == ApprovalStatus.PENDING and now > a.expires_at:
                    a.status = ApprovalStatus.EXPIRED
                    expired.append(a)
            return expired
