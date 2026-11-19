"""core/security — Central Permission Architecture, Credential Management & Onboarding for OPTIMUS."""

from __future__ import annotations

from core.security.credentials import (
    delete_credential,
    get_credential,
    list_configured_providers,
    mask_credential,
    store_credential,
)
from core.security.onboarding import (
    PROVIDER_METADATA,
    get_onboarding_config,
    is_onboarded,
    save_onboarding_config,
    verify_provider_connection,
)
from core.security.permissions import (
    PermissionManager,
    PermissionProfile,
    PermissionVerdict,
    get_permission_manager,
)
from core.security.policy_engine import (
    ApprovalToken,
    OperationRequest,
    PolicyEngine,
    compute_canonical_hash,
    get_policy_engine,
)

__all__ = [
    "PROVIDER_METADATA",
    "ApprovalToken",
    "OperationRequest",
    "PermissionManager",
    "PermissionProfile",
    "PermissionVerdict",
    "PolicyEngine",
    "compute_canonical_hash",
    "delete_credential",
    "get_credential",
    "get_onboarding_config",
    "get_permission_manager",
    "get_policy_engine",
    "is_onboarded",
    "list_configured_providers",
    "mask_credential",
    "save_onboarding_config",
    "store_credential",
    "verify_provider_connection",
]
