"""gui/optimus/section_memory.py — Dedicated Memory & Context Bus Section.

Provides inspection of:
- Persistent memory store (episodic, semantic, deliverable, conversation).
- Full text & keyword search across memories with ranking scores.
- Active Shared Context Bus channels and cross-subtask variables.
"""

from __future__ import annotations

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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C
from gui.widgets import HoloPanel


class SectionMemory(QWidget):
    """Memory & Context Bus Explorer."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("MEMORY SUBSYSTEM  ·  SHARED CONTEXT BUS")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        btn_refresh = QPushButton("REFRESH MEMORY")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_memory)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Search & Memory Table
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)
        left_lay.setSpacing(8)

        # Search Bar
        p_search = HoloPanel("MEMORY SEARCH & RETRIEVAL", "QUERY")
        s_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setFont(px_font(11))
        self.search_input.setPlaceholderText("Search memories (e.g. 'deliverable', 'task', 'calculation')...")
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL2};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """)
        self.search_input.returnPressed.connect(self._on_search)
        s_row.addWidget(self.search_input, stretch=1)

        btn_s = QPushButton("SEARCH")
        btn_s.setFont(px_font(9, QFont.Weight.Bold))
        btn_s.setStyleSheet(btn_css("primary"))
        btn_s.clicked.connect(self._on_search)
        s_row.addWidget(btn_s)
        p_search.body.addLayout(s_row)
        left_lay.addWidget(p_search)

        # Entries Table
        p_table = HoloPanel("MEMORY ENTRIES", "STORE")
        self.table_mem = QTableWidget()
        self.table_mem.setColumnCount(4)
        self.table_mem.setHorizontalHeaderLabels(["KEY / ID", "CLASS", "SOURCE", "PREVIEW"])
        self.table_mem.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table_mem.verticalHeader().setVisible(False)
        self.table_mem.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_mem.setStyleSheet(f"""
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
        self.table_mem.itemClicked.connect(self._on_entry_selected)
        p_table.body.addWidget(self.table_mem)
        left_lay.addWidget(p_table, stretch=1)

        split.addWidget(left_w)

        # Right Column: Entry Inspector & Context Bus
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        p_entry = HoloPanel("ENTRY INSPECTOR", "RECORD")
        self.entry_edit = QTextEdit()
        self.entry_edit.setReadOnly(True)
        self.entry_edit.setFont(mono_font(9))
        self.entry_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """ + scroll_css())
        p_entry.body.addWidget(self.entry_edit)
        right_lay.addWidget(p_entry, stretch=3)

        p_bus = HoloPanel("CONTEXT BUS ACTIVE CHANNELS", "BUS")
        self.bus_edit = QTextEdit()
        self.bus_edit.setReadOnly(True)
        self.bus_edit.setFont(mono_font(9))
        self.bus_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.PRI};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 6px;
            }}
        """ + scroll_css())
        p_bus.body.addWidget(self.bus_edit)
        right_lay.addWidget(p_bus, stretch=2)

        split.addWidget(right_w)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        layout.addWidget(split)

        # Property aliases for testing & external APIs
        self.table_memories = self.table_mem
        self.on_search = self._on_search

        self._entries_cache: list[Any] = []
        self.refresh_memory()

    def refresh_memory(self) -> None:
        mem_mgr = getattr(self.bridge.orchestrator, "memory_manager", None)
        if not mem_mgr:
            return

        entries = mem_mgr.store.list_all(limit=100)
        self._entries_cache = entries
        self._populate_table(entries)

        # Populate context bus
        bus = mem_mgr.context_bus
        bus_info = f"ACTIVE TASK PARTITIONS: {len(bus._task_working_context)}\n"
        for t_id, t_data in bus._task_working_context.items():
            sub_outputs = t_data.get("subtask_outputs", {})
            bus_info += f"  Task [{t_id[:12]}]: {len(sub_outputs)} outputs, {len(t_data.get('deliverables', []))} deliverables\n"
            for st_id, entry in sub_outputs.items():
                if isinstance(entry, dict):
                    k = entry.get("key", entry.get("output_key", st_id))
                    src = entry.get("source", entry.get("agent", "agent"))
                    ver = entry.get("is_verified", False)
                else:
                    k = getattr(entry, "key", st_id)
                    src = getattr(entry, "source", "agent")
                    ver = getattr(entry, "is_verified", False)
                bus_info += f"    - Subtask {st_id}: key={k} agent={src} verified={ver}\n"
        self.bus_edit.setPlainText(bus_info)

    def _populate_table(self, entries: list[Any]) -> None:
        self.table_mem.setRowCount(len(entries))
        for row, e in enumerate(entries):
            k_item = QTableWidgetItem(e.key or e.entry_id[:12])
            k_item.setFont(mono_font(9))

            cat_str = e.category.value if hasattr(e.category, "value") else str(e.category)
            c_item = QTableWidgetItem(cat_str)
            c_item.setFont(px_font(9, QFont.Weight.Bold))
            c_item.setForeground(QColor(C.PRI))

            src_item = QTableWidgetItem(e.source or "orchestrator")
            src_item.setFont(mono_font(8))

            val_preview = str(e.content).replace("\n", " ")[:60]
            val_item = QTableWidgetItem(val_preview)
            val_item.setFont(px_font(9))

            self.table_mem.setItem(row, 0, k_item)
            self.table_mem.setItem(row, 1, c_item)
            self.table_mem.setItem(row, 2, src_item)
            self.table_mem.setItem(row, 3, val_item)

        if entries:
            self._render_entry(entries[0])

    def _on_entry_selected(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if 0 <= row < len(self._entries_cache):
            self._render_entry(self._entries_cache[row])

    def _render_entry(self, e: Any) -> None:
        import json
        self.entry_edit.setPlainText(json.dumps(e.to_dict(), indent=2))

    def _on_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self.refresh_memory()
            return

        mem_mgr = getattr(self.bridge.orchestrator, "memory_manager", None)
        if not mem_mgr:
            return

        results = mem_mgr.store.search(query, limit=50)
        self._entries_cache = results
        self._populate_table(results)
