"""core/application_intelligence/frameworks/registry.py — Execution Provider Registry.

Manages modular execution providers, 3-state circuit breakers (CLOSED/OPEN/HALF_OPEN),
health probes, and multi-factor provider selection scoring (reconciles MED-3).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from core.application_intelligence.frameworks.base import (
    BaseExecutionProvider,
    ProviderHealthStatus,
)
from core.application_intelligence.types import ActionContract, AppInteractionTier

logger = logging.getLogger("optimus.application_intelligence.frameworks.registry")


class ProviderRegistry:
    """Authoritative registry and circuit-breaker coordinator for execution providers."""

    _instance: ProviderRegistry | None = None
    _lock = threading.RLock()

    FAILURE_THRESHOLD = 3         # 3 consecutive failures trips circuit to OPEN
    COOLDOWN_SECONDS = 60.0       # 60s in OPEN before probing in HALF_OPEN

    def __init__(self) -> None:
        self._providers: dict[str, BaseExecutionProvider] = {}
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> ProviderRegistry:
        """Fetch or initialize the singleton ProviderRegistry."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
                cls._instance._register_default_providers()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (testing hook)."""
        with cls._lock:
            cls._instance = None

    def register_provider(self, provider: BaseExecutionProvider) -> None:
        """Register or update an execution provider."""
        with self._lock:
            self._providers[provider.name] = provider
            logger.info("Registered execution provider '%s' (lock=%s)", provider.name, provider.requires_desktop_lock)

    def get_provider(self, name: str) -> BaseExecutionProvider | None:
        """Retrieve an execution provider by name."""
        with self._lock:
            return self._providers.get(name)

    def list_providers(self) -> list[BaseExecutionProvider]:
        """Return all registered execution providers."""
        with self._lock:
            return list(self._providers.values())

    def record_outcome(self, provider_name: str, success: bool, latency_ms: float = 0.0) -> None:
        """Update provider health metrics and circuit breaker state (reconciles MED-3)."""
        with self._lock:
            provider = self._providers.get(provider_name)
            if not provider:
                return

            if success:
                provider.success_count += 1
                provider.consecutive_failures = 0
                provider.total_latency_ms += latency_ms
                if provider.health_status in (ProviderHealthStatus.PROBING, ProviderHealthStatus.DEGRADED):
                    provider.health_status = ProviderHealthStatus.AVAILABLE
                    logger.info("Circuit CLOSED: Provider '%s' restored to AVAILABLE", provider_name)
            else:
                provider.consecutive_failures += 1
                provider.last_failure_time = time.time()
                if provider.consecutive_failures >= self.FAILURE_THRESHOLD:
                    provider.health_status = ProviderHealthStatus.UNAVAILABLE
                    logger.warning(
                        "Circuit OPEN: Provider '%s' exceeded %d consecutive failures. Set to UNAVAILABLE (cooldown %.0fs)",
                        provider_name, self.FAILURE_THRESHOLD, self.COOLDOWN_SECONDS,
                    )
                else:
                    provider.health_status = ProviderHealthStatus.DEGRADED

    def check_and_update_health(self, provider: BaseExecutionProvider, auto_probe: bool = False) -> ProviderHealthStatus:
        """Evaluate circuit breaker transitions (OPEN -> HALF_OPEN -> CLOSED)."""
        now = time.time()
        if provider.health_status == ProviderHealthStatus.UNAVAILABLE:
            if now - provider.last_failure_time > self.COOLDOWN_SECONDS:
                # Transition to HALF_OPEN / PROBING (MED-3)
                provider.health_status = ProviderHealthStatus.PROBING
                logger.info("Circuit HALF_OPEN: Entered PROBING state for provider '%s'", provider.name)
                if auto_probe:
                    try:
                        if provider.health_probe():
                            provider.health_status = ProviderHealthStatus.AVAILABLE
                            provider.consecutive_failures = 0
                            logger.info("Circuit CLOSED: Health probe passed for '%s'", provider.name)
                        else:
                            provider.last_failure_time = now
                            provider.health_status = ProviderHealthStatus.UNAVAILABLE
                            logger.warning("Health probe failed for '%s'; resetting cooldown", provider.name)
                    except Exception as ex:
                        provider.last_failure_time = now
                        provider.health_status = ProviderHealthStatus.UNAVAILABLE
                        logger.warning("Health probe exception for '%s': %s", provider.name, ex)

        return provider.health_status

    def select_best_provider(
        self,
        contract: ActionContract,
        preferred_tier: AppInteractionTier | None = None,
    ) -> BaseExecutionProvider | None:
        """Select optimal provider via multi-factor scoring (reconciles Section 5)."""
        with self._lock:
            candidates: list[tuple[float, BaseExecutionProvider]] = []
            target_app = (contract.target_app or "").lower()
            operation = (contract.operation or "").lower()

            for provider in self._providers.values():
                status = self.check_and_update_health(provider)
                if status == ProviderHealthStatus.UNAVAILABLE:
                    continue

                score = self._compute_provider_score(provider, contract, preferred_tier)
                if score > 0:
                    candidates.append((score, provider))

            if not candidates:
                return None

            candidates.sort(key=lambda x: x[0], reverse=True)
            chosen = candidates[0][1]
            logger.debug("Selected best provider '%s' (score=%.2f) for %s on %s", chosen.name, candidates[0][0], operation, target_app)
            return chosen

    def _compute_provider_score(
        self,
        provider: BaseExecutionProvider,
        contract: ActionContract,
        preferred_tier: AppInteractionTier | None,
    ) -> float:
        """Score provider based on app affinity, tier rank, reliability, and cost."""
        target_app = (contract.target_app or "").lower()
        operation = (contract.operation or "").lower()
        score = 100.0

        # Provider domain affinity
        if provider.name == "ufo_desktop":
            if any(k in target_app for k in ("word", "excel", "powerpnt", "office", "settings", "explorer")):
                score += 50.0
            if any(k in operation for k in ("com_", "document_", "office_", "export_")):
                score += 40.0

        elif provider.name in ("playwright_browser", "stagehand_intelligence"):
            if any(k in target_app for k in ("chrome", "msedge", "brave", "firefox", "browser", "web")):
                score += 50.0
            if any(k in operation for k in ("navigate", "search", "web_", "dom_", "extract")):
                score += 40.0
            # Prefer deterministic playwright over stagehand for exact locators
            if provider.name == "playwright_browser" and contract.parameters.get("selector"):
                score += 20.0

        elif provider.name == "pywinauto_uia":
            if any(k in operation for k in ("uia_", "value_pattern", "invoke_pattern", "menu_")):
                score += 60.0
            elif not any(k in target_app for k in ("chrome", "msedge", "brave", "firefox", "browser")):
                score += 30.0

        # Health penalties
        if provider.health_status == ProviderHealthStatus.DEGRADED:
            score -= 35.0
        elif provider.health_status == ProviderHealthStatus.PROBING:
            score -= 20.0

        # Latency penalty (1 point per 50ms)
        avg_lat = provider.get_average_latency_ms()
        score -= min(30.0, avg_lat / 50.0)

        return max(0.0, score)

    def _register_default_providers(self) -> None:
        """Instantiate and register default execution backends."""
        try:
            from core.application_intelligence.frameworks.ufo.ufo_backend import UFODesktopBackend
            self.register_provider(UFODesktopBackend())
        except Exception as ex:
            logger.debug("UFODesktopBackend default init deferred: %s", ex)

        try:
            from core.application_intelligence.frameworks.pywinauto.pywinauto_backend import PywinautoExecutionBackend
            self.register_provider(PywinautoExecutionBackend())
        except Exception as ex:
            logger.debug("PywinautoExecutionBackend default init deferred: %s", ex)

        try:
            from core.application_intelligence.frameworks.browser.playwright_backend import PlaywrightBrowserBackend
            self.register_provider(PlaywrightBrowserBackend())
        except Exception as ex:
            logger.debug("PlaywrightBrowserBackend default init deferred: %s", ex)

        try:
            from core.application_intelligence.frameworks.browser.stagehand_intelligence import StagehandBrowserIntelligence
            self.register_provider(StagehandBrowserIntelligence())
        except Exception as ex:
            logger.debug("StagehandBrowserIntelligence default init deferred: %s", ex)

        try:
            from core.application_intelligence.frameworks.omniparser.omniparser_grounding import OmniParserVisualGrounding
            self.register_provider(OmniParserVisualGrounding())
        except Exception as ex:
            logger.debug("OmniParserVisualGrounding default init deferred: %s", ex)
