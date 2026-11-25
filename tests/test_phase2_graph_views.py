"""Phase 2 UI Refinement: Integration Graph, Memory Graph, Task Execution Graph & Home Navigation Tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from core.orchestrator.events import EventBus
from core.orchestrator.orchestrator import CentralOrchestrator
from core.orchestrator.runtime import ExecutionPlan, Subtask, TaskStatus, TaskStore
from gui.optimus.control_center import OptimusControlCenterWidget
from gui.optimus.graph_builders import (
    build_integration_graph,
    build_memory_graph,
    build_task_execution_graph,
)
from gui.optimus.graph_widget import InteractiveGraphView
from gui.optimus.service_bridge import OptimusServiceBridge


def test_integration_graph_builds_real_nodes_and_inspector_selection():
    _app = QApplication.instance() or QApplication([])
    OptimusServiceBridge.reset()
    bridge = OptimusServiceBridge.instance()
    try:
        nodes, edges = build_integration_graph(bridge)
        assert any(n.node_id == "core:optimus" for n in nodes)
        assert any(n.category == "Agent" for n in nodes)
        assert len(edges) >= 1

        gv = InteractiveGraphView("INTEGRATION TOPOLOGY")
        gv.set_graph(nodes, edges)
        assert gv.lbl_counts.text().startswith(f"{len(nodes)} nodes")
        # Select an agent node and verify inspector updates
        agent_node = next(n for n in nodes if n.category == "Agent")
        gv.select_node(agent_node.node_id)
        assert gv.insp_title.text() == agent_node.label
        assert "AGENT" in gv.insp_status.text()
        # Filter + zoom controls execute cleanly
        gv.search_input.setText(agent_node.label[:4])
        gv.view.zoom_by(1.1)
        gv.view.reset_zoom()
        gv.close()
    finally:
        OptimusServiceBridge.reset()


def test_memory_graph_distinguishes_direct_and_derived_without_persisting(tmp_path: Path):
    _app = QApplication.instance() or QApplication([])
    OptimusServiceBridge.reset()
    bridge = OptimusServiceBridge.instance()
    try:
        mm = bridge.orchestrator.memory_manager
        mm.remember_semantic("alpha_note", "optimus verification pipeline report", metadata={"task_id": "t_mem_1"})
        mm.remember_episodic("beta_note", "optimus verification pipeline summary", task_id="t_mem_1", metadata={"source": "agent_b"})
        before_count = len(mm.store.list_all(limit=100))

        nodes, edges = build_memory_graph(bridge)
        after_count = len(mm.store.list_all(limit=100))

        assert before_count == after_count  # Never silently creates permanent memory data
        assert any(e.relation_kind == "direct" for e in edges)
        assert any(e.relation_kind == "derived" and "derived" in e.label for e in edges)

        gv = InteractiveGraphView("MEMORY GRAPH")
        gv.set_graph(nodes, edges)
        mem_node = next(n for n in nodes if n.node_id.startswith("mem:"))
        gv.select_node(mem_node.node_id)
        assert "timestamp:" in gv.insp_body.toPlainText()
        assert "source:" in gv.insp_body.toPlainText()
        gv.close()
    finally:
        OptimusServiceBridge.reset()


def test_task_execution_graph_and_home_preview_navigation(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    ws = tmp_path / "ws"
    ws.mkdir()
    st = tmp_path / "store"
    st.mkdir()
    orch = CentralOrchestrator(task_store=TaskStore(st), event_bus=EventBus(), allowed_roots=[str(ws)])

    plan_fn = lambda g, tid: ExecutionPlan(
        plan_id=f"plan_{tid}",
        task_id=tid,
        goal=g,
        subtasks=[
            Subtask(
                subtask_id=f"sub_{tid}_1",
                task_id=tid,
                index=1,
                title="Send report",
                description=g,
                assigned_agent="personal_intelligence",
                allowed_tools=[],
            )
        ],
    )
    with patch.object(orch, "_try_fast_path", return_value=None):
        with patch.object(orch, "plan", side_effect=plan_fn):
            with patch.object(orch, "_execute_subtask", return_value=(False, "[CONFIRMATION_PENDING] wait", [])):
                rec = orch.execute_task("Send quarterly email")

    assert rec.status == TaskStatus.WAITING_FOR_APPROVAL

    OptimusServiceBridge.reset()
    bridge = OptimusServiceBridge.instance()
    bridge.orchestrator = orch
    bridge._active_task_id = rec.task_id

    cc = OptimusControlCenterWidget()
    try:
        nodes, edges = build_task_execution_graph(bridge, task_id=rec.task_id)
        assert any(n.category == "Orchestrator" for n in nodes)
        assert any(n.category == "Task" and n.status == "WAITING_FOR_APPROVAL" for n in nodes)
        assert any(n.category == "Subtask" for n in nodes)
        assert any(n.category == "Agent" for n in nodes)

        # Home screen stays focused (only compact preview button, not all 3 graphs)
        dash = cc.dashboard_view
        assert not hasattr(dash, "integration_graph")
        assert not hasattr(dash, "memory_graph")
        dash.btn_open_exec_graph.click()
        app.processEvents()

        # Navigated to Missions -> Execution Graph tab
        assert cc.missions_view.tabs.currentIndex() == 1
        assert cc.missions_view.execution_graph.lbl_counts.text() != "0 nodes · 0 edges"
    finally:
        cc.close()
        cc.deleteLater()
        app.processEvents()
        OptimusServiceBridge.reset()
