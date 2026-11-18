"""gui/optimus/section_developer.py — Dedicated Developer Workspace UI for OPTIMUS.

Provides:
- Active project workspace selector and tree explorer.
- Autonomous Developer Agent task submission and cancellation.
- 8-Phase Lifecycle visual tracker (UNDERSTAND -> INSPECT -> PLAN -> APPROVE -> IMPLEMENT -> EXECUTE -> VERIFY -> REPORT).
- Modified files history and side-by-side / unified diff viewer with one-click rollback.
- Sandboxed terminal command execution log and interactive shell prompt.
- Independent verification results and evidence ledger inspector.
- Resource consumption telemetry.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.agents.contracts import AgentInputContract
from core.agents.developer import DeveloperAgent
from core.developer.lifecycle import LIFECYCLE_SEQUENCE, LifecyclePhase
from core.developer.terminal import TerminalManager
from core.developer.tools import get_active_terminal, get_active_workspace
from core.developer.workspace import DevelopmentWorkspace
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C


class PhaseIndicatorWidget(QWidget):
    """Visual horizontal breadcrumb tracker for the 8-Phase Developer Lifecycle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background: {C.PANEL}; border: 1px solid {C.BORDER_A}; border-radius: 4px;")

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._layout.setSpacing(4)

        self._labels: dict[LifecyclePhase, QLabel] = {}
        for idx, phase in enumerate(LIFECYCLE_SEQUENCE):
            lbl = QLabel(f"{idx+1}. {phase.value}")
            lbl.setFont(px_font(9, QFont.Weight.Bold, spacing=1.1))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"""
                color: {C.TEXT_DIM};
                background: transparent;
                border: 1px solid transparent;
                border-radius: 3px;
                padding: 2px 6px;
            """)
            self._layout.addWidget(lbl)
            self._labels[phase] = lbl

            if idx < len(LIFECYCLE_SEQUENCE) - 1:
                arrow = QLabel("→")
                arrow.setFont(px_font(9, QFont.Weight.Light))
                arrow.setStyleSheet(f"color: {C.BORDER_B}; background: transparent;")
                self._layout.addWidget(arrow)

        self._current_phase = LifecyclePhase.IDLE

    def set_phase(self, phase: LifecyclePhase, completed: list[LifecyclePhase] | None = None) -> None:
        """Update visual highlights for current and completed phases."""
        self._current_phase = phase
        completed_set = set(completed or [])

        for p, lbl in self._labels.items():
            if p == phase:
                # Active glowing cyan
                lbl.setStyleSheet(f"""
                    color: {C.WHITE};
                    background: rgba(0, 212, 255, 0.25);
                    border: 1px solid {C.PRI};
                    border-radius: 3px;
                    padding: 2px 6px;
                """)
            elif p in completed_set:
                # Completed glowing green
                lbl.setStyleSheet(f"""
                    color: {C.GREEN};
                    background: rgba(0, 255, 136, 0.12);
                    border: 1px solid rgba(0, 255, 136, 0.4);
                    border-radius: 3px;
                    padding: 2px 6px;
                """)
            else:
                # Inactive / pending
                lbl.setStyleSheet(f"""
                    color: {C.TEXT_DIM};
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 3px;
                    padding: 2px 6px;
                """)

    def reset(self) -> None:
        self.set_phase(LifecyclePhase.IDLE, [])


