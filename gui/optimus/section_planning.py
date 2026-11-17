"""gui/optimus/section_planning.py — Dedicated Adaptive Planning & Self-Improvement Workspace.

Features:
- Visual Adaptive Execution Plan DAG & Step Inspector.
- Version history with replanning diagnostics and root-cause classification.
- Empirical reliability matrix for agents and tools backed by physical verification.
- Evidence-driven self-improvement recommendations with [APPLY] and [ROLLBACK] actions.
- Interactive plan generation and execution controls.
"""

from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font
from gui.theme import C
from gui.widgets import HoloPanel


class SectionPlanning(QWidget):
    """Adaptive planning, failure diagnosis, empirical metrics, and evidence-based self-improvement."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("ADAPTIVE PLANNING  ·  EVIDENCE-DRIVEN OPTIMIZATION")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        self.btn_run_optimizer = QPushButton("EVALUATE OPTIMIZATIONS")
        self.btn_run_optimizer.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_run_optimizer.setStyleSheet(btn_css("primary"))
        self.btn_run_optimizer.clicked.connect(self._run_optimizer_evaluation)
        hdr.addWidget(self.btn_run_optimizer)

        self.btn_refresh = QPushButton("REFRESH METRICS")
        self.btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_refresh.setStyleSheet(btn_css("dim"))
        self.btn_refresh.clicked.connect(self.refresh_all)
        hdr.addWidget(self.btn_refresh)

        layout.addLayout(hdr)

        # ── Main Horizontal Splitter ──────────────────────────────────────────
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # ── Left Column: Plan Generator & Active Steps Graph ──────────────────
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)
        left_lay.setSpacing(8)

        # Formulation Bar
        p_formulate = HoloPanel("FORMULATE ADAPTIVE PLAN", "INPUT")
        form_lay = QVBoxLayout()
        form_row = QHBoxLayout()

        self.goal_input = QLineEdit()
        self.goal_input.setPlaceholderText("Enter complex goal (e.g. 'Analyze code dependencies and then run test suite')...")
        self.goal_input.setFont(px_font(10))
        self.goal_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px 10px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
            }}
        """)
        self.goal_input.returnPressed.connect(self._on_formulate_clicked)
        form_row.addWidget(self.goal_input, stretch=1)

        self.btn_formulate = QPushButton("FORMULATE")
        self.btn_formulate.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_formulate.setStyleSheet(btn_css("primary"))
        self.btn_formulate.clicked.connect(self._on_formulate_clicked)
        form_row.addWidget(self.btn_formulate)
        form_lay.addLayout(form_row)

        # Active Plan Meta Badges
        self.meta_row = QHBoxLayout()
        self.lbl_plan_id = QLabel("PLAN: None")
        self.lbl_plan_id.setFont(mono_font(9, QFont.Weight.Bold))
        self.lbl_plan_id.setStyleSheet(f"color: {C.TEXT_DIM}; background: {C.PANEL}; padding: 3px 6px; border-radius: 3px;")
        self.meta_row.addWidget(self.lbl_plan_id)

        self.lbl_version = QLabel("v1")
        self.lbl_version.setFont(mono_font(9, QFont.Weight.Bold))
        self.lbl_version.setStyleSheet(f"color: {C.PRI}; background: {C.PANEL}; padding: 3px 6px; border-radius: 3px;")
        self.meta_row.addWidget(self.lbl_version)

        self.lbl_strategy = QLabel("CONSERVATIVE_SEQUENTIAL")
        self.lbl_strategy.setFont(px_font(8, QFont.Weight.Bold))
        self.lbl_strategy.setStyleSheet(f"color: {C.ACC}; background: {C.PANEL}; padding: 3px 6px; border-radius: 3px;")
        self.meta_row.addWidget(self.lbl_strategy)

        self.lbl_risk = QLabel("RISK: LOW")
        self.lbl_risk.setFont(px_font(8, QFont.Weight.Bold))
        self.lbl_risk.setStyleSheet(f"color: {C.GREEN}; background: {C.PANEL}; padding: 3px 6px; border-radius: 3px;")
        self.meta_row.addWidget(self.lbl_risk)
        self.meta_row.addStretch()

        form_lay.addLayout(self.meta_row)
        p_formulate.body.addLayout(form_lay)
        left_lay.addWidget(p_formulate)

        # Step Graph Table
        p_steps = HoloPanel("EXECUTION GRAPH & POSTCONDITIONS", "STEPS")
        self.table_steps = QTableWidget()
        self.table_steps.setColumnCount(6)
        self.table_steps.setHorizontalHeaderLabels(["STEP ID", "TITLE", "AGENT", "DEPENDS ON", "APPROVAL", "POSTCONDITIONS"])
        self.table_steps.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_steps.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_steps.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_steps.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table_steps.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_steps.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table_steps.setStyleSheet(self._table_css())
        self.table_steps.verticalHeader().setVisible(False)
        self.table_steps.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_steps.cellClicked.connect(self._on_step_selected)

        step_lay = QVBoxLayout()
        step_lay.addWidget(self.table_steps)
        p_steps.body.addLayout(step_lay)
        left_lay.addWidget(p_steps, stretch=1)

        # Replan / Diagnostic History
        p_replan = HoloPanel("REPLAN AUDIT & DIAGNOSTIC LEDGER", "AUDIT")
        self.table_replan = QTableWidget()
        self.table_replan.setColumnCount(5)
        self.table_replan.setHorizontalHeaderLabels(["OLD -> NEW", "FAILED STEP", "ROOT CAUSE", "REASON", "TIME"])
        self.table_replan.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_replan.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_replan.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_replan.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_replan.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_replan.setStyleSheet(self._table_css())
        self.table_replan.verticalHeader().setVisible(False)

        replan_lay = QVBoxLayout()
        replan_lay.addWidget(self.table_replan)
        p_replan.body.addLayout(replan_lay)
        left_lay.addWidget(p_replan, stretch=1)

        split.addWidget(left_w)

        # ── Right Column: Empirical Metrics & Optimization Recommendations ────
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        # Empirical Performance Matrix
        p_metrics = HoloPanel("EMPIRICAL TELEMETRY MATRIX", "VERIFIED")
        metrics_lay = QVBoxLayout()

        # Stats Cards
        card_row = QHBoxLayout()
        self.lbl_stat_success = QLabel("SUCCESS RATE\n100%")
        self.lbl_stat_success.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_stat_success.setFont(px_font(9, QFont.Weight.Bold))
        self.lbl_stat_success.setStyleSheet(f"background: {C.PANEL}; color: {C.GREEN}; border: 1px solid {C.BORDER}; border-radius: 4px; padding: 6px;")
        card_row.addWidget(self.lbl_stat_success)

        self.lbl_stat_recovery = QLabel("REPLAN RECOVERY\n100%")
        self.lbl_stat_recovery.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_stat_recovery.setFont(px_font(9, QFont.Weight.Bold))
        self.lbl_stat_recovery.setStyleSheet(f"background: {C.PANEL}; color: {C.PRI}; border: 1px solid {C.BORDER}; border-radius: 4px; padding: 6px;")
        card_row.addWidget(self.lbl_stat_recovery)

        self.lbl_stat_execs = QLabel("EXECUTIONS\n0")
        self.lbl_stat_execs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_stat_execs.setFont(px_font(9, QFont.Weight.Bold))
        self.lbl_stat_execs.setStyleSheet(f"background: {C.PANEL}; color: {C.WHITE}; border: 1px solid {C.BORDER}; border-radius: 4px; padding: 6px;")
        card_row.addWidget(self.lbl_stat_execs)
        metrics_lay.addLayout(card_row)

        # Agent Reliability Table
        self.table_agents = QTableWidget()
        self.table_agents.setColumnCount(4)
        self.table_agents.setHorizontalHeaderLabels(["AGENT", "RUNS", "SUCCESS %", "AVG DURATION"])
        self.table_agents.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_agents.setStyleSheet(self._table_css())
        self.table_agents.verticalHeader().setVisible(False)
        metrics_lay.addWidget(self.table_agents)

        p_metrics.body.addLayout(metrics_lay)
        right_lay.addWidget(p_metrics, stretch=1)

        # Evidence-Driven Recommendations Card
        p_recs = HoloPanel("EVIDENCE-DRIVEN OPTIMIZATIONS", "SAFE CONTROL")
        rec_lay = QVBoxLayout()

        self.table_recs = QTableWidget()
        self.table_recs.setColumnCount(5)
        self.table_recs.setHorizontalHeaderLabels(["ID", "TARGET", "CONFIDENCE", "DESCRIPTION", "ACTION"])
        self.table_recs.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_recs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_recs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_recs.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_recs.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table_recs.setStyleSheet(self._table_css())
        self.table_recs.verticalHeader().setVisible(False)
        rec_lay.addWidget(self.table_recs)

        p_recs.body.addLayout(rec_lay)
        right_lay.addWidget(p_recs, stretch=1)

        split.addWidget(right_w)
        split.setSizes([600, 500])
        layout.addWidget(split, stretch=1)

        # Current plan cache
        self._current_plan: dict[str, Any] | None = None

        # Connect signals from service bridge if available
        if hasattr(self.bridge, "plan_formulated"):
            self.bridge.plan_formulated.connect(self._on_plan_formulated_event)
        if hasattr(self.bridge, "replan_triggered"):
            self.bridge.replan_triggered.connect(self._on_replan_triggered_event)
        if hasattr(self.bridge, "outcome_recorded"):
            self.bridge.outcome_recorded.connect(self._on_outcome_recorded_signal)
        if hasattr(self.bridge, "optimization_recommended"):
            self.bridge.optimization_recommended.connect(self._on_opt_recommended_signal)

        self.refresh_all()

    def _on_outcome_recorded_signal(self, _=None) -> None:
        self.refresh_all()

    def _on_opt_recommended_signal(self, _=None) -> None:
        self._refresh_recommendations()

    # ── Signal Handlers & Actions ─────────────────────────────────────────────

    def _on_formulate_clicked(self) -> None:
        goal = self.goal_input.text().strip()
        if not goal:
            return
        if hasattr(self.bridge, "create_adaptive_plan"):
            plan_data = self.bridge.create_adaptive_plan(goal)
            self._display_plan(plan_data)

    def _on_plan_formulated_event(self, data: dict[str, Any]) -> None:
        if self._current_plan and self._current_plan.get("plan_id") == data.get("plan_id"):
            self.lbl_version.setText(f"v{data.get('version', 1)}")
            self.lbl_strategy.setText(str(data.get("strategy", "")))

    def _on_replan_triggered_event(self, data: dict[str, Any]) -> None:
        self.refresh_all()

    def _on_step_selected(self, row: int, col: int) -> None:
        pass

    def _display_plan(self, plan_data: dict[str, Any]) -> None:
        self._current_plan = plan_data
        self.lbl_plan_id.setText(f"PLAN: {plan_data.get('plan_id', 'Unknown')[:12]}")
        self.lbl_version.setText(f"v{plan_data.get('version', 1)}")
        self.lbl_strategy.setText(str(plan_data.get("strategy", "CONSERVATIVE_SEQUENTIAL")))
        risk = str(plan_data.get("risk_level", "LOW"))
        self.lbl_risk.setText(f"RISK: {risk}")
        if risk in ("HIGH", "CRITICAL"):
            self.lbl_risk.setStyleSheet(f"color: {C.RED}; background: {C.PANEL}; padding: 3px 6px; border-radius: 3px;")
        else:
            self.lbl_risk.setStyleSheet(f"color: {C.GREEN}; background: {C.PANEL}; padding: 3px 6px; border-radius: 3px;")

        # Populate Steps Table
        steps = plan_data.get("steps", [])
        self.table_steps.setRowCount(len(steps))
        for row, s in enumerate(steps):
            sid_item = QTableWidgetItem(s.get("step_id", ""))
            sid_item.setFont(mono_font(9))
            sid_item.setForeground(QColor(C.PRI))

            title_item = QTableWidgetItem(s.get("title", ""))
            title_item.setFont(px_font(9))

            agent_item = QTableWidgetItem(s.get("assigned_agent", ""))
            agent_item.setFont(mono_font(9))
            agent_item.setForeground(QColor(C.WHITE))

            deps_item = QTableWidgetItem(", ".join(s.get("depends_on", [])) or "None")
            deps_item.setFont(mono_font(8))
            deps_item.setForeground(QColor(C.TEXT_DIM))

            appr_txt = "MANDATORY" if s.get("requires_approval") else "AUTO"
            appr_item = QTableWidgetItem(appr_txt)
            appr_item.setFont(px_font(8, QFont.Weight.Bold))
            appr_item.setForeground(QColor(C.ACC if s.get("requires_approval") else C.GREEN))

            pcs = s.get("postconditions", [])
            pc_desc = f"{len(pcs)} CHECK(S)" if pcs else "NONE"
            pc_item = QTableWidgetItem(pc_desc)
            pc_item.setFont(mono_font(8))

            self.table_steps.setItem(row, 0, sid_item)
            self.table_steps.setItem(row, 1, title_item)
            self.table_steps.setItem(row, 2, agent_item)
            self.table_steps.setItem(row, 3, deps_item)
            self.table_steps.setItem(row, 4, appr_item)
            self.table_steps.setItem(row, 5, pc_item)

        # Populate Replan History if any
        replan_hist = plan_data.get("replan_history", [])
        self.table_replan.setRowCount(len(replan_hist))
        for row, rh in enumerate(replan_hist):
            trans_item = QTableWidgetItem(f"v{rh.get('old_version', 1)} -> v{rh.get('new_version', 2)}")
            trans_item.setFont(mono_font(9, QFont.Weight.Bold))
            trans_item.setForeground(QColor(C.PRI))

            step_item = QTableWidgetItem(rh.get("failed_step_id", ""))
            step_item.setFont(mono_font(8))

            cause_item = QTableWidgetItem(rh.get("failure_type", "UNKNOWN"))
            cause_item.setFont(px_font(8, QFont.Weight.Bold))
            cause_item.setForeground(QColor(C.RED))

            reason_item = QTableWidgetItem(rh.get("reason", "")[:80])
            reason_item.setFont(px_font(8))

            t_str = time.strftime("%H:%M:%S", time.localtime(rh.get("timestamp", time.time())))
            t_item = QTableWidgetItem(t_str)
            t_item.setFont(mono_font(8))
            t_item.setForeground(QColor(C.TEXT_DIM))

            self.table_replan.setItem(row, 0, trans_item)
            self.table_replan.setItem(row, 1, step_item)
            self.table_replan.setItem(row, 2, cause_item)
            self.table_replan.setItem(row, 3, reason_item)
            self.table_replan.setItem(row, 4, t_item)

    def refresh_all(self) -> None:
        """Query bridge for empirical metrics, agent reliability, and recommendations."""
        self._refresh_metrics()
        self._refresh_recommendations()

    def _refresh_metrics(self) -> None:
        if not hasattr(self.bridge, "get_planning_metrics"):
            return
        m = self.bridge.get_planning_metrics()

        succ_rate = m.get("overall_success_rate", 1.0)
        self.lbl_stat_success.setText(f"SUCCESS RATE\n{round(succ_rate * 100, 1)}%")

        rec_rate = m.get("recovery_rate", 1.0)
        self.lbl_stat_recovery.setText(f"REPLAN RECOVERY\n{round(rec_rate * 100, 1)}%")

        total_execs = m.get("total_executions", 0)
        self.lbl_stat_execs.setText(f"EXECUTIONS\n{total_execs}")

        # Update Agent Metrics
        agent_stats = m.get("agent_metrics", {})
        self.table_agents.setRowCount(len(agent_stats))
        for row, (ag_name, data) in enumerate(agent_stats.items()):
            name_item = QTableWidgetItem(ag_name)
            name_item.setFont(mono_font(9, QFont.Weight.Bold))
            name_item.setForeground(QColor(C.WHITE))

            runs_item = QTableWidgetItem(str(data.get("total_runs", 0)))
            runs_item.setFont(mono_font(9))

            rate_pct = round(data.get("success_rate", 1.0) * 100, 1)
            rate_item = QTableWidgetItem(f"{rate_pct}%")
            rate_item.setFont(px_font(9, QFont.Weight.Bold))
            rate_item.setForeground(QColor(C.GREEN if rate_pct >= 80 else C.ACC))

            dur_item = QTableWidgetItem(f"{data.get('avg_duration', 0.0)}s")
            dur_item.setFont(mono_font(8))
            dur_item.setForeground(QColor(C.TEXT_DIM))

            self.table_agents.setItem(row, 0, name_item)
            self.table_agents.setItem(row, 1, runs_item)
            self.table_agents.setItem(row, 2, rate_item)
            self.table_agents.setItem(row, 3, dur_item)

    def _refresh_recommendations(self) -> None:
        if not hasattr(self.bridge, "list_optimization_recommendations"):
            return
        recs = self.bridge.list_optimization_recommendations()
        self.table_recs.setRowCount(len(recs))

        for row, r in enumerate(recs):
            rid_item = QTableWidgetItem(r.get("recommendation_id", "")[:12])
            rid_item.setFont(mono_font(8))
            rid_item.setForeground(QColor(C.PRI))

            target_item = QTableWidgetItem(r.get("target_key", r.get("target_type", "")))
            target_item.setFont(mono_font(8))

            conf = round(r.get("confidence_score", 0.0) * 100, 1)
            conf_item = QTableWidgetItem(f"{conf}%")
            conf_item.setFont(px_font(8, QFont.Weight.Bold))
            conf_item.setForeground(QColor(C.GREEN if conf >= 80 else C.ACC))

            desc_item = QTableWidgetItem(r.get("description", ""))
            desc_item.setFont(px_font(8))

            # Action button
            is_applied = r.get("is_applied", False)
            btn_act = QPushButton("ROLLBACK" if is_applied else "APPLY")
            btn_act.setFont(px_font(8, QFont.Weight.Bold))
            btn_act.setStyleSheet(btn_css("danger" if is_applied else "primary"))
            rec_id = r.get("recommendation_id", "")
            if is_applied:
                btn_act.clicked.connect(lambda checked=False, rid=rec_id: self._rollback_rec(rid))
            else:
                btn_act.clicked.connect(lambda checked=False, rid=rec_id: self._apply_rec(rid))

            self.table_recs.setItem(row, 0, rid_item)
            self.table_recs.setItem(row, 1, target_item)
            self.table_recs.setItem(row, 2, conf_item)
            self.table_recs.setItem(row, 3, desc_item)
            self.table_recs.setCellWidget(row, 4, btn_act)

    def _run_optimizer_evaluation(self) -> None:
        if hasattr(self.bridge, "evaluate_optimizations"):
            self.bridge.evaluate_optimizations()
            self._refresh_recommendations()

    def _apply_rec(self, rec_id: str) -> None:
        if hasattr(self.bridge, "apply_optimization_recommendation"):
            self.bridge.apply_optimization_recommendation(rec_id)
            self._refresh_recommendations()

    def _rollback_rec(self, rec_id: str) -> None:
        if hasattr(self.bridge, "rollback_optimization_recommendation"):
            self.bridge.rollback_optimization_recommendation(rec_id)
            self._refresh_recommendations()

    def _table_css(self) -> str:
        return f"""
            QTableWidget {{
                background: {C.PANEL};
                color: {C.WHITE};
                gridline-color: {C.BORDER};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
            }}
            QHeaderView::section {{
                background: {C.PANEL};
                color: {C.PRI};
                padding: 4px;
                border: none;
                border-bottom: 1px solid {C.BORDER};
                font-size: 10px;
                font-weight: bold;
            }}
            QTableWidget::item:selected {{
                background: rgba(0, 212, 255, 0.15);
                color: {C.WHITE};
            }}
        """
