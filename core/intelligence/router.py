"""core/intelligence/router.py — Adaptive Model Router & Policy Engine for OPTIMUS.

Implements:
- Privacy-strict routing enforcing absolute isolation of local-only context.
- Capability-based model filtering (tools, schemas, context window).
- Policy-driven candidate ranking (local-first, cloud-preferred, cost, performance).
- Automatic multi-tier fallback chains.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from core.intelligence.registry import ModelRegistry
from core.intelligence.types import (
    ModelExecutionBackend,
    ModelMetadata,
    ModelRequest,
    ModelResponse,
    NoAvailableModelError,
    PrivacyViolationError,
    RoutingPolicy,
)
from core.runtime.profiles import HardwareProfileType
from core.runtime.resources import AdaptiveResourceManager, MemoryPressureLevel

logger = logging.getLogger("optimus.intelligence.router")


class ModelRouter:
    """Adaptive routing engine directing tasks to appropriate model providers."""

    def __init__(
        self,
        registry: ModelRegistry,
        default_policy: RoutingPolicy = RoutingPolicy.CLOUD_PREFERRED,
    ) -> None:
        self.registry = registry
        self.policy = default_policy

    def set_policy(self, policy: RoutingPolicy) -> None:
        """Update active routing policy."""
        self.policy = policy

    def select_models(self, request: ModelRequest) -> list[ModelMetadata]:
        """Formulate an ordered candidate chain: primary model followed by fallback models."""
        # 1. Base eligibility filter
        candidates = self.registry.list_models(enabled_only=True)
        if not candidates:
            raise NoAvailableModelError("No enabled models found in registry.")

        # 2. Strict Privacy Boundary Enforcement
        # Rule: Privacy restrictions must strictly override performance and cost preferences!
        is_private = request.is_private or (self.policy == RoutingPolicy.PRIVACY_STRICT)
        if is_private:
            candidates = [m for m in candidates if m.is_local]
            if not candidates:
                raise PrivacyViolationError(
                    "Request flagged as private or PRIVACY_STRICT policy is active, "
                    "but no local model is available. Cloud routing strictly blocked."
                )

        # 3. Capability Matching Filter
        if request.required_capabilities:
            candidates = [
                m for m in candidates
                if request.required_capabilities.issubset(m.capabilities)
            ]

        # 4. Context Window Boundary Check
        est_tokens = request.estimated_prompt_tokens
        candidates = [m for m in candidates if m.context_window >= est_tokens]

        if not candidates:
            if is_private:
                raise PrivacyViolationError(
                    f"Request is private or PRIVACY_STRICT is active, but no qualifying local model "
                    f"satisfies capabilities {request.required_capabilities} and context limit."
                )
            raise NoAvailableModelError(
                f"No available model satisfies requirements (caps={request.required_capabilities}, "
                f"tokens={est_tokens}, private={is_private})."
            )

        # 4b. Hardware Profile & Resource Pressure Filter
        try:
            res_mgr = AdaptiveResourceManager.instance()
            profile = res_mgr.get_profile()
            pressure = res_mgr.evaluate_memory_pressure()
            budget = res_mgr.budget

            # On CPU_ONLY profiles, filter out local models that strictly mandate a GPU
            if profile.profile_type == HardwareProfileType.CPU_ONLY:
                cpu_compatible = []
                for m in candidates:
                    if not m.is_local:
                        cpu_compatible.append(m)
                    elif m.execution_backend in (ModelExecutionBackend.CPU_LOCAL, ModelExecutionBackend.CLOUD) or m.min_vram_mb == 0.0:
                        cpu_compatible.append(m)
                if cpu_compatible:
                    candidates = cpu_compatible

            # Under CRITICAL memory pressure, strictly prevent local LLM spin-up to avoid OOM
            if pressure == MemoryPressureLevel.CRITICAL and not is_private:
                cloud_models = [m for m in candidates if not m.is_local]
                if cloud_models:
                    candidates = cloud_models
            elif not budget.allow_local_llm and not is_private:
                cloud_models = [m for m in candidates if not m.is_local]
                if cloud_models:
                    candidates = cloud_models
        except Exception as e:
            logger.debug("Resource manager filter bypassed: %s", e)

        # 5. Circuit Breaker & Credential Health Filter (demote unconfigured or degraded providers)
        healthy_candidates = []
        degraded_candidates = []
        for m in candidates:
            provider = self._find_provider_for_model(m)
            if provider:
                if not getattr(provider, "is_local", False) and not getattr(provider, "api_key", None):
                    degraded_candidates.append(m)
                    continue
                if hasattr(provider, "circuit_breaker") and not provider.circuit_breaker.allow_request():
                    degraded_candidates.append(m)
                    continue
            healthy_candidates.append(m)

        active_pool = healthy_candidates or degraded_candidates

        # 6. Manual model override preference
        if request.preferred_model:
            preferred = [m for m in active_pool if m.model_id == request.preferred_model]
            rest = [m for m in active_pool if m.model_id != request.preferred_model]
            if preferred:
                return preferred + rest

        # 7. Policy-Based Sorting
        if self.policy == RoutingPolicy.LOCAL_FIRST:
            # Local models sorted by error rate, then cloud models as fallback
            active_pool.sort(key=lambda m: (not m.is_local, m.error_rate, m.average_latency_ms))
        elif self.policy == RoutingPolicy.CLOUD_PREFERRED:
            # Cloud models sorted by capability count and context, then local models
            active_pool.sort(key=lambda m: (m.is_local, -len(m.capabilities), -m.context_window))
        elif self.policy == RoutingPolicy.COST_OPTIMIZED:
            # Sorted by total blended price per million tokens
            active_pool.sort(key=lambda m: (m.input_cost_per_million + m.output_cost_per_million, m.error_rate))
        elif self.policy == RoutingPolicy.PERFORMANCE_OPTIMIZED:
            # Sorted by lowest latency and lowest error rate
            active_pool.sort(key=lambda m: (m.average_latency_ms, m.error_rate))
        elif self.policy == RoutingPolicy.PRIVACY_STRICT:
            active_pool.sort(key=lambda m: (m.error_rate, m.average_latency_ms))

        return active_pool

    def _find_provider_for_model(self, model: ModelMetadata) -> Any:
        """Locate the provider instance hosting a given model."""
        for provider in self.registry.list_providers():
            if provider.provider_type == model.provider_type:
                return provider
        return None

    def execute_with_fallback(
        self,
        request: ModelRequest,
        executor_fn: Callable[[ModelMetadata, ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Route request through ordered candidate chain with automatic failover."""
        candidate_chain = self.select_models(request)
        last_error: Exception | None = None
        attempted_models: list[str] = []

        for candidate in candidate_chain:
            provider = self._find_provider_for_model(candidate)
            if provider:
                if not getattr(provider, "is_local", False) and not getattr(provider, "api_key", None):
                    continue
                if hasattr(provider, "circuit_breaker") and not provider.circuit_breaker.allow_request():
                    logger.info("Fast-skipping candidate '%s' due to OPEN circuit breaker on '%s'", candidate.model_id, provider.name)
                    continue

            attempted_models.append(candidate.model_id)
            try:
                # Dispatch execution to provider
                req_copy = ModelRequest(
                    messages=request.messages,
                    tools=request.tools,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    response_schema=request.response_schema,
                    required_capabilities=request.required_capabilities,
                    is_private=request.is_private,
                    preferred_model=candidate.model_id,
                    stream=request.stream,
                    timeout_seconds=request.timeout_seconds,
                )
                response = executor_fn(candidate, req_copy)
                self.registry.record_invocation(candidate.model_id, response.duration_ms, is_error=False)
                return response
            except Exception as ex:
                last_error = ex
                self.registry.record_invocation(candidate.model_id, 0.0, is_error=True)
                logger.warning(
                    f"Model candidate '{candidate.model_id}' failed: {ex}. Falling back to next candidate..."
                )
                continue

        raise NoAvailableModelError(
            f"All candidate models in fallback chain failed: {attempted_models}. Last error: {last_error}"
        )
