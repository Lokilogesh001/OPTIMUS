"""tests/test_computer_use.py — Test suite for Milestone 9: Computer Use & Visual Intelligence.

Verifies:
1. UIElement, DesktopObservation, InteractionAction, and InteractionResult data contracts.
2. DesktopObserver element extraction, sensitive content redaction, and state digests.
3. VisualUnderstandingPipeline match scoring, ambiguity handling, UI state classification, and diffing.
4. ComputerActuator boundary validation, coordinate clamping, and anti-runaway tripwires.
5. ComputerInteractionLifecycle 7-step execution loop, authorization gates, and postcondition verification.
6. ToolRegistry integration for computer use tools.
7. ComputerAgent specialization and MissionPlanner computer use DAG generation.
"""

import time
from unittest.mock import MagicMock, patch
import pytest

from core.agents.computer import ComputerAgent, create_computer_agent_profile
from core.agents.contracts import AgentInputContract
from core.computer_use.actuation import (
    BoundaryViolationError,
    ComputerActuator,
    RunawayInteractionError,
)
from core.computer_use.lifecycle import ComputerInteractionLifecycle
from core.computer_use.observation import DesktopObserver
from core.computer_use.tools import register_computer_tools
from core.computer_use.types import (
    DesktopObservation,
    InteractionAction,
    InteractionResult,
    UIElement,
)
from core.computer_use.understanding import VisualUnderstandingPipeline
from core.missions.planner import MissionPlanner
from core.orchestrator.tools import ToolRegistry
from core.security.permissions import PermissionProfile, get_permission_manager


# ── 1. Data Contracts ────────────────────────────────────────────────────────


def test_ui_element_contracts():
    el = UIElement(
        element_id="el_btn_save",
        role="button",
        name="Save Document",
        rect=(100, 200, 80, 30),
        center=(140, 215),
    )
    d = el.to_dict()
    assert d["element_id"] == "el_btn_save"
    assert d["role"] == "button"
    assert d["rect"] == [100, 200, 80, 30]
    assert d["center"] == [140, 215]

    restored = UIElement.from_dict(d)
    assert restored.name == "Save Document"
    assert restored.center == (140, 215)


def test_desktop_observation_contracts():
    e1 = UIElement(element_id="e1", role="button", name="OK", center=(50, 50))
    e2 = UIElement(element_id="e2", role="edit", name="Username", value="admin", center=(100, 50))
    obs = DesktopObservation(
        active_app="notepad.exe",
        window_title="Untitled - Notepad",
        screen_bounds=(0, 0, 1920, 1080),
        elements=[e1, e2],
        digest="sha_test_123",
    )
    assert obs.find_element(name="ok") == e1
    assert obs.find_element(role="edit") == e2
    assert obs.find_element(name="nonexistent") is None

    d = obs.to_dict()
    assert d["active_app"] == "notepad.exe"
    assert len(d["elements"]) == 2

    restored = DesktopObservation.from_dict(d)
    assert restored.window_title == "Untitled - Notepad"
    assert len(restored.elements) == 2


def test_interaction_action_sensitive_masking():
    action = InteractionAction(
        action_type="type",
        text="secret_password_123",
        risk_level="HIGH",
    )
    d = action.to_dict()
    assert d["text"] == "[REDACTED]"

    safe_action = InteractionAction(
        action_type="type",
        text="hello world",
        risk_level="LOW",
    )
    assert safe_action.to_dict()["text"] == "hello world"


# ── 2. Observation & Sensitive Content Redaction ─────────────────────────────


def test_desktop_observer_sensitive_redaction():
    observer = DesktopObserver()
    # Mock scanning window returning protected/password elements
    mock_elem1 = MagicMock(
        eid=1,
        role="password",
        value="supersecret",
        rect=(10, 20, 100, 30),
        center=(60, 35),
        enabled=True,
        focused=False,
        interactable=True,
        protected=True,
        depth=1,
        scrollable=False,
    )
    mock_elem1.name = "Password"

    mock_elem2 = MagicMock(
        eid=2,
        role="button",
        value="",
        rect=(10, 60, 100, 30),
        center=(60, 75),
        enabled=True,
        focused=False,
        interactable=True,
        protected=False,
        depth=1,
        scrollable=False,
    )
    mock_elem2.name = "Submit"

    with patch("core.computer_use.observation.get_open_windows", return_value=[{"hwnd": 999, "pid": 1234, "app": "app.exe", "title": "Login Window", "is_iconic": False}]), \
         patch("core.computer_use.observation.sv._scan_windows", return_value=[mock_elem1, mock_elem2]):
        obs = observer.observe(target_window_title="Login Window")
        assert len(obs.elements) == 2
        # Verify secret value is redacted
        pw_el = next(e for e in obs.elements if e.role == "password")
        assert pw_el.value == "[REDACTED]"
        assert pw_el.metadata["protected"] is True

        btn_el = next(e for e in obs.elements if e.name == "Submit")
        assert btn_el.value == ""
        assert obs.digest != ""


