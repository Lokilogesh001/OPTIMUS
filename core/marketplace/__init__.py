"""core/marketplace/__init__.py — OPTIMUS Plugin Discovery & Marketplace Subsystem."""
from core.marketplace.installer import (
    InstallationError,
    IntegrityCheckFailed,
    PluginInstaller,
)
from core.marketplace.manager import (
    MarketplaceManager,
    MarketplacePluginRecord,
    PluginLifecycleState,
)
from core.marketplace.scanner import (
    LicenseCategory,
    MarketplaceSecurityScanner,
    SecurityAssessment,
    levenshtein_distance,
)
from core.marketplace.sources import (
    DiscoveredPlugin,
    MarketplaceHarvester,
    MarketplaceSource,
    PluginExecutionType,
    SSRFHardenedClient,
    SSRFSecurityViolation,
    encapsulate_untrusted_text,
)

__all__ = [
    "DiscoveredPlugin",
    "InstallationError",
    "IntegrityCheckFailed",
    "LicenseCategory",
    "MarketplaceHarvester",
    "MarketplaceManager",
    "MarketplacePluginRecord",
    "MarketplaceSecurityScanner",
    "MarketplaceSource",
    "PluginExecutionType",
    "PluginInstaller",
    "PluginLifecycleState",
    "SSRFHardenedClient",
    "SSRFSecurityViolation",
    "SecurityAssessment",
    "encapsulate_untrusted_text",
    "levenshtein_distance",
]
