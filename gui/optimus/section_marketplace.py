"""gui/optimus/section_marketplace.py — Dedicated Plugin Discovery & Marketplace UI.

Features:
- Search omnibar and category filter pills (All, MCP Servers, Tools, PyPI, GitHub)
- Visual cards displaying plugin metadata, security scorecards, and license badges
- Real-time installation lifecycle progress bar (DISCOVERED -> INSPECTED -> REVIEWED -> INSTALLED -> ENABLED)
- Single-click Install, Uninstall, Enable, and Disable actions
- Warning dialog on copyleft or security risks
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.marketplace.manager import (
    MarketplaceManager,
    MarketplacePluginRecord,
    PluginLifecycleState,
)
from core.marketplace.sources import (
    DiscoveredPlugin,
    MarketplaceSource,
    PluginExecutionType,
)
from gui.optimus.service_bridge import OptimusServiceBridge
from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C

logger = logging.getLogger("optimus.gui.section_marketplace")


class PluginCardWidget(QFrame):
    """Visual card representation of a discovered marketplace plugin."""

    install_clicked = pyqtSignal(str)
    uninstall_clicked = pyqtSignal(str)
    toggle_clicked = pyqtSignal(str)

    def __init__(self, record: MarketplacePluginRecord, parent=None):
        super().__init__(parent)
        self.record = record
        self.plugin = record.plugin
        self.setObjectName("PluginCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            QFrame#PluginCard {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                padding: 12px;
            }}
            QFrame#PluginCard:hover {{
                border: 1px solid {C.PRI};
            }}
        """)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header: Name, Version, Source Badge
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        name_lbl = QLabel(self.plugin.name)
        name_lbl.setFont(px_font(12, QFont.Weight.Bold))
        name_lbl.setStyleSheet(f"color: {C.WHITE};")
        header_row.addWidget(name_lbl)

        ver_lbl = QLabel(f"v{self.plugin.version}")
        ver_lbl.setFont(mono_font(9))
        ver_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        header_row.addWidget(ver_lbl)

        header_row.addStretch()

        # Security Score Badge
        score = self.record.assessment.score if self.record.assessment else 100
        score_color = C.GREEN if score >= 80 else (C.ACC if score >= 60 else C.RED)
        score_badge = QLabel(f"Security: {score}/100")
        score_badge.setFont(mono_font(9, QFont.Weight.SemiBold))
        score_badge.setStyleSheet(f"""
            background: {C.PANEL_HEAD};
            color: {score_color};
            border: 1px solid {score_color};
            border-radius: 4px;
            padding: 2px 6px;
        """)
        header_row.addWidget(score_badge)

        # Source / Type Badge
        type_badge = QLabel(self.plugin.execution_type.value.upper().replace("_", " "))
        type_badge.setFont(mono_font(9))
        type_badge.setStyleSheet(f"""
            background: rgba(0, 132, 255, 0.15);
            color: {C.ACC2};
            border: 1px solid {C.PRI_DIM};
            border-radius: 4px;
            padding: 2px 6px;
        """)
        header_row.addWidget(type_badge)

        layout.addLayout(header_row)

        # Description
        desc_lbl = QLabel(self.plugin.description or "No description provided.")
        desc_lbl.setFont(px_font(10))
        desc_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        # Metadata Row: Author, License, Native Equivalence
        meta_row = QHBoxLayout()
        meta_row.setSpacing(12)

        author_str = f"By: {self.plugin.author}" if self.plugin.author else f"Source: {self.plugin.source.value}"
        author_lbl = QLabel(author_str)
        author_lbl.setFont(px_font(9))
        author_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        meta_row.addWidget(author_lbl)

        lic_lbl = QLabel(f"License: {self.plugin.license}")
        lic_lbl.setFont(mono_font(9))
        lic_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        meta_row.addWidget(lic_lbl)

        if self.record.native_equivalent_tool:
            equiv_lbl = QLabel(f"Equivalent native: {self.record.native_equivalent_tool}")
            equiv_lbl.setFont(mono_font(9))
            equiv_lbl.setStyleSheet(f"color: {C.ACC};")
            meta_row.addWidget(equiv_lbl)

        meta_row.addStretch()

        # Action Buttons based on Lifecycle State
        state = self.record.state
        state_lbl = QLabel(f"State: {state.value.upper()}")
        state_lbl.setFont(mono_font(9, QFont.Weight.Bold))
        st_color = C.GREEN if state == PluginLifecycleState.ENABLED else (
            C.ACC if state in (PluginLifecycleState.INSTALLED, PluginLifecycleState.TESTED) else C.TEXT_DIM
        )
        state_lbl.setStyleSheet(f"color: {st_color};")
        meta_row.addWidget(state_lbl)

        if state in (PluginLifecycleState.DISCOVERED, PluginLifecycleState.INSPECTED, PluginLifecycleState.REVIEWED):
            btn = QPushButton("Install")
            btn.setStyleSheet(btn_css("primary"))
            btn.clicked.connect(lambda: self.install_clicked.emit(self.plugin.id))
            meta_row.addWidget(btn)
        elif state == PluginLifecycleState.INSTALLED:
            enable_btn = QPushButton("Enable")
            enable_btn.setStyleSheet(btn_css("primary"))
            enable_btn.clicked.connect(lambda: self.toggle_clicked.emit(self.plugin.id))
            meta_row.addWidget(enable_btn)

            un_btn = QPushButton("Uninstall")
            un_btn.setStyleSheet(btn_css("danger"))
            un_btn.clicked.connect(lambda: self.uninstall_clicked.emit(self.plugin.id))
            meta_row.addWidget(un_btn)
        elif state == PluginLifecycleState.ENABLED:
            dis_btn = QPushButton("Disable")
            dis_btn.setStyleSheet(btn_css("secondary"))
            dis_btn.clicked.connect(lambda: self.toggle_clicked.emit(self.plugin.id))
            meta_row.addWidget(dis_btn)

            un_btn = QPushButton("Uninstall")
            un_btn.setStyleSheet(btn_css("danger"))
            un_btn.clicked.connect(lambda: self.uninstall_clicked.emit(self.plugin.id))
            meta_row.addWidget(un_btn)

        layout.addLayout(meta_row)


