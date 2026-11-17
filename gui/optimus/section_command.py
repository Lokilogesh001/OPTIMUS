"""gui/optimus/section_command.py — Unified Interaction & Command Center for OPTIMUS.

Provides:
- Sovereign conversation panel supporting multi-turn text, voice, and image interactions.
- Central directive submission with goal parameters.
- Voice intelligence controls (Mic toggle, Stop Speaking, Cancel Task).
- Multimodal attachment handling (Image attachments with privacy enforcement).
- Concise task execution progress and deliverable viewer.
- Historical task execution ledger.
"""

from __future__ import annotations

import os
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.interaction.types import ImageAttachment
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel


class SectionCommandCenter(QWidget):
    """Full-featured Unified Interaction & Command Center workspace."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        self._active_attachment: ImageAttachment | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header bar
        hdr = QHBoxLayout()
        title = QLabel("COMMAND CENTER  ·  UNIFIED INTERACTION RUNTIME")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)

        # Status pills
        self.lbl_voice_status = QLabel("VOICE: IDLE")
        self.lbl_voice_status.setFont(mono_font(9))
        self.lbl_voice_status.setStyleSheet(f"color: {C.TEXT_DIM}; border: 1px solid {C.BORDER}; padding: 3px 6px; border-radius: 3px;")
        hdr.addWidget(self.lbl_voice_status)

        self.lbl_privacy_status = QLabel("PRIVACY: LOCAL_FIRST")
        self.lbl_privacy_status.setFont(mono_font(9))
        self.lbl_privacy_status.setStyleSheet(f"color: {C.GREEN}; border: 1px solid {C.BORDER}; padding: 3px 6px; border-radius: 3px;")
        hdr.addWidget(self.lbl_privacy_status)

        hdr.addStretch()

        btn_refresh = QPushButton("REFRESH TASKS")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_tasks)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        # Splitter: Left is Interaction & Output, Right is Task History
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Conversation & Execution Output
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)
        left_lay.setSpacing(8)

        # Panel 1: Conversation & Interaction Panel
        p_interact = HoloPanel("SOVEREIGN INTERACTION & DIRECTIVES", "CONV")

        # Chat history display
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setFont(px_font(10))
        self.chat_history.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 8px;
            }}
        """ + scroll_css())
        p_interact.body.addWidget(self.chat_history, stretch=1)

        # Attachment preview strip
        self.att_row = QWidget()
        att_lay = QHBoxLayout(self.att_row)
        att_lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_att = QLabel("")
        self.lbl_att.setFont(mono_font(9))
        self.lbl_att.setStyleSheet(f"color: {C.PRI};")
        btn_clear_att = QPushButton("✕ REMOVE")
        btn_clear_att.setFont(px_font(8))
        btn_clear_att.setStyleSheet(btn_css("dim"))
        btn_clear_att.clicked.connect(self._clear_attachment)
        att_lay.addWidget(self.lbl_att)
        att_lay.addWidget(btn_clear_att)
        att_lay.addStretch()
        self.att_row.setVisible(False)
        p_interact.body.addWidget(self.att_row)

        # Input Row
        in_row = QHBoxLayout()
        self.btn_mic = QPushButton("🎤")
        self.btn_mic.setToolTip("Toggle microphone listening")
        self.btn_mic.setFont(px_font(11))
        self.btn_mic.setStyleSheet(btn_css("dim"))
        self.btn_mic.setFixedWidth(36)
        self.btn_mic.clicked.connect(self._toggle_mic)
        in_row.addWidget(self.btn_mic)

        self.btn_attach = QPushButton("📎")
        self.btn_attach.setToolTip("Attach image for vision analysis")
        self.btn_attach.setFont(px_font(11))
        self.btn_attach.setStyleSheet(btn_css("dim"))
        self.btn_attach.setFixedWidth(36)
        self.btn_attach.clicked.connect(self._pick_attachment)
        in_row.addWidget(self.btn_attach)

        self.cmd_field = QLineEdit()
        self.cmd_field.setFont(px_font(12))
        self.cmd_field.setPlaceholderText("Direct OPTIMUS via text or prompt (e.g. 'Analyze data' or 'Explain system status')...")
        self.cmd_field.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 3px;
                padding: 8px 10px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
                background: rgba(0, 212, 255, 0.08);
            }}
        """)
        self.cmd_field.returnPressed.connect(self._on_dispatch)
        in_row.addWidget(self.cmd_field, stretch=1)

        btn_run = QPushButton("DISPATCH")
        btn_run.setFont(px_font(10, QFont.Weight.Bold))
        btn_run.setStyleSheet(btn_css("primary"))
        btn_run.clicked.connect(self._on_dispatch)
        in_row.addWidget(btn_run)
        p_interact.body.addLayout(in_row)

        # Control strip: Stop speaking, Cancel task
        ctrl_row = QHBoxLayout()
        self.btn_stop_speaking = QPushButton("■ STOP SPEAKING")
        self.btn_stop_speaking.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_stop_speaking.setStyleSheet(btn_css("dim"))
        self.btn_stop_speaking.clicked.connect(self._on_stop_speaking)
        ctrl_row.addWidget(self.btn_stop_speaking)

        self.btn_cancel_task = QPushButton("✕ CANCEL TASK")
        self.btn_cancel_task.setFont(px_font(9, QFont.Weight.Bold))
        self.btn_cancel_task.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 51, 85, 0.15);
                color: {C.RED};
                border: 1px solid {C.RED};
                padding: 4px 10px;
                border-radius: 3px;
            }}
            QPushButton:hover {{
                background: rgba(255, 51, 85, 0.3);
            }}
        """)
        self.btn_cancel_task.clicked.connect(self._on_cancel_task)
        ctrl_row.addWidget(self.btn_cancel_task)
        ctrl_row.addStretch()

        p_interact.body.addLayout(ctrl_row)
        left_lay.addWidget(p_interact, stretch=3)

        # Panel 2: Execution Details & Deliverables Console
        p_out = HoloPanel("EXECUTION OUTPUT & DELIVERABLES", "STDOUT")
        self.out_display = QTextEdit()
        self.out_display.setReadOnly(True)
        self.out_display.setFont(mono_font(10))
        self.out_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """ + scroll_css())
        p_out.body.addWidget(self.out_display)
        left_lay.addWidget(p_out, stretch=2)

        split.addWidget(left_w)

        # Right Column: Historical Tasks Ledger
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        p_hist = HoloPanel("TASK EXECUTION LEDGER", "HISTORY")
        self.table_tasks = QTableWidget()
        self.table_tasks.setColumnCount(4)
        self.table_tasks.setHorizontalHeaderLabels(["TASK ID", "STATUS", "DURATION", "GOAL"])
        self.table_tasks.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_tasks.verticalHeader().setVisible(False)
        self.table_tasks.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_tasks.setStyleSheet(f"""
            QTableWidget {{
                background: {C.PANEL2};
                color: {C.WHITE};
                gridline-color: {C.BORDER};
                border: 1px solid {C.BORDER};
                selection-background-color: {C.PRI_GHO};
            }}
            QHeaderView::section {{
                background: {C.PANEL};
                color: {C.PRI};
                border: 1px solid {C.BORDER};
                padding: 4px;
                font-weight: bold;
            }}
        """ + scroll_css())
        self.table_tasks.itemClicked.connect(self._on_task_selected)
        p_hist.body.addWidget(self.table_tasks)
        right_lay.addWidget(p_hist)

        split.addWidget(right_w)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 4)
        layout.addWidget(split)

        # Wire Bridge Signals
        self.bridge.task_completed.connect(self._on_task_completed)
        self.bridge.task_failed.connect(self._on_task_failed)
        self.bridge.user_message_received.connect(self._on_user_message_received)
        self.bridge.agent_response_completed.connect(self._on_agent_response_completed)
        self.bridge.speech_state_changed.connect(self._on_speech_state_changed)
        self.bridge.speech_interrupted.connect(self._on_speech_interrupted)

        # Property aliases for testing & backward compatibility
        self.cmd_input = self.cmd_field
        self.output_console = self.out_display
        self.table_history = self.table_tasks
        self._dispatch_command = self._on_dispatch
        self._refresh_task_history = self.refresh_tasks

        self._append_system_msg("OPTIMUS Autonomous Interaction & Voice Intelligence online. Ready for directives.")
        self.refresh_tasks()

    def _toggle_mic(self) -> None:
        """Toggle microphone active listening state."""
        vb = self.bridge.interaction_runtime.voice_bridge
        new_state = not vb.is_listening
        self.bridge.set_voice_listening(new_state)

    def _pick_attachment(self) -> None:
        """Pick image file for vision routing."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image Attachment",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if file_path and os.path.exists(file_path):
            self._active_attachment = ImageAttachment(
                file_path=file_path,
                mime_type="image/png" if file_path.endswith(".png") else "image/jpeg",
                description=os.path.basename(file_path),
                is_local_only=True,
            )
            self.lbl_att.setText(f"📎 {os.path.basename(file_path)}")
            self.att_row.setVisible(True)

    def _clear_attachment(self) -> None:
        self._active_attachment = None
        self.lbl_att.setText("")
        self.att_row.setVisible(False)

    def _on_dispatch(self) -> None:
        text = self.cmd_field.text().strip()
        if not text:
            return
        self.cmd_field.clear()

        attachments = [self._active_attachment] if self._active_attachment else []
        self._clear_attachment()

        modality = "multimodal" if attachments else "text"
        self._append_user_msg(text, has_image=bool(attachments))
        self.out_display.append(f"<font color='{C.PRI}'><b>[DISPATCH]</b></font> {text}\n")

        # Submit via non-blocking bridge
        self.bridge.submit_interaction(
            text=text,
            modality=modality,
            attachments=attachments,
            callback=self._on_reply_received,
        )

    def _on_reply_received(self, reply: str) -> None:
        if reply:
            self._append_assistant_msg(reply)

    def _on_stop_speaking(self) -> None:
        self.bridge.stop_speaking()
        self._append_system_msg("Speech playback halted by user.")

    def _on_cancel_task(self) -> None:
        self.bridge.cancel_active_task()
        self._append_system_msg("Directive cancellation requested.")

    def _append_user_msg(self, text: str, has_image: bool = False) -> None:
        att_tag = " <font color='#00d4ff'>[📎 Image Attached]</font>" if has_image else ""
        self.chat_history.append(
            f"<p style='margin:4px 0;'><font color='{C.PRI}'><b>USER:</b></font> {text}{att_tag}</p>"
        )

    def _append_assistant_msg(self, text: str) -> None:
        self.chat_history.append(
            f"<p style='margin:4px 0;'><font color='{C.GREEN}'><b>OPTIMUS:</b></font> {text}</p>"
        )

    def _append_system_msg(self, text: str) -> None:
        self.chat_history.append(
            f"<p style='margin:4px 0;'><font color='{C.TEXT_DIM}'><i>[SYSTEM] {text}</i></font></p>"
        )

    def _on_user_message_received(self, data: dict[str, Any]) -> None:
        pass  # Handled directly on dispatch

    def _on_agent_response_completed(self, data: dict[str, Any]) -> None:
        # Update timeline / out_display
        txt = data.get("text", "")
        if txt:
            self.out_display.append(f"<font color='{C.GREEN}'><b>[RESPONSE]</b></font> {txt}\n")

    def _on_speech_state_changed(self, data: dict[str, Any]) -> None:
        st = data.get("state", "IDLE")
        is_list = data.get("is_listening", False)
        is_spk = data.get("is_speaking", False)

        if is_spk:
            self.lbl_voice_status.setText("VOICE: SPEAKING")
            self.lbl_voice_status.setStyleSheet(f"color: {C.GREEN}; border: 1px solid {C.GREEN}; padding: 3px 6px; border-radius: 3px;")
        elif is_list:
            self.lbl_voice_status.setText("VOICE: LISTENING")
            self.lbl_voice_status.setStyleSheet(f"color: {C.PRI}; border: 1px solid {C.PRI}; padding: 3px 6px; border-radius: 3px;")
            self.btn_mic.setStyleSheet(btn_css("primary"))
        else:
            self.lbl_voice_status.setText(f"VOICE: {st.upper()}")
            self.lbl_voice_status.setStyleSheet(f"color: {C.TEXT_DIM}; border: 1px solid {C.BORDER}; padding: 3px 6px; border-radius: 3px;")
            self.btn_mic.setStyleSheet(btn_css("dim"))

    def _on_speech_interrupted(self, data: dict[str, Any]) -> None:
        reason = data.get("reason", "interrupted")
        self._append_system_msg(f"Voice interrupted: {reason}")

    def _on_task_completed(self, data: dict[str, Any]) -> None:
        goal = data.get("goal", "")
        res = data.get("result", "")
        dur = data.get("duration_seconds", 0.0)
        self.out_display.append(
            f"<font color='{C.GREEN}'><b>[TASK COMPLETED]</b> ({dur:.2f}s)</font>\n<b>Goal:</b> {goal}\n<b>Result:</b>\n{res}\n" + "─"*50 + "\n"
        )
        self._append_system_msg(f"Task completed: '{goal}' ({dur:.2f}s)")
        self.refresh_tasks()

    def _on_task_failed(self, data: dict[str, Any]) -> None:
        goal = data.get("goal", "")
        err = data.get("error", "")
        self.out_display.append(
            f"<font color='{C.RED}'><b>[TASK FAILED]</b></font>\n<b>Goal:</b> {goal}\n<b>Error:</b> {err}\n" + "─"*50 + "\n"
        )
        self._append_system_msg(f"Task failed: '{goal}' - {err}")
        self.refresh_tasks()

    def refresh_tasks(self) -> None:
        store = self.bridge.orchestrator.task_store
        records = store.list_tasks(limit=40)
        self.table_tasks.setRowCount(len(records))
        for row, rec in enumerate(records):
            id_item = QTableWidgetItem(rec.task_id[:16])
            id_item.setFont(mono_font(9))

            st_item = QTableWidgetItem(rec.status.value)
            st_item.setFont(px_font(9, QFont.Weight.Bold))
            if rec.status.value == "COMPLETED":
                st_item.setForeground(QColor(C.GREEN))
            elif rec.status.value == "FAILED":
                st_item.setForeground(QColor(C.RED))
            else:
                st_item.setForeground(QColor(C.PRI))

            dur_str = f"{rec.duration_seconds:.1f}s" if rec.duration_seconds else "--"
            dur_item = QTableWidgetItem(dur_str)
            dur_item.setFont(mono_font(9))

            goal_item = QTableWidgetItem(rec.goal)
            goal_item.setFont(px_font(9))

            self.table_tasks.setItem(row, 0, id_item)
            self.table_tasks.setItem(row, 1, st_item)
            self.table_tasks.setItem(row, 2, dur_item)
            self.table_tasks.setItem(row, 3, goal_item)

    def _on_task_selected(self, item: QTableWidgetItem) -> None:
        row = item.row()
        task_id = self.table_tasks.item(row, 0).text()
        store = self.bridge.orchestrator.task_store
        records = [r for r in store.list_tasks() if r.task_id.startswith(task_id)]
        if records:
            rec = records[0]
            details = (
                f"TASK ID: {rec.task_id}\n"
                f"GOAL: {rec.goal}\n"
                f"STATUS: {rec.status.value}\n"
                f"DURATION: {rec.duration_seconds}s\n"
                f"SUBTASKS ({len(rec.subtasks)}):\n"
            )
            for st in rec.subtasks:
                details += f"  - [{st.assigned_agent.upper()}] {st.title} -> {st.status.value}\n"
            if rec.result:
                details += f"\nRESULT:\n{rec.result}\n"
            if rec.error:
                details += f"\nERROR:\n{rec.error}\n"
            self.out_display.setPlainText(details)
