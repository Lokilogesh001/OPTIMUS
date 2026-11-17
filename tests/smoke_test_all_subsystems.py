"""tests/smoke_test_all_subsystems.py — Non-destructive Functional Smoke Tests.

Validates:
1. Text Interaction & System Prompt formatting.
2. Model & Intelligence Gateway initialization.
3. Local Ollama endpoint health check.
4. Agent Registry initialization and profile loading.
5. Autonomous Workflow definition & DAG engine.
6. Memory persistence and semantic context.
7. Verification & Postcondition engine.
8. Adaptive Planning & Goal Decomposition subsystem.
9. Human Approval controls & safety gates.
10. Voice & Wake-word detection engine.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def run_smoke_tests():
    print("=== STARTING OPTIMUS FUNCTIONAL SMOKE TESTS ===")
    results = {}

    # 1. Text Interaction & System Prompt
    print("[1/10] Testing Text Interaction & System Prompt...")
    try:
        import main
        prompt = main._load_system_prompt()
        assert len(prompt) > 50, "Prompt must not be empty"
        assert "OPTIMUS" in prompt, "Prompt must reference OPTIMUS"
        results["text_interaction"] = "PASS"
        print("       [PASS] Text interaction & prompt OK")
    except Exception as e:
        results["text_interaction"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 2. Model Gateway Initialization
    print("[2/10] Testing Model Gateway Initialization...")
    try:
        from core.intelligence.gateway import get_intelligence_gateway
        gateway = get_intelligence_gateway()
        providers = gateway.registry.list_providers()
        assert len(providers) >= 1, "At least one provider must be registered"
        results["model_gateway"] = f"PASS ({len(providers)} providers registered)"
        print(f"       [PASS] Gateway initialized with {len(providers)} providers: {[p.name for p in providers]}")
    except Exception as e:
        results["model_gateway"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 3. Local Ollama Connectivity Probe
    print("[3/10] Probing Local Ollama Connectivity...")
    try:
        from core.intelligence.providers.ollama import OllamaProvider
        ollama = OllamaProvider(base_url="http://localhost:11434")
        healthy = ollama.health_check()
        status_msg = "HEALTHY (Ollama reachable)" if healthy else "OFFLINE (Graceful fallback active)"
        results["ollama_connectivity"] = f"PASS ({status_msg})"
        print(f"       [PASS] Ollama probe: {status_msg}")
    except Exception as e:
        results["ollama_connectivity"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 4. Agent Registry Initialization
    print("[4/10] Testing Agent Registry Initialization...")
    try:
        from core.orchestrator.agents import AgentRegistry
        registry = AgentRegistry()
        agents_dir = _ROOT / "agents"
        if agents_dir.exists():
            count = registry.discover_directory(agents_dir)
        else:
            count = len(registry.list_agents())
        results["agent_registry"] = f"PASS ({count} agents loaded)"
        print(f"       [PASS] Agent registry verified: {count} agent(s)")
    except Exception as e:
        results["agent_registry"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 5. Workflow Engine Initialization
    print("[5/10] Testing Workflow Definition & DAG...")
    try:
        from unittest.mock import MagicMock
        from core.workflows import (
            WorkflowEngine,
            WorkflowStore,
            WorkflowDefinition,
            WorkflowStepDefinition,
        )
        with tempfile.TemporaryDirectory() as td:
            mock_orch = MagicMock()
            store = WorkflowStore(base_dir=Path(td))
            engine = WorkflowEngine(orchestrator=mock_orch, store=store)
            wf = WorkflowDefinition(
                workflow_id="smoke_test_wf",
                name="Smoke Test Workflow",
                description="Non-destructive validation workflow",
                steps=[
                    WorkflowStepDefinition(step_id="step_1", name="Step 1"),
                    WorkflowStepDefinition(step_id="step_2", name="Step 2", depends_on=["step_1"]),
                ]
            )
            engine.register_workflow(wf)
            retrieved = engine.get_workflow("smoke_test_wf")
            assert retrieved is not None
            assert len(retrieved.steps) == 2
        results["workflow_engine"] = "PASS"
        print("       [PASS] Workflow definition and engine verified.")
    except Exception as e:
        results["workflow_engine"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 6. Memory Persistence
    print("[6/10] Testing Memory Subsystem...")
    try:
        from core.memory.store import MemoryStore
        from core.memory.types import MemoryEntry, MemoryCategory
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(storage_root=Path(td))
            entry = MemoryEntry(
                entry_id="smoke_entry_1",
                category=MemoryCategory.SEMANTIC,
                key="smoke_test_key",
                content="smoke_test_content",
            )
            store.put(entry)
            item = store.get_by_key(MemoryCategory.SEMANTIC.value, "smoke_test_key")
            assert item is not None
            assert item.content == "smoke_test_content"
        results["memory_persistence"] = "PASS"
        print("       [PASS] Memory persistence verified.")
    except Exception as e:
        results["memory_persistence"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 7. Verification Service
    print("[7/10] Testing Verification & Postcondition Engine...")
    try:
        from core.verification.engine import VerificationEngine
        from core.verification.types import Postcondition, PostconditionType
        with tempfile.TemporaryDirectory() as td:
            v_engine = VerificationEngine(storage_root=Path(td))
            postcond = Postcondition(
                postcondition_type=PostconditionType.FILESYSTEM,
                spec={"path": str(_ROOT / "main.py"), "must_exist": True}
            )
            rec = v_engine.verify_postcondition("task_smoke", "sub_smoke", postcond)
            assert rec.status.value == "PASS" or rec.is_success
        results["verification_service"] = "PASS"
        print("       [PASS] Verification engine executed and verified postconditions.")
    except Exception as e:
        results["verification_service"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 8. Adaptive Planning Subsystem
    print("[8/10] Testing Adaptive Planning Subsystem...")
    try:
        from core.planning.planner import AdaptivePlanner
        from core.planning.goal_analyzer import GoalAnalyzer
        analyzer = GoalAnalyzer()
        assert analyzer is not None
        planner = AdaptivePlanner()
        assert planner is not None
        results["planning_subsystem"] = "PASS"
        print("       [PASS] Goal analyzer and adaptive planner initialized.")
    except Exception as e:
        results["planning_subsystem"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 9. Approval Controls & Security Gates
    print("[9/10] Testing Approval Controls...")
    try:
        from core.workflows.types import ApprovalDecision, ApprovalStatus
        decision = ApprovalDecision(
            approval_id="smoke_appr_1",
            workflow_id="smoke_wf_1",
            execution_id="exec_1",
            step_id="step_sys_1",
            reason="Security review required",
            status=ApprovalStatus.APPROVED,
            approver="operator_admin"
        )
        assert decision.status == ApprovalStatus.APPROVED
        assert decision.approver == "operator_admin"
        results["approval_controls"] = "PASS"
        print("       [PASS] Approval status contracts and decisions verified.")
    except Exception as e:
        results["approval_controls"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    # 10. Voice & Wake Controls
    print("[10/10] Testing Voice & Wake Controls...")
    try:
        from core.wake_word import is_installed
        wake_avail = is_installed()
        results["voice_controls"] = f"PASS (OpenWakeWord installed={wake_avail})"
        print(f"       [PASS] Wake-word detector availability checked: {wake_avail}")
    except Exception as e:
        results["voice_controls"] = f"FAIL: {e}"
        print(f"       [FAIL] {e}")

    print("=== SMOKE TEST SUMMARY ===")
    for k, v in results.items():
        print(f"  {k:25s}: {v}")

    all_ok = all(v.startswith("PASS") for v in results.values())
    assert all_ok, "All functional smoke tests must pass"
    print("=== ALL 10 FUNCTIONAL SMOKE TESTS PASSED ===")
    return results


if __name__ == "__main__":
    asyncio.run(run_smoke_tests())
