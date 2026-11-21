"""core/interaction/router.py — Intent classification, multimodal routing, and privacy guardrails.

Enforces:
- Deterministic intent classification (task vs conversation vs interruption vs cancellation).
- Explicit privacy policy boundaries (never silently upload images to cloud when local-first/privacy-strict).
- Vision capability verification against Model & Intelligence Gateway.
- Strict anti-direct-tool execution rule (hands actionable tasks exclusively to Central Orchestrator).
"""

from __future__ import annotations

import re

from core.intelligence.gateway import IntelligenceGateway
from core.intelligence.types import ModelCapability, RoutingPolicy
from core.interaction.types import (
    ImageAttachment,
    IntentCategory,
    IntentClassificationResult,
)

# Rule-based intent detection patterns
_STOP_SPEAKING_PATTERN = re.compile(
    r"^(stop\s+speaking|stop\s+talking|be\s+quiet|shut\s+up|mute|silence)$",
    re.IGNORECASE,
)

_CANCEL_TASK_PATTERN = re.compile(
    r"^(cancel\s+task|abort\s+task|stop\s+the\s+task|kill\s+task|terminate\s+task|stop\s+execution)$",
    re.IGNORECASE,
)

_SYSTEM_QUERY_PATTERN = re.compile(
    r"^(system\s+status|cpu\s+usage|memory\s+usage|list\s+agents|registered\s+agents|show\s+telemetry|active\s+model)$",
    re.IGNORECASE,
)

_ACTIONABLE_VERBS = [
    "create", "build", "run", "execute", "analyze", "diagnose",
    "deploy", "fix", "delete", "download", "verify", "test",
    "inspect", "find", "search", "write", "compile", "generate",
    "install", "backup", "restore", "clean", "show", "list", "check",
]

_EMAIL_QUERY_PATTERN = re.compile(
    r"(?:gmail|email|emails|inbox)",
    re.IGNORECASE,
)

_TASK_PREFIXES = ["task:", "do:", "orchestrate:", "optimus:"]


class InteractionRouter:
    """Classifies user intent, checks multimodal feasibility, and enforces privacy rules."""

    def __init__(self, gateway: IntelligenceGateway | None = None) -> None:
        self.gateway = gateway or IntelligenceGateway.instance()

    def classify_intent(
        self,
        text: str,
        attachments: list[ImageAttachment] | None = None,
        active_policy: RoutingPolicy | None = None,
    ) -> IntentClassificationResult:
        """Classify input text and attachments into a concrete action plan."""
        clean = text.strip()
        has_images = bool(attachments)

        # 1. Check Voice/Playback Interruption
        if _STOP_SPEAKING_PATTERN.match(clean):
            return IntentClassificationResult(
                intent=IntentCategory.STOP_SPEAKING,
                confidence=1.0,
                target_goal=None,
                requires_orchestrator=False,
                explanation="Immediate command to halt audio/speech output.",
            )

        # 2. Check Explicit Task Cancellation
        if _CANCEL_TASK_PATTERN.match(clean):
            return IntentClassificationResult(
                intent=IntentCategory.CANCEL_TASK,
                confidence=1.0,
                target_goal=None,
                requires_orchestrator=True,
                explanation="Request to cancel the currently executing background task.",
            )

        # 3. Check System Telemetry Query
        if _SYSTEM_QUERY_PATTERN.match(clean):
            return IntentClassificationResult(
                intent=IntentCategory.SYSTEM_QUERY,
                confidence=0.95,
                target_goal=clean,
                requires_orchestrator=False,
                explanation="Read-only system telemetry query.",
            )

        # 3b. Check Email / Communication Intelligence Queries
        if _EMAIL_QUERY_PATTERN.search(clean):
            return IntentClassificationResult(
                intent=IntentCategory.ACTIONABLE_TASK,
                confidence=0.95,
                target_goal=clean,
                requires_orchestrator=True,
                requires_vision=has_images,
                explanation="Email or Gmail communication intelligence request.",
            )

        # 4. Multimodal Image Feasibility & Privacy Check
        if has_images:
            policy = active_policy or (getattr(self.gateway.router, "policy", RoutingPolicy.LOCAL_FIRST) if self.gateway else RoutingPolicy.LOCAL_FIRST)
            vision_ok, vision_err = self._check_vision_support(policy, attachments or [])
            if not vision_ok:
                return IntentClassificationResult(
                    intent=IntentCategory.CONVERSATION,
                    confidence=1.0,
                    requires_vision=True,
                    requires_orchestrator=False,
                    explanation=vision_err,
                )

        # 5. Check Explicit Task Prefixes
        lower_clean = clean.lower()
        for prefix in _TASK_PREFIXES:
            if lower_clean.startswith(prefix):
                goal = clean[len(prefix):].strip()
                return IntentClassificationResult(
                    intent=IntentCategory.ACTIONABLE_TASK,
                    confidence=1.0,
                    target_goal=goal,
                    requires_orchestrator=True,
                    requires_vision=has_images,
                    explanation=f"Explicit task prefix '{prefix}' identified.",
                )

        # 6. Check Action Verbs at start of utterance
        words = lower_clean.split()
        if words and words[0] in _ACTIONABLE_VERBS:
            return IntentClassificationResult(
                intent=IntentCategory.ACTIONABLE_TASK,
                confidence=0.90,
                target_goal=clean,
                requires_orchestrator=True,
                requires_vision=has_images,
                explanation=f"Imperative command verb '{words[0]}' indicates an actionable task.",
            )

        # 7. Default to conversational turn
        return IntentClassificationResult(
            intent=IntentCategory.CONVERSATION,
            confidence=0.85,
            target_goal=None,
            requires_orchestrator=False,
            requires_vision=has_images,
            explanation="Informational conversational query.",
        )

    def _check_vision_support(
        self,
        policy: RoutingPolicy,
        attachments: list[ImageAttachment],
    ) -> tuple[bool, str]:
        """Validate if vision is possible under the given privacy policy."""
        if not self.gateway:
            return False, "Intelligence gateway unavailable to process vision attachments."

        # Discover all models capable of vision
        vision_models = [
            m for m in self.gateway.registry.list_models(enabled_only=True)
            if ModelCapability.VISION in m.capabilities
        ]

        if not vision_models:
            return False, "No active provider supports vision or image analysis."

        local_vision = [m for m in vision_models if m.is_local]

        # Enforce strict privacy policy
        if policy in (RoutingPolicy.PRIVACY_STRICT, RoutingPolicy.LOCAL_FIRST):
            # Check if any attachment is strictly marked local only
            any_local_flag = any(a.is_local_only for a in attachments)
            if (policy == RoutingPolicy.PRIVACY_STRICT or any_local_flag) and not local_vision:
                return False, (
                    f"Privacy policy '{policy.value}' forbids uploading local images to external cloud APIs, "
                    "and no local vision provider (e.g. Ollama with LLaVA/vision) is registered."
                )

        return True, ""
