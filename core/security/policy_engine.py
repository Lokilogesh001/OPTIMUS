"""core/security/policy_engine.py — Authoritative Central Policy Engine for OPTIMUS.

Enforces:
- Single-pass, deterministic policy evaluation before any tool, sandbox, or OS execution.
- Immutable security deny invariants that can NEVER be overridden by operator approval.
- Operation-bound, expiring, single-use, atomically consumed ApprovalTokens.
- Strict agent capability boundaries preventing dynamic tool expansion.
- Integration with PermissionProfile (LOW, MODERATE, ADVANCED) and WindowsRuntimePolicy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.orchestrator.tools import _TIER_RANK, CapabilityTier
from core.security.permissions import PermissionManager, PermissionProfile

logger = logging.getLogger("optimus.security.policy_engine")


@dataclass(frozen=True)
class ApprovalToken:
    """Cryptographic, single-use token authorizing an explicit privileged operation."""
    token_id: str
    mission_id: str
    subtask_id: str
    operator_id: str
    nonce: str
    expires_at: float
    canonical_hash: str  # SHA-256 of tool_name + canonical arguments

    def is_expired(self) -> bool:
        """Check if token has passed its expiration timestamp."""
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "mission_id": self.mission_id,
            "subtask_id": self.subtask_id,
            "operator_id": self.operator_id,
            "nonce": self.nonce,
            "expires_at": self.expires_at,
            "canonical_hash": self.canonical_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalToken:
        return cls(
            token_id=str(data["token_id"]),
            mission_id=str(data.get("mission_id", "")),
            subtask_id=str(data.get("subtask_id", "")),
            operator_id=str(data.get("operator_id", "operator")),
            nonce=str(data["nonce"]),
            expires_at=float(data["expires_at"]),
            canonical_hash=str(data["canonical_hash"]),
        )


@dataclass
class OperationRequest:
    """Standardized request envelope evaluated before any tool or OS execution."""
    tool_name: str
    agent_name: str
    arguments: dict[str, Any]
    allowed_tools: list[str] | None = None
    allowed_tiers: list[CapabilityTier] | None = None
    approval_token: ApprovalToken | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyVerdict:
    """Outcome of an authoritative policy evaluation."""
    is_authorized: bool
    requires_approval: bool = False
    reason: str = ""
    approval_challenge: dict[str, Any] | None = None


TOOL_ALIASES: dict[str, str] = {
    "web_search": "search_web",
    "python_env": "python_version",
    "ollama_check": "check_ollama",
    "disk_space": "disk_status",
    "git_status": "dev_git_status",
}


def compute_canonical_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 digest over normalized tool name and arguments."""
    norm_tool = str(tool_name or "").strip().lower()
    # Normalize nested dicts/lists into deterministic JSON string
    norm_payload = json.dumps({"tool": norm_tool, "args": arguments}, sort_keys=True, default=str)
    return hashlib.sha256(norm_payload.encode("utf-8")).hexdigest()


# ── Immutable Security Deny Invariants ─────────────────────────────────────────
# These destructive patterns CAN NEVER be overridden even with human approval tokens.

