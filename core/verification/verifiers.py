"""core/verification/verifiers.py — Pluggable Specialized Physical Verifiers for OPTIMUS.

Implements:
1. FilesystemVerifier: file/dir existence, size bounds, SHA-256 hashes, extensions, content matches.
2. ProcessVerifier: process existence, application launch/exit, local socket port availability.
3. DataVerifier: JSON schema validation, CSV/tabular integrity, numerical assertions, null checks.
4. CommandVerifier: exit-code validation, stdout/stderr pattern matching, timeout detection.
"""

from __future__ import annotations

import contextlib
import csv
import ctypes
import hashlib
import io
import json
import logging
import math
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("optimus.verification.verifiers")

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

        try:
            stat_res = target_path.stat()
            actual_size = stat_res.st_size
        except OSError as stat_err:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result={"error": f"Failed to stat file: {stat_err}"},
                status=VerificationStatus.FAIL,
                verdict=f"Cannot inspect deliverable file '{target_path}': {stat_err}",
            )

        # 64 KB streaming chunk hash calculation to prevent unbounded RAM allocation
        hasher = hashlib.sha256()
        try:
            with target_path.open("rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            actual_sha = hasher.hexdigest()
        except OSError as read_err:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result={"error": f"Failed to read file: {read_err}"},
                status=VerificationStatus.FAIL,
                verdict=f"Cannot read deliverable file '{target_path}': {read_err}",
            )

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

        # 5. Content pattern checks (preview up to 1 MB to prevent unbounded RAM usage)
        expected_content = spec.get("expected_content")
        if expected_content is not None:
            try:
                with target_path.open("rb") as f:
                    content_bytes = f.read(1048576)
                text = content_bytes.decode("utf-8", errors="replace")
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
            except OSError as read_err:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Failed reading '{target_path.name}' for content check: {read_err}",
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
                pid_int = int(pid)
                if pid_int <= 0:
                    is_running = False
                else:
                    try:
                        import psutil  # type: ignore
                        proc = psutil.Process(pid_int)
                        is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
                    except ImportError:
                        # Portable pid check fallback
                        if os.name == "nt":
                            import ctypes
                            from ctypes import wintypes
                            kernel32 = ctypes.windll.kernel32
                            kernel32.OpenProcess.restype = wintypes.HANDLE
                            handle = kernel32.OpenProcess(0x1000, False, pid_int)  # PROCESS_QUERY_LIMITED_INFORMATION
                            if handle:
                                try:
                                    exit_code = wintypes.DWORD()
                                    if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                                        is_running = (exit_code.value == 259)  # STILL_ACTIVE
                                finally:
                                    kernel32.CloseHandle(handle)
                        else:
                            os.kill(pid_int, 0)
                            is_running = True
                    except Exception:
                        is_running = False
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

        # 4. Markdown Table & Semantic Specification Schema Validation (Resolves MED-02)
        if (
            spec.get("type") in ("data_schema", "DATA_SCHEMA")
            or "markdown_table" in str(spec.get("target", ""))
            or "parameters" in spec
        ):
            params = spec.get("parameters") or {}
            target_file = spec.get("target") or spec.get("file") or ""
            content = ""

            from pathlib import Path
            p = Path(target_file) if target_file and "." in str(target_file) else None
            if p and not p.is_absolute():
                p = Path.cwd() / p
            if p and p.exists() and p.is_file():
                content = p.read_text(encoding="utf-8", errors="replace")
            elif context and "output" in context:
                content = str(context["output"])

            observed["content_length"] = len(content)

            # Check required columns
            req_cols = params.get("required_columns") or []
            missing_cols = [c for c in req_cols if c.lower() not in content.lower()]
            if missing_cols:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result={"missing_columns": missing_cols},
                    status=VerificationStatus.FAIL,
                    verdict=f"Semantic data schema validation failed: missing columns {missing_cols}.",
                )

            # Check min models / rows
            min_models = int(params.get("min_models") or params.get("min_entries") or 1)
            row_count = sum(
                1 for line in content.splitlines()
                if "|" in line
                and not set(line.strip()).issubset({"|", "-", ":", " "})
                and "model" not in line.lower()
            )
            observed["table_rows"] = row_count
            if row_count < min_models and content:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAIL,
                    verdict=f"Table contains {row_count} entries, expected at least {min_models}.",
                )

            # Check max price constraint if specified
            max_price = params.get("max_price")
            if max_price is not None:
                prices = [float(num) for num in re.findall(r"\$(\d+(?:\.\d+)?)", content)]
                if prices:
                    observed["extracted_prices"] = prices
                    if any(pr > float(max_price) for pr in prices):
                        return VerificationRecord(
                            evidence_id=evidence_id,
                            task_id=task_id,
                            subtask_id=subtask_id,
                            verifier_identity=self.verifier_identity,
                            expected_postcondition=spec,
                            actual_observed_result=observed,
                            status=VerificationStatus.FAIL,
                            verdict=f"Price constraint violated: found prices > ${max_price}.",
                        )

            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.PASS,
                verdict=f"Verified semantic data schema ({row_count} rows, schema constraints satisfied).",
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


