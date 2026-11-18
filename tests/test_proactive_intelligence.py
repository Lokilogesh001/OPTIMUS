"""tests/test_proactive_intelligence.py — Comprehensive Test Suite for Milestone 10.

Verifies:
- Test A: Scheduled Task Execution (one-time execution, state persistence, single-fire guarantee)
- Test B: Recurring Tasks (frequency calculation, pause, resume, cancel)
- Test C: Condition Monitoring & Edge Alerting (metrics, services, deduplication)
- Test D: Unfinished Mission Continuity & Resumption (discovery across restarts, recommendation accept)
- Test E: Action Governance & 4-Tier Matrix (Level 4 approval gates, rejection, safe execution)
- Test F: Resource & Daemon Lifecycle (lightweight ticks, clean shutdown without leaks)
- Test G: Failure Recovery & Bounded Retries (graceful error capture, diagnostics)
- Test H: Notification Center (priority ordering, unread count, read, dismiss, snooze)
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.proactive.context import (
    ProactiveContextManager,
    sanitize_dict,
    sanitize_sensitive_data,
)
from core.proactive.engine import (
    ProactiveIntelligenceEngine,
)
from core.proactive.governance import ActionGovernor
from core.proactive.monitors import ConditionMonitorEngine
from core.proactive.notifications import NotificationManager
from core.proactive.scheduler import IntelligentScheduler
from core.proactive.types import (
    ActionGovernanceLevel,
    MonitorStatus,
    RecommendationCategory,
    RecommendationPriority,
    RecommendationStatus,
    ScheduleFrequency,
    ScheduleStatus,
    ScheduleType,
)


@pytest.fixture
def temp_dir():
    d = Path(tempfile.mkdtemp(prefix="optimus_proactive_test_"))
    yield d
    try:
        shutil.rmtree(d)
    except Exception:
        pass


@pytest.fixture
def governor():
    return ActionGovernor()


@pytest.fixture
def notif_mgr(temp_dir):
    return NotificationManager(storage_dir=temp_dir / "notifications")


@pytest.fixture
def scheduler(temp_dir, governor, notif_mgr):
    return IntelligentScheduler(
        storage_dir=temp_dir / "schedules",
        governor=governor,
        notification_manager=notif_mgr,
    )


@pytest.fixture
def monitor_engine(temp_dir, notif_mgr):
    return ConditionMonitorEngine(
        storage_dir=temp_dir / "monitors",
        notification_manager=notif_mgr,
    )


# ── Test A: Scheduled Task Execution ─────────────────────────────────────────

def test_a_one_time_scheduled_task_execution(temp_dir, scheduler):
    """Test A: One-time scheduled task reaches execution time, executes, and does not re-fire."""
    executed_calls = []

    def dummy_runner(action_type, target, payload):
        executed_calls.append({"action_type": action_type, "target": target, "payload": payload})
        return {"status": "ok", "delivered": True}

    scheduler.register_action_runner("test_action", dummy_runner)

    now = time.time()
    sch = scheduler.create_schedule(
        name="Test One-Time Job",
        target="target_endpoint",
        schedule_type=ScheduleType.ONE_TIME,
        frequency=ScheduleFrequency.ONCE,
        run_at=now - 5.0,  # Scheduled in past so it triggers immediately
        action_type="test_action",
        governance_level=ActionGovernanceLevel.LEVEL_3_EXECUTE,
    )

    assert sch.status == ScheduleStatus.ACTIVE
    assert sch.execution_count == 0

    # First evaluation tick -> should trigger and complete
    scheduler.evaluate_tick()

    reloaded = scheduler.get_schedule(sch.schedule_id)
    assert reloaded is not None
    assert reloaded.status == ScheduleStatus.COMPLETED
    assert reloaded.execution_count == 1
    assert len(executed_calls) == 1
    assert reloaded.last_result.get("delivered") is True

    # Second evaluation tick -> should NOT fire again
    scheduler.evaluate_tick()
    assert len(executed_calls) == 1
    reloaded2 = scheduler.get_schedule(sch.schedule_id)
    assert reloaded2.execution_count == 1


# ── Test B: Recurring Tasks Lifecycle ────────────────────────────────────────

def test_b_recurring_task_lifecycle(temp_dir, scheduler):
    """Test B: Recurring task triggers, updates next_run_at, pauses, resumes, and cancels."""
    executed_calls = []

    def dummy_runner(action_type, target, payload):
        executed_calls.append(time.time())
        return {"executed": True}

    scheduler.register_action_runner("recurring_action", dummy_runner)

    now = time.time()
    sch = scheduler.create_schedule(
        name="Test Recurring Job",
        target="backup_service",
        schedule_type=ScheduleType.RECURRING,
        frequency=ScheduleFrequency.INTERVAL,
        interval_seconds=60.0,
        run_at=now - 1.0,
        action_type="recurring_action",
        governance_level=ActionGovernanceLevel.LEVEL_3_EXECUTE,
    )

    # 1. Trigger first execution
    scheduler.evaluate_tick()
    assert len(executed_calls) == 1

    reloaded = scheduler.get_schedule(sch.schedule_id)
    assert reloaded.status == ScheduleStatus.ACTIVE
    assert reloaded.execution_count == 1
    assert reloaded.next_run_at is not None
    assert reloaded.next_run_at > now

    # 2. Pause schedule
    assert scheduler.pause_schedule(sch.schedule_id) is True
    reloaded = scheduler.get_schedule(sch.schedule_id)
    assert reloaded.status == ScheduleStatus.PAUSED

    # Fast forward time to past next_run_at while PAUSED
    reloaded.next_run_at = time.time() - 10.0
    scheduler.evaluate_tick()
    # Should not execute while PAUSED
    assert len(executed_calls) == 1

    # 3. Resume schedule
    assert scheduler.resume_schedule(sch.schedule_id) is True
    reloaded = scheduler.get_schedule(sch.schedule_id)
    assert reloaded.status == ScheduleStatus.ACTIVE

    # Now evaluate tick -> should execute
    scheduler.evaluate_tick()
    assert len(executed_calls) == 2

    # 4. Cancel schedule
    assert scheduler.cancel_schedule(sch.schedule_id) is True
    reloaded = scheduler.get_schedule(sch.schedule_id)
    assert reloaded.status == ScheduleStatus.CANCELLED


# ── Test C: Condition Monitoring & Edge Alerting ─────────────────────────────

def test_c_condition_monitoring_and_edge_alerting(temp_dir, monitor_engine, notif_mgr):
    """Test C: Condition monitor evaluates vitals, triggers ON_EDGE, suppresses duplicates."""
    # Create monitor for RAM >= 80%
    mon = monitor_engine.create_monitor(
        name="High Memory Warning",
        condition_type="metric",
        target="ram",
        operator=">=",
        threshold=80.0,
        evaluation_interval=1.0,
        policy="ON_EDGE",
    )

    # Mock vitals returning 70% (below threshold)
    with patch.object(monitor_engine, "get_system_vitals", return_value={"ram_percent": 70.0, "cpu_percent": 20.0, "disk_percent": 50.0}):
        monitor_engine.evaluate_monitors()
        m_rec = monitor_engine.get_monitor(mon.monitor_id)
        assert m_rec.trigger_count == 0
        assert len(notif_mgr.list_notifications()) == 0

    # Mock vitals transitioning to 85% (crosses threshold -> EDGE TRIGGER)
    with patch.object(monitor_engine, "get_system_vitals", return_value={"ram_percent": 85.0, "cpu_percent": 20.0, "disk_percent": 50.0}):
        monitor_engine.evaluate_monitors()
        m_rec = monitor_engine.get_monitor(mon.monitor_id)
        assert m_rec.trigger_count == 1
        assert m_rec.status == MonitorStatus.TRIGGERED
        assert len(notif_mgr.list_notifications()) == 1

    # Mock vitals remaining at 87% (still above threshold -> ON_EDGE should NOT duplicate trigger)
    with patch.object(monitor_engine, "get_system_vitals", return_value={"ram_percent": 87.0, "cpu_percent": 20.0, "disk_percent": 50.0}):
        # Reset interval so it evaluates
        m_rec.last_evaluated_at = time.time() - 10.0
        monitor_engine.evaluate_monitors()
        m_rec = monitor_engine.get_monitor(mon.monitor_id)
        assert m_rec.trigger_count == 1  # No duplicate trigger!
        assert len(notif_mgr.list_notifications()) == 1


# ── Test D: Unfinished Mission Continuity ────────────────────────────────────

def test_d_unfinished_mission_continuity(temp_dir, governor, notif_mgr, scheduler, monitor_engine):
    """Test D: Proactive engine discovers unfinished missions across restarts and recommends resumption."""
    # Create mock mission store with an unfinished mission
    from core.missions import (
        MissionPlan,
        MissionRecord,
        MissionState,
        MissionSubtask,
        SubtaskStatus,
    )

    mock_mission_store = MagicMock()
    mock_unfinished_mission = MissionRecord(
        mission_id="m_interrupted_999",
        objective="Run Automated System Security Audit",
        state=MissionState.PAUSED,
        created_at=time.time() - 3600,
        updated_at=time.time() - 1800,
        plan=MissionPlan(
            plan_id="p_999",
            objective="Run Automated System Security Audit",
            subtasks=[
                MissionSubtask(subtask_id="t1", title="Port scan", status=SubtaskStatus.COMPLETED),
                MissionSubtask(subtask_id="t2", title="Check firewall", status=SubtaskStatus.PENDING),
            ],
        ),
    )
    mock_mission_store.find_resumable.return_value = [mock_unfinished_mission]
    mock_mission_store.list_all.return_value = [mock_unfinished_mission]

    context_mgr = ProactiveContextManager(mission_store=mock_mission_store)
    unfinished = context_mgr.get_unfinished_missions()
    assert len(unfinished) == 1
    assert unfinished[0]["mission_id"] == "m_interrupted_999"

    engine = ProactiveIntelligenceEngine(
        storage_dir=temp_dir / "proactive",
        context_manager=context_mgr,
        scheduler=scheduler,
        monitor_engine=monitor_engine,
        notification_manager=notif_mgr,
        governor=governor,
    )

    recs = engine.evaluate_proactive_cycle()
    assert len(recs) >= 1
    pending_rec = next((r for r in recs if r.category == RecommendationCategory.PENDING_MISSION), None)
    assert pending_rec is not None
    assert "m_interrupted_999" in pending_rec.description
    assert pending_rec.suggested_action.get("action_type") == "resume_mission"

    # Accept recommendation
    with patch("core.missions.engine.get_mission_engine") as mock_get_eng:
        mock_eng = MagicMock()
        mock_eng.resume_mission.return_value = True
        mock_get_eng.return_value = mock_eng

        accept_res = engine.accept_recommendation(pending_rec.recommendation_id)
        assert accept_res["success"] is True
        mock_eng.resume_mission.assert_called_with("m_interrupted_999")

    # Confirm status is updated
    updated_rec = engine.get_recommendation(pending_rec.recommendation_id)
    assert updated_rec.status == RecommendationStatus.ACCEPTED


# ── Test E: Action Governance & 4-Tier Matrix ────────────────────────────────

def test_e_action_governance_and_approval_gate(temp_dir, governor, scheduler, notif_mgr):
    """Test E: Consequential tasks are classified as LEVEL 4 and require operator approval."""
    # 1. Classification check
    res_safe = governor.classify_action("tool", "system_info", {})
    level_safe = res_safe[0] if isinstance(res_safe, tuple) else res_safe
    assert level_safe in {ActionGovernanceLevel.LEVEL_1_RECOMMEND, ActionGovernanceLevel.LEVEL_2_PREPARE, ActionGovernanceLevel.LEVEL_3_EXECUTE}

    res_dangerous = governor.classify_action("tool", "delete_file", {"path": "C:\\Windows\\system32"})
    level_dangerous = res_dangerous[0] if isinstance(res_dangerous, tuple) else res_dangerous
    assert level_dangerous == ActionGovernanceLevel.LEVEL_4_APPROVAL_REQUIRED

    # 2. Scheduling a dangerous action without approval should pause it
    sch = scheduler.create_schedule(
        name="Purge Old Temp Files",
        target="delete_file",
        schedule_type=ScheduleType.ONE_TIME,
        frequency=ScheduleFrequency.ONCE,
        run_at=time.time() - 5.0,
        action_type="tool",
        payload={"path": "/tmp/cache"},
        governance_level=level_dangerous,
    )

    # First tick -> scheduler should detect LEVEL_4 and pause for approval
    scheduler.evaluate_tick()

    reloaded = scheduler.get_schedule(sch.schedule_id)
    assert reloaded.status == ScheduleStatus.PAUSED
    assert reloaded.last_result.get("status") == "APPROVAL_REQUIRED"

    # 3. Notification should be posted for operator approval
    notifs = notif_mgr.list_notifications()
    approval_notif = next((n for n in notifs if n.category == "APPROVAL"), None)
    assert approval_notif is not None
    assert "Approval Required" in approval_notif.title


# ── Test F: Resource & Daemon Lifecycle ──────────────────────────────────────

def test_f_daemon_thread_lifecycle(temp_dir, governor, notif_mgr, scheduler, monitor_engine):
    """Test F: Background proactive daemon starts, executes ticks, and shuts down cleanly."""
    engine = ProactiveIntelligenceEngine(
        storage_dir=temp_dir / "proactive",
        scheduler=scheduler,
        monitor_engine=monitor_engine,
        notification_manager=notif_mgr,
        governor=governor,
    )

    assert engine._eval_thread is None
    engine.start(interval_seconds=5.0)
    assert engine._eval_thread is not None
    assert engine._eval_thread.is_alive()

    # Allow thread to run briefly
    time.sleep(0.5)

    # Clean shutdown
    engine.stop()
    assert engine._eval_thread is None or not engine._eval_thread.is_alive()


# ── Test G: Failure Recovery & Bounded Retries ───────────────────────────────

def test_g_failure_recovery_and_bounded_retries(temp_dir, scheduler):
    """Test G: Invalid or failing tool schedules capture errors without crashing."""
    def broken_runner(action_type, target, payload):
        raise RuntimeError("Simulated transient connection failure")

    scheduler.register_action_runner("broken_service", broken_runner)

    sch = scheduler.create_schedule(
        name="Broken Service Sync",
        target="endpoint",
        schedule_type=ScheduleType.ONE_TIME,
        frequency=ScheduleFrequency.ONCE,
        run_at=time.time() - 1.0,
        action_type="broken_service",
        governance_level=ActionGovernanceLevel.LEVEL_3_EXECUTE,
    )

    # Evaluate tick -> should handle exception gracefully
    scheduler.evaluate_tick()

    reloaded = scheduler.get_schedule(sch.schedule_id)
    assert reloaded.status == ScheduleStatus.FAILED
    assert "Simulated transient connection failure" in reloaded.last_result.get("error", "")


# ── Test H: Notification Center Lifecycle ───────────────────────────────────

def test_h_notification_center_lifecycle(temp_dir, notif_mgr):
    """Test H: Priority ordering, unread count tracking, read, dismiss, and snooze."""
    # Post notifications of varying priority
    n1 = notif_mgr.post("Info Update", "Everything running normally", priority=RecommendationPriority.LOW)
    n2 = notif_mgr.post("Warning", "High temperature", priority=RecommendationPriority.HIGH)
    n3 = notif_mgr.post("Emergency Alert", "System drive full", priority=RecommendationPriority.CRITICAL)

    assert notif_mgr.unread_count() == 3

    # Listing should place CRITICAL and HIGH at the top
    notifs = notif_mgr.list_notifications()
    assert len(notifs) == 3
    assert notifs[0].priority in {RecommendationPriority.CRITICAL, RecommendationPriority.HIGH}

    # Mark n3 as read
    assert notif_mgr.mark_read(n3.notification_id) is True
    assert notif_mgr.unread_count() == 2

    # Snooze n2 for 1 hour
    assert notif_mgr.snooze(n2.notification_id, duration_seconds=3600.0) is True
    # Snoozed item should not be in visible list
    visible = notif_mgr.list_notifications()
    assert len(visible) == 2
    assert all(n.notification_id != n2.notification_id for n in visible)

    # Dismiss n1
    assert notif_mgr.dismiss(n1.notification_id) is True
    visible_after_dismiss = notif_mgr.list_notifications()
    assert len(visible_after_dismiss) == 1
    assert visible_after_dismiss[0].notification_id == n3.notification_id


# ── Sanitization & Privacy Shield Test ───────────────────────────────────────

def test_sanitization_privacy_shield():
    """Verify credentials and private keys are scrubbed before storage or display."""
    dirty_text = "API Key: sk-1234567890abcdef12345678 and token=ghp_abcdefghijklmnopqrstuvwxyz123456"
    clean_text = sanitize_sensitive_data(dirty_text)
    assert "sk-" not in clean_text
    assert "ghp_" not in clean_text
    assert "[REDACTED_CREDENTIAL]" in clean_text

    dirty_payload = {
        "user": "admin",
        "password": "SuperSecretPassword123!",
        "nested": {"api_key": "AIzaSyD-1234567890abcdefghijklmnop"},
    }
    clean_payload = sanitize_dict(dirty_payload)
    assert clean_payload["password"] == "[REDACTED_KEY]"
    assert clean_payload["nested"]["api_key"] == "[REDACTED_KEY]"
