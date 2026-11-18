"""core/windows_runtime/__init__.py — Public API for Windows Privileged Runtime Subsystem."""

from core.windows_runtime.broker import (
    PrivilegedExecutionBroker,
    get_current_runtime_identity,
)
from core.windows_runtime.client import (
    WindowsRuntimeClient,
    get_windows_runtime_client,
)
from core.windows_runtime.ipc import (
    PIPE_NAME,
    NamedPipeClient,
    NamedPipeServer,
    ReplayProtector,
    get_or_create_shared_secret,
)
from core.windows_runtime.policy import (
    WindowsRuntimePolicy,
    discover_mounted_drives,
)
from core.windows_runtime.service import (
    SERVICE_DESCRIPTION,
    SERVICE_DISPLAY_NAME,
    SERVICE_NAME,
    WindowsPrivilegedDaemon,
    get_service_status,
    install_service,
    start_service,
    stop_service,
    uninstall_service,
)
from core.windows_runtime.types import (
    OperationCategory,
    OperationRequest,
    OperationResponse,
    OperationType,
    PrivilegedAuditRecord,
    RuntimeErrorCode,
    RuntimeIdentity,
)

__all__ = [
    # IPC
    "PIPE_NAME",
    "SERVICE_DESCRIPTION",
    "SERVICE_DISPLAY_NAME",
    # Service Management
    "SERVICE_NAME",
    "NamedPipeClient",
    "NamedPipeServer",
    # Types & Enums
    "OperationCategory",
    "OperationRequest",
    "OperationResponse",
    "OperationType",
    "PrivilegedAuditRecord",
    # Broker & Identity
    "PrivilegedExecutionBroker",
    "ReplayProtector",
    "RuntimeErrorCode",
    "RuntimeIdentity",
    "WindowsPrivilegedDaemon",
    # Client Facade
    "WindowsRuntimeClient",
    # Policy
    "WindowsRuntimePolicy",
    "discover_mounted_drives",
    "get_current_runtime_identity",
    "get_or_create_shared_secret",
    "get_service_status",
    "get_windows_runtime_client",
    "install_service",
    "start_service",
    "stop_service",
    "uninstall_service",
]
