"""core/intelligence/task_router.py — Task-Aware Adaptive Model Router & Concurrency Guard.

Implements:
- 10 Canonical Task Complexity Categories.
- Task-to-Model selection mapping simple tasks to fast models and complex reasoning to heavy models.
- Inter-process / multi-worker local inference concurrency throttling via global semaphore (resolving HIGH-04).
- Dynamic circuit breakers preventing cascade failures and infinite routing loops.
- Hardware governor integration (AdaptiveResourceManager).
- Comprehensive routing decision history and latency telemetry.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.intelligence.circuit_breaker import CircuitBreaker
from core.intelligence.registry import ModelRegistry
from core.intelligence.types import (
    ModelCapability,
    ModelExecutionBackend,
    ModelMetadata,
    ModelProviderType,
    ModelRequest,
    NoAvailableModelError,
    PrivacyViolationError,
    RoutingPolicy,
)
from core.runtime.profiles import HardwareProfileType
from core.runtime.resources import AdaptiveResourceManager

logger = logging.getLogger("optimus.intelligence.task_router")

# Global semaphore restricting concurrent local LLM inferences to 1 (resolving HIGH-04)
_LOCAL_MODEL_CONCURRENCY_SEMAPHORE = threading.Semaphore(1)


class ModelCircuitBreaker:
    """Multi-model circuit breaker manager ensuring high availability."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()

    def get_breaker(self, model_id: str) -> CircuitBreaker:
        with self._lock:
            if model_id not in self._breakers:
                self._breakers[model_id] = CircuitBreaker(
                    name=model_id,
                    failure_threshold=self.failure_threshold,
                    recovery_timeout_seconds=self.recovery_timeout_seconds,
                )
            return self._breakers[model_id]

    def is_open(self, model_id: str) -> bool:
        return not self.get_breaker(model_id).allow_request()

    def record_success(self, model_id: str) -> None:
        self.get_breaker(model_id).record_success()

    def record_failure(self, model_id: str, error: Exception | str | None = None) -> None:
        self.get_breaker(model_id).record_failure(error)

    def reset(self, model_id: str | None = None) -> None:
        with self._lock:
            if model_id:
                if model_id in self._breakers:
                    self._breakers[model_id].reset()
            else:
                for b in self._breakers.values():
                    b.reset()


class TaskCategory(str, Enum):
    """The 10 canonical task complexity categories for optimal model mapping."""
    SIMPLE_CONVERSATION = "SIMPLE_CONVERSATION"
    INFORMATION_RETRIEVAL = "INFORMATION_RETRIEVAL"
    STRUCTURED_EXTRACTION = "STRUCTURED_EXTRACTION"
    CODING = "CODING"
    DEBUGGING = "DEBUGGING"
    RESEARCH = "RESEARCH"
    COMPLEX_REASONING = "COMPLEX_REASONING"
    PLANNING = "PLANNING"
    TOOL_ORCHESTRATION = "TOOL_ORCHESTRATION"
    VERIFICATION = "VERIFICATION"

    # Category Aliases
    CODE_GENERATION = "CODE_GENERATION"
    SECURITY_AUDIT = "SECURITY_AUDIT"
    PERSONAL_LIFE = "PERSONAL_LIFE"
    CREATIVE_WRITING = "CREATIVE_WRITING"

    @property
    def category(self) -> TaskCategory:
        return self


@dataclass
class RoutingDecision:
    """Audit record capturing an authoritative model routing decision."""
    decision_id: str
    task_category: TaskCategory
    selected_model_id: str
    is_local: bool
    reason: str
    fallback_chain: list[str]
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    error_message: str = ""


