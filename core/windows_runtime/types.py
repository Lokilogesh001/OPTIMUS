"""core/windows_runtime/types.py — Typed Contracts & Models for Windows Privileged Runtime.

Defines:
- Supported operation categories (FILESYSTEM, PROCESS, SYSTEM, DIAGNOSTIC)
- Operation types and request/response payloads
- Privilege and identity models
- Error codes and audit event records
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OperationCategory(str, Enum):
    FILESYSTEM = "FILESYSTEM"
    PROCESS = "PROCESS"
    SYSTEM = "SYSTEM"
    DIAGNOSTIC = "DIAGNOSTIC"


class OperationType(str, Enum):
    # Filesystem operations
    DISCOVER_DRIVES = "DISCOVER_DRIVES"
    LIST_DIRECTORY = "LIST_DIRECTORY"
    SEARCH_FILES = "SEARCH_FILES"
    READ_FILE = "READ_FILE"
    WRITE_FILE = "WRITE_FILE"
    CREATE_DIRECTORY = "CREATE_DIRECTORY"
    COPY_FILE = "COPY_FILE"
    MOVE_FILE = "MOVE_FILE"
    RENAME_FILE = "RENAME_FILE"
    DELETE_FILE = "DELETE_FILE"
    FILE_METADATA = "FILE_METADATA"

    # Process and Application operations
    LAUNCH_APPLICATION = "LAUNCH_APPLICATION"
    OPEN_WITH_ASSOCIATED = "OPEN_WITH_ASSOCIATED"
    OPEN_IN_EXPLORER = "OPEN_IN_EXPLORER"
    LIST_PROCESSES = "LIST_PROCESSES"
    INSPECT_PROCESS = "INSPECT_PROCESS"
    START_PROCESS = "START_PROCESS"
    STOP_PROCESS = "STOP_PROCESS"

    # System operations
    INSPECT_SERVICE = "INSPECT_SERVICE"
    LIST_SERVICES = "LIST_SERVICES"
    GET_SYSTEM_TELEMETRY = "GET_SYSTEM_TELEMETRY"
    GET_EXECUTION_IDENTITY = "GET_EXECUTION_IDENTITY"


class RuntimeErrorCode(str, Enum):
    SUCCESS = "SUCCESS"
    UNAUTHORIZED_CLIENT = "UNAUTHORIZED_CLIENT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    INVALID_REQUEST = "INVALID_REQUEST"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    ACCESS_DENIED = "ACCESS_DENIED"
    OPERATION_TIMEOUT = "OPERATION_TIMEOUT"
    PROCESS_FAILED = "PROCESS_FAILED"
    REPLAY_DETECTED = "REPLAY_DETECTED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass
class RuntimeIdentity:
    """Windows security identity of the process executing operations."""
    username: str = ""
    domain: str = ""
    is_admin: bool = False
    is_system: bool = False
    process_id: int = 0
    integrity_level: str = "Medium"

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "domain": self.domain,
            "is_admin": self.is_admin,
            "is_system": self.is_system,
            "process_id": self.process_id,
            "integrity_level": self.integrity_level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeIdentity:
        return cls(
            username=data.get("username", ""),
            domain=data.get("domain", ""),
            is_admin=bool(data.get("is_admin", False)),
            is_system=bool(data.get("is_system", False)),
            process_id=int(data.get("process_id", 0)),
            integrity_level=data.get("integrity_level", "Medium"),
        )


@dataclass
class OperationRequest:
    """Structured, authenticated request delivered to the Privileged Broker."""
    request_id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    operation: OperationType = OperationType.GET_EXECUTION_IDENTITY
    params: dict[str, Any] = field(default_factory=dict)
    client_pid: int = 0
    client_token: str = ""
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)
    is_pre_approved: bool = False
    approval_token: str | None = None
    timeout_seconds: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "operation": self.operation.value,
            "params": self.params,
            "client_pid": self.client_pid,
            "client_token": self.client_token,
            "nonce": self.nonce,
            "timestamp": self.timestamp,
            "is_pre_approved": self.is_pre_approved,
            "approval_token": self.approval_token,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationRequest:
        op_val = data.get("operation", OperationType.GET_EXECUTION_IDENTITY.value)
        return cls(
            request_id=data.get("request_id", f"req_{uuid.uuid4().hex[:12]}"),
            operation=OperationType(op_val),
            params=dict(data.get("params", {})),
            client_pid=int(data.get("client_pid", 0)),
            client_token=data.get("client_token", ""),
            nonce=data.get("nonce", uuid.uuid4().hex),
            timestamp=float(data.get("timestamp", time.time())),
            is_pre_approved=bool(data.get("is_pre_approved", False)),
            approval_token=data.get("approval_token"),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
        )


@dataclass
class OperationResponse:
    """Structured result returned by the Privileged Execution Broker."""
    request_id: str
    success: bool
    error_code: RuntimeErrorCode = RuntimeErrorCode.SUCCESS
    error_message: str = ""
    data: Any = None
    execution_identity: RuntimeIdentity | None = None
    duration_ms: float = 0.0
    requires_approval: bool = False
    approval_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "success": self.success,
            "error_code": self.error_code.value,
            "error_message": self.error_message,
            "data": self.data,
            "execution_identity": self.execution_identity.to_dict() if self.execution_identity else None,
            "duration_ms": self.duration_ms,
            "requires_approval": self.requires_approval,
            "approval_reason": self.approval_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationResponse:
        ec_val = data.get("error_code", RuntimeErrorCode.SUCCESS.value)
        ident_data = data.get("execution_identity")
        ident = RuntimeIdentity.from_dict(ident_data) if ident_data else None
        return cls(
            request_id=data.get("request_id", ""),
            success=bool(data.get("success", False)),
            error_code=RuntimeErrorCode(ec_val),
            error_message=data.get("error_message", ""),
            data=data.get("data"),
            execution_identity=ident,
            duration_ms=float(data.get("duration_ms", 0.0)),
            requires_approval=bool(data.get("requires_approval", False)),
            approval_reason=data.get("approval_reason", ""),
        )


@dataclass
class PrivilegedAuditRecord:
    """Persistent audit ledger record for all privileged operations."""
    audit_id: str = field(default_factory=lambda: f"aud_{uuid.uuid4().hex[:12]}")
    request_id: str = ""
    operation: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    error_code: str = ""
    client_pid: int = 0
    execution_user: str = ""
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "request_id": self.request_id,
            "operation": self.operation,
            "params": self.params,
            "success": self.success,
            "error_code": self.error_code,
            "client_pid": self.client_pid,
            "execution_user": self.execution_user,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
        }