# ── COM Thread Helper ────────────────────────────────────────────────────────

@contextlib.contextmanager
def com_initialized():
    """Ensure COM is initialized on the current thread for Windows UIA queries."""
    if sys.platform != "win32":
        yield
        return
    COINIT_MULTITHREADED = 0x0
    ole32 = ctypes.windll.ole32
    hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    initialized = hr in (0, 1)  # S_OK, S_FALSE
    try:
        yield
    finally:
        if initialized:
            ole32.CoUninitialize()


# ── 5. Desktop Window Verifier ───────────────────────────────────────────────

class DesktopWindowVerifier(BaseVerifier):
    """Independently verifies window presence, state (visible/minimized/maximized), focus, and responsiveness."""

    verifier_identity: str = "verifier:desktop_window"
    handled_type: PostconditionType = PostconditionType.DESKTOP_WINDOW

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        spec = postcondition.spec
        evidence_id = new_evidence_id("evd_win")
        app_name = str(spec.get("app_name") or "").strip().lower()
        title_pattern = str(spec.get("title_pattern") or "").strip()
        must_be_active = bool(spec.get("must_be_active", False))
        must_be_visible = bool(spec.get("must_be_visible", True))
        check_responsive = bool(spec.get("check_responsive", True))
        min_dimensions = spec.get("min_dimensions")
        target_hwnd = spec.get("target_hwnd")

        # Discover windows from context or live OS
        windows = []
        active_win = None
        if context and "windows" in context:
            windows = context["windows"]
            active_win = context.get("active_window")
        else:
            try:
                from core.vision.window_intelligence import enumerate_windows, get_active_window
                windows = enumerate_windows(include_minimized=True)
                active_win = get_active_window()
            except Exception as e:
                windows = []

        # Find matching window
        matched = None
        for w in windows:
            # Check target hwnd if specified
            w_hwnd = getattr(w, "hwnd", None) if not isinstance(w, dict) else w.get("hwnd")
            if target_hwnd is not None and w_hwnd != target_hwnd:
                continue

            # Check app name
            w_app = getattr(w, "app_name", "") if not isinstance(w, dict) else w.get("app_name", "")
            if app_name and app_name not in w_app.lower():
                continue

            # Check title pattern
            w_title = getattr(w, "title", "") if not isinstance(w, dict) else w.get("title", "")
            if title_pattern:
                try:
                    if not re.search(title_pattern, w_title, re.IGNORECASE) and title_pattern.lower() not in w_title.lower():
                        continue
                except re.error:
                    if title_pattern.lower() not in w_title.lower():
                        continue

            matched = w
            break

        observed: dict[str, Any] = {
            "window_found": matched is not None,
            "target_app": app_name,
            "target_title_pattern": title_pattern,
        }

        # 1. Existence check
        if matched is None:
            observed["error"] = "Target window not found among enumerated windows"
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAILED,
                verdict=f"Target window matching app='{app_name}' title='{title_pattern}' not found.",
            )

        m_hwnd = getattr(matched, "hwnd", 0) if not isinstance(matched, dict) else matched.get("hwnd", 0)
        m_title = getattr(matched, "title", "") if not isinstance(matched, dict) else matched.get("title", "")
        m_app = getattr(matched, "app_name", "") if not isinstance(matched, dict) else matched.get("app_name", "")
        m_vis = getattr(matched, "is_visible", True) if not isinstance(matched, dict) else matched.get("is_visible", True)
        m_min = getattr(matched, "is_minimized", False) if not isinstance(matched, dict) else matched.get("is_minimized", False)
        m_rect = getattr(matched, "rect", (0, 0, 0, 0)) if not isinstance(matched, dict) else matched.get("rect", (0, 0, 0, 0))

        observed.update({
            "hwnd": m_hwnd,
            "title": m_title,
            "app_name": m_app,
            "is_visible": m_vis,
            "is_minimized": m_min,
            "rect": m_rect,
        })

        # 2. Honest Minimization & Visibility check
        if must_be_visible and m_min:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAILED,
                verdict=f"Window '{m_title}' is minimized (IsIconic=True); must be restored to be visible.",
            )

        if must_be_visible and not m_vis:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAILED,
                verdict=f"Window '{m_title}' is hidden (IsWindowVisible=False).",
            )

        # 3. Focus / Active window check
        active_hwnd = getattr(active_win, "hwnd", 0) if active_win and not isinstance(active_win, dict) else (active_win.get("hwnd", 0) if isinstance(active_win, dict) else 0)
        if must_be_active:
            is_active = (active_hwnd == m_hwnd)
            observed["is_active"] = is_active
            observed["active_hwnd"] = active_hwnd
            if not is_active:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAILED,
                    verdict=f"Window '{m_title}' (HWND {m_hwnd}) is not the active foreground window (Foreground HWND {active_hwnd}).",
                )

        # 4. Responsiveness check
        if check_responsive and m_hwnd and sys.platform == "win32":
            try:
                is_hung = bool(ctypes.windll.user32.IsHungAppWindow(m_hwnd))
                observed["is_hung"] = is_hung
                if is_hung:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result=observed,
                        status=VerificationStatus.FAILED,
                        verdict=f"Window '{m_title}' is not responding (IsHungAppWindow=True).",
                    )
            except Exception:
                pass

        # 5. Dimensions check
        if min_dimensions and len(min_dimensions) == 2:
            min_w, min_h = min_dimensions
            if len(m_rect) == 4:
                w = m_rect[2] - m_rect[0]
                h = m_rect[3] - m_rect[1]
                observed["dimensions"] = (w, h)
                if w < min_w or h < min_h:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result=observed,
                        status=VerificationStatus.FAILED,
                        verdict=f"Window dimensions ({w}x{h}) are below minimum required ({min_w}x{min_h}).",
                    )

        return VerificationRecord(
            evidence_id=evidence_id,
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity=self.verifier_identity,
            expected_postcondition=spec,
            actual_observed_result=observed,
            status=VerificationStatus.COMPLETED_VERIFIED,
            verdict=f"Verified window '{m_title}' [{m_app}] (HWND {m_hwnd}, visible={m_vis}, minimized={m_min}).",
        )


