"""gui/optimus/section_integrations.py — Unified Integrations & System Telemetry Section.

Provides:
- Real-time status for Google Workspace MCP (Gmail and Calendar).
- OAuth credential configuration and permission scope visibility.
- Real-time host telemetry via WindowsSystemMonitorAdapter (psutil, pynvml, LibreHardwareMonitor).
- Dedicated GPU sensor analytics (VRAM, compute, thermals, power, clocks).
- PolicyEngine security boundary and ApprovalToken challenge indicators.
- Audit ledger for recent integration operations.
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.personal_intelligence.oauth import (
    get_google_client_id,
    has_valid_google_credentials,
)
from core.system_monitor.adapter import WindowsSystemMonitorAdapter
from core.workspace_mcp.adapter import WorkspaceMCPAdapter
from gui.optimus.section_personal import GoogleCredentialsDialog
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel, MetricBar, RingGauge

logger = logging.getLogger("optimus.gui.integrations")


class SectionIntegrations(QWidget):
    """Unified command center for Google Workspace MCP and Native System Monitoring."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.system_adapter = WindowsSystemMonitorAdapter.instance()
        self.workspace_adapter = WorkspaceMCPAdapter.instance()
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── 1. Top Section Header ────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("INTEGRATIONS  ·  WORKSPACE MCP & SYSTEM MONITOR")
        title.setFont(px_font(12, QFont.Weight.Bold, spacing=1.5))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addSpacing(15)

        self.lbl_workspace_badge = QLabel("[WORKSPACE: INITIALIZING]")
        self.lbl_workspace_badge.setFont(mono_font(8))
        self.lbl_workspace_badge.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 212, 255, 0.1); border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_workspace_badge)

        self.lbl_sysmon_badge = QLabel("[MONITOR: SAMPLING 2.0s]")
        self.lbl_sysmon_badge.setFont(mono_font(8))
        self.lbl_sysmon_badge.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 128, 0.1); border: 1px solid {C.GREEN}; border-radius: 3px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_sysmon_badge)

        self.lbl_gpu_badge = QLabel("[GPU: PROBING]")
        self.lbl_gpu_badge.setFont(mono_font(8))
        self.lbl_gpu_badge.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 212, 255, 0.1); border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_gpu_badge)

        hdr.addStretch()

        btn_refresh = QPushButton("REFRESH ALL")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_all)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        # ── 2. Main Splitter View ────────────────────────────────────────────
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Workspace & Permissions
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 6, 0)
        left_lay.setSpacing(10)

        # Panel: Google Workspace MCP
        p_workspace = HoloPanel("GOOGLE WORKSPACE MCP", "GMAIL & CALENDAR")
        self.lbl_ws_status = QLabel("Status: Checking...")
        self.lbl_ws_status.setFont(mono_font(9))
        self.lbl_ws_status.setStyleSheet(f"color: {C.WHITE};")
        p_workspace.body.addWidget(self.lbl_ws_status)

        self.lbl_ws_scopes = QLabel("Scopes: Reading least-privilege configuration...")
        self.lbl_ws_scopes.setFont(mono_font(8))
        self.lbl_ws_scopes.setWordWrap(True)
        self.lbl_ws_scopes.setStyleSheet(f"color: {C.TEXT_DIM};")
        p_workspace.body.addWidget(self.lbl_ws_scopes)

        btn_row = QHBoxLayout()
        btn_oauth = QPushButton("CONFIGURE OAUTH CREDENTIALS")
        btn_oauth.setFont(px_font(9, QFont.Weight.Bold))
        btn_oauth.setStyleSheet(btn_css("primary"))
        btn_oauth.clicked.connect(self._open_oauth_dialog)
        btn_row.addWidget(btn_oauth)

        btn_test_ws = QPushButton("TEST MCP CONNECTION")
        btn_test_ws.setFont(px_font(9, QFont.Weight.Bold))
        btn_test_ws.setStyleSheet(btn_css("dim"))
        btn_test_ws.clicked.connect(self._test_mcp_connection)
        btn_row.addWidget(btn_test_ws)
        p_workspace.body.addLayout(btn_row)

        left_lay.addWidget(p_workspace)

        # Panel: Security & Permissions Gate
        p_sec = HoloPanel("SECURITY BOUNDARIES", "POLICY ENGINE & CBAC")
        self.lbl_sec_policy = QLabel("• PolicyEngine: ACTIVE (Single-pass deterministic gate)")
        self.lbl_sec_policy.setFont(mono_font(8))
        self.lbl_sec_policy.setStyleSheet(f"color: {C.GREEN};")
        p_sec.body.addWidget(self.lbl_sec_policy)

        self.lbl_sec_mode = QLabel("• Default Access Mode: READ_ONLY (Least Privilege)")
        self.lbl_sec_mode.setFont(mono_font(8))
        self.lbl_sec_mode.setStyleSheet(f"color: {C.WHITE};")
        p_sec.body.addWidget(self.lbl_sec_mode)

        self.lbl_sec_mutation = QLabel("• Mutating Operations: GATED by cryptographic ApprovalTokens")
        self.lbl_sec_mutation.setFont(mono_font(8))
        self.lbl_sec_mutation.setStyleSheet(f"color: {C.ACC};")
        p_sec.body.addWidget(self.lbl_sec_mutation)

        self.lbl_sec_quarantine = QLabel("• Prompt Injection Defense: ACTIVE (core.outcome.untrusted defanging)")
        self.lbl_sec_quarantine.setFont(mono_font(8))
        self.lbl_sec_quarantine.setStyleSheet(f"color: {C.PRI};")
        p_sec.body.addWidget(self.lbl_sec_quarantine)

        left_lay.addWidget(p_sec)
        left_lay.addStretch()
        split.addWidget(left_w)

        # Right Column: System Monitor & Hardware Sensors
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(6, 0, 0, 0)
        right_lay.setSpacing(10)

        # Panel: Telemetry Gauges
        p_sys = HoloPanel("HOST TELEMETRY", "PSUTIL VITALS")
        g_row = QHBoxLayout()
        self.gauge_cpu = RingGauge("CPU")
        self.gauge_ram = RingGauge("RAM")
        self.gauge_gpu = RingGauge("GPU")
        g_row.addWidget(self.gauge_cpu)
        g_row.addWidget(self.gauge_ram)
        g_row.addWidget(self.gauge_gpu)
        p_sys.body.addLayout(g_row)

        self.lbl_ram_detail = QLabel("RAM: Loading...")
        self.lbl_ram_detail.setFont(mono_font(8))
        self.lbl_ram_detail.setStyleSheet(f"color: {C.TEXT_MED};")
        p_sys.body.addWidget(self.lbl_ram_detail)

        self.lbl_storage_detail = QLabel("Storage: Loading...")
        self.lbl_storage_detail.setFont(mono_font(8))
        self.lbl_storage_detail.setStyleSheet(f"color: {C.TEXT_MED};")
        p_sys.body.addWidget(self.lbl_storage_detail)

        right_lay.addWidget(p_sys)

        # Panel: Dedicated GPU Analytics
        p_gpu = HoloPanel("GPU SENSORS", "NVIDIA NVML")
        self.lbl_gpu_name = QLabel("Device: Probing...")
        self.lbl_gpu_name.setFont(mono_font(9))
        self.lbl_gpu_name.setStyleSheet(f"color: {C.WHITE};")
        p_gpu.body.addWidget(self.lbl_gpu_name)

        self.lbl_gpu_vram = QLabel("VRAM: Loading...")
        self.lbl_gpu_vram.setFont(mono_font(8))
        self.lbl_gpu_vram.setStyleSheet(f"color: {C.TEXT_MED};")
        p_gpu.body.addWidget(self.lbl_gpu_vram)

        self.lbl_gpu_extra = QLabel("Thermals: Loading...")
        self.lbl_gpu_extra.setFont(mono_font(8))
        self.lbl_gpu_extra.setStyleSheet(f"color: {C.TEXT_DIM};")
        p_gpu.body.addWidget(self.lbl_gpu_extra)

        right_lay.addWidget(p_gpu)
        right_lay.addStretch()
        split.addWidget(right_w)

        layout.addWidget(split, stretch=1)

        # ── 3. Bottom Integration Operations Ledger ──────────────────────────
        p_ledger = HoloPanel("OPERATIONS LEDGER", "AUDIT & DIAGNOSTICS")
        p_ledger.setFixedHeight(180)
        self.tbl_ledger = QTableWidget(0, 5)
        self.tbl_ledger.setHorizontalHeaderLabels(["TIME", "SUBSYSTEM", "OPERATION", "STATUS", "DETAILS"])
        self.tbl_ledger.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tbl_ledger.horizontalHeader().setStretchLastSection(True)
        self.tbl_ledger.verticalHeader().setVisible(False)
        self.tbl_ledger.setFont(mono_font(8))
        self.tbl_ledger.setStyleSheet(f"""
            QTableWidget {{
                background: {C.BG};
                color: {C.WHITE};
                gridline-color: {C.BORDER};
                border: 1px solid {C.BORDER};
            }}
            QHeaderView::section {{
                background: {C.PANEL};
                color: {C.PRI};
                padding: 4px;
                border: 1px solid {C.BORDER};
            }}
        """ + scroll_css())
        p_ledger.body.addWidget(self.tbl_ledger)
        layout.addWidget(p_ledger)

        # ── 4. Periodic UI Update Timer ──────────────────────────────────────
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(2000)
        self.ui_timer.timeout.connect(self.update_telemetry_ui)
        if self.isVisible():
            self.ui_timer.start()

        # Initial UI populate
        self.update_telemetry_ui()
        self.refresh_workspace_ui()

    # ── Lifecycle Control ────────────────────────────────────────────────────

    def pause_polling(self) -> None:
        """Pause 2.0s refresh timer when tab is hidden."""
        if hasattr(self, "ui_timer") and self.ui_timer.isActive():
            self.ui_timer.stop()

    def resume_polling(self) -> None:
        """Resume 2.0s refresh timer when tab becomes visible."""
        if hasattr(self, "ui_timer") and not self.ui_timer.isActive():
            self.ui_timer.start(2000)
        self.update_telemetry_ui()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.resume_polling()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.pause_polling()

    # ── UI Refresh Logic ─────────────────────────────────────────────────────

    def refresh_all(self) -> None:
        """Full manual refresh."""
        self.update_telemetry_ui()
        self.refresh_workspace_ui()
        self._record_ledger("SYSTEM", "Manual UI Refresh", "SUCCESS", "All integration widgets updated")

    def update_telemetry_ui(self) -> None:
        """Update telemetry indicators from non-blocking snapshot."""
        snap = self.system_adapter.get_snapshot()

        # Update Gauges
        self.gauge_cpu.set_value(int(snap.cpu.total_percent))
        self.gauge_ram.set_value(int(snap.memory.percent_used))
        self.gauge_gpu.set_value(int(snap.gpu.gpu_percent) if snap.gpu.has_gpu else 0)

        # Memory detail
        self.lbl_ram_detail.setText(
            f"Physical RAM: {snap.memory.used_gb} GB used / {snap.memory.total_gb} GB total ({snap.memory.percent_used}%)\n"
            f"Available Free: {snap.memory.available_gb} GB | Swap: {snap.memory.swap_used_mb} MB ({snap.memory.swap_percent}%)"
        )

        # Storage detail
        if snap.partitions:
            p_strs = [f"{p.mountpoint} ({p.percent_used}% load, {p.free_gb} GB free)" for p in snap.partitions[:3]]
            self.lbl_storage_detail.setText("Drives: " + " | ".join(p_strs))

        # GPU detail
        gpu = snap.gpu
        if gpu.has_gpu:
            self.lbl_gpu_badge.setText(f"[GPU: {gpu.model.split()[0]} {gpu.gpu_percent:.0f}%]")
            self.lbl_gpu_badge.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 128, 0.1); border: 1px solid {C.GREEN}; border-radius: 3px; padding: 2px 8px;")
            self.lbl_gpu_name.setText(f"Device: {gpu.model} (Driver: {gpu.driver_version})")
            self.lbl_gpu_vram.setText(
                f"VRAM: {gpu.used_vram_mb} MB used / {gpu.total_vram_mb} MB total ({gpu.vram_percent}%)\n"
                f"Memory Controller: {gpu.memory_percent:.1f}% load"
            )
            extra_parts = []
            if gpu.temperature_c is not None:
                extra_parts.append(f"Temp: {gpu.temperature_c:.1f}°C")
            if gpu.power_watts is not None:
                extra_parts.append(f"Power: {gpu.power_watts:.1f}W")
            if gpu.fan_speed_percent is not None:
                extra_parts.append(f"Fan: {gpu.fan_speed_percent:.1f}%")
            lhm_str = "LibreHW: Active" if snap.libre_hardware_available else "LibreHW: Standalone offline"
            extra_parts.append(lhm_str)
            self.lbl_gpu_extra.setText(" | ".join(extra_parts))
        else:
            self.lbl_gpu_badge.setText("[GPU: CPU ONLY]")
            self.lbl_gpu_badge.setStyleSheet(f"color: {C.TEXT_DIM}; background: rgba(255, 255, 255, 0.05); border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px;")
            self.lbl_gpu_name.setText("Device: CPU Only (Discrete NVIDIA GPU offline)")
            self.lbl_gpu_vram.setText("VRAM: N/A (Standard Host RAM shared)")
            self.lbl_gpu_extra.setText("Hardware acceleration: Standard CPU / DirectML")

    def refresh_workspace_ui(self) -> None:
        """Update Workspace MCP status card."""
        has_creds = has_valid_google_credentials()
        client_id = get_google_client_id()

        try:
            status = self.workspace_adapter.get_gmail_status()
            healthy = status.get("healthy", False)
            account = status.get("account", "user@optimus.local")
            scopes = status.get("scopes", [])

            if healthy:
                self.lbl_workspace_badge.setText("[WORKSPACE: CONNECTED]")
                self.lbl_workspace_badge.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 128, 0.1); border: 1px solid {C.GREEN}; border-radius: 3px; padding: 2px 8px;")
                self.lbl_ws_status.setText(f"Status: ACTIVE & AUTHENTICATED\nAccount: {account}\nLast Synchronized: {status.get('last_synced_at', 'Just now')}")
            else:
                self.lbl_workspace_badge.setText("[WORKSPACE: NOT CONNECTED]")
                self.lbl_workspace_badge.setStyleSheet(f"color: {C.ACC}; background: rgba(245, 158, 11, 0.1); border: 1px solid {C.ACC}; border-radius: 3px; padding: 2px 8px;")
                self.lbl_ws_status.setText(f"Status: CONFIGURED (Authentication Pending)\nClient ID: {client_id[:20]}..." if client_id else "Status: NO OAUTH CREDENTIALS CONFIGURED")

            scope_text = ", ".join([s.split("/")[-1] for s in scopes]) if scopes else "gmail.readonly, calendar.readonly"
            self.lbl_ws_scopes.setText(f"Active Least-Privilege Scopes: {scope_text}")
        except Exception as e:
            self.lbl_ws_status.setText(f"Status: MCP Bridge Error ({e})")

    def _open_oauth_dialog(self) -> None:
        """Open the secure Google Credentials setup dialog."""
        dlg = GoogleCredentialsDialog(self)
        if dlg.exec():
            self.refresh_workspace_ui()
            self._record_ledger("OAUTH", "Google Credentials Setup", "SUCCESS", "Client credentials updated in vault")

    def _test_mcp_connection(self) -> None:
        """Trigger interactive verification of Workspace MCP tools."""
        t0 = time.time()
        try:
            res = self.workspace_adapter.get_gmail_status()
            lat = round((time.time() - t0) * 1000, 1)
            self._record_ledger("MCP", "mcp_get_gmail_status", "SUCCESS", f"Latency {lat}ms: Account {res.get('account')}")
            self.refresh_workspace_ui()
        except Exception as e:
            lat = round((time.time() - t0) * 1000, 1)
            self._record_ledger("MCP", "mcp_get_gmail_status", "FAILED", f"Latency {lat}ms: {e}")

    def _record_ledger(self, subsystem: str, operation: str, status: str, details: str) -> None:
        """Add row to the operations ledger table."""
        row = self.tbl_ledger.rowCount()
        self.tbl_ledger.insertRow(row)

        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        self.tbl_ledger.setItem(row, 0, QTableWidgetItem(now_str))
        self.tbl_ledger.setItem(row, 1, QTableWidgetItem(subsystem))
        self.tbl_ledger.setItem(row, 2, QTableWidgetItem(operation))

        status_item = QTableWidgetItem(status)
        if status == "SUCCESS":
            status_item.setForeground(QColor(C.GREEN))
        elif status == "FAILED":
            status_item.setForeground(QColor(C.RED))
        else:
            status_item.setForeground(QColor(C.ACC))
        self.tbl_ledger.setItem(row, 3, status_item)

        self.tbl_ledger.setItem(row, 4, QTableWidgetItem(details))
        self.tbl_ledger.scrollToBottom()
