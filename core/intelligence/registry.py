"""core/intelligence/registry.py — Dynamic Model & Provider Registry for OPTIMUS.

Maintains catalogs of model providers, model metadata, capabilities, availability states,
cost structures, and runtime operational telemetry without requiring system restarts.
"""

from __future__ import annotations

import threading
from typing import Any

from core.intelligence.providers.anthropic import AnthropicProvider
from core.intelligence.providers.base import ModelProvider
from core.intelligence.providers.local_custom import LocalCustomProvider
from core.intelligence.providers.nvidia_nim import NvidiaNimProvider
from core.intelligence.providers.ollama import OllamaProvider
from core.intelligence.providers.openai import OpenAIProvider
from core.intelligence.types import (
    ModelCapability,
    ModelMetadata,
)


class ModelRegistry:
    """Thread-safe registry for model providers, models, and performance telemetry."""

    def __init__(self, auto_seed: bool = True) -> None:
        self._lock = threading.RLock()
        self._providers: dict[str, ModelProvider] = {}
        self._models: dict[str, ModelMetadata] = {}

        if auto_seed:
            self._seed_default_providers()

    def _seed_default_providers(self) -> None:
        """Initialize standard providers and populate catalog."""
        default_providers = [
            LocalCustomProvider(),
            OllamaProvider(),
            OpenAIProvider(),
            AnthropicProvider(),
            NvidiaNimProvider(),
        ]
        for p in default_providers:
            self.register_provider(p)

    def register_provider(self, provider: ModelProvider) -> None:
        """Register a model provider and discover its supported models."""
        with self._lock:
            self._providers[provider.name] = provider
            # Ingest models discovered from provider
            try:
                for model in provider.list_models():
                    self._models[model.model_id] = model
            except Exception:
                pass

    def get_provider(self, name: str) -> ModelProvider | None:
        """Retrieve provider by name."""
        with self._lock:
            return self._providers.get(name)

    def list_providers(self) -> list[ModelProvider]:
        """Return list of all registered providers."""
        with self._lock:
            return list(self._providers.values())

    def register_model(self, model: ModelMetadata) -> None:
        """Register or update a model metadata definition."""
        with self._lock:
            self._models[model.model_id] = model

    def unregister_model(self, model_id: str) -> bool:
        """Deregister a model from the registry."""
        with self._lock:
            return self._models.pop(model_id, None) is not None

    def get_model(self, model_id: str) -> ModelMetadata | None:
        """Fetch model metadata by identifier."""
        with self._lock:
            return self._models.get(model_id)

    def enable_model(self, model_id: str) -> bool:
        """Enable a model dynamically at runtime."""
        with self._lock:
            model = self._models.get(model_id)
            if model:
                model.is_enabled = True
                return True
            return False

    def disable_model(self, model_id: str) -> bool:
        """Disable a model dynamically without restarting."""
        with self._lock:
            model = self._models.get(model_id)
            if model:
                model.is_enabled = False
                return True
            return False

    def list_models(
        self,
        enabled_only: bool = True,
        local_only: bool = False,
        required_capabilities: set[ModelCapability] | None = None,
    ) -> list[ModelMetadata]:
        """Query models filtered by availability, privacy boundary, and capabilities."""
        with self._lock:
            results = []
            for m in self._models.values():
                if enabled_only and not m.is_enabled:
                    continue
                if local_only and not m.is_local:
                    continue
                if required_capabilities and not required_capabilities.issubset(m.capabilities):
                    continue
                results.append(m)
            return results

    def record_invocation(self, model_id: str, duration_ms: float, is_error: bool = False) -> None:
        """Update operational latency and reliability statistics for a model."""
        with self._lock:
            model = self._models.get(model_id)
            if model:
                model.request_count += 1
                model.total_latency_ms += duration_ms
                if is_error:
                    model.error_count += 1

    def get_summary(self) -> dict[str, Any]:
        """Generate registry health and catalog summary."""
        with self._lock:
            return {
                "total_providers": len(self._providers),
                "total_models": len(self._models),
                "enabled_models": len([m for m in self._models.values() if m.is_enabled]),
                "local_models": len([m for m in self._models.values() if m.is_local]),
                "providers": {
                    name: {
                        "type": p.provider_type.value,
                        "is_local": p.is_local,
                        "circuit": p.circuit_breaker.get_status(),
                    }
                    for name, p in self._providers.items()
                },
            }
