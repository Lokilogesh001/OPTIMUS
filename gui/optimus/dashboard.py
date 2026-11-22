"""gui/optimus/dashboard.py — De-Slopped Mission Control Home Screen for OPTIMUS.

Implements the central workspace command center:
1. Assistant Conversation & Directive Input Hub (The Primary Focus).
2. Active Mission & Desktop Visual Awareness Horizon.
3. Multi-Agent Orchestration Roster.
4. Precision Hardware Telemetry & Runtime Health.
5. Chronological Execution & Verification Ledger.
"""

from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel


class TelemetryMetricBar(QWidget):
    """High-density linear telemetry meter with tabular figures."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._v: float = 0.0
        self._text: str = "--"

        self.setFixedHeight(38)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)

        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        self.lbl_name = QLabel(label)
        self.lbl_name.setFont(px_font(9, QFont.Weight.Bold))
        self.lbl_name.setStyleSheet(f"color: {C.TEXT_MED};")
        hdr_row.addWidget(self.lbl_name)
        hdr_row.addStretch()

        self.lbl_val = QLabel("--")
        self.lbl_val.setFont(mono_font(9))
        self.lbl_val.setStyleSheet(f"color: {C.WHITE};")
        hdr_row.addWidget(self.lbl_val)
        lay.addLayout(hdr_row)

        self.bar = QProgressBar()
        self.bar.setFixedHeight(5)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self._update_bar_style()
        lay.addWidget(self.bar)

    def set_value(self, pct: float, text: str) -> None:
        self._v = max(0.0, min(100.0, float(pct)))
        self._text = text
        self.bar.setValue(int(round(self._v)))
        self.lbl_val.setText(text)
        self._update_bar_style()

    def value(self) -> float:
        return self._v

    def _update_bar_style(self) -> None:
        if self._v > 85:
            chunk_color = C.RED
        elif self._v > 65:
            chunk_color = C.ACC
        else:
            chunk_color = C.PRI

        self.bar.setStyleSheet(f"""
            QProgressBar {{
                background: {C.BAR_BG};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {chunk_color};
                border-radius: 2px;
            }}
        """)


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
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }" + scroll_css())

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.addWidget(scroll)

        main_lay = QVBoxLayout(container)
        main_lay.setContentsMargins(12, 12, 12, 12)
        main_lay.setSpacing(12)
        scroll.setWidget(container)

        # ── 1. Top Horizon: Assistant Conversation & Directive Center ────────
        self.panel_cmd = HoloPanel("ASSISTANT CONVERSATION & DIRECTIVE STREAM", "JARVIS_CORE")
        self._build_conversation_hub(self.panel_cmd.body)
        main_lay.addWidget(self.panel_cmd)

        # ── 2. Mid Horizon: Mission Control & Awareness (Split View) ─────────
        mid_split = QSplitter(Qt.Orientation.Horizontal)
        mid_split.setStyleSheet("QSplitter::handle { background: transparent; }")

        # 2a. Left Column: Mission Execution + Desktop Visual Awareness
        left_col = QWidget()
        left_lay = QVBoxLayout(left_col)
        left_lay.setContentsMargins(0, 0, 6, 0)
        left_lay.setSpacing(12)

        self.panel_wf = HoloPanel("ACTIVE MISSION & WORKFLOW MONITOR", "DAG_STATE")
        self._build_workflow_monitor(self.panel_wf.body)
        left_lay.addWidget(self.panel_wf)

        self.panel_intel = HoloPanel("INTELLIGENCE GATEWAY & DESKTOP CONTEXT", "NEURAL")
        self._build_intelligence_monitor(self.panel_intel.body)
        left_lay.addWidget(self.panel_intel)

        mid_split.addWidget(left_col)

        # 2b. Right Column: Agent Roster + System Telemetry
        right_col = QWidget()
        right_lay = QVBoxLayout(right_col)
        right_lay.setContentsMargins(6, 0, 0, 0)
        right_lay.setSpacing(12)

        self.panel_agents = HoloPanel("AGENT ORCHESTRATION ROSTER", "ROSTER")
        self._build_agent_activity(self.panel_agents.body)
        right_lay.addWidget(self.panel_agents)

        self.panel_sys = HoloPanel("SYSTEM TELEMETRY & RUNTIME HEALTH", "METRICS")
        self._build_system_monitor(self.panel_sys.body)
        right_lay.addWidget(self.panel_sys)

        mid_split.addWidget(right_col)
        mid_split.setSizes([540, 460])
        main_lay.addWidget(mid_split)

        # ── 3. Bottom Horizon: Execution & Verification Timeline ─────────────
        self.panel_time = HoloPanel("CHRONOLOGICAL EXECUTION & VERIFICATION LEDGER", "EVENT_BUS")
        self._build_execution_timeline(self.panel_time.body)
        main_lay.addWidget(self.panel_time)

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

    # ── Module 1: Conversation & Directive Input Hub ─────────────────────────

    def _build_conversation_hub(self, lay: QVBoxLayout) -> None:
        # Status header row
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)

        self.cmd_status_lbl = QLabel("READY")
        self.cmd_status_lbl.setFont(px_font(9, QFont.Weight.Bold))
        self.cmd_status_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(16, 185, 129, 0.12); border: 1px solid {C.GREEN}; padding: 2px 8px; border-radius: 3px;")
        hdr_row.addWidget(self.cmd_status_lbl)

        self.cmd_voice_lbl = QLabel("MIC STANDBY")
        self.cmd_voice_lbl.setFont(mono_font(8))
        self.cmd_voice_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        hdr_row.addWidget(self.cmd_voice_lbl)

        hdr_row.addStretch()

        self.cmd_time_lbl = QLabel("0.0s")
        self.cmd_time_lbl.setFont(mono_font(9))
        self.cmd_time_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr_row.addWidget(self.cmd_time_lbl)
        lay.addLayout(hdr_row)

        # Active goal label
        self.active_goal_lbl = QLabel("No active task executing. Enter natural language directive below.")
        self.active_goal_lbl.setFont(px_font(10))
        self.active_goal_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: {C.PANEL2}; border: 1px solid {C.BORDER}; padding: 6px 10px; border-radius: 4px;")
        self.active_goal_lbl.setWordWrap(True)
        lay.addWidget(self.active_goal_lbl)

        # Transcript stream container
        self.chat_transcript = QTextEdit()
        self.chat_transcript.setReadOnly(True)
        self.chat_transcript.setFont(px_font(10))
        self.chat_transcript.setFixedHeight(110)
        self.chat_transcript.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
            }}
        """ + scroll_css())
        self.chat_transcript.append(
            f"<font color='{C.TEXT_DIM}'>[OPTIMUS ONLINE]</font> "
            f"<font color='{C.WHITE}'>Autonomous Agentic AI Assistant ready. Directives are validated via policy engine.</font>"
        )
        lay.addWidget(self.chat_transcript)

        # Command input row
        in_row = QHBoxLayout()
        in_row.setSpacing(6)

        prompt_sym = QLabel(">")
        prompt_sym.setFont(mono_font(11))
        prompt_sym.setStyleSheet(f"color: {C.PRI}; background: transparent; padding-left: 2px;")
        in_row.addWidget(prompt_sym)

        self.cmd_input = QLineEdit()
        self.cmd_input.setFont(px_font(10))
        self.cmd_input.setPlaceholderText("Enter goal or instruction for OPTIMUS (e.g. 'Audit filesystem access', 'List open windows')...")
        self.cmd_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px 10px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
                background: #141f36;
            }}
        """)
        self.cmd_input.returnPressed.connect(self._submit_task)
        in_row.addWidget(self.cmd_input, stretch=1)

        self.btn_submit = QPushButton("Dispatch")
        self.btn_submit.setFont(px_font(10, QFont.Weight.Medium))
        self.btn_submit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_submit.setStyleSheet(btn_css("primary"))
        self.btn_submit.clicked.connect(self._submit_task)
        in_row.addWidget(self.btn_submit)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFont(px_font(10, QFont.Weight.Medium))
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(btn_css("danger"))
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
        self.cmd_status_lbl.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 132, 255, 0.12); border: 1px solid {C.PRI}; padding: 2px 8px; border-radius: 3px;")
        self.chat_transcript.append(
            f"<font color='{C.PRI}'><b>User:</b></font> <font color='{C.WHITE}'>{text}</font>"
        )
        self.bridge.submit_command(text)

    def _cancel_task(self) -> None:
        self.bridge.cancel_active_task()
        self.cmd_status_lbl.setText("CANCELLED")
        self.cmd_status_lbl.setStyleSheet(f"color: {C.RED}; background: rgba(239, 68, 68, 0.12); border: 1px solid {C.RED}; padding: 2px 8px; border-radius: 3px;")
        self.chat_transcript.append(
            f"<font color='{C.RED}'><b>System:</b></font> <font color='{C.TEXT_DIM}'>Active task cancelled by user.</font>"
        )

    # ── Module 2: Workflow Monitor ───────────────────────────────────────────

    def _build_workflow_monitor(self, lay: QVBoxLayout) -> None:
        ctrl_row = QHBoxLayout()
        self.wf_active_title = QLabel("NO ACTIVE WORKFLOW")
        self.wf_active_title.setFont(px_font(10, QFont.Weight.DemiBold))
        self.wf_active_title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        ctrl_row.addWidget(self.wf_active_title)
        ctrl_row.addStretch()

        self.btn_appr_gate = QPushButton("Approvals (0)")
        self.btn_appr_gate.setFont(px_font(9, QFont.Weight.Medium))
        self.btn_appr_gate.setStyleSheet(btn_css("dim"))
        ctrl_row.addWidget(self.btn_appr_gate)
        lay.addLayout(ctrl_row)

        self.wf_dag_scroll = QScrollArea()
        self.wf_dag_scroll.setWidgetResizable(True)
        self.wf_dag_scroll.setFixedHeight(95)
        self.wf_dag_scroll.setStyleSheet("QScrollArea { background: transparent; border: 1px solid " + C.BORDER + "; }" + scroll_css())

        self.wf_dag_widget = QWidget()
        self.wf_dag_widget.setStyleSheet("background: transparent;")
        self.wf_dag_layout = QHBoxLayout(self.wf_dag_widget)
        self.wf_dag_layout.setContentsMargins(6, 6, 6, 6)
        self.wf_dag_layout.setSpacing(6)
        self.wf_dag_scroll.setWidget(self.wf_dag_widget)
        lay.addWidget(self.wf_dag_scroll)

    def update_workflow_dag(self, steps_data: list[dict[str, Any]]) -> None:
        while self.wf_dag_layout.count():
            item = self.wf_dag_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not steps_data:
            lbl = QLabel("No active DAG steps. Select or launch a mission.")
            lbl.setFont(px_font(9))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
            self.wf_dag_layout.addWidget(lbl)
            return

        for idx, s in enumerate(steps_data):
            card = QFrame()
            card.setFixedWidth(120)
            status = str(s.get("status", "PENDING")).upper()
            if "COMPLETED" in status:
                border_c, bg_c = C.GREEN, "rgba(16, 185, 129, 0.1)"
            elif "RUNNING" in status:
                border_c, bg_c = C.PRI, "rgba(0, 132, 255, 0.12)"
            elif "APPROVAL" in status:
                border_c, bg_c = C.ACC, "rgba(245, 158, 11, 0.12)"
            elif "FAILED" in status:
                border_c, bg_c = C.RED, "rgba(239, 68, 68, 0.12)"
            else:
                border_c, bg_c = C.BORDER, C.PANEL2

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
            name_lbl.setFont(px_font(9, QFont.Weight.Bold))
            name_lbl.setStyleSheet(f"color: {C.WHITE};")
            c_lay.addWidget(name_lbl)

            agent_lbl = QLabel(f"[{s.get('assigned_agent', 'agent')}]")
            agent_lbl.setFont(mono_font(8))
            agent_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
            c_lay.addWidget(agent_lbl)

            st_lbl = QLabel(status)
            st_lbl.setFont(px_font(8, QFont.Weight.Medium))
            st_lbl.setStyleSheet(f"color: {border_c};")
            c_lay.addWidget(st_lbl)

            self.wf_dag_layout.addWidget(card)

            if idx < len(steps_data) - 1:
                arrow = QLabel("➔")
                arrow.setFont(px_font(9))
                arrow.setStyleSheet(f"color: {C.BORDER_B};")
                self.wf_dag_layout.addWidget(arrow)

        self.wf_dag_layout.addStretch()

    # ── Module 3: Agent Activity Roster ──────────────────────────────────────

    def _build_agent_activity(self, lay: QVBoxLayout) -> None:
        self.agents_scroll = QScrollArea()
        self.agents_scroll.setWidgetResizable(True)
        self.agents_scroll.setFixedHeight(95)
        self.agents_scroll.setStyleSheet("QScrollArea { background: transparent; border: 1px solid " + C.BORDER + "; }" + scroll_css())

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
            row.setStyleSheet(f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 4px;")
            r_lay = QHBoxLayout(row)
            r_lay.setContentsMargins(8, 4, 8, 4)
            r_lay.setSpacing(6)

            st_color = C.ACC if a["status"] == "BUSY" else C.GREEN
            st_dot = QLabel("●")
            st_dot.setFont(px_font(8))
            st_dot.setStyleSheet(f"color: {st_color};")
            r_lay.addWidget(st_dot)

            name = QLabel(a.get("title", a.get("name", "agent")))
            name.setFont(px_font(9, QFont.Weight.Bold))
            name.setStyleSheet(f"color: {C.WHITE};")
            r_lay.addWidget(name)

            role = QLabel(f"({a.get('domain', 'general')})")
            role.setFont(mono_font(8))
            role.setStyleSheet(f"color: {C.TEXT_DIM};")
            r_lay.addWidget(role)
            r_lay.addStretch()

            status_lbl = QLabel(a.get("status", "IDLE"))
            status_lbl.setFont(px_font(8, QFont.Weight.Medium))
            status_lbl.setStyleSheet(f"color: {st_color};")
            r_lay.addWidget(status_lbl)

            self.agents_lay.addWidget(row)

        self.agents_lay.addStretch()

    # ── Module 4: Intelligence Gateway & Context ─────────────────────────────

    def _build_intelligence_monitor(self, lay: QVBoxLayout) -> None:
        self.intel_model_lbl = QLabel("MODEL: Initializing...")
        self.intel_model_lbl.setFont(px_font(10, QFont.Weight.DemiBold))
        self.intel_model_lbl.setStyleSheet(f"color: {C.WHITE};")
        lay.addWidget(self.intel_model_lbl)

        row2 = QHBoxLayout()
        self.intel_prov_lbl = QLabel("PROVIDER: LOCAL")
        self.intel_prov_lbl.setFont(mono_font(8))
        self.intel_prov_lbl.setStyleSheet(f"color: {C.PRI};")
        row2.addWidget(self.intel_prov_lbl)

        self.intel_class_lbl = QLabel("LOCAL ONLY")
        self.intel_class_lbl.setFont(px_font(8, QFont.Weight.Bold))
        self.intel_class_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(16, 185, 129, 0.12); border: 1px solid {C.GREEN}; border-radius: 2px; padding: 1px 4px;")
        row2.addWidget(self.intel_class_lbl)
        row2.addStretch()
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.intel_cb_lbl = QLabel("CIRCUIT BREAKER: CLOSED")
        self.intel_cb_lbl.setFont(mono_font(8))
        self.intel_cb_lbl.setStyleSheet(f"color: {C.GREEN};")
        row3.addWidget(self.intel_cb_lbl)

        self.intel_policy_lbl = QLabel("POLICY: LOCAL_FIRST")
        self.intel_policy_lbl.setFont(mono_font(8))
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
            self.intel_class_lbl.setStyleSheet(f"color: {C.GREEN}; background: rgba(16, 185, 129, 0.12); border: 1px solid {C.GREEN}; border-radius: 2px; padding: 1px 4px;")
        else:
            self.intel_class_lbl.setText("CLOUD (HYBRID)")
            self.intel_class_lbl.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 132, 255, 0.12); border: 1px solid {C.PRI}; border-radius: 2px; padding: 1px 4px;")

    # ── Module 5: System Telemetry & Health ──────────────────────────────────

    def _build_system_monitor(self, lay: QVBoxLayout) -> None:
        gauges_col = QVBoxLayout()
        gauges_col.setSpacing(2)

        self.g_cpu = TelemetryMetricBar("CPU")
        self.g_mem = TelemetryMetricBar("RAM")
        self.g_gpu = TelemetryMetricBar("GPU")
        self.g_disk = TelemetryMetricBar("DISK")

        for g in (self.g_cpu, self.g_mem, self.g_gpu, self.g_disk):
            gauges_col.addWidget(g)
        lay.addLayout(gauges_col)

        info_row = QHBoxLayout()
        self.sys_procs_lbl = QLabel("PROCS: --")
        self.sys_procs_lbl.setFont(mono_font(8))
        self.sys_procs_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        info_row.addWidget(self.sys_procs_lbl)

        self.sys_health_lbl = QLabel("HEALTH: OPTIMAL")
        self.sys_health_lbl.setFont(px_font(8, QFont.Weight.Bold))
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

        self.sys_procs_lbl.setText(f"PROCS: {telem.get('process_count', len(telem.get('processes', [])) or '--')}")

    # ── Module 6: Execution & Verification Timeline ──────────────────────────

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
                border-radius: 4px;
                padding: 6px;
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
            st_color = C.GREEN
            bg_color = "rgba(16, 185, 129, 0.12)"
        elif status == "FAILED":
            st_color = C.RED
            bg_color = "rgba(239, 68, 68, 0.12)"
        elif status == "RUNNING":
            st_color = C.PRI
            bg_color = "rgba(0, 132, 255, 0.12)"
        else:
            st_color = C.TEXT_MED
            bg_color = C.PANEL2

        self.cmd_status_lbl.setStyleSheet(f"color: {st_color}; background: {bg_color}; border: 1px solid {st_color}; padding: 2px 8px; border-radius: 3px;")
        if text:
            self.active_goal_lbl.setText(text)
            self.chat_transcript.append(
                f"<font color='{st_color}'><b>[{status}]</b></font> <font color='{C.WHITE}'>{text[:180]}</font>"
            )

    def _on_approval_change(self, _) -> None:
        pending = self.bridge.get_pending_approvals()
        self.btn_appr_gate.setText(f"Approvals ({len(pending)})")
        if pending:
            self.btn_appr_gate.setStyleSheet(btn_css("on"))
        else:
            self.btn_appr_gate.setStyleSheet(btn_css("dim"))

    def refresh_all(self) -> None:
        """Trigger sync across all bridge telemetry sources."""
        self.update_intelligence(self.bridge.get_intelligence_status())
        self.update_agents_list(self.bridge.get_agents_status())
        self.update_telemetry(self.bridge.get_system_telemetry() if hasattr(self.bridge, "get_system_telemetry") else {})
        self._on_approval_change(None)