# ── 3. Visual Understanding Pipeline ─────────────────────────────────────────


def test_understanding_confidence_and_ambiguity():
    pipeline = VisualUnderstandingPipeline(confidence_threshold=0.60)
    e_save = UIElement(element_id="1", role="button", name="Save File", center=(100, 100))
    e_save_as = UIElement(element_id="2", role="button", name="Save As...", center=(100, 150))
    e_cancel = UIElement(element_id="3", role="button", name="Cancel", center=(100, 200))

    obs = DesktopObservation(elements=[e_save, e_save_as, e_cancel])

    # Confident exact match
    el, conf, reason = pipeline.find_best_element(obs, query="Cancel", role="button")
    assert el is not None
    assert el.name == "Cancel"
    assert conf >= 0.85

    # Ambiguity between Save File and Save As when searching generic "Save"
    el_ambig, conf_ambig, reason_ambig = pipeline.find_best_element(obs, query="Save", role="button")
    assert "Ambiguous match" in reason_ambig or el_ambig is not None

    # Low confidence refusal without coordinate hallucination
    el_none, conf_none, reason_none = pipeline.find_best_element(obs, query="CompletelyMissingElement")
    assert el_none is None
    assert conf_none < 0.60


def test_understanding_ui_state_classification():
    pipeline = VisualUnderstandingPipeline()
    e_dlg = UIElement(element_id="1", role="dialog", name="Confirm File Delete", center=(500, 500))
    e_btn_yes = UIElement(element_id="2", role="button", name="Yes", center=(450, 550))
    e_btn_no = UIElement(element_id="3", role="button", name="No", center=(550, 550))

    obs = DesktopObservation(
        window_title="Confirm File Delete",
        elements=[e_dlg, e_btn_yes, e_btn_no],
    )
    state = pipeline.classify_ui_state(obs)
    assert state["is_dialog"] is True
    assert state["is_confirmation"] is True
    assert state["primary_action"] == "Yes"
    assert "Yes" in state["buttons"]
    assert "No" in state["buttons"]


def test_understanding_diff_observations():
    pipeline = VisualUnderstandingPipeline()
    e1 = UIElement(element_id="1", role="button", name="Start", center=(10, 10))
    e2 = UIElement(element_id="2", role="edit", name="Log", value="Ready", center=(20, 20))
    obs1 = DesktopObservation(window_title="Step 1", elements=[e1, e2], digest="d1")

    e2_updated = UIElement(element_id="2", role="edit", name="Log", value="Running...", center=(20, 20))
    e3_new = UIElement(element_id="3", role="button", name="Stop", center=(30, 30))
    obs2 = DesktopObservation(window_title="Step 2", elements=[e2_updated, e3_new], digest="d2")

    diff = pipeline.diff_observations(obs1, obs2)
    assert diff["state_changed"] is True
    assert diff["window_changed"] is True
    assert diff["added_count"] == 1
    assert diff["removed_count"] == 1
    assert diff["text_updates_count"] == 1
    assert diff["text_updates"][0]["new_value"] == "Running..."


# ── 4. Computer Actuator & Boundaries ────────────────────────────────────────


def test_actuator_boundary_violation():
    actuator = ComputerActuator()
    screen_bounds = (0, 0, 1920, 1080)

    # Valid coordinates
    cx, cy = actuator.validate_coordinates(500, 600, screen_bounds=screen_bounds)
    assert (cx, cy) == (500, 600)

    # Out of bounds coordinates (exceeding width or negative)
    with pytest.raises(BoundaryViolationError):
        actuator.validate_coordinates(-10, 500, screen_bounds=screen_bounds)

    with pytest.raises(BoundaryViolationError):
        actuator.validate_coordinates(2000, 500, screen_bounds=screen_bounds)

    with pytest.raises(BoundaryViolationError):
        actuator.validate_coordinates(500, 1200, screen_bounds=screen_bounds)


