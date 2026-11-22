"""tests/test_mre_acceptance_queries.py — End-to-End Acceptance Verification for All 7 User Queries.

Tests the complete OPTIMUS execution pipeline:
User Query -> Central Orchestrator -> ToolRegistry -> PolicyEngine -> Adapter -> Outcome Verification -> Output

Verifies the 7 mandatory acceptance queries:
1. "Check my unread Gmail messages."
2. "Summarize my emails from today."
3. "What's on my calendar tomorrow?"
4. "Find conflicts in my schedule."
5. "How much RAM am I using?"
6. "What's my GPU utilization and VRAM usage?"
7. "Which processes are consuming the most resources?"
"""

from __future__ import annotations

import os
import pytest

from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.tools import ToolRegistry


@pytest.fixture(autouse=True)
def enable_mock_network():
    old = os.environ.get("OPTIMUS_MOCK_NETWORK")
    os.environ["OPTIMUS_MOCK_NETWORK"] = "1"
    yield
    if old is not None:
        os.environ["OPTIMUS_MOCK_NETWORK"] = old
    else:
        os.environ.pop("OPTIMUS_MOCK_NETWORK", None)


@pytest.fixture
def orchestrator():
    registry = ToolRegistry.instance()
    return CentralOrchestrator(tool_registry=registry)


def test_query_01_check_unread_gmail(orchestrator):
    """Query 1: 'Check my unread Gmail messages.'"""
    goal = "Check my unread Gmail messages."
    res = orchestrator._try_fast_path(goal, "task_q01")

    assert res is not None, "MRE fast-path must classify Query 1"
    success, output, agent, side_effects = res
    assert success is True
    assert "personal_intelligence" in agent or "general" in agent
    assert "Gmail message(s)" in output or "From:" in output or "UNREAD" in output


def test_query_02_summarize_emails_today(orchestrator):
    """Query 2: 'Summarize my emails from today.'"""
    goal = "Summarize my emails from today."
    res = orchestrator._try_fast_path(goal, "task_q02")

    assert res is not None, "MRE fast-path must classify Query 2"
    success, output, agent, side_effects = res
    assert success is True
    assert "Email Summary" in output or "Summary" in output or "Key Points:" in output


def test_query_03_calendar_tomorrow(orchestrator):
    """Query 3: 'What's on my calendar tomorrow?'"""
    goal = "What's on my calendar tomorrow?"
    res = orchestrator._try_fast_path(goal, "task_q03")

    assert res is not None, "MRE fast-path must classify Query 3"
    success, output, agent, side_effects = res
    assert success is True
    assert "Schedule" in output or "event(s)" in output or "Tomorrow" in output


def test_query_04_find_calendar_conflicts(orchestrator):
    """Query 4: 'Find conflicts in my schedule.'"""
    goal = "Find conflicts in my schedule."
    res = orchestrator._try_fast_path(goal, "task_q04")

    assert res is not None, "MRE fast-path must classify Query 4"
    success, output, agent, side_effects = res
    assert success is True
    assert "conflict" in output.lower() or "overlap" in output.lower() or "no scheduling conflicts" in output.lower()


def test_query_05_ram_usage(orchestrator):
    """Query 5: 'How much RAM am I using?'"""
    goal = "How much RAM am I using?"
    res = orchestrator._try_fast_path(goal, "task_q05")

    assert res is not None, "MRE fast-path must classify Query 5"
    success, output, agent, side_effects = res
    assert success is True
    assert "RAM" in output or "Memory" in output
    assert "GB" in output or "%" in output


def test_query_06_gpu_vram_usage(orchestrator):
    """Query 6: 'What's my GPU utilization and VRAM usage?'"""
    goal = "What's my GPU utilization and VRAM usage?"
    res = orchestrator._try_fast_path(goal, "task_q06")

    assert res is not None, "MRE fast-path must classify Query 6"
    success, output, agent, side_effects = res
    assert success is True
    assert "GPU" in output
    assert "VRAM" in output or "CPU" in output


def test_query_07_top_processes(orchestrator):
    """Query 7: 'Which processes are consuming the most resources?'"""
    goal = "Which processes are consuming the most resources?"
    res = orchestrator._try_fast_path(goal, "task_q07")

    assert res is not None, "MRE fast-path must classify Query 7"
    success, output, agent, side_effects = res
    assert success is True
    assert "Active Processes" in output or "PID" in output
