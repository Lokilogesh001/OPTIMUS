"""core/windows_runtime/broker.py — Privileged Execution Broker for Windows Runtime.

Implements Sections 3, 4, and 5 of Milestone 11:
- Typed, validated operation registry covering Filesystem, Processes, and System resources.
- Central authorization policy enforcement (routine operations vs critical approval gating).
- Cross-drive access across C:, D:, and all mounted volumes.
- Real Windows process management, application launching, and service inspection.
- Comprehensive audit ledger recording with timing and Windows error diagnostics.
"""

from __future__ import annotations

import ctypes
import fnmatch
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from core.windows_runtime.policy import WindowsRuntimePolicy, discover_mounted_drives
from core.windows_runtime.types import (
    OperationRequest,
    OperationResponse,
    PrivilegedAuditRecord,
    RuntimeErrorCode,
    RuntimeIdentity,
)

logger = logging.getLogger("optimus.windows_runtime.broker")


def get_current_runtime_identity() -> RuntimeIdentity:
    """Retrieve detailed Windows security identity for the executing process."""
    username = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    is_admin = False
    is_system = False

    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001, S110
        pass

    try:
        import win32api
        username = win32api.GetUserName()
        if username.upper() in ("SYSTEM", "LOCAL SYSTEM"):
            is_system = True
            is_admin = True
    except Exception:  # noqa: BLE001, S110
        pass

    return RuntimeIdentity(
        username=username,
        domain=domain,
        is_admin=is_admin,
        is_system=is_system,
        process_id=os.getpid(),
        integrity_level="High" if is_admin else "Medium",
    )


