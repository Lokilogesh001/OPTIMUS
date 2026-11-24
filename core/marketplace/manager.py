"""core/marketplace/manager.py — Central Plugin Discovery & Marketplace Subsystem Manager.

Orchestrates the complete lifecycle:
DISCOVERED -> INSPECTED -> REVIEWED -> INSTALLED -> TESTED -> ENABLED
                                           ↓          ↓
                                         FAILED    DISABLED / REMOVED

Integrates with:
- MarketplaceHarvester (discovery across registries)
- MarketplaceSecurityScanner (AST, typosquatting, Zip-Slip, licenses)
- PluginInstaller (isolated virtualenvs, atomic rollback, SHA-256 verification)
- UniversalCapabilityRegistry / ToolRegistry (detects duplicates and native equivalency)
- config/marketplace_cache.json (persistent catalog and state storage)
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.marketplace.installer import PluginInstaller
from core.marketplace.scanner import (
    CORE_TOOL_NAMES,
    LicenseCategory,
    MarketplaceSecurityScanner,
    SecurityAssessment,
)
from core.marketplace.sources import (
    DiscoveredPlugin,
    MarketplaceHarvester,
    MarketplaceSource,
    PluginExecutionType,
)

logger = logging.getLogger("optimus.marketplace.manager")

DEFAULT_CACHE_FILE = Path("config/marketplace_cache.json")


class PluginLifecycleState(str, Enum):
    DISCOVERED = "discovered"
    INSPECTED = "inspected"
    REVIEWED = "reviewed"
    INSTALLED = "installed"
    TESTED = "tested"
    ENABLED = "enabled"
    DISABLED = "disabled"
    FAILED = "failed"
    REJECTED = "rejected"
    REMOVED = "removed"


@dataclass
class MarketplacePluginRecord:
    plugin: DiscoveredPlugin
    state: PluginLifecycleState
    assessment: Optional[SecurityAssessment] = None
    native_equivalent_tool: Optional[str] = None
    install_path: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plugin": self.plugin.to_dict(),
            "state": self.state.value,
            "assessment": self.assessment.to_dict() if self.assessment else None,
            "native_equivalent_tool": self.native_equivalent_tool,
            "install_path": self.install_path,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MarketplacePluginRecord:
        plugin = DiscoveredPlugin.from_dict(data["plugin"])
        assessment = None
        if data.get("assessment"):
            a_data = data["assessment"]
            assessment = SecurityAssessment(
                is_safe=a_data["is_safe"],
                score=a_data["score"],
                warnings=a_data.get("warnings", []),
                findings=a_data.get("findings", []),
                license_category=LicenseCategory(a_data.get("license_category", LicenseCategory.UNKNOWN.value)),
                typosquat_target=a_data.get("typosquat_target"),
                has_zip_slip=a_data.get("has_zip_slip", False),
                has_build_hooks=a_data.get("has_build_hooks", False),
                ast_safe=a_data.get("ast_safe", True),
            )
        return cls(
            plugin=plugin,
            state=PluginLifecycleState(data.get("state", PluginLifecycleState.DISCOVERED.value)),
            assessment=assessment,
            native_equivalent_tool=data.get("native_equivalent_tool"),
            install_path=data.get("install_path"),
            error_message=data.get("error_message"),
        )


class MarketplaceManager:
    """Coordinates marketplace operations, cache persistence, and security verification."""

    def __init__(
        self,
        cache_path: Optional[Path] = None,
        harvester: Optional[MarketplaceHarvester] = None,
        scanner: Optional[MarketplaceSecurityScanner] = None,
        installer: Optional[PluginInstaller] = None,
        native_tools: Optional[Set[str]] = None,
    ):
        self.cache_path = cache_path or DEFAULT_CACHE_FILE
        self.harvester = harvester or MarketplaceHarvester()
        self.scanner = scanner or MarketplaceSecurityScanner()
        self.installer = installer or PluginInstaller()
        self.native_tools = native_tools or CORE_TOOL_NAMES
        self.records: Dict[str, MarketplacePluginRecord] = {}
        self.load_cache()

    def load_cache(self) -> None:
        """Loads cached marketplace records from disk."""
        if not self.cache_path.exists():
            return
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data.get("items", {})
                for pid, rec_data in items.items():
                    self.records[pid] = MarketplacePluginRecord.from_dict(rec_data)
            logger.info("Loaded %d marketplace records from %s", len(self.records), self.cache_path)
        except Exception as e:
            logger.warning("Failed loading marketplace cache from %s: %s", self.cache_path, e)

    def save_cache(self) -> None:
        """Saves current marketplace records to disk."""
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": "2.0",
                "items": {pid: rec.to_dict() for pid, rec in self.records.items()}
            }
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning("Failed saving marketplace cache to %s: %s", self.cache_path, e)

    def discover(self, query: str = "") -> List[MarketplacePluginRecord]:
        """Harvests plugins and stores them in state DISCOVERED."""
        discovered = self.harvester.harvest_all(query)
        new_or_updated: List[MarketplacePluginRecord] = []

        for p in discovered:
            rec = self.records.get(p.id)
            if not rec:
                rec = MarketplacePluginRecord(plugin=p, state=PluginLifecycleState.DISCOVERED)
                self.records[p.id] = rec
            else:
                rec.plugin = p
            new_or_updated.append(rec)

        self.save_cache()
        return new_or_updated

    def inspect_plugin(self, plugin_id: str, archive_path: Optional[Path] = None) -> MarketplacePluginRecord:
        """Transitions DISCOVERED -> INSPECTED: runs AST scanner, typosquatting, Zip-Slip, and license tests."""
        rec = self.records.get(plugin_id)
        if not rec:
            raise ValueError(f"Plugin '{plugin_id}' not found in marketplace catalog")

        p = rec.plugin
        assessment = self.scanner.assess_plugin(
            name=p.name,
            description=p.description,
            license_str=p.license,
            archive_path=archive_path,
        )
        rec.assessment = assessment

        # Check native equivalence
        clean_name = p.name.lower().replace("-", "_").replace(" ", "_")
        for tool in self.native_tools:
            if clean_name == tool or clean_name in tool or tool in clean_name:
                rec.native_equivalent_tool = tool
                break

        if not assessment.is_safe:
            rec.state = PluginLifecycleState.REJECTED
            rec.error_message = "; ".join(assessment.findings)
        else:
            rec.state = PluginLifecycleState.INSPECTED

        self.save_cache()
        return rec

    def review_plugin(self, plugin_id: str, accept_warnings: bool = False) -> MarketplacePluginRecord:
        """Transitions INSPECTED -> REVIEWED: operator review gate."""
        rec = self.records.get(plugin_id)
        if not rec:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        if rec.state not in (PluginLifecycleState.INSPECTED, PluginLifecycleState.DISCOVERED):
            if rec.state == PluginLifecycleState.REJECTED and not accept_warnings:
                raise ValueError(f"Cannot review rejected plugin '{plugin_id}': {rec.error_message}")

        if rec.state == PluginLifecycleState.DISCOVERED:
            self.inspect_plugin(plugin_id)

        rec.state = PluginLifecycleState.REVIEWED
        self.save_cache()
        return rec

    def install_plugin(
        self,
        plugin_id: str,
        archive_path: Optional[Path] = None,
        expected_hash: Optional[str] = None
    ) -> MarketplacePluginRecord:
        """Transitions REVIEWED -> INSTALLED into isolated virtualenv with rollback guarantees."""
        rec = self.records.get(plugin_id)
        if not rec:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        # Duplicate installation check: if already installed and enabled, block duplicate registration
        if rec.state in (PluginLifecycleState.INSTALLED, PluginLifecycleState.ENABLED, PluginLifecycleState.TESTED):
            logger.info("Plugin '%s' is already installed", plugin_id)
            return rec

        if rec.state not in (PluginLifecycleState.REVIEWED, PluginLifecycleState.INSPECTED):
            self.review_plugin(plugin_id, accept_warnings=True)

        try:
            target_path = self.installer.install_virtualenv(
                plugin_id=plugin_id,
                package_spec=rec.plugin.name if not archive_path else None,
                archive_path=archive_path,
                expected_hash=expected_hash or rec.plugin.integrity_hash,
                metadata=rec.plugin.to_dict(),
            )
            rec.install_path = str(target_path)
            rec.state = PluginLifecycleState.INSTALLED
            rec.error_message = None
        except Exception as e:
            rec.state = PluginLifecycleState.FAILED
            rec.error_message = str(e)
            logger.error("Failed to install plugin '%s': %s", plugin_id, e)
            self.save_cache()
            raise

        self.save_cache()
        return rec

    def test_plugin(self, plugin_id: str) -> MarketplacePluginRecord:
        """Transitions INSTALLED -> TESTED: verifies environment health."""
        rec = self.records.get(plugin_id)
        if not rec:
            raise ValueError(f"Plugin '{plugin_id}' not found")
        if rec.state != PluginLifecycleState.INSTALLED:
            raise ValueError(f"Plugin '{plugin_id}' must be in INSTALLED state to test (currently {rec.state})")

        # Verify installation directory and metadata exists
        if not self.installer.is_installed(plugin_id):
            rec.state = PluginLifecycleState.FAILED
            rec.error_message = "Installation directory missing or corrupt"
            self.save_cache()
            raise ValueError(rec.error_message)

        rec.state = PluginLifecycleState.TESTED
        self.save_cache()
        return rec

    def enable_plugin(self, plugin_id: str) -> MarketplacePluginRecord:
        """Transitions TESTED -> ENABLED: registers into active runtime."""
        rec = self.records.get(plugin_id)
        if not rec:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        if rec.state == PluginLifecycleState.INSTALLED:
            self.test_plugin(plugin_id)

        if rec.state != PluginLifecycleState.TESTED:
            raise ValueError(f"Cannot enable plugin in state '{rec.state}'")

        rec.state = PluginLifecycleState.ENABLED
        self.save_cache()
        return rec

    def disable_plugin(self, plugin_id: str) -> MarketplacePluginRecord:
        """Disables an active plugin without removing files."""
        rec = self.records.get(plugin_id)
        if not rec:
            raise ValueError(f"Plugin '{plugin_id}' not found")

        rec.state = PluginLifecycleState.DISABLED
        self.save_cache()
        return rec

    def remove_plugin(self, plugin_id: str) -> bool:
        """Uninstalls and removes plugin completely."""
        rec = self.records.get(plugin_id)
        if not rec:
            return False

        self.installer.uninstall(plugin_id)
        rec.state = PluginLifecycleState.REMOVED
        rec.install_path = None
        self.save_cache()
        return True