IMMUTABLE_DENY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Disk partition formatting", re.compile(r"(format\s+[a-zA-Z]:|mkfs|fdisk|diskpart)", re.IGNORECASE)),
    ("Recursive root deletion", re.compile(r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+([/~]|\$HOME)", re.IGNORECASE)),
    ("Windows system root deletion", re.compile(r"rmdir\s+/[sS]\s+/[qQ]\s+[cC]:\\", re.IGNORECASE)),
    ("Raw block device overwrite", re.compile(r"dd\s+if=.*of=(/dev/|\\\\.\\)", re.IGNORECASE)),
    ("Fork bomb", re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:", re.IGNORECASE)),
    ("System shutdown", re.compile(r"(shutdown\s+/[sS]|shutdown\s+-h|init\s+0)", re.IGNORECASE)),
    ("Firewall disabling", re.compile(r"(netsh\s+advfirewall\s+set|ufw\s+disable)", re.IGNORECASE)),
    ("Windows Defender tampering", re.compile(r"Set-MpPreference\s+-Disable", re.IGNORECASE)),
    ("Database destruction", re.compile(r"(drop\s+database|drop\s+table|truncate\s+table)", re.IGNORECASE)),
]

# Actions that are dangerous/privileged by default but CAN be authorized with an ApprovalToken
PRIVILEGED_APPROVAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Privilege escalation", re.compile(r"(sudo\s+|su\s+-|runas\s+/user:)", re.IGNORECASE)),
    ("Credential dump", re.compile(r"(dump_credentials|mimikatz|sekurlsa|samdump)", re.IGNORECASE)),
    ("Personal communication mutation", re.compile(r"^(send_email|delete_email|delete_message|create_calendar_event|update_calendar_event|delete_calendar_event|send_whatsapp_message|purge_personal_store|wipe_personal_data|wipe_all_data|disconnect_account|revoke_account|delete_account)$", re.IGNORECASE)),
    ("Self-improvement promotion", re.compile(r"^(promote_skill|install_skill|self_improvement_.*)$", re.IGNORECASE)),
]


class PolicyEngine:
    """Central authority governing tool execution, capability boundaries, and approval tokens."""

    _instance: PolicyEngine | None = None

    def __init__(
        self,
        permission_manager: PermissionManager | None = None,
        journal_path: Path | str | None = None,
    ) -> None:
        self.permission_manager = permission_manager or PermissionManager.instance()
        self._consumed_nonces: set[str] = set()
        self._active_tokens: dict[str, ApprovalToken] = {}
        self._lock = threading.RLock()
        if journal_path:
            self.journal_path = Path(journal_path)
        else:
            self.journal_path = Path.cwd() / "memory" / "security" / "consumed_nonces.journal"
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_lock_path = self.journal_path.with_name(".nonces.lock")
        self._load_journal()

    def _load_journal(self) -> None:
        """Hydrate consumed nonces from append-only journal on disk."""
        if self.journal_path.is_file():
            try:
                for line in self.journal_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        parts = line.split(":", 1)
                        if parts:
                            self._consumed_nonces.add(parts[0])
            except Exception as ex:
                logger.warning("Failed loading nonces journal: %s", ex)

    @classmethod
    def instance(cls, journal_path: Path | str | None = None) -> PolicyEngine:
        """Singleton accessor."""
        if cls._instance is None:
            cls._instance = cls(journal_path=journal_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Testing hook to reset singleton state."""
        cls._instance = None

    def create_approval_token(
        self,
        mission_id: str,
        subtask_id: str,
        operator_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        ttl_seconds: float = 300.0,
    ) -> ApprovalToken:
        """Generate a cryptographically-bound, single-use approval token."""
        with self._lock:
            # Enforce non-empty human approver identity
            if not operator_id or operator_id.lower() in ("llm", "ai", "model", "assistant", "agent"):
                raise ValueError("SECURITY_VIOLATION: Automated LLM output cannot provide human approval.")

            canonical_hash = compute_canonical_hash(tool_name, arguments)
            token = ApprovalToken(
                token_id=f"tok_{secrets.token_hex(6)}",
                mission_id=mission_id,
                subtask_id=subtask_id,
                operator_id=operator_id,
                nonce=secrets.token_hex(16),
                expires_at=time.time() + ttl_seconds,
                canonical_hash=canonical_hash,
            )
            self._active_tokens[token.token_id] = token
            return token

    def get_token(self, token_id: str) -> ApprovalToken | None:
        """Retrieve an active approval token by token ID."""
        with self._lock:
            token = self._active_tokens.get(token_id)
            if token and token.is_expired():
                self._active_tokens.pop(token_id, None)
                return None
            return token

    def is_nonce_consumed(self, nonce: str) -> bool:
        """Check if a token nonce has already been consumed."""
        with self._lock:
            if nonce in self._consumed_nonces:
                return True
            self._load_journal()
            return nonce in self._consumed_nonces

    def _consume_nonce(self, token: ApprovalToken) -> None:
        """Atomically persist consumed nonce to journal under inter-process lock."""
        with self._lock:
            self._consumed_nonces.add(token.nonce)
            try:
                with open(self.journal_lock_path, "a+b") as lf:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            lf.seek(0)
                            msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                        with open(self.journal_path, "a", encoding="utf-8") as jf:
                            jf.write(f"{token.nonce}:{token.token_id}:{int(time.time())}\n")
                            jf.flush()
                    finally:
                        try:
                            if os.name == "nt":
                                import msvcrt
                                lf.seek(0)
                                msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl
                                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                        except OSError:
                            pass
            except Exception as ex:
                logger.warning("Failed writing consumed nonce to journal: %s", ex)

    def evaluate_operation(self, request: OperationRequest) -> PolicyVerdict:
        """Authoritatively evaluate an OperationRequest through the 4-step security pipeline.

        Pipeline:
        1. Immutable Mandatory Deny Rules (Zero override, even with human approval).
        2. Agent Capability Boundaries (Prevent dynamic tool expansion).
        3. Approval Token Validation (Single-use, non-expired, parameter-bound).
        4. Permission Profile & System Scope (LOW / MODERATE / ADVANCED + Protected Paths).
        """
        tool_name = str(request.tool_name or "").strip().lower()
        arg_str = json.dumps(request.arguments, default=str)

        with self._lock:
            # ── 1. Immutable Mandatory Deny Rules ──────────────────────────────
            for rule_name, pattern in IMMUTABLE_DENY_PATTERNS:
                if pattern.search(arg_str) or pattern.search(tool_name):
                    return PolicyVerdict(
                        is_authorized=False,
                        requires_approval=False,
                        reason=f"IMMUTABLE_SECURITY_VIOLATION: Operation matches prohibited rule '{rule_name}'. Cannot be authorized.",
                    )

            # ── 2. Agent Capability Boundaries ────────────────────────────────
            # Prevent dynamic tool expansion / least-privilege violation (DEF-03)
            canonical_tool = TOOL_ALIASES.get(tool_name.lower(), tool_name.lower())
            if request.allowed_tools is not None:
                allowed_set = {t.lower() for t in request.allowed_tools}
                # Also recognize aliases in allowed_set
                canonical_allowed = {TOOL_ALIASES.get(t, t) for t in allowed_set}
                if tool_name.lower() not in allowed_set and canonical_tool not in canonical_allowed:
                    return PolicyVerdict(
                        is_authorized=False,
                        requires_approval=False,
                        reason=(
                            f"CAPABILITY_VIOLATION: Agent '{request.agent_name}' is not permitted to use tool '{tool_name}'. "
                            f"Allowed tools: {request.allowed_tools}"
                        ),
                    )

            # ── 3. Approval Token Validation (If Supplied) ────────────────────
            token = request.approval_token
            if token is not None:
                if token.is_expired():
                    return PolicyVerdict(
                        is_authorized=False,
                        requires_approval=True,
                        reason="APPROVAL_TOKEN_EXPIRED: The supplied approval token has expired.",
                    )

                if self.is_nonce_consumed(token.nonce):
                    return PolicyVerdict(
                        is_authorized=False,
                        requires_approval=True,
                        reason="APPROVAL_TOKEN_REPLAY_DETECTED: The supplied approval token has already been consumed.",
                    )

                expected_hash = compute_canonical_hash(tool_name, request.arguments)
                if token.canonical_hash != expected_hash:
                    return PolicyVerdict(
                        is_authorized=False,
                        requires_approval=True,
                        reason="APPROVAL_TOKEN_HASH_MISMATCH: The approval token does not match the requested tool arguments.",
                    )

                # Atomically consume token
                self._consume_nonce(token)
                logger.info(
                    "ApprovalToken '%s' atomically consumed for tool '%s' by agent '%s'.",
                    token.token_id, tool_name, request.agent_name,
                )
                return PolicyVerdict(
                    is_authorized=True,
                    requires_approval=False,
                    reason=f"Authorized via valid single-use approval token '{token.token_id}'.",
                )

            # ── 4. Privileged Pattern Gate (Requires Token If No Token Present) ─
            for rule_name, pattern in PRIVILEGED_APPROVAL_PATTERNS:
                if pattern.search(arg_str) or pattern.search(tool_name):
                    challenge = {
                        "tool_name": tool_name,
                        "arguments": request.arguments,
                        "rule_name": rule_name,
                        "canonical_hash": compute_canonical_hash(tool_name, request.arguments),
                    }
                    return PolicyVerdict(
                        is_authorized=False,
                        requires_approval=True,
                        reason=f"MANDATORY_APPROVAL_REQUIRED: Operation matches privileged security rule '{rule_name}'. Operator approval token required.",
                        approval_challenge=challenge,
                    )

            # ── 5. Permission Profile & System Scope Evaluation ───────────────
            profile = self.permission_manager.get_profile()

            # Profile LOW: Read-only operations only
            if profile == PermissionProfile.LOW and request.allowed_tiers:
                max_tier = max(_TIER_RANK.get(t, 0) for t in request.allowed_tiers)
                if max_tier > _TIER_RANK[CapabilityTier.READ_ONLY]:
                    return PolicyVerdict(
                        is_authorized=False,
                        requires_approval=True,
                        reason=f"PROFILE_RESTRICTION: Profile LOW requires operator approval for mutating tool '{tool_name}'.",
                    )

            # Protected Windows paths inspection
            target_path_str = str(
                request.arguments.get("path")
                or request.arguments.get("target_path")
                or request.arguments.get("dst_path")
                or ""
            )
            if target_path_str:
                try:
                    from core.windows_runtime.policy import WindowsRuntimePolicy
                    if WindowsRuntimePolicy.is_protected_path(target_path_str):
                        challenge = {
                            "tool_name": tool_name,
                            "path": target_path_str,
                            "canonical_hash": compute_canonical_hash(tool_name, request.arguments),
                        }
                        return PolicyVerdict(
                            is_authorized=False,
                            requires_approval=True,
                            reason=f"PROTECTED_PATH_RESTRICTION: Operation on protected Windows system path '{target_path_str}' requires explicit approval.",
                            approval_challenge=challenge,
                        )
                except ImportError:
                    pass

            return PolicyVerdict(
                is_authorized=True,
                requires_approval=False,
                reason="Authorized by PolicyEngine under active profile.",
            )

    def evaluate(self, request: OperationRequest) -> PolicyVerdict:
        """Convenience alias for evaluate_operation."""
        return self.evaluate_operation(request)



def get_policy_engine() -> PolicyEngine:
    """Convenience accessor for central PolicyEngine instance."""
    return PolicyEngine.instance()
