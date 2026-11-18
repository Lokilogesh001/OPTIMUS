"""core/verification/verifiers.py — Pluggable Specialized Physical Verifiers for OPTIMUS.

Implements:
1. FilesystemVerifier: file/dir existence, size bounds, SHA-256 hashes, extensions, content matches.
2. ProcessVerifier: process existence, application launch/exit, local socket port availability.
3. DataVerifier: JSON schema validation, CSV/tabular integrity, numerical assertions, null checks.
4. CommandVerifier: exit-code validation, stdout/stderr pattern matching, timeout detection.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import socket
from pathlib import Path
from typing import Any

from core.verification.types import (
    Postcondition,
    PostconditionType,
    VerificationRecord,
    VerificationStatus,
    new_evidence_id,
)


class BaseVerifier:
    """Abstract base class for all pluggable physical postcondition verifiers."""

    verifier_identity: str = "base"
    handled_type: PostconditionType

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        raise NotImplementedError


# ── 1. Filesystem Verifier ───────────────────────────────────────────────────

class FilesystemVerifier(BaseVerifier):
    """Independently verifies file existence, size, SHA-256 integrity, format, and content."""

    verifier_identity: str = "verifier:filesystem"
    handled_type: PostconditionType = PostconditionType.FILESYSTEM

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        spec = postcondition.spec
        raw_path = str(spec.get("path") or spec.get("file_path") or "")
        evidence_id = new_evidence_id("evd_fs")

        if not raw_path:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result={"error": "Missing target path in postcondition specification"},
                status=VerificationStatus.FAIL,
                verdict="Failed: No target path provided in postcondition spec.",
            )

        allowed_roots = (context or {}).get("allowed_roots")
        target_path = Path(raw_path)
        if not target_path.is_absolute() and allowed_roots:
            cand = Path(allowed_roots[0]) / target_path
            if cand.exists() or not target_path.exists():
                target_path = cand
        target_path = target_path.resolve()
        is_directory = bool(spec.get("is_directory", False))
        observed: dict[str, Any] = {
            "path": str(target_path),
            "exists": target_path.exists(),
        }

        # 1. Existence Check
        if not target_path.exists():
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAIL,
                verdict=f"Missing deliverable file: target '{target_path}' does not exist on disk.",
            )

        # Directory handling
        if is_directory:
            if not target_path.is_dir():
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Physical postcondition failed: '{target_path}' is not a directory.",
                )
            entries = list(target_path.iterdir())
            observed["entry_count"] = len(entries)
            min_entries = spec.get("min_entries", 0)
            if len(entries) < min_entries:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Directory '{target_path}' contains {len(entries)} items (expected at least {min_entries}).",
                )
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.PASS,
                verdict=f"Directory '{target_path}' verified with {len(entries)} items.",
            )

        # File assertions
        if not target_path.is_file():
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAIL,
                verdict=f"Missing deliverable file: '{target_path}' is not a regular file.",
            )

        raw_bytes = target_path.read_bytes()
        actual_size = len(raw_bytes)
        actual_sha = hashlib.sha256(raw_bytes).hexdigest()
        observed["bytes"] = actual_size
        observed["sha256"] = actual_sha

        # 2. Non-empty & size checks
        if spec.get("non_empty", True) and actual_size == 0:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAIL,
                verdict=f"Missing deliverable file: empty '{target_path}' (0 bytes).",
            )

        min_bytes = spec.get("min_bytes")
        if min_bytes is not None and actual_size < min_bytes:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAIL,
                verdict=f"File '{target_path.name}' size {actual_size} bytes is below required min {min_bytes} bytes.",
            )

        max_bytes = spec.get("max_bytes")
        if max_bytes is not None and actual_size > max_bytes:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAIL,
                verdict=f"File '{target_path.name}' size {actual_size} bytes exceeds required max {max_bytes} bytes.",
            )

        # 3. SHA-256 Hash check
        expected_sha = spec.get("sha256") or spec.get("expected_sha256")
        if expected_sha and actual_sha.lower() != str(expected_sha).lower():
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAIL,
                verdict=f"SHA-256 hash mismatch on '{target_path.name}': expected '{expected_sha}', got '{actual_sha}'.",
            )

        # 4. Extension format validation
        expected_extension = spec.get("extension") or spec.get("expected_extension")
        if expected_extension:
            expected_ext = expected_extension if expected_extension.startswith(".") else f".{expected_extension}"
            if target_path.suffix.lower() != expected_ext.lower():
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"File extension mismatch on '{target_path.name}': expected '{expected_ext}', got '{target_path.suffix}'.",
                )

        # 5. Content pattern checks
        expected_content = spec.get("expected_content")
        if expected_content is not None:
            text = raw_bytes.decode("utf-8", errors="replace")
            if expected_content not in text:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Expected content string not found in '{target_path.name}'.",
                )

        return VerificationRecord(
            evidence_id=evidence_id,
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity=self.verifier_identity,
            expected_postcondition=spec,
            actual_observed_result=observed,
            status=VerificationStatus.PASS,
            verdict=f"Verified file '{target_path.name}': {actual_size} byte(s), SHA-256: {actual_sha}.",
            metadata={"path": str(target_path), "bytes": actual_size, "sha256": actual_sha},
        )


# ── 2. Process Verifier ──────────────────────────────────────────────────────

class ProcessVerifier(BaseVerifier):
    """Verifies process state, application execution, and local service socket ports."""

    verifier_identity: str = "verifier:process"
    handled_type: PostconditionType = PostconditionType.PROCESS

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        spec = postcondition.spec
        evidence_id = new_evidence_id("evd_proc")
        observed: dict[str, Any] = {}

        # 1. Local Port / Socket check
        port = spec.get("port")
        if port is not None:
            host = spec.get("host", "127.0.0.1")
            timeout = float(spec.get("timeout", 1.5))
            observed["host"] = host
            observed["port"] = port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                res = sock.connect_ex((host, int(port)))
                is_open = (res == 0)
            except OSError as e:
                is_open = False
                observed["error"] = str(e)
            finally:
                sock.close()

            observed["is_open"] = is_open
            should_be_open = spec.get("should_be_open") if "should_be_open" in spec else bool(spec.get("port_must_be_open", True))
            if is_open != should_be_open:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Port verification failed: {host}:{port} is {'open' if is_open else 'closed'} (expected {'open' if should_be_open else 'closed'}).",
                )
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.PASS,
                verdict=f"Verified socket service on {host}:{port} ({'open' if is_open else 'closed'}).",
            )

        # 2. Process check by PID
        pid = spec.get("pid")
        if pid is not None:
            observed["pid"] = pid
            is_running = False
            try:
                # Portable pid check
                if os.name == "nt":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x0400, False, int(pid))  # PROCESS_QUERY_INFORMATION
                    if handle != 0:
                        is_running = True
                        kernel32.CloseHandle(handle)
                else:
                    os.kill(int(pid), 0)
                    is_running = True
            except (OSError, ProcessLookupError, ValueError):
                is_running = False

            observed["is_running"] = is_running
            should_be_running = spec.get("should_be_running") if "should_be_running" in spec else bool(spec.get("must_be_running", True))
            if is_running != should_be_running:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Process PID {pid} is {'running' if is_running else 'not running'} (expected {'running' if should_be_running else 'terminated'}).",
                )
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.PASS,
                verdict=f"Verified process PID {pid} is {'running' if is_running else 'terminated'}.",
            )

        return VerificationRecord(
            evidence_id=evidence_id,
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity=self.verifier_identity,
            expected_postcondition=spec,
            actual_observed_result={"error": "Neither port nor pid specified in process spec"},
            status=VerificationStatus.FAIL,
            verdict="Process verification failed: spec must specify 'port' or 'pid'.",
        )


# ── 3. Data Verifier ─────────────────────────────────────────────────────────

class DataVerifier(BaseVerifier):
    """Verifies structured outputs, JSON schemas, CSV/tabular integrity, and numerical assertions."""

    verifier_identity: str = "verifier:data"
    handled_type: PostconditionType = PostconditionType.DATA

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        spec = postcondition.spec
        evidence_id = new_evidence_id("evd_data")
        observed: dict[str, Any] = {}

        # 1. Numerical assertion checks
        if "numerical_assertion" in spec:
            n_spec = spec["numerical_assertion"]
            val = n_spec.get("actual")
            if val is None and context:
                val = context.get(n_spec.get("variable_name", ""))
            try:
                numeric_val = float(val)
            except (ValueError, TypeError):
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result={"raw_value": val},
                    status=VerificationStatus.FAIL,
                    verdict=f"Numerical assertion failed: value '{val}' is not a valid number.",
                )

            op = n_spec.get("operator", "eq").lower()
            target = float(n_spec.get("expected", 0.0))
            tol = float(n_spec.get("abs_tol", 1e-6))
            observed["value"] = numeric_val
            observed["expected"] = target
            observed["operator"] = op

            passed = False
            if op == "eq":
                passed = math.isclose(numeric_val, target, abs_tol=tol)
            elif op == "gt":
                passed = numeric_val > target
            elif op == "gte":
                passed = numeric_val >= target - tol
            elif op == "lt":
                passed = numeric_val < target
            elif op == "lte":
                passed = numeric_val <= target + tol
            elif op == "between":
                high = float(n_spec.get("expected_max", target))
                passed = (target - tol) <= numeric_val <= (high + tol)

            if not passed:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Numerical assertion failed: {numeric_val} not {op} {target}.",
                )
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.PASS,
                verdict=f"Verified numerical assertion: {numeric_val} {op} {target}.",
            )

        # 2. JSON Schema & Structure Validation
        if "json_schema" in spec or spec.get("format") == "json" or isinstance(spec.get("data"), (dict, list)):
            raw_json = spec.get("raw_json") if "raw_json" in spec else (spec.get("data") if "data" in spec else None)
            if not raw_json and context:
                raw_json = context.get("output") or context.get("raw_json")

            if isinstance(raw_json, dict):
                parsed = raw_json
            else:
                try:
                    parsed = json.loads(str(raw_json or ""))
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result={"raw_json": raw_json, "error": str(e)},
                        status=VerificationStatus.FAIL,
                        verdict=f"JSON validation failed: unable to parse valid JSON ({e}).",
                    )

            observed["parsed_type"] = type(parsed).__name__
            required_keys = spec.get("required_keys") or spec.get("required_fields") or []
            missing_keys = [k for k in required_keys if k not in parsed]
            if missing_keys:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result={"parsed": parsed, "missing_keys": missing_keys},
                    status=VerificationStatus.FAIL,
                    verdict=f"JSON validation failed: Missing required field(s): {missing_keys}.",
                )

            # Type checks
            type_checks = spec.get("type_checks") or spec.get("field_types") or {}
            for k, exp_type in type_checks.items():
                val = parsed.get(k)
                type_name = str(exp_type).lower()
                type_ok = True
                if type_name in ("int", "integer"):
                    type_ok = isinstance(val, int) and not isinstance(val, bool)
                elif type_name in ("float", "number"):
                    type_ok = isinstance(val, (int, float)) and not isinstance(val, bool)
                elif type_name in ("str", "string"):
                    type_ok = isinstance(val, str)
                elif type_name in ("bool", "boolean"):
                    type_ok = isinstance(val, bool)
                elif type_name in ("list", "array"):
                    type_ok = isinstance(val, list)
                elif type_name in ("dict", "object"):
                    type_ok = isinstance(val, dict)

                if not type_ok:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result={"key": k, "actual_type": type(val).__name__},
                        status=VerificationStatus.FAIL,
                        verdict=f"JSON type mismatch on key '{k}': expected '{type_name}', got '{type(val).__name__}'.",
                    )

            # Nested numerical assertions on JSON fields
            num_asserts = spec.get("numerical_assertions") or []
            for na in num_asserts:
                field_name = na.get("field", "")
                val = parsed.get(field_name) if isinstance(parsed, dict) else None
                if val is None:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result={"field": field_name, "error": "Field not found"},
                        status=VerificationStatus.FAIL,
                        verdict=f"Numerical assertion failed: field '{field_name}' not found.",
                    )
                try:
                    num_val = float(val)
                except (ValueError, TypeError):
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result={"field": field_name, "raw_val": val},
                        status=VerificationStatus.FAIL,
                        verdict=f"Numerical assertion failed: field '{field_name}' has non-numeric value '{val}'.",
                    )

                op = str(na.get("operator", "eq")).lower()
                target = float(na.get("value", na.get("expected", 0.0)))
                tol = float(na.get("abs_tol", 1e-6))
                passed = False
                if op == "eq":
                    passed = math.isclose(num_val, target, abs_tol=tol)
                elif op == "gt":
                    passed = num_val > target
                elif op == "gte":
                    passed = num_val >= target - tol
                elif op == "lt":
                    passed = num_val < target
                elif op == "lte":
                    passed = num_val <= target + tol
                elif op == "between":
                    min_val = float(na.get("min", target))
                    max_val = float(na.get("max", target))
                    passed = (min_val - tol) <= num_val <= (max_val + tol)

                if not passed:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result={"field": field_name, "actual": num_val, "expected": target},
                        status=VerificationStatus.FAIL,
                        verdict=f"Assertion failed: '{field_name}' value {num_val} not {op} target.",
                    )

            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result={"key_count": len(parsed)},
                status=VerificationStatus.PASS,
                verdict=f"Verified structured JSON schema ({len(parsed)} fields validated).",
            )

        # 3. CSV / Tabular validation
        if "csv_tabular" in spec or spec.get("format") == "csv":
            raw_csv = spec.get("raw_csv") if "raw_csv" in spec else (spec.get("data") if "data" in spec else None)
            if not raw_csv and context:
                raw_csv = context.get("output") or context.get("raw_csv")

            reader = csv.reader(io.StringIO(str(raw_csv or "").strip()))
            rows = list(reader)
            if not rows:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result={"rows": 0},
                    status=VerificationStatus.FAIL,
                    verdict="CSV tabular validation failed: dataset is completely empty.",
                )

            headers = [h.strip() for h in rows[0]]
            observed["headers"] = headers
            observed["row_count"] = len(rows) - 1

            required_cols = spec.get("required_columns") or spec.get("required_headers") or []
            missing_cols = [c for c in required_cols if c not in headers]
            if missing_cols:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Missing required CSV header(s): {missing_cols}.",
                )

            min_rows = spec.get("min_rows", 1)
            if len(rows) - 1 < min_rows:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"CSV tabular row count {len(rows) - 1} is below required min {min_rows}.",
                )

            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.PASS,
                verdict=f"Verified CSV tabular integrity ({len(headers)} columns, {len(rows)-1} rows).",
            )

        return VerificationRecord(
            evidence_id=evidence_id,
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity=self.verifier_identity,
            expected_postcondition=spec,
            actual_observed_result={"error": "Unsupported data verification spec"},
            status=VerificationStatus.FAIL,
            verdict="Data verification failed: spec must contain 'numerical_assertion', 'json_schema', or 'csv_tabular'.",
        )


# ── 4. Command Verifier ──────────────────────────────────────────────────────

class CommandVerifier(BaseVerifier):
    """Verifies CLI and shell execution outcomes, exit codes, output matching, and timeout errors."""

    verifier_identity: str = "verifier:command"
    handled_type: PostconditionType = PostconditionType.COMMAND

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        spec = postcondition.spec
        evidence_id = new_evidence_id("evd_cmd")

        actual_exit_code = spec.get("exit_code")
        if actual_exit_code is None and context:
            actual_exit_code = context.get("exit_code")

        actual_stdout = spec.get("stdout") if "stdout" in spec else (spec.get("actual_stdout") or (context.get("stdout") if context else "") or "")
        actual_stderr = spec.get("stderr") if "stderr" in spec else (spec.get("actual_stderr") or (context.get("stderr") if context else "") or "")

        timed_out = bool(spec.get("timed_out", False) or (context and context.get("timed_out")))
        observed: dict[str, Any] = {
            "exit_code": actual_exit_code,
            "timed_out": timed_out,
            "stdout_snippet": str(actual_stdout)[:200],
            "stderr_snippet": str(actual_stderr)[:200],
        }

        # 1. Timeout Check
        if timed_out:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAIL,
                verdict="Command execution timed out before completion.",
            )

        # 2. Exit Code Check
        expected_exit_code = spec.get("expected_exit_code", 0)
        if actual_exit_code != expected_exit_code:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAIL,
                verdict=f"Exit code {actual_exit_code} does not match expected {expected_exit_code}.",
            )

        # 3. Output pattern match
        expected_output = spec.get("expected_output") or spec.get("expected_stdout_pattern")
        if expected_output and not re.search(expected_output, str(actual_stdout)) and expected_output not in str(actual_stdout):
            return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Command output did not match expected pattern '{expected_output}'.",
                )

        # 4. Prohibited error output check
        prohib = list(spec.get("prohibited_patterns") or [])
        if spec.get("prohibited_output"):
            prohib.append(spec["prohibited_output"])
        for p in prohib:
            if p in str(actual_stdout) or p in str(actual_stderr):
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Command produced prohibited error text '{p}'.",
                )

        return VerificationRecord(
            evidence_id=evidence_id,
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity=self.verifier_identity,
            expected_postcondition=spec,
            actual_observed_result=observed,
            status=VerificationStatus.PASS,
            verdict=f"Verified command execution: exit_code={actual_exit_code}.",
        )