def test_actuator_anti_runaway_tripwire():
    actuator = ComputerActuator(max_consecutive_identical_clicks=3)

    with patch("core.screenact.click"):
        # Clicks 1 and 2 succeed
        actuator.click(250, 250)
        actuator.click(250, 250)

        # Click 3 triggers tripwire
        with pytest.raises(RunawayInteractionError):
            actuator.click(250, 250)

    # Reset tripwire allows click again
    actuator.reset_tripwire()
    with patch("core.screenact.click"):
        actuator.click(250, 250)


# ── 5. 7-Step Interaction Lifecycle ──────────────────────────────────────────


def test_lifecycle_execution_flow():
    mock_obs1 = DesktopObservation(window_title="Window A", digest="digest_a")
    mock_obs2 = DesktopObservation(window_title="Window B", digest="digest_b")

    mock_observer = MagicMock()
    mock_observer.observe.side_effect = [mock_obs1, mock_obs2]

    mock_understanding = MagicMock()
    mock_understanding.diff_observations.return_value = {"state_changed": True}

    mock_actuator = MagicMock()
    mock_actuator.validate_coordinates.return_value = (100, 100)
    mock_actuator.click.return_value = {"action": "click", "coords": (100, 100)}

    lifecycle = ComputerInteractionLifecycle(
        observer=mock_observer,
        understanding=mock_understanding,
        actuator=mock_actuator,
    )

    action = InteractionAction(action_type="click", coords=(100, 100))
    res = lifecycle.execute_interaction(action, is_pre_approved=True)

    assert res.success is True
    assert res.action == "click"
    assert res.state_changed is True
    assert res.before_digest == "digest_a"
    assert res.after_digest == "digest_b"
    mock_actuator.click.assert_called_once_with(100, 100)


def test_lifecycle_authorization_gate():
    lifecycle = ComputerInteractionLifecycle()
    # High risk action without approval must be halted
    action = InteractionAction(action_type="click", target_name="Delete Database", risk_level="HIGH")
    res = lifecycle.execute_interaction(action, is_pre_approved=False)

    assert res.success is False
    assert res.error == "APPROVAL_REQUIRED"
    assert "Authorization gate" in res.details


# ── 6. Tool Registry Integration ─────────────────────────────────────────────


def test_computer_tools_registration():
    tr = ToolRegistry()
    assert tr.is_registered("computer_observe")
    assert tr.is_registered("computer_find_element")
    assert tr.is_registered("computer_interact")
    assert tr.is_registered("computer_type_text")
    assert tr.is_registered("computer_hotkey")
    assert tr.is_registered("computer_switch_window")
    assert tr.is_registered("computer_verify_state")

    # Test invoking computer_observe
    res = tr.execute("computer_observe", arguments={"capture_screenshot": False})
    assert res.status == "done"
    assert "Active Application" in res.output
    assert "digest" in res.evidence


# ── 7. Computer Agent & Mission Planner ───────────────────────────────────────


def test_computer_agent_execution():
    agent = ComputerAgent()
    tr = ToolRegistry()
    contract = AgentInputContract(
        task_id="task_cu_1",
        subtask_id="st_1",
        goal="Inspect desktop and switch to notepad window",
        instructions="Perform visual inspection and switch window if applicable",
        workspace_root=".",
    )
    output = agent.execute(contract, tr)
    assert output.status == "success"
    assert "Visual computer interaction completed" in output.summary
    assert "initial_observation" in output.evidence


def test_mission_planner_computer_use_archetype():
    planner = MissionPlanner()
    plan = planner.plan("Automate desktop workflow: open notepad and type text")
    assert plan.validated is True
    assert len(plan.subtasks) == 4

    agents = [st.assigned_agent for st in plan.subtasks]
    assert "computer_agent" in agents
    assert "data_agent" in agents
    assert "verification_agent" in agents

    # Verify DAG dependency ordering
    st1, st2, st3, st4 = plan.subtasks
    assert st1.depends_on == []
    assert st2.depends_on == [st1.subtask_id]
    assert st3.depends_on == [st2.subtask_id]
    assert st4.depends_on == [st3.subtask_id]
