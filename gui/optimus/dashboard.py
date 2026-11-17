"""gui/optimus/dashboard.py — Main Dashboard for OPTIMUS Control Center.

Implements the functional home screen containing:
1. Command Center: central command input, voice status, task submission/cancellation, status.
2. Agent Activity: registered agents, active/idle/busy states, current assignments, progress.
3. Workflow Monitor: interactive DAG cards, step dependencies, running/completed/failed/pending/approval states.
4. Intelligence Monitor: active model & provider, local/cloud badge, circuit breaker health, routing policy.
5. System Monitor: CPU, RAM, GPU, storage metrics, active processes, application health.
6. Execution Timeline: chronological stream of task events, tool executions, verification results, errors.
"""

from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel, RingGauge


class OptimusDashboardView(QWidget):
    """The central multi-module OPTIMUS HUD dashboard."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        # Root Scroll Area to guarantee responsiveness
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;" + scroll_css())

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.addWidget(scroll)

        grid = QGridLayout(container)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setSpacing(10)
        scroll.setWidget(container)

        # ── 1. Command Center Panel (Top Left) ───────────────────────────────
        self.panel_cmd = HoloPanel("COMMAND CENTER", "SYS_CMD")
        self._build_command_center(self.panel_cmd.body)
        grid.addWidget(self.panel_cmd, 0, 0)

        # ── 2. Workflow Monitor Panel (Top Center) ───────────────────────────
        self.panel_wf = HoloPanel("WORKFLOW MONITOR", "DAG_STATE")
        self._build_workflow_monitor(self.panel_wf.body)
        grid.addWidget(self.panel_wf, 0, 1)

        # ── 3. Agent Activity Panel (Top Right) ──────────────────────────────
        self.panel_agents = HoloPanel("AGENT ACTIVITY", "ROSTER")
        self._build_agent_activity(self.panel_agents.body)
        grid.addWidget(self.panel_agents, 0, 2)

        # ── 4. Intelligence Monitor Panel (Bottom Left) ──────────────────────
        self.panel_intel = HoloPanel("INTELLIGENCE GATEWAY", "NEURAL")
        self._build_intelligence_monitor(self.panel_intel.body)
        grid.addWidget(self.panel_intel, 1, 0)

        # ── 5. System Monitor Panel (Bottom Center) ──────────────────────────
        self.panel_sys = HoloPanel("SYSTEM TELEMETRY", "METRICS")
        self._build_system_monitor(self.panel_sys.body)
        grid.addWidget(self.panel_sys, 1, 1)

        # ── 6. Execution Timeline Panel (Bottom Right) ───────────────────────
        self.panel_time = HoloPanel("EXECUTION TIMELINE", "EVENT_BUS")
        self._build_execution_timeline(self.panel_time.body)
        grid.addWidget(self.panel_time, 1, 2)

        # Set column stretch
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 4)
        grid.setColumnStretch(2, 3)

        # Wire bridge signals
        self._connect_signals()

        # Property aliases for testing & external APIs
        self.btn_dispatch = self.btn_submit
        self.gauge_cpu = self.g_cpu
        self.gauge_ram = self.g_mem
        self.gauge_gpu = self.g_gpu
        self.gauge_storage = self.g_disk
        self.active_model_lbl = self.intel_model_lbl
        self.provider_status_lbl = self.intel_prov_lbl
        self.table_timeline = self.timeline_edit
        self.table_agents = self.agents_container
        self.table_workflows = self.wf_dag_widget

        # Initial data sync
        self.refresh_all()

    # ── Module 1: Command Center ─────────────────────────────────────────────

    def _build_command_center(self, lay: QVBoxLayout) -> None:
        # Status header row
        hdr_row = QHBoxLayout()
        self.cmd_status_lbl = QLabel("IDLE")
        self.cmd_status_lbl.setFont(px_font(10, QFont.Weight.Bold))
        self.cmd_status_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 136, 0.1); border: 1px solid {C.GREEN}; padding: 2px 6px; border-radius: 3px;")
        hdr_row.addWidget(self.cmd_status_lbl)

        self.cmd_voice_lbl = QLabel("🎙 VOICE READY")
        self.cmd_voice_lbl.setFont(px_font(10, QFont.Weight.DemiBold))
        self.cmd_voice_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr_row.addWidget(self.cmd_voice_lbl)
        hdr_row.addStretch()

        self.cmd_time_lbl = QLabel("0.0s")
        self.cmd_time_lbl.setFont(mono_font(10))
        self.cmd_time_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        hdr_row.addWidget(self.cmd_time_lbl)
        lay.addLayout(hdr_row)

        # Active goal label
        self.active_goal_lbl = QLabel("No active task executing. Enter command below.")
        self.active_goal_lbl.setFont(px_font(11, QFont.Weight.Normal))
        self.active_goal_lbl.setStyleSheet(f"color: {C.WHITE}; background: rgba(0, 212, 255, 0.05); border: 1px solid {C.BORDER}; padding: 8px; border-radius: 3px;")
        self.active_goal_lbl.setWordWrap(True)
        self.active_goal_lbl.setMinimumHeight(44)
        lay.addWidget(self.active_goal_lbl)

        # Command input row
        in_row = QHBoxLayout()
        in_row.setSpacing(6)
        self.cmd_input = QLineEdit()
        self.cmd_input.setFont(px_font(12))
        self.cmd_input.setPlaceholderText("Enter goal or instruction for OPTIMUS...")
        self.cmd_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 3px;
                padding: 6px 10px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
                background: rgba(0, 212, 255, 0.07);
            }}
        """)
        self.cmd_input.returnPressed.connect(self._submit_task)
        in_row.addWidget(self.cmd_input, stretch=1)

        self.btn_submit = QPushButton("EXECUTE")
        self.btn_submit.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_submit.setStyleSheet(btn_css("primary"))
        self.btn_submit.clicked.connect(self._submit_task)
        in_row.addWidget(self.btn_submit)

        self.btn_cancel = QPushButton("CANCEL")
        self.btn_cancel.setFont(px_font(10, QFont.Weight.Bold))
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(f"""
            QPushButton {{
                color: {C.RED}; background: rgba(255, 51, 85, 0.1);
                border: 1px solid {C.RED}; border-radius: 3px; padding: 4px 8px;
            }}
            QPushButton:hover {{ background: rgba(255, 51, 85, 0.25); }}
        """)
        self.btn_cancel.clicked.connect(self._cancel_task)
        in_row.addWidget(self.btn_cancel)
        lay.addLayout(in_row)

    def _submit_task(self) -> None:
        text = self.cmd_input.text().strip()
        if not text:
            return
        self.cmd_input.clear()
        self.active_goal_lbl.setText(f"Submitting: {text}")
        self.cmd_status_lbl.setText("PLANNING")
        self.cmd_status_lbl.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 212, 255, 0.1); border: 1px solid {C.PRI}; padding: 2px 6px; border-radius: 3px;")
        self.bridge.submit_command(text)

    def _cancel_task(self) -> None:
        self.bridge.cancel_active_task()
        self.cmd_status_lbl.setText("CANCELLED")
        self.cmd_status_lbl.setStyleSheet(f"color: {C.RED}; background: rgba(255, 51, 85, 0.1); border: 1px solid {C.RED}; padding: 2px 6px; border-radius: 3px;")

    # ── Module 2: Workflow Monitor ───────────────────────────────────────────

    def _build_workflow_monitor(self, lay: QVBoxLayout) -> None:
        # Controls row
        ctrl_row = QHBoxLayout()
        self.wf_active_title = QLabel("NO ACTIVE WORKFLOW")
        self.wf_active_title.setFont(px_font(11, QFont.Weight.DemiBold))
        self.wf_active_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        ctrl_row.addWidget(self.wf_active_title)
        ctrl_row.addStretch()

        self.btn_appr_gate = QPushButton("APPROVALS (0)")
        self.btn_appr_gate.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_appr_gate.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: 1px solid {C.BORDER}; padding: 3px 8px; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.ACC}; border: 1px solid {C.ACC}; }}
        """)
        ctrl_row.addWidget(self.btn_appr_gate)
        lay.addLayout(ctrl_row)

        # DAG container
        self.wf_dag_scroll = QScrollArea()
        self.wf_dag_scroll.setWidgetResizable(True)
        self.wf_dag_scroll.setFixedHeight(120)
        self.wf_dag_scroll.setStyleSheet("background: transparent; border: 1px solid " + C.BORDER + ";" + scroll_css())

        self.wf_dag_widget = QWidget()
        self.wf_dag_widget.setStyleSheet("background: transparent;")
        self.wf_dag_layout = QHBoxLayout(self.wf_dag_widget)
        self.wf_dag_layout.setContentsMargins(6, 6, 6, 6)
        self.wf_dag_layout.setSpacing(8)
        self.wf_dag_scroll.setWidget(self.wf_dag_widget)
        lay.addWidget(self.wf_dag_scroll)

    def update_workflow_dag(self, steps_data: list[dict[str, Any]]) -> None:
        # Clear existing step cards
        while self.wf_dag_layout.count():
            item = self.wf_dag_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not steps_data:
            lbl = QLabel("No active DAG steps. Select or start a workflow.")
            lbl.setFont(px_font(10))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
            self.wf_dag_layout.addWidget(lbl)
            return

        for idx, s in enumerate(steps_data):
            card = QFrame()
            card.setFixedWidth(130)
            status = str(s.get("status", "PENDING")).upper()
            if "COMPLETED" in status:
                border_c, bg_c = C.GREEN, "rgba(0, 255, 136, 0.08)"
            elif "RUNNING" in status:
                border_c, bg_c = C.PRI, "rgba(0, 212, 255, 0.12)"
            elif "APPROVAL" in status:
                border_c, bg_c = C.ACC, "rgba(255, 107, 0, 0.12)"
            elif "FAILED" in status:
                border_c, bg_c = C.RED, "rgba(255, 51, 85, 0.12)"
            elif "SKIP" in status:
                border_c, bg_c = C.BORDER, "rgba(255, 255, 255, 0.02)"
            else:
                border_c, bg_c = C.BORDER_B, "rgba(0, 212, 255, 0.04)"

            card.setStyleSheet(f"""
                QFrame {{
                    background: {bg_c};
                    border: 1px solid {border_c};
                    border-radius: 4px;
                    padding: 4px;
                }}
            """)
            c_lay = QVBoxLayout(card)
            c_lay.setContentsMargins(4, 4, 4, 4)
            c_lay.setSpacing(2)

            name_lbl = QLabel(s.get("step_id", f"step_{idx+1}"))
            name_lbl.setFont(px_font(10, QFont.Weight.Bold))
            name_lbl.setStyleSheet(f"color: {C.WHITE};")
            c_lay.addWidget(name_lbl)

            agent_lbl = QLabel(f"[{s.get('assigned_agent', 'agent')}]")
            agent_lbl.setFont(mono_font(8))
            agent_lbl.setStyleSheet(f"color: {C.PRI_DIM};")
            c_lay.addWidget(agent_lbl)

            st_lbl = QLabel(status)
            st_lbl.setFont(px_font(8, QFont.Weight.DemiBold))
            st_lbl.setStyleSheet(f"color: {border_c};")
            c_lay.addWidget(st_lbl)

            self.wf_dag_layout.addWidget(card)

            if idx < len(steps_data) - 1:
                arrow = QLabel("➔")
                arrow.setFont(px_font(10))
                arrow.setStyleSheet(f"color: {C.BORDER_B};")
                self.wf_dag_layout.addWidget(arrow)

        self.wf_dag_layout.addStretch()

    # ── Module 3: Agent Activity ─────────────────────────────────────────────

    def _build_agent_activity(self, lay: QVBoxLayout) -> None:
        self.agents_scroll = QScrollArea()
        self.agents_scroll.setWidgetResizable(True)
        self.agents_scroll.setFixedHeight(120)
        self.agents_scroll.setStyleSheet("background: transparent; border: 1px solid " + C.BORDER + ";" + scroll_css())

        self.agents_container = QWidget()
        self.agents_container.setStyleSheet("background: transparent;")
        self.agents_lay = QVBoxLayout(self.agents_container)
        self.agents_lay.setContentsMargins(4, 4, 4, 4)
        self.agents_lay.setSpacing(4)
        self.agents_scroll.setWidget(self.agents_container)
        lay.addWidget(self.agents_scroll)

    def update_agents_list(self, agents_list: list[dict[str, Any]]) -> None:
        while self.agents_lay.count():
            item = self.agents_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for a in agents_list:
            row = QWidget()
            row.setStyleSheet(f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 3px;")
            r_lay = QHBoxLayout(row)
            r_lay.setContentsMargins(6, 4, 6, 4)
            r_lay.setSpacing(6)

            st_color = C.ACC if a["status"] == "BUSY" else C.GREEN
            st_dot = QLabel("●")
            st_dot.setFont(px_font(8))
            st_dot.setStyleSheet(f"color: {st_color};")
            r_lay.addWidget(st_dot)

            name = QLabel(a.get("title", a.get("name", "agent")))
            name.setFont(px_font(10, QFont.Weight.Bold))
            name.setStyleSheet(f"color: {C.WHITE};")
            r_lay.addWidget(name)

            role = QLabel(f"({a.get('domain', 'general')})")
            role.setFont(mono_font(8))
            role.setStyleSheet(f"color: {C.TEXT_DIM};")
            r_lay.addWidget(role)
            r_lay.addStretch()

            status_lbl = QLabel(a.get("status", "IDLE"))
            status_lbl.setFont(px_font(9, QFont.Weight.DemiBold))
            status_lbl.setStyleSheet(f"color: {st_color};")
            r_lay.addWidget(status_lbl)

            self.agents_lay.addWidget(row)

        self.agents_lay.addStretch()

    # ── Module 4: Intelligence Monitor ───────────────────────────────────────

    def _build_intelligence_monitor(self, lay: QVBoxLayout) -> None:
        self.intel_model_lbl = QLabel("MODEL: Initializing...")
        self.intel_model_lbl.setFont(px_font(11, QFont.Weight.DemiBold))
        self.intel_model_lbl.setStyleSheet(f"color: {C.WHITE};")
        lay.addWidget(self.intel_model_lbl)

        row2 = QHBoxLayout()
        self.intel_prov_lbl = QLabel("PROVIDER: LOCAL")
        self.intel_prov_lbl.setFont(mono_font(9))
        self.intel_prov_lbl.setStyleSheet(f"color: {C.PRI};")
        row2.addWidget(self.intel_prov_lbl)

        self.intel_class_lbl = QLabel("LOCAL ONLY")
        self.intel_class_lbl.setFont(px_font(9, QFont.Weight.Bold))
        self.intel_class_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 136, 0.1); border: 1px solid {C.GREEN}; border-radius: 2px; padding: 1px 4px;")
        row2.addWidget(self.intel_class_lbl)
        row2.addStretch()
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.intel_cb_lbl = QLabel("CIRCUIT BREAKER: CLOSED")
        self.intel_cb_lbl.setFont(mono_font(9))
        self.intel_cb_lbl.setStyleSheet(f"color: {C.GREEN};")
        row3.addWidget(self.intel_cb_lbl)

        self.intel_policy_lbl = QLabel("POLICY: LOCAL_FIRST")
        self.intel_policy_lbl.setFont(mono_font(9))
        self.intel_policy_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        row3.addWidget(self.intel_policy_lbl)
        lay.addLayout(row3)

    def update_intelligence(self, data: dict[str, Any]) -> None:
        model = data.get("active_model", "None")
        prov = data.get("active_provider", "LOCAL")
        is_local = data.get("is_local", True)
        policy = data.get("active_policy", "LOCAL_FIRST")

        self.intel_model_lbl.setText(f"MODEL: {model}")
        self.intel_prov_lbl.setText(f"PROVIDER: {prov}")
        self.intel_policy_lbl.setText(f"POLICY: {policy}")

        if is_local:
            self.intel_class_lbl.setText("LOCAL ONLY")
            self.intel_class_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 136, 0.1); border: 1px solid {C.GREEN}; border-radius: 2px; padding: 1px 4px;")
        else:
            self.intel_class_lbl.setText("CLOUD (HYBRID)")
            self.intel_class_lbl.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 212, 255, 0.1); border: 1px solid {C.PRI}; border-radius: 2px; padding: 1px 4px;")

    # ── Module 5: System Monitor ─────────────────────────────────────────────

    def _build_system_monitor(self, lay: QVBoxLayout) -> None:
        gauges_row = QHBoxLayout()
        gauges_row.setSpacing(6)
        self.g_cpu = RingGauge("CPU")
        self.g_mem = RingGauge("RAM")
        self.g_gpu = RingGauge("GPU")
        self.g_disk = RingGauge("DISK")
        for g in (self.g_cpu, self.g_mem, self.g_gpu, self.g_disk):
            gauges_row.addWidget(g)
        lay.addLayout(gauges_row)

        info_row = QHBoxLayout()
        self.sys_procs_lbl = QLabel("PROCS: --")
        self.sys_procs_lbl.setFont(mono_font(9))
        self.sys_procs_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        info_row.addWidget(self.sys_procs_lbl)

        self.sys_health_lbl = QLabel("HEALTH: OPTIMAL")
        self.sys_health_lbl.setFont(px_font(9, QFont.Weight.Bold))
        self.sys_health_lbl.setStyleSheet(f"color: {C.GREEN};")
        info_row.addWidget(self.sys_health_lbl)
        lay.addLayout(info_row)

    def _on_telemetry_updated(self, telem: dict[str, Any]) -> None:
        self.update_telemetry(telem)

    def update_telemetry(self, telem: dict[str, Any]) -> None:
        cpu = telem.get("cpu_percent", telem.get("cpu_pct", 0.0))
        ram = telem.get("ram_percent", telem.get("ram_pct", 0.0))
        gpu = telem.get("gpu_percent", telem.get("gpu_pct", 0.0))
        disk = telem.get("disk_percent", telem.get("storage_pct", 0.0))

        self.g_cpu.set_value(cpu, f"{cpu:.0f}%")
        self.g_mem.set_value(ram, f"{ram:.0f}%")
        self.g_gpu.set_value(gpu, f"{gpu:.0f}%")
        self.g_disk.set_value(disk, f"{disk:.0f}%")

        self.sys_procs_lbl.setText(f"PROCS: {telem.get('process_count', '--')}")

    # ── Module 6: Execution Timeline ─────────────────────────────────────────

    def _build_execution_timeline(self, lay: QVBoxLayout) -> None:
        self.timeline_edit = QTextEdit()
        self.timeline_edit.setReadOnly(True)
        self.timeline_edit.setFont(mono_font(9))
        self.timeline_edit.setFixedHeight(120)
        self.timeline_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 4px;
            }}
        """ + scroll_css())
        lay.addWidget(self.timeline_edit)

    def append_timeline_event(self, ev: dict[str, Any]) -> None:
        t_str = ev.get("time_str", time.strftime("%H:%M:%S"))
        cat = ev.get("category", "INFO")
        title = ev.get("title", "")
        detail = ev.get("detail", "")
        lvl = ev.get("level", "INFO")

        if lvl == "SUCCESS":
            color = C.GREEN
        elif lvl == "ERROR":
            color = C.RED
        elif lvl == "WARNING":
            color = C.ACC
        else:
            color = C.PRI

        line = f"<font color='{C.TEXT_DIM}'>[{t_str}]</font> <font color='{color}'><b>[{cat}]</b> {title}</font>"
        if detail:
            line += f"<br>&nbsp;&nbsp;<font color='{C.WHITE}'>{detail[:140]}</font>"
        self.timeline_edit.append(line)

    # ── Signals & Data Sync ──────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self.bridge.telemetry_updated.connect(self.update_telemetry)
        self.bridge.agent_states_updated.connect(self.update_agents_list)
        self.bridge.timeline_event.connect(self.append_timeline_event)

        self.bridge.task_created.connect(self._on_bridge_task_created)
        self.bridge.task_completed.connect(self._on_bridge_task_completed)
        self.bridge.task_failed.connect(self._on_bridge_task_failed)
        self.bridge.task_cancelled.connect(self._on_bridge_task_cancelled)

        self.bridge.approval_requested.connect(self._on_approval_change)
        self.bridge.approval_resolved.connect(self._on_approval_change)

    def _on_bridge_task_created(self, d: dict) -> None:
        self._on_task_state("RUNNING", d.get("goal", ""))

    def _on_bridge_task_completed(self, d: dict) -> None:
        self._on_task_state("COMPLETED", d.get("result", ""))

    def _on_bridge_task_failed(self, d: dict) -> None:
        self._on_task_state("FAILED", d.get("error", ""))

    def _on_bridge_task_cancelled(self, d: dict) -> None:
        self._on_task_state("CANCELLED", d.get("reason", ""))

    def _on_task_state(self, status: str, text: str) -> None:
        self.cmd_status_lbl.setText(status)
        if status == "COMPLETED":
            self.cmd_status_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 136, 0.1); border: 1px solid {C.GREEN}; padding: 2px 6px; border-radius: 3px;")
        elif status == "FAILED":
            self.cmd_status_lbl.setStyleSheet(f"color: {C.RED}; background: rgba(255, 51, 85, 0.1); border: 1px solid {C.RED}; padding: 2px 6px; border-radius: 3px;")
        elif status == "RUNNING":
            self.cmd_status_lbl.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 212, 255, 0.1); border: 1px solid {C.PRI}; padding: 2px 6px; border-radius: 3px;")
        self.active_goal_lbl.setText(text[:180] or "Task in progress.")

    def _on_approval_change(self, _) -> None:
        pending = self.bridge.get_pending_approvals()
        count = len(pending)
        self.btn_appr_gate.setText(f"APPROVALS ({count})")
        if count > 0:
            self.btn_appr_gate.setStyleSheet(f"""
                QPushButton {{
                    color: {C.ACC}; background: rgba(255, 107, 0, 0.15);
                    border: 1px solid {C.ACC}; padding: 3px 8px; border-radius: 3px; font-weight: bold;
                }}
            """)
        else:
            self.btn_appr_gate.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: 1px solid {C.BORDER}; padding: 3px 8px; border-radius: 3px;
                }}
            """)

    def refresh_all(self) -> None:
        """Fetch real data across all subsystems to populate the home dashboard."""
        self.update_telemetry(self.bridge.get_system_telemetry())
        self.update_agents_list(self.bridge.get_agents_status())
        self.update_intelligence(self.bridge.get_intelligence_status())

        # Load workflows
        wfs = self.bridge.get_workflows_status()
        if wfs:
            first_wf = wfs[0]
            self.wf_active_title.setText(f"WORKFLOW: {first_wf['name'].upper()}")
            self.update_workflow_dag(first_wf.get("steps", []))
        else:
            self.update_workflow_dag([])

        self._on_approval_change(None)