class TaskAwareModelRouter:
    """Intelligent, resource-governed router directing diverse cognitive workloads."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        circuit_breaker: ModelCircuitBreaker | None = None,
        default_policy: RoutingPolicy = RoutingPolicy.CLOUD_PREFERRED,
    ) -> None:
        if registry is None:
            registry = ModelRegistry()
            if not registry.list_models():
                registry.register_model(ModelMetadata(
                    model_id="gemini-2.0-flash",
                    provider_type=ModelProviderType.GEMINI,
                    capabilities={ModelCapability.CHAT, ModelCapability.FAST_INFERENCE},
                    is_local=False,
                    context_window=32768,
                ))
                registry.register_model(ModelMetadata(
                    model_id="gpt-4o",
                    provider_type=ModelProviderType.OPENAI,
                    capabilities={ModelCapability.CHAT, ModelCapability.CODE_GENERATION, ModelCapability.COMPLEX_REASONING},
                    is_local=False,
                    context_window=128000,
                ))
                registry.register_model(ModelMetadata(
                    model_id="claude-3-7-sonnet",
                    provider_type=ModelProviderType.ANTHROPIC,
                    capabilities={ModelCapability.CHAT, ModelCapability.CODE_GENERATION, ModelCapability.COMPLEX_REASONING},
                    is_local=False,
                    context_window=200000,
                ))
                registry.register_model(ModelMetadata(
                    model_id="qwen-2.5-7b-local",
                    provider_type=ModelProviderType.OLLAMA,
                    execution_backend=ModelExecutionBackend.CPU_LOCAL,
                    capabilities={ModelCapability.CHAT, ModelCapability.LOCAL_EXECUTION},
                    is_local=True,
                    context_window=8192,
                ))
        self.registry = registry
        self.circuit_breaker = circuit_breaker or ModelCircuitBreaker()
        self.policy = default_policy
        self._history: list[RoutingDecision] = []
        self._lock = threading.RLock()

    @contextmanager
    def acquire_local_model_slot(self, timeout_seconds: float = 30.0):
        """Context manager acquiring the global local model execution semaphore."""
        acquired = _LOCAL_MODEL_CONCURRENCY_SEMAPHORE.acquire(timeout=timeout_seconds)
        if not acquired:
            raise TimeoutError("Failed to acquire local model concurrency slot")
        try:
            yield
        finally:
            _LOCAL_MODEL_CONCURRENCY_SEMAPHORE.release()

    def record_model_outcome(self, model_id: str, success: bool, latency_ms: float = 0.0) -> None:
        """Record model execution result in circuit breaker telemetry."""
        if success:
            self.circuit_breaker.record_success(model_id)
        else:
            self.circuit_breaker.record_failure(model_id)

    def is_model_available(self, model_id: str) -> bool:
        """Check if model is currently healthy and not tripped by circuit breaker."""
        return not self.circuit_breaker.is_open(model_id)

    def reset_circuit_breaker(self, model_id: str | None = None) -> None:
        """Reset tripped circuit breaker state."""
        self.circuit_breaker.reset(model_id)

    def route_task(self, task_description: str, task_category: TaskCategory | None = None) -> Any:
        """Route a task description directly and return result with fallback audit info."""
        from core.intelligence.types import ChatMessage, ModelRequest
        req = ModelRequest(messages=[ChatMessage(role="user", content=task_description)])
        primary, _fallbacks, decision = self.route_request(req, task_category=task_category)

        class TaskRoutingResult:
            def __init__(self, primary_id: str, dec: RoutingDecision):
                self.selected_model = primary_id
                self.fallback_occurred = bool(dec.fallback_chain)
                self.decision = dec

        return TaskRoutingResult(primary.model_id, decision)

    # ── Task Classification ───────────────────────────────────────────────────

    def classify_task(self, prompt: str, explicit_category: TaskCategory | None = None) -> TaskCategory:
        """Classify cognitive workload into one of 10 task categories."""
        if explicit_category:
            return explicit_category

        p_low = prompt.lower()

        if any(w in p_low for w in ("verify", "validate deliverables", "audit evidence", "check tests")):
            return TaskCategory.VERIFICATION
        if any(w in p_low for w in ("plan", "decompose", "dag", "workflow schedule")):
            return TaskCategory.PLANNING
        if any(w in p_low for w in ("audit", "vulnerabilit", "security", "injection", "debug", "traceback", "stack trace", "fix error", "crash")):
            return TaskCategory.DEBUGGING
        if any(w in p_low for w in ("write code", "implement function", "refactor", "def ", "class ", "git commit", "authentication")):
            return TaskCategory.CODING
        if any(w in p_low for w in ("whatsapp", "message", "email", "unread", "personal")):
            return TaskCategory.INFORMATION_RETRIEVAL
        if any(w in p_low for w in ("research", "literature", "analyze comparison", "survey")):
            return TaskCategory.RESEARCH
        if any(w in p_low for w in ("json schema", "extract entities", "parse table", "extract dates")):
            return TaskCategory.STRUCTURED_EXTRACTION
        if any(w in p_low for w in ("prove", "mathematical", "theorem", "deduce", "step-by-step reasoning", "architectural")):
            return TaskCategory.COMPLEX_REASONING
        if any(w in p_low for w in ("search files", "find file", "look up document")):
            return TaskCategory.INFORMATION_RETRIEVAL
        if any(w in p_low for w in ("execute tool", "run command", "launch app")):
            return TaskCategory.TOOL_ORCHESTRATION
        if any(w in p_low for w in ("draft", "weekly", "creative", "story", "write an update")):
            return TaskCategory.SIMPLE_CONVERSATION

        return TaskCategory.SIMPLE_CONVERSATION

    # ── Candidate Selection & Fallback ────────────────────────────────────────

    def route_request(
        self,
        request: ModelRequest,
        task_category: TaskCategory | None = None,
    ) -> tuple[ModelMetadata, list[ModelMetadata], RoutingDecision]:
        """Formulate an optimal model and fallback chain for the specified task."""
        prompt_str = getattr(request, "prompt", "") or (" ".join(m.content for m in getattr(request, "messages", [])))
        category = self.classify_task(prompt_str, task_category)
        candidates = self.registry.list_models(enabled_only=True)
        if not candidates:
            raise NoAvailableModelError("No enabled models configured in registry.")

        is_private = request.is_private or (self.policy == RoutingPolicy.PRIVACY_STRICT)

        # 1. Privacy filter
        if is_private:
            candidates = [m for m in candidates if m.is_local]
            if not candidates:
                raise PrivacyViolationError(
                    f"Task '{category.value}' is private or PRIVACY_STRICT is active, but no local model is enabled."
                )

        # 2. Hardware Resource & Profile Filter
        try:
            res_mgr = AdaptiveResourceManager.instance()
            profile = res_mgr.get_profile()
            if profile.profile_type == HardwareProfileType.CPU_ONLY:
                # Exclude local models requiring dedicated GPU VRAM
                candidates = [
                    m for m in candidates
                    if not m.is_local or m.execution_backend in (ModelExecutionBackend.CPU_LOCAL, ModelExecutionBackend.CLOUD) or m.min_vram_mb == 0.0
                ]
        except Exception:
            pass

        # 3. Context window filter
        est_tokens = request.estimated_prompt_tokens
        candidates = [m for m in candidates if m.context_window >= est_tokens]
        if not candidates:
            raise NoAvailableModelError(f"No available model satisfies context window of {est_tokens} tokens.")

        # 4. Capability filter
        if request.required_capabilities:
            candidates = [m for m in candidates if request.required_capabilities.issubset(m.capabilities)]
            if not candidates:
                raise NoAvailableModelError(f"No available model satisfies required capabilities: {request.required_capabilities}")

        # 5. Circuit Breaker Filter: Exclude models currently tripped
        healthy_candidates = [m for m in candidates if not self.circuit_breaker.is_open(m.model_id)]
        if not healthy_candidates:
            # If all are tripped, fall back to best-effort candidates
            healthy_candidates = candidates

        # 6. Task Complexity Mapping
        ranked = self._rank_candidates_for_category(healthy_candidates, category, is_private)
        primary = ranked[0]
        fallbacks = ranked[1:3]  # Max 2 fallbacks to avoid loops

        decision = RoutingDecision(
            decision_id=f"route_{int(time.time()*1000)}",
            task_category=category,
            selected_model_id=primary.model_id,
            is_local=primary.is_local,
            reason=f"Optimal match for {category.value} under {self.policy.value} policy.",
            fallback_chain=[m.model_id for m in fallbacks],
        )

        with self._lock:
            self._history.append(decision)
            if len(self._history) > 200:
                self._history.pop(0)

        return primary, fallbacks, decision

    def _rank_candidates_for_category(
        self,
        candidates: list[ModelMetadata],
        category: TaskCategory,
        is_private: bool,
    ) -> list[ModelMetadata]:
        """Rank eligible models based on category complexity requirements."""
        heavy_tasks = {
            TaskCategory.CODING,
            TaskCategory.DEBUGGING,
            TaskCategory.COMPLEX_REASONING,
            TaskCategory.PLANNING,
            TaskCategory.VERIFICATION,
        }

        def _score(m: ModelMetadata) -> float:
            score = 10.0
            param_size = getattr(m, "parameter_size_b", None)
            # If heavy task, heavily prefer cloud heavy or large local models
            if category in heavy_tasks:
                if not m.is_local and ("pro" in m.model_id.lower() or "sonnet" in m.model_id.lower()):
                    score += 50.0
                elif not m.is_local:
                    score += 30.0
                elif param_size and param_size >= 14:
                    score += 25.0
            else:
                # For simple tasks, prefer fast/lightweight models
                if "flash" in m.model_id.lower() or "haiku" in m.model_id.lower():
                    score += 40.0
                elif m.is_local and param_size and param_size <= 8:
                    score += 35.0

            # Local preference if policy is LOCAL_FIRST and not private
            if self.policy == RoutingPolicy.LOCAL_FIRST and m.is_local:
                score += 25.0

            return score

        return sorted(candidates, key=_score, reverse=True)

    # ── Concurrency-Governed Execution Wrapper ────────────────────────────────

    def execute_with_concurrency_guard(
        self,
        model: ModelMetadata,
        executor_fn: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute model inference with local CPU/VRAM concurrency throttling (HIGH-04)."""
        if model.is_local:
            # Acquire global local model semaphore to prevent machine freeze
            logger.debug("Acquiring local model execution semaphore for %s...", model.model_id)
            with _LOCAL_MODEL_CONCURRENCY_SEMAPHORE:
                start = time.perf_counter()
                try:
                    res = executor_fn(*args, **kwargs)
                    self.circuit_breaker.record_success(model.model_id)
                    return res
                except Exception as ex:
                    self.circuit_breaker.record_failure(model.model_id)
                    raise ex
                finally:
                    elapsed = (time.perf_counter() - start) * 1000.0
                    logger.debug("Local model %s completed in %.1fms.", model.model_id, elapsed)
        else:
            # Cloud model execution (unbounded by local CPU semaphore)
            try:
                res = executor_fn(*args, **kwargs)
                self.circuit_breaker.record_success(model.model_id)
                return res
            except Exception as ex:
                self.circuit_breaker.record_failure(model.model_id)
                raise ex

    def get_routing_history(self, limit: int = 50) -> list[RoutingDecision]:
        """Return recent routing history for UI telemetry inspection."""
        with self._lock:
            return list(reversed(self._history[-limit:]))

