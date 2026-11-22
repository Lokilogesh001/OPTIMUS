"""core/application_intelligence/frameworks/ufo/ufo_desktop_provider.py — UFO² Desktop Agent Provider.

Exposes the full Microsoft UFO² desktop-agent runtime as an authoritative BaseExecutionProvider
in OPTIMUS's ProviderRegistry (M19).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.application_intelligence.frameworks.base import (
    BaseExecutionProvider,
    ExecutionRequest,
    ExecutionResponse,
    ProviderHealthStatus,
)
from core.application_intelligence.frameworks.ufo.compat import ensure_ufo_environment
from core.application_intelligence.frameworks.ufo.mission_runner import UFODesktopMissionRunner
from core.application_intelligence.types import ActionContract, AppInteractionTier

logger = logging.getLogger("optimus.application_intelligence.frameworks.ufo.provider")


class UFODesktopAgentProvider(BaseExecutionProvider):
    """Full Microsoft UFO² desktop-agent execution provider (HostAgent, AppAgents, FastMCP)."""

    name = "ufo_desktop_agent"
    requires_desktop_lock = True  # HostAgent / AppAgent interactively activate and focus windows

    def __init__(self, ufo_root: str | None = None) -> None:
        super().__init__()
        self.runner = UFODesktopMissionRunner(ufo_root=ufo_root)

    def health_probe(self) -> bool:
        """Synthetic non-mutating check verifying UFO FastMCP servers and configuration are ready."""
        try:
            from ufo.client.mcp.mcp_registry import MCPRegistry
            servers = MCPRegistry.list()
            return len(servers) >= 4
        except Exception as e:
            logger.debug("UFODesktopAgentProvider health probe failed: %s", e)
            return False

    def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        """Execute a delegated desktop mission through the UFO² HostAgent/AppAgent state machine."""
        sec_err = self.validate_security(request)
        if sec_err:
            return ExecutionResponse(
                action_id=request.action_id,
                status="failed",
                error_message=sec_err,
                facts=[f"security_check_failed: {sec_err}"],
            )

        start = time.perf_counter()

        # Build ActionContract from ExecutionRequest
        allowed_apps = request.parameters.get("allowed_apps", [])
        if not allowed_apps and request.target_app:
            allowed_apps = [request.target_app]

        contract = ActionContract(
            action_id=request.action_id,
            target_app=request.target_app,
            operation=request.operation,
            parameters=request.parameters,
            tier=AppInteractionTier.TIER_5_UFO_DESKTOP_AGENT,
            is_destructive=bool(request.parameters.get("is_destructive", False)),
            requires_approval=bool(request.parameters.get("requires_approval", False)),
            approval_token=request.approval_token,
            session_nonce=request.session_nonce,
            caller_identity=request.caller_identity,
            allowed_apps=allowed_apps,
            allowed_operations=request.parameters.get("allowed_operations", [request.operation]),
        )

        evidence = self.runner.execute_mission(
            contract=contract,
            timeout=request.timeout,
            max_steps=request.parameters.get("max_steps", 15),
        )

        dur = (time.perf_counter() - start) * 1000.0

        if evidence.status == "done":
            self.success_count += 1
            self.total_latency_ms += dur
            self.consecutive_failures = 0
            self.health_status = ProviderHealthStatus.AVAILABLE
        else:
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            if self.consecutive_failures >= 3:
                self.health_status = ProviderHealthStatus.UNAVAILABLE

        return ExecutionResponse(
            action_id=evidence.action_id,
            status=evidence.status,
            facts=evidence.facts,
            evidence_data=evidence.evidence_data,
            error_message=evidence.error_message,
            duration_ms=dur,
            output_data=evidence.evidence_data,
        )

    def rollback(self, request: ExecutionRequest, response: ExecutionResponse) -> bool:
        """Polymorphic rollback: Clean up open documents or active state on mission failure."""
        logger.info("Executing UFODesktopAgentProvider polymorphic rollback for %s", request.action_id)
        # Attempt to reset UFO server state
        try:
            from ufo.client.mcp.local_servers.ui_mcp_server import UIServerState
            state = UIServerState()
            if hasattr(state, "puppeteer") and state.puppeteer:
                state.puppeteer.command_queue.clear()
            return True
        except Exception:
            return False
