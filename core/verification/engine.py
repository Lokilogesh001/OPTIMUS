"""core/verification/engine.py — Central Verification Engine for OPTIMUS.

Coordinates pluggable physical verifiers, automated postcondition inference,
tamper-evident evidence ledger persistence, and independent postcondition validation.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from core.verification.ledger import EvidenceLedger
from core.verification.types import (
    Postcondition,
    PostconditionType,
    VerificationRecord,
    VerificationStatus,
    new_evidence_id,
)
from core.verification.verifiers import (
    BaseVerifier,
    BrowserPageVerifier,
    CommandVerifier,
    DataVerifier,
    DesktopWindowVerifier,
    FilesystemVerifier,
    MediaPlaybackVerifier,
    ProcessVerifier,
    UIControlVerifier,
)


class VerificationEngine:
    """Central engine managing pluggable verifiers and enforcing independent physical validation."""

    def __init__(self, storage_root: Path | str | None = None) -> None:
        if storage_root is None:
            # Default to project root / memory / verification
            default_root = Path(__file__).resolve().parent.parent.parent / "memory" / "verification"
            self.storage_root = default_root
        else:
            self.storage_root = Path(storage_root)

        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.ledger = EvidenceLedger(self.storage_root)
        self._lock = threading.RLock()
        self._verifiers: dict[PostconditionType, BaseVerifier] = {}

        # Register default built-in verifiers
        self._register_default_verifiers()

    def _register_default_verifiers(self) -> None:
        """Register the 8 standard built-in verifiers."""
        self.register_verifier(FilesystemVerifier())
        self.register_verifier(ProcessVerifier())
        self.register_verifier(DataVerifier())
        self.register_verifier(CommandVerifier())
        self.register_verifier(DesktopWindowVerifier())
        self.register_verifier(BrowserPageVerifier())
        self.register_verifier(MediaPlaybackVerifier())
        self.register_verifier(UIControlVerifier())

    def register_verifier(self, verifier: BaseVerifier) -> None:
        """Register or override a pluggable verifier for a postcondition type."""
        with self._lock:
            self._verifiers[verifier.handled_type] = verifier

    def get_verifier(self, postcondition_type: PostconditionType) -> BaseVerifier | None:
        """Retrieve the registered verifier for a specific postcondition type."""
        with self._lock:
            return self._verifiers.get(postcondition_type)

    def infer_postconditions(
        self,
        subtask: Any,
        side_effects: list[dict[str, Any]] | None = None,
        output_summary: str = "",
    ) -> list[Postcondition]:
        """Automatically infer physical postconditions from subtask definitions and tool side effects."""
        postconditions: list[Postcondition] = []
        seen_keys: set[str] = set()

        # 1. Explicit postconditions attached to the subtask
        explicit_pcs = getattr(subtask, "postconditions", None) or []
        for pc in explicit_pcs:
            if isinstance(pc, Postcondition):
                postconditions.append(pc)
            elif isinstance(pc, dict):
                try:
                    postconditions.append(Postcondition.from_dict(pc))
                except (KeyError, ValueError, TypeError):
                    pass

        # 2. Expected deliverable files declared on the subtask
        expected_deliverables = getattr(subtask, "expected_deliverables", None) or []
        for item in expected_deliverables:
            path_str = str(item)
            is_file_path = ("/" in path_str) or ("\\" in path_str) or bool(re.search(r"\.[a-zA-Z0-9]{1,6}$", path_str))
            if is_file_path:
                key = f"fs_exist:{path_str}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    postconditions.append(
                        Postcondition(
                            postcondition_type=PostconditionType.FILESYSTEM,
                            spec={
                                "path": path_str,
                                "must_exist": True,
                                "non_empty": True,
                            },
                            description=f"Deliverable artifact '{Path(path_str).name}' must physically exist and be non-empty",
                            critical=True,
                        )
                    )

        # Fallback: Check subtask description/title if deliverables were not explicitly registered
        if not expected_deliverables and getattr(subtask, "assigned_agent", "") == "filesystem":
            desc = f"{getattr(subtask, 'title', '')} {getattr(subtask, 'description', '')}"
            matches = re.findall(r"([\w\-]+\.[a-zA-Z0-9]{2,5})", desc)
            for m in matches:
                if m.lower() not in ("txt", "json", "csv", "md"):
                    key = f"fs_exist:{m}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        postconditions.append(
                            Postcondition(
                                postcondition_type=PostconditionType.FILESYSTEM,
                                spec={"path": m, "must_exist": True, "non_empty": True},
                                description=f"Deliverable artifact '{m}' declared in subtask",
                                critical=True,
                            )
                        )

        # 3. Tool execution side effects (e.g. write_file, execute_command)
        if isinstance(side_effects, list):
            for effect in side_effects:
                if not isinstance(effect, dict):
                    continue
                tool_name = str(effect.get("tool_name") or "")
                args = effect.get("arguments") or {}

                if tool_name == "write_file":
                    raw_path = args.get("path") or effect.get("path")
                    if raw_path:
                        path_str = str(raw_path)
                        key = f"fs_write:{path_str}"
                        if key not in seen_keys:
                            seen_keys.add(key)
                            spec: dict[str, Any] = {
                                "path": path_str,
                                "must_exist": True,
                                "non_empty": True,
                            }
                            if "content" in args:
                                spec["expected_content"] = args["content"]
                            postconditions.append(
                                Postcondition(
                                    postcondition_type=PostconditionType.FILESYSTEM,
                                    spec=spec,
                                    description=f"Physical artifact produced by write_file at '{Path(path_str).name}'",
                                    critical=True,
                                )
                            )

                elif tool_name in ("execute_command", "run_command", "bash", "shell"):
                    cmd = str(args.get("command") or "")
                    key = f"cmd:{cmd}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        spec = {
                            "command": cmd,
                            "exit_code": effect.get("exit_code", 0),
                            "actual_stdout": effect.get("output") or effect.get("stdout", ""),
                            "actual_stderr": effect.get("error") or effect.get("stderr", ""),
                        }
                        if "expected_stdout_pattern" in args:
                            spec["expected_stdout_pattern"] = args["expected_stdout_pattern"]
                        postconditions.append(
                            Postcondition(
                                postcondition_type=PostconditionType.COMMAND,
                                spec=spec,
                                description=f"Execution outcome for command: {cmd[:40]}",
                                critical=True,
                            )
                        )

                elif tool_name == "open_app":
                    pid = (effect.get("evidence") or {}).get("pid")
                    if pid:
                        key = f"proc_open:{pid}"
                        if key not in seen_keys:
                            seen_keys.add(key)
                            postconditions.append(
                                Postcondition(
                                    postcondition_type=PostconditionType.PROCESS,
                                    spec={"pid": pid, "should_be_running": True},
                                    description=f"Process for '{args.get('app_name', 'application')}' (PID: {pid}) must be active",
                                    critical=True,
                                )
                            )

                elif tool_name in ("create_directory", "create_folder"):
                    raw_path = args.get("path") or (effect.get("evidence") or {}).get("path")
                    if raw_path:
                        path_str = str(raw_path)
                        key = f"fs_mkdir:{path_str}"
                        if key not in seen_keys:
                            seen_keys.add(key)
                            postconditions.append(
                                Postcondition(
                                    postcondition_type=PostconditionType.FILESYSTEM,
                                    spec={"path": path_str, "must_exist": True, "is_directory": True},
                                    description=f"Created directory must physically exist on disk: {Path(path_str).name}",
                                    critical=True,
                                )
                            )

        return postconditions

    def verify_postcondition(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        """Independently execute a physical postcondition check and record tamper-evident evidence."""
        verifier = self.get_verifier(postcondition.postcondition_type)

        if not verifier:
            record = VerificationRecord(
                evidence_id=new_evidence_id("evd_err"),
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity="engine:unregistered",
                expected_postcondition=postcondition.to_dict(),
                actual_observed_result={"error": f"No verifier registered for {postcondition.postcondition_type.value}"},
                status=VerificationStatus.ERROR,
                verdict=f"Error: Missing verifier for {postcondition.postcondition_type.value}",
            )
        else:
            try:
                record = verifier.verify(
                    task_id=task_id,
                    subtask_id=subtask_id,
                    postcondition=postcondition,
                    context=context,
                )
            except Exception as ex:  # noqa: BLE001
                record = VerificationRecord(
                    evidence_id=new_evidence_id("evd_exc"),
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=verifier.verifier_identity,
                    expected_postcondition=postcondition.to_dict(),
                    actual_observed_result={"exception": str(ex), "type": type(ex).__name__},
                    status=VerificationStatus.ERROR,
                    verdict=f"Internal verification error: {ex}",
                )

        # Ensure cryptographic hash and record into append-only evidence ledger
        self.ledger.record(record)
        return record

    def verify_subtask(
        self,
        subtask: Any,
        side_effects: list[dict[str, Any]] | None = None,
        output_summary: str = "",
        extra_postconditions: list[Postcondition] | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, str, list[VerificationRecord], dict[str, Any]]:
        """Verify all postconditions for a subtask independently of agent claims.

        Returns:
            Tuple of:
            - is_verified: True if all critical postconditions passed.
            - verdict_summary: Readable combined verdict message.
            - records: List of generated VerificationRecord objects.
            - evidence_map: Deliverable evidence formatted for ContextBus and UI events.
        """
        task_id = str(getattr(subtask, "task_id", "unknown_task"))
        subtask_id = str(getattr(subtask, "subtask_id", "unknown_subtask"))

        # Infer postconditions from subtask definition and tool side effects
        postconditions = self.infer_postconditions(subtask, side_effects, output_summary)

        # Merge in any explicitly supplied postconditions
        if extra_postconditions:
            postconditions.extend(extra_postconditions)

        # If no physical postconditions were declared or inferred
        if not postconditions:
            # Check for silent failures or model failure strings
            if not output_summary or output_summary.startswith("[Model offline"):
                rec = VerificationRecord(
                    evidence_id=new_evidence_id("evd_nonphys"),
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity="engine:logical",
                    expected_postcondition={"type": "logical_output"},
                    actual_observed_result={"summary": output_summary},
                    status=VerificationStatus.PASS if output_summary else VerificationStatus.FAIL,
                    verdict="Logical completion verified (no physical deliverables required)." if output_summary else "Failed: Empty output.",
                )
                self.ledger.record(rec)
                return rec.is_pass, rec.verdict, [rec], {}

            # Subtask had side effects but no postconditions could be independently verified
            status = VerificationStatus.ACTION_EXECUTED_UNVERIFIED if side_effects else VerificationStatus.COMPLETED_VERIFIED
            rec = VerificationRecord(
                evidence_id=new_evidence_id("evd_logical"),
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity="engine:logical",
                expected_postcondition={"type": "logical_completion"},
                actual_observed_result={"output_summary_length": len(output_summary), "side_effects_count": len(side_effects or [])},
                status=status,
                verdict="Action executed unverified (no physical postconditions declared or observable)." if side_effects else "Logical subtask output verified (pure reasoning, no physical artifacts required).",
            )
            self.ledger.record(rec)
            return True, rec.verdict, [rec], {}

        records: list[VerificationRecord] = []
        evidence_map: dict[str, Any] = {}
        verdicts: list[str] = []
        all_passed = True

        for pc in postconditions:
            rec = self.verify_postcondition(task_id, subtask_id, pc, context)
            records.append(rec)

            if not rec.is_pass:
                if pc.critical:
                    all_passed = False
                verdicts.append(f"[FAIL] {rec.verdict}")
            else:
                verdicts.append(f"[PASS] {rec.verdict}")

            # Extract deliverable artifacts for context bus promotion
            if pc.postcondition_type == PostconditionType.FILESYSTEM and rec.is_pass:
                target_path = rec.actual_observed_result.get("path")
                if target_path:
                    fname = Path(target_path).name
                    evidence_map[fname] = {
                        "evidence_id": rec.evidence_id,
                        "path": str(target_path),
                        "bytes": rec.actual_observed_result.get("bytes", rec.actual_observed_result.get("size_bytes", 0)),
                        "sha256": rec.actual_observed_result.get("sha256", ""),
                        "verifier": rec.verifier_identity,
                        "integrity_hash": rec.integrity_hash,
                    }

        summary_verdict = " | ".join(verdicts)
        return all_passed, summary_verdict, records, evidence_map

    # ── Evidence Query & Ledger Integrity ────────────────────────────────────

    def get_evidence(self, evidence_id: str) -> VerificationRecord | None:
        """Fetch a specific verification record by its ID."""
        return self.ledger.get(evidence_id)

    def get_task_evidence(self, task_id: str) -> list[VerificationRecord]:
        """Fetch all verification records produced under a task."""
        return self.ledger.list_for_task(task_id)

    def get_subtask_evidence(self, subtask_id: str) -> list[VerificationRecord]:
        """Fetch all verification records produced under a subtask."""
        return self.ledger.list_for_subtask(subtask_id)

    def verify_ledger_integrity(self) -> tuple[bool, list[str]]:
        """Verify the cryptographic hash of every record stored in the evidence ledger."""
        return self.ledger.verify_ledger_integrity()


# ── Global Singleton Pattern ─────────────────────────────────────────────────

_global_verification_engine: VerificationEngine | None = None
_engine_lock = threading.Lock()


def get_verification_engine(storage_root: Path | str | None = None) -> VerificationEngine:
    """Retrieve or initialize the global VerificationEngine singleton."""
    global _global_verification_engine
    with _engine_lock:
        if _global_verification_engine is None:
            _global_verification_engine = VerificationEngine(storage_root=storage_root)
        elif storage_root is not None:
            # Rebind storage root if explicitly requested
            _global_verification_engine = VerificationEngine(storage_root=storage_root)
        return _global_verification_engine


def reset_verification_engine() -> None:
    """Reset the global verification engine singleton (useful for test isolation)."""
    global _global_verification_engine
    with _engine_lock:
        _global_verification_engine = None