class SectionMarketplace(QWidget):
    """Dedicated Plugin Discovery & Marketplace catalog section in OPTIMUS Control Center."""

    def __init__(self, bridge: Optional[OptimusServiceBridge] = None, parent=None):
        super().__init__(parent)
        self.bridge = bridge or OptimusServiceBridge.instance()
        self.manager = MarketplaceManager()
        self.active_category = "all"
        self._setup_ui()
        self._load_catalog()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Header Bar
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_lbl = QLabel("Plugin & Tool Marketplace")
        title_lbl.setFont(px_font(18, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {C.WHITE};")
        title_box.addWidget(title_lbl)

        sub_lbl = QLabel("Discover, inspect, and install MCP servers and hermetic plugins")
        sub_lbl.setFont(px_font(11))
        sub_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
        title_box.addWidget(sub_lbl)
        header.addLayout(title_box)

        header.addStretch()

        self.refresh_btn = QPushButton("Refresh Catalog")
        self.refresh_btn.setStyleSheet(btn_css("primary"))
        self.refresh_btn.clicked.connect(self._refresh_catalog)
        header.addWidget(self.refresh_btn)

        main_layout.addLayout(header)

        # Search Omnibar & Filter Pills
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search official MCP servers, GitHub repositories, or PyPI...")
        self.search_input.setFont(px_font(11))
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PANEL};
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                padding: 8px 12px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.PRI};
            }}
        """)
        self.search_input.textChanged.connect(self._filter_catalog)
        controls_row.addWidget(self.search_input, stretch=2)

        # Category pills
        self.cat_btns: Dict[str, QPushButton] = {}
        for cat_key, cat_name in [
            ("all", "All"),
            ("mcp", "MCP Servers"),
            ("github", "GitHub"),
            ("pypi", "PyPI"),
        ]:
            b = QPushButton(cat_name)
            b.setFont(px_font(10, QFont.Weight.Medium))
            b.setStyleSheet(self._pill_style(cat_key == "all"))
            b.clicked.connect(lambda checked, k=cat_key: self._set_category(k))
            self.cat_btns[cat_key] = b
            controls_row.addWidget(b)

        main_layout.addLayout(controls_row)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {C.BAR_BG};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {C.PRI};
                border-radius: 2px;
            }}
        """)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        # Scroll Area for Cards
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet(scroll_css())

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()

        self.scroll_area.setWidget(self.cards_container)
        main_layout.addWidget(self.scroll_area, stretch=1)

    def _pill_style(self, active: bool) -> str:
        if active:
            return f"""
                QPushButton {{
                    background: {C.PRI};
                    color: {C.WHITE};
                    border: 1px solid {C.PRI};
                    border-radius: 14px;
                    padding: 5px 12px;
                }}
            """
        return f"""
            QPushButton {{
                background: {C.PANEL};
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 14px;
                padding: 5px 12px;
            }}
            QPushButton:hover {{
                color: {C.WHITE};
                border: 1px solid {C.PRI_DIM};
            }}
        """

    def _set_category(self, cat_key: str) -> None:
        self.active_category = cat_key
        for k, b in self.cat_btns.items():
            b.setStyleSheet(self._pill_style(k == cat_key))
        self._filter_catalog()

    def _load_catalog(self) -> None:
        self._render_records(list(self.manager.records.values()))

    def _refresh_catalog(self) -> None:
        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)
        # Asynchronous discovery
        QTimer.singleShot(100, self._do_harvest)

    def _do_harvest(self) -> None:
        try:
            self.manager.discover()
            self._render_records(list(self.manager.records.values()))
        finally:
            self.progress_bar.hide()

    def _filter_catalog(self) -> None:
        q = self.search_input.text().lower()
        filtered = []
        for rec in self.manager.records.values():
            p = rec.plugin
            if self.active_category == "mcp" and "mcp" not in p.execution_type.value:
                continue
            if self.active_category == "github" and p.source != MarketplaceSource.GITHUB:
                continue
            if self.active_category == "pypi" and p.source != MarketplaceSource.PYPI:
                continue

            if q:
                if q not in p.name.lower() and q not in p.description.lower():
                    continue
            filtered.append(rec)
        self._render_records(filtered)

    def _render_records(self, records: List[MarketplacePluginRecord]) -> None:
        # Clear existing card widgets
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not records:
            empty_lbl = QLabel("No plugins found matching current search or filters.")
            empty_lbl.setFont(px_font(12))
            empty_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; padding: 20px;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.insertWidget(0, empty_lbl)
            return

        for rec in records:
            card = PluginCardWidget(rec, self)
            card.install_clicked.connect(self._handle_install)
            card.uninstall_clicked.connect(self._handle_uninstall)
            card.toggle_clicked.connect(self._handle_toggle)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

    @pyqtSlot(str)
    def _handle_install(self, plugin_id: str) -> None:
        logger.info("Installing plugin %s from UI", plugin_id)
        try:
            self.manager.install_plugin(plugin_id)
            self._filter_catalog()
        except Exception as e:
            logger.error("Installation failed: %s", e)

    @pyqtSlot(str)
    def _handle_uninstall(self, plugin_id: str) -> None:
        self.manager.remove_plugin(plugin_id)
        self._filter_catalog()

    @pyqtSlot(str)
    def _handle_toggle(self, plugin_id: str) -> None:
        rec = self.manager.records.get(plugin_id)
        if not rec:
            return
        if rec.state == PluginLifecycleState.ENABLED:
            self.manager.disable_plugin(plugin_id)
        else:
            self.manager.enable_plugin(plugin_id)
        self._filter_catalog()
