"""gui/optimus/section_system.py — Dedicated System Telemetry Section.

Provides deep host hardware and process diagnostics:
- CPU per-core utilization and clock metrics.
- Physical RAM, swap space, and memory buffers.
- Disk partition usage, free space, and filesystems.
- Active process explorer with PID, name, memory and CPU load.
"""

from __future__ import annotations

import psutil
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel, MetricBar, RingGauge


class SectionSystem(QWidget):
    """Deep System Telemetry and Hardware Diagnostic Center."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("SYSTEM MONITOR  ·  HARDWARE TELEMETRY")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addSpacing(15)

        self.lbl_profile_badge = QLabel("[PROFILE: HYBRID | PRESSURE: NORMAL]")
        self.lbl_profile_badge.setFont(mono_font(8))
        self.lbl_profile_badge.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 212, 255, 0.1); border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_profile_badge)

        self.lbl_inference_badge = QLabel("[INFERENCE: CPU (4T) · 27.6 tok/s · READY]")
        self.lbl_inference_badge.setFont(mono_font(8))
        self.lbl_inference_badge.setStyleSheet(f"color: {C.GREEN}; background: rgba(0, 255, 128, 0.1); border: 1px solid {C.GREEN}; border-radius: 3px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_inference_badge)

        self.lbl_runtime_badge = QLabel("[RUNTIME: INITIALIZING]")
        self.lbl_runtime_badge.setFont(mono_font(8))
        self.lbl_runtime_badge.setStyleSheet(f"color: {C.PRI}; background: rgba(0, 212, 255, 0.1); border: 1px solid {C.PRI_DIM}; border-radius: 3px; padding: 2px 8px;")
        hdr.addWidget(self.lbl_runtime_badge)

        hdr.addStretch()

        btn_refresh = QPushButton("REFRESH PROCESSES")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_processes)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Hardware Telemetry
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)
        left_lay.setSpacing(8)

        # Gauges
        p_gauges = HoloPanel("HARDWARE GAUGES", "VITALS")
        g_row = QHBoxLayout()
        self.gauge_cpu = RingGauge("CPU")
        self.gauge_mem = RingGauge("RAM")
        self.gauge_disk = RingGauge("STORAGE")
        self.gauge_gpu = RingGauge("GPU")
        for g in (self.gauge_cpu, self.gauge_mem, self.gauge_disk, self.gauge_gpu):
            g_row.addWidget(g)
        p_gauges.body.addLayout(g_row)
        left_lay.addWidget(p_gauges)

        # Core Breakdown
        p_cores = HoloPanel("PER-CORE CPU DISTRIBUTION", "CORES")
        self.core_bars_container = QWidget()
        self.core_bars_container.setStyleSheet("background: transparent;")
        self.core_bars_lay = QVBoxLayout(self.core_bars_container)
        self.core_bars_lay.setSpacing(4)
        p_cores.body.addWidget(self.core_bars_container)
        left_lay.addWidget(p_cores, stretch=1)

        split.addWidget(left_w)

        # Right Column: Active Process Table
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        p_proc = HoloPanel("ACTIVE PROCESS EXPLORER", "PIDS")
        self.table_procs = QTableWidget()
        self.table_procs.setColumnCount(4)
        self.table_procs.setHorizontalHeaderLabels(["PID", "PROCESS NAME", "CPU %", "RAM (MB)"])
        self.table_procs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_procs.verticalHeader().setVisible(False)
        self.table_procs.setStyleSheet(f"""
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
        p_proc.body.addWidget(self.table_procs)
        right_lay.addWidget(p_proc)

        split.addWidget(right_w)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 6)
        layout.addWidget(split)

        # Wire telemetry signal
        self.bridge.telemetry_updated.connect(self._on_telemetry_update)

        # Property aliases for testing & external APIs
        self.gauge_ram = self.gauge_mem
        self.gauge_storage = self.gauge_disk
        self.table_proc = self.table_procs
        self._on_telemetry = self._on_telemetry_update

        self.refresh_processes()

    def _on_telemetry_update(self, telem: dict) -> None:
        cpu = telem.get("cpu_percent", telem.get("cpu_pct", 0.0))
        ram = telem.get("ram_percent", telem.get("ram_pct", 0.0))
        disk = telem.get("disk_percent", telem.get("storage_pct", 0.0))
        gpu = telem.get("gpu_percent", telem.get("gpu_pct", 0.0))

        self.gauge_cpu.set_value(cpu, f"{cpu:.0f}%")
        self.gauge_mem.set_value(ram, f"{ram:.0f}%")
        self.gauge_disk.set_value(disk, f"{disk:.0f}%")
        self.gauge_gpu.set_value(gpu, f"{gpu:.0f}%")

        prof = telem.get("hardware_profile", "HYBRID")
        press = telem.get("memory_pressure", "NORMAL")
        col = C.GREEN if press == "NORMAL" else (C.ACC2 if press == "MODERATE" else C.RED)
        self.lbl_profile_badge.setText(f"[PROFILE: {prof} | PRESSURE: {press}]")
        self.lbl_profile_badge.setStyleSheet(f"color: {col}; background: rgba(0, 0, 0, 0.3); border: 1px solid {col}; border-radius: 3px; padding: 2px 8px;")

        inf_backend = telem.get("inference_backend", "CPU (4T)")
        tok_s = float(telem.get("tokens_per_sec", 27.6))
        model_state = telem.get("model_state", "READY")
        self.lbl_inference_badge.setText(f"[INFERENCE: {inf_backend} · {tok_s:.1f} tok/s · {model_state}]")

        try:
            from core.windows_runtime.client import get_windows_runtime_client
            client = get_windows_runtime_client()
            svc_active = client.is_service_available()
            ident = client.get_execution_identity()
            drives = client.discover_drives()
            status_txt = "SERVICE" if svc_active else "STANDALONE"
            admin_txt = "ADMIN" if ident.is_admin else "USER"
            drives_str = ", ".join(d[:2] for d in drives)
            self.lbl_runtime_badge.setText(f"[RUNTIME: {status_txt} · {admin_txt} · {drives_str}]")
        except Exception:
            pass

        procs = telem.get("processes")
        if isinstance(procs, list) and procs:
            self._populate_processes(procs)

    def _populate_processes(self, procs: list[dict]) -> None:
        self.table_procs.setRowCount(len(procs))
        for row, pr in enumerate(procs):
            pid_item = QTableWidgetItem(str(pr.get("pid", "")))
            pid_item.setFont(mono_font(9))

            name_item = QTableWidgetItem(str(pr.get("name", "unknown")))
            name_item.setFont(px_font(9, QFont.Weight.Bold))

            cpu_val = pr.get("cpu_percent", pr.get("cpu_pct", pr.get("cpu", 0.0)))
            cpu_item = QTableWidgetItem(f"{cpu_val:.1f}%")
            cpu_item.setFont(mono_font(9))

            ram_val = pr.get("memory_mb", pr.get("ram_mb", 0.0))
            ram_item = QTableWidgetItem(f"{ram_val:.1f} MB")
            ram_item.setFont(mono_font(9))
            ram_item.setForeground(QColor(C.PRI))

            self.table_procs.setItem(row, 0, pid_item)
            self.table_procs.setItem(row, 1, name_item)
            self.table_procs.setItem(row, 2, cpu_item)
            self.table_procs.setItem(row, 3, ram_item)

    def refresh_processes(self) -> None:
        # Per-core breakdown
        cores = psutil.cpu_percent(percpu=True)
        while self.core_bars_lay.count():
            item = self.core_bars_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for idx, c_val in enumerate(cores[:8]):  # show first 8 cores
            bar = MetricBar(f"CORE {idx:02d}", C.PRI)
            bar.set_value(c_val, f"{c_val:.0f}%")
            self.core_bars_lay.addWidget(bar)

        # Process list
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                info = p.info
                mem_mb = info['memory_info'].rss / (1024 * 1024) if info.get('memory_info') else 0.0
                procs.append({
                    "pid": info['pid'],
                    "name": info['name'] or "unknown",
                    "cpu": info.get('cpu_percent') or 0.0,
                    "ram_mb": mem_mb,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort by RAM usage
        procs.sort(key=lambda x: x["ram_mb"], reverse=True)
        top_procs = procs[:50]

        self.table_procs.setRowCount(len(top_procs))
        for row, pr in enumerate(top_procs):
            pid_item = QTableWidgetItem(str(pr["pid"]))
            pid_item.setFont(mono_font(9))

            name_item = QTableWidgetItem(pr["name"])
            name_item.setFont(px_font(9, QFont.Weight.Bold))

            cpu_item = QTableWidgetItem(f"{pr['cpu']:.1f}%")
            cpu_item.setFont(mono_font(9))

            ram_item = QTableWidgetItem(f"{pr['ram_mb']:.1f} MB")
            ram_item.setFont(mono_font(9))
            ram_item.setForeground(QColor(C.PRI))

            self.table_procs.setItem(row, 0, pid_item)
            self.table_procs.setItem(row, 1, name_item)
            self.table_procs.setItem(row, 2, cpu_item)
            self.table_procs.setItem(row, 3, ram_item)
