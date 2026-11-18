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

__all__ = [
    "PROVIDER_METADATA",
    "PermissionManager",
    "PermissionProfile",
    "PermissionVerdict",
    "delete_credential",
    "get_credential",
    "get_onboarding_config",
    "get_permission_manager",
    "is_onboarded",
    "list_configured_providers",
    "mask_credential",
    "save_onboarding_config",
    "store_credential",
    "verify_provider_connection",
]