class SectionDeveloper(QWidget):
    """Full-featured Developer Workspace and Autonomous Coding Agent Surface."""

    task_state_signal = pyqtSignal(dict)

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.workspace: DevelopmentWorkspace = get_active_workspace()
        self.terminal: TerminalManager = get_active_terminal()
        self.agent = DeveloperAgent()

        self._active_task_thread: threading.Thread | None = None
        self._is_task_running = False

        self.setStyleSheet(f"background: {C.BG};")
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        # ── 1. Top Workspace & Quick Action Header ────────────────────────────
        top_bar = QHBoxLayout()
        title = QLabel("DEVELOPER WORKSPACE  ·  AUTONOMOUS CODING RUNTIME")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        top_bar.addWidget(title)
        top_bar.addStretch()

        # Workspace root display
        ws_box = QHBoxLayout()
        ws_box.setSpacing(6)
        ws_lbl = QLabel("PROJECT:")
        ws_lbl.setFont(px_font(10, QFont.Weight.Bold))
        ws_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        ws_box.addWidget(ws_lbl)

        self.lbl_ws_path = QLabel(self.workspace.root.name)
        self.lbl_ws_path.setFont(mono_font(10))
        self.lbl_ws_path.setStyleSheet(f"color: {C.PRI_DIM}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 3px 8px;")
        self.lbl_ws_path.setToolTip(str(self.workspace.root))
        ws_box.addWidget(self.lbl_ws_path)

        self.btn_change_dir = QPushButton("CHANGE...")
        self.btn_change_dir.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_change_dir.setStyleSheet(btn_css(kind="dim"))
        self.btn_change_dir.clicked.connect(self._on_change_directory)
        ws_box.addWidget(self.btn_change_dir)

        self.btn_rescan = QPushButton("RESCAN")
        self.btn_rescan.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_rescan.setStyleSheet(btn_css(kind="dim"))
        self.btn_rescan.clicked.connect(self.refresh_all)
        ws_box.addWidget(self.btn_rescan)

        self.btn_run_tests = QPushButton("RUN PYTEST")
        self.btn_run_tests.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_run_tests.setStyleSheet(btn_css(kind="dim"))
        self.btn_run_tests.clicked.connect(self._on_run_tests)
        ws_box.addWidget(self.btn_run_tests)

        self.btn_git_status = QPushButton("GIT STATUS")
        self.btn_git_status.setFont(px_font(10, QFont.Weight.DemiBold))
        self.btn_git_status.setStyleSheet(btn_css(kind="dim"))
        self.btn_git_status.clicked.connect(self._on_git_status)
        ws_box.addWidget(self.btn_git_status)

        top_bar.addLayout(ws_box)
        root_layout.addLayout(top_bar)

        # ── 2. Lifecycle Phase Tracker ────────────────────────────────────────
        self.phase_tracker = PhaseIndicatorWidget(self)
        root_layout.addWidget(self.phase_tracker)

        # ── 3. Task Input & Control Bar ───────────────────────────────────────
        task_bar = QHBoxLayout()
        task_bar.setSpacing(8)

        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("Enter natural language development task (e.g., 'Refactor database models and add validation tests')...")
        self.task_input.setFont(px_font(11))
        self.task_input.setStyleSheet(f"""
            QLineEdit {{
                color: {C.WHITE};
                background: {C.PANEL};
                border: 1px solid {C.BORDER_A};
                border-radius: 4px;
                padding: 6px 12px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
                background: rgba(0, 212, 255, 0.05);
            }}
        """)
        self.task_input.returnPressed.connect(self._on_execute_task)
        task_bar.addWidget(self.task_input, stretch=1)

        self.btn_execute = QPushButton("EXECUTE TASK")
        self.btn_execute.setFont(px_font(11, QFont.Weight.Bold))
        self.btn_execute.setStyleSheet(btn_css(kind="primary"))
        self.btn_execute.clicked.connect(self._on_execute_task)
        task_bar.addWidget(self.btn_execute)

        self.btn_cancel = QPushButton("CANCEL")
        self.btn_cancel.setFont(px_font(11, QFont.Weight.DemiBold))
        self.btn_cancel.setStyleSheet(btn_css(kind="dim"))
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel_task)
        task_bar.addWidget(self.btn_cancel)

        root_layout.addLayout(task_bar)

        # ── 4. Main Workbench Splitter ────────────────────────────────────────
        workbench_splitter = QSplitter(Qt.Orientation.Horizontal)
        workbench_splitter.setStyleSheet("QSplitter::handle { background: rgba(0, 212, 255, 0.15); width: 2px; }")

        # ── Left Navigation Tabs (Files & Modified) ───────────────────────────
        left_tabs = QTabWidget()
        left_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {C.BORDER_A}; background: {C.PANEL}; border-radius: 4px; }}
            QTabBar::tab {{ color: {C.TEXT_DIM}; background: {C.BG}; border: 1px solid {C.BORDER_A}; padding: 6px 12px; margin-right: 2px; }}
            QTabBar::tab:selected {{ color: {C.PRI}; background: {C.PANEL}; border-bottom: 2px solid {C.PRI}; font-weight: bold; }}
        """)

        # Tab A: Workspace Explorer
        tab_explorer = QWidget()
        exp_lay = QVBoxLayout(tab_explorer)
        exp_lay.setContentsMargins(6, 6, 6, 6)
        exp_lay.setSpacing(6)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter files / symbols...")
        self.search_input.setFont(px_font(10))
        self.search_input.setStyleSheet(f"color: {C.WHITE}; background: {C.BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px 8px;")
        self.search_input.textChanged.connect(self._on_filter_files)
        exp_lay.addWidget(self.search_input)

        self.file_list = QListWidget()
        self.file_list.setFont(mono_font(10))
        self.file_list.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none; {scroll_css()}")
        self.file_list.itemClicked.connect(self._on_file_selected)
        exp_lay.addWidget(self.file_list)
        left_tabs.addTab(tab_explorer, "EXPLORER")

        # Tab B: Modified Files & Rollback
        tab_modified = QWidget()
        mod_lay = QVBoxLayout(tab_modified)
        mod_lay.setContentsMargins(6, 6, 6, 6)
        mod_lay.setSpacing(6)

        self.mod_table = QTableWidget(0, 3)
        self.mod_table.setHorizontalHeaderLabels(["FILE", "ACTION", "TIME"])
        self.mod_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.mod_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.mod_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.mod_table.setFont(mono_font(10))
        self.mod_table.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none; {scroll_css()}")
        self.mod_table.itemSelectionChanged.connect(self._on_mod_selected)
        mod_lay.addWidget(self.mod_table)

        btn_rollback = QPushButton("ROLLBACK SELECTED FILE")
        btn_rollback.setFont(px_font(10, QFont.Weight.DemiBold))
        btn_rollback.setStyleSheet(btn_css(kind="dim"))
        btn_rollback.clicked.connect(self._on_rollback_file)
        mod_lay.addWidget(btn_rollback)
        left_tabs.addTab(tab_modified, "CHANGES")

        workbench_splitter.addWidget(left_tabs)

        # ── Right Workspace Tabs (Code/Diff, Terminal, Verification) ──────────
        right_tabs = QTabWidget()
        right_tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {C.BORDER_A}; background: {C.PANEL}; border-radius: 4px; }}
            QTabBar::tab {{ color: {C.TEXT_DIM}; background: {C.BG}; border: 1px solid {C.BORDER_A}; padding: 6px 12px; margin-right: 2px; }}
            QTabBar::tab:selected {{ color: {C.PRI}; background: {C.PANEL}; border-bottom: 2px solid {C.PRI}; font-weight: bold; }}
        """)

        # Tab 1: Code & Diff Viewer
        tab_viewer = QWidget()
        v_lay = QVBoxLayout(tab_viewer)
        v_lay.setContentsMargins(6, 6, 6, 6)
        v_lay.setSpacing(6)

        self.lbl_viewer_title = QLabel("NO FILE SELECTED")
        self.lbl_viewer_title.setFont(px_font(10, QFont.Weight.Bold))
        self.lbl_viewer_title.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        v_lay.addWidget(self.lbl_viewer_title)

        self.code_viewer = QTextEdit()
        self.code_viewer.setReadOnly(True)
        self.code_viewer.setFont(mono_font(11))
        self.code_viewer.setStyleSheet(f"""
            QTextEdit {{
                color: {C.WHITE};
                background: #020617;
                border: 1px solid {C.BORDER_A};
                border-radius: 3px;
                padding: 8px;
            }}
            {scroll_css()}
        """)
        v_lay.addWidget(self.code_viewer)
        right_tabs.addTab(tab_viewer, "EDITOR & DIFFS")

        # Tab 2: Sandboxed Terminal
        tab_terminal = QWidget()
        t_lay = QVBoxLayout(tab_terminal)
        t_lay.setContentsMargins(6, 6, 6, 6)
        t_lay.setSpacing(6)

        self.term_log = QTextEdit()
        self.term_log.setReadOnly(True)
        self.term_log.setFont(mono_font(10))
        self.term_log.setStyleSheet(f"""
            QTextEdit {{
                color: {C.GREEN};
                background: #010409;
                border: 1px solid {C.BORDER_A};
                border-radius: 3px;
                padding: 8px;
            }}
            {scroll_css()}
        """)
        t_lay.addWidget(self.term_log)

        term_cmd_row = QHBoxLayout()
        term_cmd_row.setSpacing(6)
        self.term_input = QLineEdit()
        self.term_input.setPlaceholderText("Enter command (e.g. 'git status', 'pytest tests', 'dir')...")
        self.term_input.setFont(mono_font(10))
        self.term_input.setStyleSheet(f"color: {C.WHITE}; background: {C.PANEL}; border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 4px 8px;")
        self.term_input.returnPressed.connect(self._on_run_term_command)
        term_cmd_row.addWidget(self.term_input, stretch=1)

        btn_run_cmd = QPushButton("RUN")
        btn_run_cmd.setFont(px_font(10, QFont.Weight.Bold))
        btn_run_cmd.setStyleSheet(btn_css(kind="dim"))
        btn_run_cmd.clicked.connect(self._on_run_term_command)
        term_cmd_row.addWidget(btn_run_cmd)

        t_lay.addLayout(term_cmd_row)
        right_tabs.addTab(tab_terminal, "TERMINAL")

        # Tab 3: Verification & Execution Trace
        tab_verif = QWidget()
        verif_lay = QVBoxLayout(tab_verif)
        verif_lay.setContentsMargins(6, 6, 6, 6)
        verif_lay.setSpacing(6)

        self.verif_log = QTextEdit()
        self.verif_log.setReadOnly(True)
        self.verif_log.setFont(mono_font(10))
        self.verif_log.setStyleSheet(f"""
            QTextEdit {{
                color: {C.TEXT};
                background: #020617;
                border: 1px solid {C.BORDER_A};
                border-radius: 3px;
                padding: 8px;
            }}
            {scroll_css()}
        """)
        verif_lay.addWidget(self.verif_log)
        right_tabs.addTab(tab_verif, "VERIFICATION")

        workbench_splitter.addWidget(right_tabs)
        workbench_splitter.setStretchFactor(0, 3)
        workbench_splitter.setStretchFactor(1, 7)
        root_layout.addWidget(workbench_splitter, stretch=1)

        # ── 5. Telemetry & Statistics Footer ─────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(12)

        self.lbl_stat_files = QLabel("FILES: 0 (0 Python)")
        self.lbl_stat_files.setFont(mono_font(10))
        self.lbl_stat_files.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        footer.addWidget(self.lbl_stat_files)

        self.lbl_stat_size = QLabel("SIZE: 0.0 MB")
        self.lbl_stat_size.setFont(mono_font(10))
        self.lbl_stat_size.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        footer.addWidget(self.lbl_stat_size)

        self.lbl_stat_mod = QLabel("MODIFIED IN SESSION: 0")
        self.lbl_stat_mod.setFont(mono_font(10))
        self.lbl_stat_mod.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        footer.addWidget(self.lbl_stat_mod)

        footer.addStretch()

        self.lbl_active_agent = QLabel("AGENT: DEVELOPER (ADVANCED)")
        self.lbl_active_agent.setFont(mono_font(10))
        self.lbl_active_agent.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        footer.addWidget(self.lbl_active_agent)

        root_layout.addLayout(footer)

        # Connect internal signal for safe cross-thread UI updates
        self.task_state_signal.connect(self._handle_task_state_update)

        # Initial workspace population
        self.refresh_all()

    # ── Workspace Actions ────────────────────────────────────────────────────

    def refresh_all(self) -> None:
        """Refresh workspace file tree, statistics, and modified list."""
        self.lbl_ws_path.setText(self.workspace.root.name)
        self.lbl_ws_path.setToolTip(str(self.workspace.root))

        # Refresh Tree
        self._populate_file_tree()

        # Refresh Stats
        stats = self.workspace.get_stats()
        self.lbl_stat_files.setText(f"FILES: {stats['total_files']} ({stats['python_files']} Python)")
        self.lbl_stat_size.setText(f"SIZE: {stats['total_size_mb']} MB")
        self.lbl_stat_mod.setText(f"MODIFIED IN SESSION: {stats['modified_in_session']}")

        # Refresh Modified Files Table
        self._refresh_modified_table()

    def _populate_file_tree(self, filter_query: str = "") -> None:
        """Populate explorer list widget with workspace files."""
        self.file_list.clear()
        entries = self.workspace.get_tree(max_depth=4, max_entries=300)
        q = filter_query.strip().lower()

        for e in entries:
            name = e["name"]
            path = e["path"]
            if q and q not in path.lower() and q not in name.lower():
                continue

            icon = "📁 " if e["is_dir"] else "📄 "
            indent = "  " * e["depth"]
            item = QListWidgetItem(f"{indent}{icon}{name}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setData(Qt.ItemDataRole.UserRole + 1, e["is_dir"])
            self.file_list.addItem(item)

    def _on_filter_files(self, text: str) -> None:
        self._populate_file_tree(text)

    def _on_change_directory(self) -> None:
        """Prompt user to select a new authorized development directory."""
        folder = QFileDialog.getExistingDirectory(self, "Select Development Workspace Root", str(self.workspace.root))
        if folder:
            p = Path(folder).resolve()
            if self.workspace.set_root(p):
                self.terminal.set_workspace_root(p)
                self.refresh_all()
                self._append_terminal_log(f"WORKSPACE: Switched root to '{p}'")

    def _on_file_selected(self, item: QListWidgetItem) -> None:
        """Display selected file contents or diff in the viewer."""
        rel_path = item.data(Qt.ItemDataRole.UserRole)
        is_dir = item.data(Qt.ItemDataRole.UserRole + 1)
        if is_dir:
            return

        try:
            content, digest, size = self.workspace.read_file(rel_path)
            self.lbl_viewer_title.setText(f"FILE: {rel_path} ({size} bytes, sha256={digest[:8]})")
            self.code_viewer.setPlainText(content)
        except Exception as e:
            self.lbl_viewer_title.setText(f"ERROR: {rel_path}")
            self.code_viewer.setPlainText(f"Failed to load file: {e}")

    def _refresh_modified_table(self) -> None:
        """Populate modified files list."""
        mods = self.workspace.list_modified_files()
        self.mod_table.setRowCount(len(mods))
        for row, m in enumerate(mods):
            f_item = QTableWidgetItem(m["relative_path"])
            a_item = QTableWidgetItem(m["action"].upper())
            t_item = QTableWidgetItem(m["time_str"])

            a_item.setForeground(QColor(C.GREEN if m["action"] == "create" else C.ACC))
            self.mod_table.setItem(row, 0, f_item)
            self.mod_table.setItem(row, 1, a_item)
            self.mod_table.setItem(row, 2, t_item)

    def _on_mod_selected(self) -> None:
        """Show diff when modified file is clicked."""
        row = self.mod_table.currentRow()
        if row >= 0:
            item = self.mod_table.item(row, 0)
            if item:
                rel_path = item.text()
                diff_str = self.workspace.get_diff(rel_path)
                self.lbl_viewer_title.setText(f"DIFF: {rel_path}")
                self.code_viewer.setPlainText(diff_str or "No diff available.")

    def _on_rollback_file(self) -> None:
        """Rollback selected file in modification list."""
        row = self.mod_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Rollback", "Please select a modified file to roll back.")
            return

        rel_path = self.mod_table.item(row, 0).text()
        ok, msg = self.workspace.rollback(rel_path)
        if ok:
            QMessageBox.information(self, "Rollback Successful", msg)
            self.refresh_all()
            self._append_terminal_log(f"ROLLBACK: {msg}")
        else:
            QMessageBox.warning(self, "Rollback Failed", msg)

    # ── Autonomous Task Execution ────────────────────────────────────────────

    def _on_execute_task(self) -> None:
        """Launch the Developer Agent to autonomously execute the development task."""
        goal = self.task_input.text().strip()
        if not goal:
            return

        if self._is_task_running:
            QMessageBox.warning(self, "Task In Progress", "A development task is already executing.")
            return

        self._is_task_running = True
        self.btn_execute.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.phase_tracker.reset()

        self._append_terminal_log(f"\n>>> TASK SUBMITTED: {goal}")
        self._append_verif_log(f"=== INITIALIZING DEVELOPER TASK: {goal} ===")

        # Run in background worker thread to keep UI completely responsive
        def _worker():
            try:
                contract = AgentInputContract(
                    task_id=f"dev_{int(time.time())}",
                    subtask_id=f"sub_{int(time.time())}",
                    goal=goal,
                    instructions="Execute the 8-phase developer lifecycle autonomously.",
                    context_data={"target_files": [], "file_edits": [], "commands": []},
                )
                tool_registry = getattr(self.bridge.orchestrator, "tool_registry", None)
                if not tool_registry:
                    from core.orchestrator.tools import ToolRegistry
                    tool_registry = ToolRegistry()

                output = self.agent.execute(contract, tool_registry)
                self.task_state_signal.emit({
                    "status": output.status,
                    "summary": output.summary,
                    "evidence": output.evidence,
                    "error": output.error,
                })
            except Exception as e:
                self.task_state_signal.emit({
                    "status": "failed",
                    "summary": f"Execution error: {e}",
                    "evidence": {},
                    "error": str(e),
                })

        self._active_task_thread = threading.Thread(target=_worker, daemon=True)
        self._active_task_thread.start()

    def _on_cancel_task(self) -> None:
        """Cancel running development task."""
        if self._is_task_running:
            self._is_task_running = False
            self.btn_execute.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.phase_tracker.set_phase(LifecyclePhase.FAILED, [])
            self._append_terminal_log(">>> TASK CANCELLED BY USER.")
            self._append_verif_log("TASK CANCELLED.")

    def _handle_task_state_update(self, data: dict[str, Any]) -> None:
        """Handle completed task result emitted from worker thread."""
        self._is_task_running = False
        self.btn_execute.setEnabled(True)
        self.btn_cancel.setEnabled(False)

        status = data.get("status")
        summary = data.get("summary", "")
        error = data.get("error")

        if status == "success":
            self.phase_tracker.set_phase(LifecyclePhase.COMPLETED, LIFECYCLE_SEQUENCE)
            self._append_terminal_log(f">>> TASK COMPLETED:\n{summary}")
            self._append_verif_log(f"VERIFIED DELIVERABLES:\n{summary}")
        elif status == "waiting_approval":
            self.phase_tracker.set_phase(LifecyclePhase.APPROVE, [LifecyclePhase.UNDERSTAND, LifecyclePhase.INSPECT, LifecyclePhase.PLAN])
            self._append_terminal_log(f">>> WAITING FOR APPROVAL: {error}")
            self._append_verif_log(f"APPROVAL REQUIRED: {error}")
            QMessageBox.warning(self, "Authorization Required", f"Operation paused: {error}")
        else:
            self.phase_tracker.set_phase(LifecyclePhase.FAILED, [])
            self._append_terminal_log(f">>> TASK FAILED: {error or summary}")
            self._append_verif_log(f"TASK FAILED: {error or summary}")

        self.refresh_all()

    # ── Terminal & Testing Actions ───────────────────────────────────────────

    def _on_run_term_command(self) -> None:
        """Execute command from interactive terminal prompt."""
        cmd = self.term_input.text().strip()
        if not cmd:
            return
        self.term_input.clear()
        self._append_terminal_log(f"$ {cmd}")

        rec = self.terminal.execute(cmd, timeout=30.0)
        if rec.output:
            self._append_terminal_log(rec.output)
        if rec.error:
            self._append_terminal_log(f"[STDERR]: {rec.error}")
        self._append_terminal_log(f"[Process finished with exit code {rec.exit_code} in {rec.duration_ms:.1f}ms]\n")
        self.refresh_all()

    def _on_run_tests(self) -> None:
        """Run pytest from quick action button."""
        self._append_terminal_log("$ pytest tests/test_onboarding_and_permissions.py")
        rec = self.terminal.run_tests("tests/test_onboarding_and_permissions.py")
        if rec.output:
            self._append_terminal_log(rec.output)
        if rec.error:
            self._append_terminal_log(f"[ERR]: {rec.error}")
        self._append_terminal_log(f"[Tests finished: exit code {rec.exit_code} in {rec.duration_ms:.1f}ms]\n")

    def _on_git_status(self) -> None:
        """Run git status and show in terminal."""
        rec = self.terminal.run_git(["status"])
        self._append_terminal_log("$ git status\n" + (rec.output or rec.error))

    def _append_terminal_log(self, text: str) -> None:
        self.term_log.append(text)

    def _append_verif_log(self, text: str) -> None:
        self.verif_log.append(text)