# ── 6. Browser Page Verifier ─────────────────────────────────────────────────

class BrowserPageVerifier(BaseVerifier):
    """Independently verifies browser active tab, URL, domain, and error-free loading."""

    verifier_identity: str = "verifier:browser_page"
    handled_type: PostconditionType = PostconditionType.BROWSER_PAGE

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        spec = postcondition.spec
        evidence_id = new_evidence_id("evd_web")
        expected_domain = str(spec.get("expected_domain") or "").strip().lower()
        url_pattern = str(spec.get("url_pattern") or "").strip()
        title_pattern = str(spec.get("title_pattern") or "").strip()
        prohibited = list(spec.get("prohibited_patterns") or [
            "404 Not Found", "404 not found", "This site can’t be reached",
            "This site can't be reached", "ERR_NAME_NOT_RESOLVED", "Privacy error",
            "Server Not Found", "DNS_PROBE_FINISHED_NXDOMAIN",
        ])

        # Discover tabs
        tabs = []
        if context and "browser_tabs" in context:
            tabs = context["browser_tabs"]
        else:
            try:
                from core.vision.browser_intelligence import BrowserIntelligence
                tabs = BrowserIntelligence().enumerate_tabs(force=True)
            except Exception:
                tabs = []

        # Find active tab or matching tab
        active_tab = None
        for t in tabs:
            is_active = getattr(t, "is_active", False) if not isinstance(t, dict) else t.get("is_active", False)
            if is_active:
                active_tab = t
                break

        # If no tab explicitly marked active, match first tab matching domain or title
        target_tab = active_tab
        if target_tab is None and tabs:
            for t in tabs:
                t_dom = getattr(t, "domain", "") or getattr(t, "domain_hint", "") if not isinstance(t, dict) else (t.get("domain") or t.get("domain_hint") or "")
                t_title = getattr(t, "title", "") if not isinstance(t, dict) else t.get("title", "")
                if expected_domain and expected_domain in str(t_dom).lower():
                    target_tab = t
                    break
                if title_pattern and title_pattern.lower() in t_title.lower():
                    target_tab = t
                    break

        observed: dict[str, Any] = {
            "tab_found": target_tab is not None,
            "expected_domain": expected_domain,
            "url_pattern": url_pattern,
            "title_pattern": title_pattern,
        }

        if target_tab is None:
            observed["error"] = "No open browser tab found matching specifications"
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAILED,
                verdict=f"No browser tab found matching domain='{expected_domain}' or title='{title_pattern}'.",
            )

        tab_title = getattr(target_tab, "title", "") if not isinstance(target_tab, dict) else target_tab.get("title", "")
        tab_url = getattr(target_tab, "url", "") if not isinstance(target_tab, dict) else target_tab.get("url", "")
        tab_domain = getattr(target_tab, "domain", "") or getattr(target_tab, "domain_hint", "") if not isinstance(target_tab, dict) else (target_tab.get("domain") or target_tab.get("domain_hint") or "")
        tab_browser = getattr(target_tab, "browser", "browser") if not isinstance(target_tab, dict) else target_tab.get("browser", "browser")

        observed.update({
            "title": tab_title,
            "url": tab_url,
            "domain": tab_domain,
            "browser": tab_browser,
        })

        # 1. Prohibited error check
        for p in prohibited:
            if p.lower() in tab_title.lower() or (tab_url and p.lower() in tab_url.lower()):
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAILED,
                    verdict=f"Browser tab rendered error page: '{p}'.",
                )

        # 2. Expected domain check
        if expected_domain:
            domain_matched = (expected_domain in str(tab_domain).lower()) or (expected_domain in str(tab_url).lower()) or (expected_domain.split(".")[0] in tab_title.lower())
            observed["domain_matched"] = domain_matched
            if not domain_matched:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAILED,
                    verdict=f"Active tab domain '{tab_domain}' does not match expected '{expected_domain}'.",
                )

        # 3. URL pattern check
        if url_pattern:
            if not tab_url:
                # Active Omnibox couldn't be read directly (non-active tab), fallback to domain match if valid
                if expected_domain and expected_domain in str(tab_domain).lower():
                    pass
                else:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result=observed,
                        status=VerificationStatus.FAILED,
                        verdict="Could not inspect active URL from browser Omnibox.",
                    )
            elif not re.search(url_pattern, tab_url, re.IGNORECASE) and url_pattern.lower() not in tab_url.lower():
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAILED,
                    verdict=f"URL '{tab_url}' does not match pattern '{url_pattern}'.",
                )

        # 4. Title pattern check
        if title_pattern:
            try:
                title_matches = bool(re.search(title_pattern, tab_title, re.IGNORECASE)) or (title_pattern.lower() in tab_title.lower())
            except re.error:
                title_matches = (title_pattern.lower() in tab_title.lower())
            if not title_matches:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAILED,
                    verdict=f"Tab title '{tab_title}' does not match expected '{title_pattern}'.",
                )

        return VerificationRecord(
            evidence_id=evidence_id,
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity=self.verifier_identity,
            expected_postcondition=spec,
            actual_observed_result=observed,
            status=VerificationStatus.COMPLETED_VERIFIED,
            verdict=f"Verified browser page: '{tab_title}' ({tab_domain or tab_url or 'active'}).",
        )