class PrivilegedExecutionBroker:
    """Core execution engine processing validated typed Windows operations."""

    def __init__(self, audit_dir: Path | str | None = None, audit_file: Path | str | None = None) -> None:
        if audit_file:
            self.audit_file = Path(audit_file)
            self.audit_dir = self.audit_file.parent
        elif audit_dir:
            p = Path(audit_dir)
            if p.suffix == ".jsonl":
                self.audit_file = p
                self.audit_dir = p.parent
            else:
                self.audit_dir = p
                self.audit_file = self.audit_dir / "privileged_operations.jsonl"
        else:
            base = Path.cwd() / "memory" / "audit"
            self.audit_dir = base
            self.audit_file = self.audit_dir / "privileged_operations.jsonl"
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def _record_audit(self, record: PrivilegedAuditRecord) -> None:
        """Append an operation audit record to the persistent ledger."""
        try:
            with open(self.audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception as ex:  # noqa: BLE001
            logger.warning("Could not write audit record: %s", ex)

    def execute_request(self, request: OperationRequest) -> OperationResponse:
        """Route, authorize, and execute a typed request."""
        start_time = time.perf_counter()
        ident = get_current_runtime_identity()

        # 1. Evaluate Authorization Policy
        is_allowed, requires_approval, reason = WindowsRuntimePolicy.evaluate_authorization(
            operation=request.operation,
            params=request.params,
            is_pre_approved=request.is_pre_approved,
        )

        if requires_approval and not request.is_pre_approved:
            resp = OperationResponse(
                request_id=request.request_id,
                success=False,
                error_code=RuntimeErrorCode.APPROVAL_REQUIRED,
                error_message=reason,
                execution_identity=ident,
                requires_approval=True,
                approval_reason=reason,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )
            self._record_audit(
                PrivilegedAuditRecord(
                    request_id=request.request_id,
                    operation=request.operation.value,
                    params=request.params,
                    success=False,
                    error_code=RuntimeErrorCode.APPROVAL_REQUIRED.value,
                    client_pid=request.client_pid,
                    execution_user=ident.username,
                    duration_ms=resp.duration_ms,
                )
            )
            return resp

        if not is_allowed:
            resp = OperationResponse(
                request_id=request.request_id,
                success=False,
                error_code=RuntimeErrorCode.ACCESS_DENIED,
                error_message=f"Policy violation: {reason}",
                execution_identity=ident,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )
            return resp

        # 2. Dispatch to Typed Operation Handler
        handler_map = {
            # Filesystem
            "DISCOVER_DRIVES": self._op_discover_drives,
            "LIST_DIRECTORY": self._op_list_directory,
            "SEARCH_FILES": self._op_search_files,
            "READ_FILE": self._op_read_file,
            "WRITE_FILE": self._op_write_file,
            "CREATE_DIRECTORY": self._op_create_directory,
            "COPY_FILE": self._op_copy_file,
            "MOVE_FILE": self._op_move_file,
            "RENAME_FILE": self._op_rename_file,
            "DELETE_FILE": self._op_delete_file,
            "FILE_METADATA": self._op_file_metadata,
            # Process & Applications
            "LAUNCH_APPLICATION": self._op_launch_application,
            "OPEN_WITH_ASSOCIATED": self._op_open_with_associated,
            "OPEN_IN_EXPLORER": self._op_open_in_explorer,
            "LIST_PROCESSES": self._op_list_processes,
            "INSPECT_PROCESS": self._op_inspect_process,
            "START_PROCESS": self._op_start_process,
            "STOP_PROCESS": self._op_stop_process,
            # System
            "INSPECT_SERVICE": self._op_inspect_service,
            "LIST_SERVICES": self._op_list_services,
            "GET_SYSTEM_TELEMETRY": self._op_get_system_telemetry,
            "GET_EXECUTION_IDENTITY": self._op_get_execution_identity,
        }

        handler = handler_map.get(request.operation.value)
        if not handler:
            return OperationResponse(
                request_id=request.request_id,
                success=False,
                error_code=RuntimeErrorCode.INVALID_REQUEST,
                error_message=f"Unsupported operation: {request.operation.value}",
                execution_identity=ident,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        try:
            result_data = handler(request.params)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            resp = OperationResponse(
                request_id=request.request_id,
                success=True,
                error_code=RuntimeErrorCode.SUCCESS,
                data=result_data,
                execution_identity=ident,
                duration_ms=duration_ms,
            )
            self._record_audit(
                PrivilegedAuditRecord(
                    request_id=request.request_id,
                    operation=request.operation.value,
                    params=request.params,
                    success=True,
                    error_code="SUCCESS",
                    client_pid=request.client_pid,
                    execution_user=ident.username,
                    duration_ms=duration_ms,
                )
            )
            return resp

        except FileNotFoundError as ex:
            return self._build_error_response(request, start_time, ident, RuntimeErrorCode.PATH_NOT_FOUND, str(ex))
        except PermissionError as ex:
            return self._build_error_response(request, start_time, ident, RuntimeErrorCode.ACCESS_DENIED, str(ex))
        except Exception as ex:
            logger.exception("Error executing %s", request.operation.value)
            return self._build_error_response(request, start_time, ident, RuntimeErrorCode.UNKNOWN_ERROR, str(ex))

    def _build_error_response(
        self,
        request: OperationRequest,
        start_time: float,
        ident: RuntimeIdentity,
        code: RuntimeErrorCode,
        msg: str,
    ) -> OperationResponse:
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        resp = OperationResponse(
            request_id=request.request_id,
            success=False,
            error_code=code,
            error_message=msg,
            execution_identity=ident,
            duration_ms=duration_ms,
        )
        self._record_audit(
            PrivilegedAuditRecord(
                request_id=request.request_id,
                operation=request.operation.value,
                params=request.params,
                success=False,
                error_code=code.value,
                client_pid=request.client_pid,
                execution_user=ident.username,
                duration_ms=duration_ms,
            )
        )
        return resp

    # ── Filesystem Operation Handlers ────────────────────────────────────────

    def _op_discover_drives(self, params: dict[str, Any]) -> dict[str, Any]:
        drives = discover_mounted_drives()
        details = []
        for d in drives:
            try:
                import psutil
                usage = psutil.disk_usage(d)
                details.append({
                    "drive": d,
                    "total_gb": round(usage.total / (1024**3), 2),
                    "free_gb": round(usage.free / (1024**3), 2),
                    "percent_used": usage.percent,
                })
            except Exception:  # noqa: BLE001
                details.append({"drive": d, "total_gb": 0, "free_gb": 0, "percent_used": 0})
        return {"drives": drives, "details": details}

    def _op_list_directory(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = params.get("path", "")
        p = Path(path_str).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Path '{path_str}' does not exist.")
        if not p.is_dir():
            raise NotADirectoryError(f"Path '{path_str}' is not a directory.")

        entries = []
        limit = int(params.get("limit", 200))
        for item in p.iterdir():
            try:
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size_bytes": stat.st_size if not item.is_dir() else 0,
                    "modified_at": stat.st_mtime,
                })
                if len(entries) >= limit:
                    break
            except Exception:  # noqa: BLE001, S112
                continue

        return {"path": str(p), "count": len(entries), "entries": entries}

    def _op_search_files(self, params: dict[str, Any]) -> dict[str, Any]:
        root_path = Path(params.get("root_path", "C:\\")).resolve()
        pattern = params.get("pattern", "*")
        max_results = int(params.get("max_results", 100))
        recursive = bool(params.get("recursive", True))

        if not root_path.exists():
            raise FileNotFoundError(f"Root path '{root_path}' does not exist.")

        results = []
        if recursive:
            for root, dirs, files in os.walk(str(root_path)):
                for name in files:
                    if fnmatch.fnmatch(name, pattern):
                        full_p = os.path.join(root, name)
                        try:
                            st = os.stat(full_p)
                            results.append({
                                "name": name,
                                "path": full_p,
                                "size_bytes": st.st_size,
                                "modified_at": st.st_mtime,
                            })
                            if len(results) >= max_results:
                                return {"root": str(root_path), "pattern": pattern, "results": results}
                        except Exception:  # noqa: BLE001, S112
                            continue
        else:
            for item in root_path.iterdir():
                if item.is_file() and fnmatch.fnmatch(item.name, pattern):
                    results.append({"name": item.name, "path": str(item), "size_bytes": item.stat().st_size})

        return {"root": str(root_path), "pattern": pattern, "results": results}

    def _op_read_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = params.get("path", "")
        max_bytes = int(params.get("max_bytes", 500000))
        p = Path(path_str).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"File '{path_str}' not found.")

        raw_bytes = p.read_bytes()
        truncated = len(raw_bytes) > max_bytes
        content_bytes = raw_bytes[:max_bytes]
        text_content = content_bytes.decode("utf-8", errors="replace")

        return {
            "path": str(p),
            "size_bytes": len(raw_bytes),
            "content": text_content,
            "truncated": truncated,
        }

    def _op_write_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = params.get("path", "")
        content = params.get("content", "")
        p = Path(path_str).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(content, str):
            p.write_text(content, encoding="utf-8")
            written_bytes = len(content.encode("utf-8"))
        else:
            p.write_bytes(content)
            written_bytes = len(content)

        return {"path": str(p), "bytes_written": written_bytes, "status": "written"}

    def _op_create_directory(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = params.get("path", "")
        p = Path(path_str).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return {"path": str(p), "status": "created"}

    def _op_copy_file(self, params: dict[str, Any]) -> dict[str, Any]:
        src = Path(params.get("src_path", "")).resolve()
        dst = Path(params.get("dst_path", "")).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Source file '{src}' does not exist.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return {"src": str(src), "dst": str(dst), "status": "copied"}

    def _op_move_file(self, params: dict[str, Any]) -> dict[str, Any]:
        src = Path(params.get("src_path", "")).resolve()
        dst = Path(params.get("dst_path", "")).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Source path '{src}' does not exist.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {"src": str(src), "dst": str(dst), "status": "moved"}

    def _op_rename_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = params.get("path", "")
        new_name = params.get("new_name", "")
        p = Path(path_str).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Target '{p}' does not exist.")
        new_path = p.parent / new_name
        p.rename(new_path)
        return {"old_path": str(p), "new_path": str(new_path), "status": "renamed"}

    def _op_delete_file(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = params.get("path", "")
        p = Path(path_str).resolve()
        if not p.exists():
            raise FileNotFoundError(f"File or directory '{p}' does not exist.")

        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()

        return {"path": str(p), "status": "deleted"}

    def _op_file_metadata(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = params.get("path", "")
        p = Path(path_str).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Path '{p}' not found.")
        st = p.stat()
        return {
            "path": str(p),
            "is_file": p.is_file(),
            "is_dir": p.is_dir(),
            "size_bytes": st.st_size,
            "created_at": st.st_ctime,
            "modified_at": st.st_mtime,
            "accessed_at": st.st_atime,
            "drive": p.drive,
        }

    # ── Process & Application Handlers ───────────────────────────────────────

    def _op_launch_application(self, params: dict[str, Any]) -> dict[str, Any]:
        app_name_or_path = params.get("app_name", "")
        args = params.get("args", [])

        # Support shell apps (e.g. notepad.exe, calc.exe)
        cmd = [app_name_or_path] + [str(a) for a in args]
        proc = subprocess.Popen(cmd)
        return {
            "app": app_name_or_path,
            "pid": proc.pid,
            "status": "launched",
        }

    def _op_open_with_associated(self, params: dict[str, Any]) -> dict[str, Any]:
        file_path = params.get("path", "")
        p = Path(file_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"File '{p}' does not exist.")
        if os.name == "nt":
            os.startfile(str(p))
            return {"path": str(p), "status": "opened_with_associated_app"}
        subprocess.Popen(["xdg-open", str(p)])
        return {"path": str(p), "status": "opened"}

    def _op_open_in_explorer(self, params: dict[str, Any]) -> dict[str, Any]:
        path_str = params.get("path", "")
        p = Path(path_str).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Target '{p}' does not exist.")

        if p.is_file():
            cmd = ["explorer.exe", f"/select,{p}"]
        else:
            cmd = ["explorer.exe", str(p)]
        subprocess.Popen(cmd)
        return {"path": str(p), "status": "explorer_opened"}

    def _op_list_processes(self, params: dict[str, Any]) -> dict[str, Any]:
        import psutil
        limit = int(params.get("limit", 150))
        name_filter = params.get("name_filter", "").lower()
        procs = []

        for p in psutil.process_iter(["pid", "name", "username", "memory_percent", "cpu_percent"]):
            try:
                info = p.info
                pname = (info.get("name") or "").lower()
                if name_filter and name_filter not in pname:
                    continue
                procs.append({
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "username": info.get("username"),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 1),
                })
                if len(procs) >= limit:
                    break
            except Exception:  # noqa: BLE001, S112
                continue

        return {"count": len(procs), "processes": procs}

    def _op_inspect_process(self, params: dict[str, Any]) -> dict[str, Any]:
        import psutil
        pid = int(params.get("pid", 0))
        if not psutil.pid_exists(pid):
            raise ProcessLookupError(f"Process PID {pid} does not exist.")

        proc = psutil.Process(pid)
        mem_pct = 0.0
        try:
            mem_pct = round(proc.memory_percent(), 2)
        except Exception:  # noqa: BLE001, S110
            pass
        return {
            "pid": pid,
            "name": proc.name(),
            "status": proc.status(),
            "cpu_percent": proc.cpu_percent(interval=0.1),
            "memory_mb": round(proc.memory_info().rss / (1024 * 1024), 2),
            "memory_percent": mem_pct,
            "num_threads": proc.num_threads(),
            "create_time": proc.create_time(),
            "cmdline": proc.cmdline() if hasattr(proc, "cmdline") else [],
        }

    def _op_start_process(self, params: dict[str, Any]) -> dict[str, Any]:
        command = params.get("command", "")
        cwd = params.get("cwd")
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return {"command": command, "pid": proc.pid, "status": "started"}

    def _op_stop_process(self, params: dict[str, Any]) -> dict[str, Any]:
        import psutil
        pid = int(params.get("pid", 0))
        if not psutil.pid_exists(pid):
            raise ProcessLookupError(f"PID {pid} not found.")
        proc = psutil.Process(pid)
        proc.terminate()
        return {"pid": pid, "status": "terminated"}

    # ── System Operation Handlers ────────────────────────────────────────────

    def _op_list_services(self, params: dict[str, Any]) -> dict[str, Any]:
        services = []
        if os.name == "nt":
            try:
                import win32service
                manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE)
                statuses = win32service.EnumServicesStatus(manager)
                for svc in statuses[:150]:
                    services.append({
                        "name": svc[0],
                        "display_name": svc[1],
                        "status": "RUNNING" if svc[2][1] == win32service.SERVICE_RUNNING else "STOPPED",
                    })
                win32service.CloseServiceHandle(manager)
            except Exception as ex:  # noqa: BLE001
                logger.warning("Could not list services via Win32: %s", ex)

        return {"count": len(services), "services": services}

    def _op_inspect_service(self, params: dict[str, Any]) -> dict[str, Any]:
        service_name = params.get("service_name", "")
        if os.name != "nt":
            return {"name": service_name, "status": "UNKNOWN"}

        try:
            import win32service
            manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            svc_handle = win32service.OpenService(manager, service_name, win32service.SERVICE_QUERY_STATUS | win32service.SERVICE_QUERY_CONFIG)
            status = win32service.QueryServiceStatus(svc_handle)
            config = win32service.QueryServiceConfig(svc_handle)
            win32service.CloseServiceHandle(svc_handle)
            win32service.CloseServiceHandle(manager)

            status_str = "RUNNING" if status[1] == win32service.SERVICE_RUNNING else "STOPPED"
            return {
                "name": service_name,
                "display_name": config[1],
                "binary_path": config[3],
                "start_type": config[2],
                "status": status_str,
            }
        except Exception as ex:  # noqa: BLE001
            return {"name": service_name, "status": "UNKNOWN", "error": str(ex)}

    def _op_get_system_telemetry(self, params: dict[str, Any]) -> dict[str, Any]:
        import psutil
        return {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "ram_percent": psutil.virtual_memory().percent,
            "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "ram_available_gb": round(psutil.virtual_memory().available / (1024**3), 2),
            "drives": discover_mounted_drives(),
            "uptime_seconds": time.time() - psutil.boot_time(),
        }

    def _op_get_execution_identity(self, params: dict[str, Any]) -> dict[str, Any]:
        ident = get_current_runtime_identity()
        return ident.to_dict()