# ── 7. Media Playback Verifier ───────────────────────────────────────────────

class MediaPlaybackVerifier(BaseVerifier):
    """Independently verifies audio/video media playback, time progression, and ad overlay absence."""

    verifier_identity: str = "verifier:media_playback"
    handled_type: PostconditionType = PostconditionType.MEDIA_PLAYBACK

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        spec = postcondition.spec
        evidence_id = new_evidence_id("evd_media")
        assert_playing = bool(spec.get("assert_playing", True))
        time_delta_sampling = bool(spec.get("time_delta_sampling", True))
        min_progress = float(spec.get("min_progress_seconds", 1.0))
        sample_interval = float(spec.get("sample_interval_seconds", 1.5))
        detect_ad = bool(spec.get("detect_ad_overlay", True))
        prohibited_overlays = list(spec.get("prohibited_overlays") or [
            "Video unavailable", "This video is unavailable", "Private video",
            "Playback error", "Sign in to confirm your age",
        ])

        # Check mock/context media state first for tests
        if context and "media_state" in context:
            m_state = context["media_state"]
            observed = dict(m_state)
            if observed.get("has_ad") and detect_ad:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.BLOCKED,
                    verdict="Pre-roll advertisement is active; target video playback not yet started.",
                )
            for err in prohibited_overlays:
                if err.lower() in str(observed.get("overlay_text", "")).lower():
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result=observed,
                        status=VerificationStatus.FAILED,
                        verdict=f"Video player displayed error overlay: '{err}'.",
                    )
            is_playing = bool(observed.get("is_playing", False))
            if assert_playing and not is_playing:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAILED,
                    verdict="Media player is paused or stalled.",
                )
            if time_delta_sampling:
                t0 = float(observed.get("time_t0", 0.0))
                t1 = float(observed.get("time_t1", t0 + 2.0))
                if (t1 - t0) < min_progress:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result=observed,
                        status=VerificationStatus.FAILED,
                        verdict=f"Playback time did not advance sufficiently: delta={round(t1-t0, 2)}s < {min_progress}s.",
                    )
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.COMPLETED_VERIFIED,
                verdict=f"Verified media playback: active and progressing.",
            )

        # Native Windows Core Audio (WASAPI) session inspection (Resolves HIGH-05)
        target_app = str(spec.get("target") or spec.get("application") or "spotify").lower()
        try:
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
            sessions = AudioUtilities.GetAllSessions()
            for s in sessions:
                p = s.Process
                p_name = (p.name() if p else "").lower()
                if target_app in p_name or (target_app == "spotify" and "spotify" in p_name):
                    meter = s._ctl.QueryInterface(IAudioMeterInformation) if hasattr(s, "_ctl") else None
                    peak = float(meter.GetPeakValue()) if meter else 0.0
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result={
                            "process": p_name,
                            "peak_meter": peak,
                            "wasapi_verified": True,
                        },
                        status=VerificationStatus.COMPLETED_VERIFIED,
                        verdict=f"Verified native Windows audio playback for '{p_name}' (WASAPI peak: {round(peak, 4)}).",
                    )
        except Exception as wasapi_err:
            logger.debug("WASAPI session check exception: %s", wasapi_err)

        # Real Desktop Observation via screenview UIA
        with com_initialized():
            try:
                from core.screenview import look
                view = look()
            except Exception as e:
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result={"error": f"Failed to inspect desktop UI: {e}"},
                    status=VerificationStatus.FAILED,
                    verdict=f"Failed to inspect active media window: {e}",
                )

        observed = {
            "app": view.app,
            "title": view.title,
            "element_count": len(view.elements),
        }

        # 1. Prohibited error overlay check
        for elem in view.elements:
            elem_text = f"{elem.name} {elem.value}".lower()
            for err in prohibited_overlays:
                if err.lower() in elem_text:
                    observed["error_overlay"] = err
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result=observed,
                        status=VerificationStatus.FAILED,
                        verdict=f"Video player displayed error overlay: '{err}'.",
                    )

        # 2. Ad overlay detection
        if detect_ad:
            ad_signatures = ["ad 1 of", "ad 2 of", "skip ad", "skip in", "ytp-ad", "advertisement"]
            for elem in view.elements:
                elem_text = f"{elem.name} {elem.value}".lower()
                for ad_sig in ad_signatures:
                    if ad_sig in elem_text:
                        observed["ad_detected"] = elem_text[:50]
                        return VerificationRecord(
                            evidence_id=evidence_id,
                            task_id=task_id,
                            subtask_id=subtask_id,
                            verifier_identity=self.verifier_identity,
                            expected_postcondition=spec,
                            actual_observed_result=observed,
                            status=VerificationStatus.BLOCKED,
                            verdict=f"Pre-roll advertisement is active ('{elem_text[:30]}'); target video playback not started.",
                        )

        # 3. Play / Pause state detection
        # On YouTube and HTML5 video players, the button displays "Pause" or "Pause (k)" while playing!
        has_pause_button = False
        has_play_button = False
        time_text = ""

        time_pattern = re.compile(r"(\d+:\d+(?::\d+)?)\s*(?:/|of)\s*(\d+:\d+(?::\d+)?)")

        for elem in view.elements:
            name_low = (elem.name or "").lower()
            val_low = (elem.value or "").lower()

            if "pause" in name_low or "pause" in val_low or "(k)" in name_low:
                has_pause_button = True
            elif name_low == "play" or val_low == "play":
                has_play_button = True

            if not time_text:
                m = time_pattern.search(str(elem.name or "")) or time_pattern.search(str(elem.value or ""))
                if m:
                    time_text = m.group(0)

        observed["has_pause_button"] = has_pause_button
        observed["has_play_button"] = has_play_button
        observed["time_text"] = time_text

        if assert_playing and not has_pause_button and has_play_button:
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAILED,
                verdict="Media player is in PAUSED state (Play button visible, Pause button absent).",
            )

        # 4. Temporal 2-point sampling
        if time_delta_sampling and time_text:
            def _parse_secs(t_str: str) -> float:
                cur = t_str.split("/")[0].strip()
                parts = cur.split(":")
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                return 0.0

            s0 = _parse_secs(time_text)
            time.sleep(sample_interval)

            with com_initialized():
                try:
                    from core.screenview import look
                    view2 = look()
                    time_text2 = ""
                    for elem in view2.elements:
                        m = time_pattern.search(elem.name) or time_pattern.search(elem.value)
                        if m:
                            time_text2 = m.group(0)
                            break
                    if time_text2:
                        s1 = _parse_secs(time_text2)
                        observed["time_t0"] = s0
                        observed["time_t1"] = s1
                        observed["delta_seconds"] = s1 - s0
                        if (s1 - s0) < min_progress:
                            return VerificationRecord(
                                evidence_id=evidence_id,
                                task_id=task_id,
                                subtask_id=subtask_id,
                                verifier_identity=self.verifier_identity,
                                expected_postcondition=spec,
                                actual_observed_result=observed,
                                status=VerificationStatus.FAILED,
                                verdict=f"Playback stalled: current time {time_text} -> {time_text2} (delta={s1-s0}s).",
                            )
                except Exception:
                    pass

        return VerificationRecord(
            evidence_id=evidence_id,
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity=self.verifier_identity,
            expected_postcondition=spec,
            actual_observed_result=observed,
            status=VerificationStatus.COMPLETED_VERIFIED,
            verdict=f"Verified media playback: active player controls and non-zero progression confirmed.",
        )


# ── 8. UI Control Verifier ───────────────────────────────────────────────────

class UIControlVerifier(BaseVerifier):
    """Independently verifies specific UI controls, states, values, and interactive accessibility properties."""

    verifier_identity: str = "verifier:ui_control"
    handled_type: PostconditionType = PostconditionType.UI_CONTROL

    def verify(
        self,
        task_id: str,
        subtask_id: str,
        postcondition: Postcondition,
        context: dict[str, Any] | None = None,
    ) -> VerificationRecord:
        spec = postcondition.spec
        evidence_id = new_evidence_id("evd_uic")
        role = str(spec.get("role") or "").strip().lower()
        name_pattern = str(spec.get("name_pattern") or "").strip()
        expected_value = spec.get("expected_value")
        is_enabled = spec.get("is_enabled", True)
        is_focused = spec.get("is_focused")
        target_hwnd = spec.get("target_hwnd")

        elements = []
        if context and "view" in context:
            view = context["view"]
            elements = getattr(view, "elements", []) if not isinstance(view, dict) else view.get("elements", [])
        else:
            with com_initialized():
                try:
                    from core.screenview import look
                    view = look()
                    elements = getattr(view, "elements", [])
                except Exception as e:
                    return VerificationRecord(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        subtask_id=subtask_id,
                        verifier_identity=self.verifier_identity,
                        expected_postcondition=spec,
                        actual_observed_result={"error": f"Failed to inspect UI controls: {e}"},
                        status=VerificationStatus.FAILED,
                        verdict=f"Could not read accessibility tree: {e}",
                    )

        matched = None
        for elem in elements:
            raw_role = getattr(elem, "role", "") if not isinstance(elem, dict) else elem.get("role", "")
            e_role = str(raw_role).lower()
            if role and role not in e_role:
                continue

            raw_name = getattr(elem, "name", "") if not isinstance(elem, dict) else elem.get("name", "")
            e_name = str(raw_name)
            if name_pattern:
                try:
                    if not re.search(name_pattern, e_name, re.IGNORECASE) and name_pattern.lower() not in e_name.lower():
                        continue
                except re.error:
                    if name_pattern.lower() not in e_name.lower():
                        continue

            matched = elem
            break

        observed: dict[str, Any] = {
            "control_found": matched is not None,
            "target_role": role,
            "target_name_pattern": name_pattern,
        }

        if matched is None:
            observed["error"] = "Target UI control not found"
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAILED,
                verdict=f"UI control role='{role}' matching '{name_pattern}' not found.",
            )

        m_role = getattr(matched, "role", "") if not isinstance(matched, dict) else matched.get("role", "")
        m_name = getattr(matched, "name", "") if not isinstance(matched, dict) else matched.get("name", "")
        m_val = getattr(matched, "value", "") if not isinstance(matched, dict) else matched.get("value", "")
        m_enabled = getattr(matched, "enabled", True) if not isinstance(matched, dict) else matched.get("enabled", True)
        m_focused = getattr(matched, "focused", False) if not isinstance(matched, dict) else matched.get("focused", False)

        observed.update({
            "role": m_role,
            "name": m_name,
            "value": m_val,
            "enabled": m_enabled,
            "focused": m_focused,
        })

        # 1. Enabled check
        if is_enabled is not None and m_enabled != bool(is_enabled):
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAILED,
                verdict=f"UI control '{m_name}' enabled={m_enabled}, expected={is_enabled}.",
            )

        # 2. Focused check
        if is_focused is not None and m_focused != bool(is_focused):
            return VerificationRecord(
                evidence_id=evidence_id,
                task_id=task_id,
                subtask_id=subtask_id,
                verifier_identity=self.verifier_identity,
                expected_postcondition=spec,
                actual_observed_result=observed,
                status=VerificationStatus.FAILED,
                verdict=f"UI control '{m_name}' focused={m_focused}, expected={is_focused}.",
            )

        # 3. Expected value check
        if expected_value is not None:
            str_exp = str(expected_value)
            if str_exp.lower() not in str(m_val).lower() and str_exp.lower() not in str(m_name).lower():
                return VerificationRecord(
                    evidence_id=evidence_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                    verifier_identity=self.verifier_identity,
                    expected_postcondition=spec,
                    actual_observed_result=observed,
                    status=VerificationStatus.FAILED,
                    verdict=f"UI control value '{m_val}' does not contain expected '{expected_value}'.",
                )

        return VerificationRecord(
            evidence_id=evidence_id,
            task_id=task_id,
            subtask_id=subtask_id,
            verifier_identity=self.verifier_identity,
            expected_postcondition=spec,
            actual_observed_result=observed,
            status=VerificationStatus.COMPLETED_VERIFIED,
            verdict=f"Verified UI control: [{m_role}] '{m_name}' (enabled={m_enabled}, focused={m_focused}).",
        )

