"""gui/optimus/section_intelligence.py — Dedicated Intelligence Gateway Section.

Provides comprehensive Model Gateway control:
- Registered model registry & context limits.
- Provider cards (Ollama, OpenAI, Anthropic, Local Custom).
- Circuit breaker status monitoring & reset buttons.
- Routing policy controls (LOCAL_FIRST, QUALITY_FIRST, LOW_COST, LATENCY_FIRST).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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


class SectionIntelligence(QWidget):
    """Model & Intelligence Gateway management dashboard."""

    def __init__(self, bridge: OptimusServiceBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.setStyleSheet(f"background: {C.BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("INTELLIGENCE GATEWAY  ·  MULTI-MODEL ROUTER")
        title.setFont(px_font(13, QFont.Weight.Bold, spacing=2.0))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        btn_refresh = QPushButton("REFRESH GATEWAY")
        btn_refresh.setFont(px_font(9, QFont.Weight.Bold))
        btn_refresh.setStyleSheet(btn_css("dim"))
        btn_refresh.clicked.connect(self.refresh_intelligence)
        hdr.addWidget(btn_refresh)
        layout.addLayout(hdr)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"""
            QSplitter::handle {{ background: {C.BORDER}; }}
            QSplitter::handle:hover {{ background: {C.PRI}; }}
        """)

        # Left Column: Providers & Policies
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 5, 0)
        left_lay.setSpacing(8)

        # Policy Control
        p_policy = HoloPanel("ROUTING POLICY & PRIVACY", "POLICY")
        pol_row = QHBoxLayout()
        pol_lbl = QLabel("ACTIVE POLICY:")
        pol_lbl.setFont(px_font(10, QFont.Weight.Bold))
        pol_lbl.setStyleSheet(f"color: {C.WHITE};")
        pol_row.addWidget(pol_lbl)

        self.policy_combo = QComboBox()
        self.policy_combo.setFont(mono_font(9))
        self.policy_combo.addItems([
            "LOCAL_FIRST",
            "CLOUD_PREFERRED",
            "PRIVACY_STRICT",
            "COST_OPTIMIZED",
            "PERFORMANCE_OPTIMIZED",
            "MANUAL",
        ])
        self.policy_combo.setStyleSheet(f"""
            QComboBox {{
                background: {C.PANEL2};
                color: {C.PRI};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 4px;
            }}
        """)
        self.policy_combo.currentTextChanged.connect(self._on_policy_changed)
        pol_row.addWidget(self.policy_combo)
        pol_row.addStretch()
        p_policy.body.addLayout(pol_row)
        left_lay.addWidget(p_policy)

        # Provider Cards
        p_prov = HoloPanel("PROVIDER STATUS & CIRCUIT BREAKERS", "PROVIDERS")
        self.prov_cards_container = QWidget()
        self.prov_cards_container.setStyleSheet("background: transparent;")
        self.prov_cards_lay = QVBoxLayout(self.prov_cards_container)
        self.prov_cards_lay.setSpacing(6)
        self.provider_cards: list[QFrame] = []
        p_prov.body.addWidget(self.prov_cards_container)
        left_lay.addWidget(p_prov, stretch=1)

        split.addWidget(left_w)

        # Right Column: Model Catalog & Test Box
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(5, 0, 0, 0)
        right_lay.setSpacing(8)

        p_models = HoloPanel("REGISTERED MODEL CATALOG", "REGISTRY")
        self.table_models = QTableWidget()
        self.table_models.setColumnCount(4)
        self.table_models.setHorizontalHeaderLabels(["MODEL ID", "PROVIDER", "TIER", "CONTEXT"])
        self.table_models.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_models.verticalHeader().setVisible(False)
        self.table_models.setStyleSheet(f"""
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
        p_models.body.addWidget(self.table_models)
        right_lay.addWidget(p_models, stretch=3)

        # Health Summary Display
        p_health = HoloPanel("HEALTH TELEMETRY", "CIRCUITS")
        self.health_edit = QTextEdit()
        self.health_edit.setReadOnly(True)
        self.health_edit.setFont(mono_font(9))
        self.health_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 3px;
                padding: 4px;
            }}
        """ + scroll_css())
        p_health.body.addWidget(self.health_edit)
        right_lay.addWidget(p_health, stretch=2)

        split.addWidget(right_w)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 5)
        layout.addWidget(split)

        self.refresh_intelligence()

    def refresh_intelligence(self) -> None:
        data = self.bridge.get_intelligence_status()
        policy = data.get("active_policy", "LOCAL_FIRST")
        self.policy_combo.setCurrentText(policy)

        # Render provider cards
        while self.prov_cards_lay.count():
            item = self.prov_cards_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.provider_cards = []
        providers = data.get("providers", {})
        for p_name, p_info in providers.items():
            card = QFrame()
            self.provider_cards.append(card)
            is_healthy = p_info.get("is_healthy", True)
            border_c = C.GREEN if is_healthy else C.RED
            card.setStyleSheet(f"""
                QFrame {{
                    background: {C.PANEL2};
                    border: 1px solid {border_c};
                    border-radius: 4px;
                    padding: 6px;
                }}
            """)
            c_lay = QHBoxLayout(card)
            c_lay.setContentsMargins(6, 4, 6, 4)

            p_title = QLabel(p_name.upper())
            p_title.setFont(px_font(10, QFont.Weight.Bold))
            p_title.setStyleSheet(f"color: {C.WHITE};")
            c_lay.addWidget(p_title)

            kind = "LOCAL" if p_info.get("is_local") else "CLOUD"
            k_lbl = QLabel(f"[{kind}]")
            k_lbl.setFont(mono_font(8))
            k_lbl.setStyleSheet(f"color: {C.PRI_DIM};")
            c_lay.addWidget(k_lbl)
            c_lay.addStretch()

            cb = p_info.get("circuit_breaker", {})
            cb_state = cb.get("state", "CLOSED")
            cb_lbl = QLabel(f"CB: {cb_state}")
            cb_lbl.setFont(mono_font(8))
            cb_color = C.GREEN if cb_state == "CLOSED" else C.RED
            cb_lbl.setStyleSheet(f"color: {cb_color}; font-weight: bold;")
            c_lay.addWidget(cb_lbl)

            btn_reset = QPushButton("RESET")
            btn_reset.setFont(px_font(8, QFont.Weight.Bold))
            btn_reset.setStyleSheet(btn_css("dim"))
            btn_reset.clicked.connect(lambda checked=False, n=p_name: self._reset_cb(n))
            c_lay.addWidget(btn_reset)

            self.prov_cards_lay.addWidget(card)

        self.prov_cards_lay.addStretch()

        # Render models table
        models = data.get("models", [])
        self.table_models.setRowCount(len(models))
        for row, m in enumerate(models):
            m_id = QTableWidgetItem(m.get("model_id", "model"))
            m_id.setFont(mono_font(9))

            p_type = QTableWidgetItem(m.get("provider_type", "LOCAL"))
            p_type.setFont(px_font(9))

            tier_str = "LOCAL" if m.get("is_local") else "CLOUD"
            tier = QTableWidgetItem(tier_str)
            tier.setFont(px_font(9, QFont.Weight.Bold))
            tier.setForeground(QColor(C.GREEN if m.get("is_local") else C.PRI))

            ctx = QTableWidgetItem(str(m.get("context_window", "--")))
            ctx.setFont(mono_font(9))

            self.table_models.setItem(row, 0, m_id)
            self.table_models.setItem(row, 1, p_type)
            self.table_models.setItem(row, 2, tier)
            self.table_models.setItem(row, 3, ctx)

        import json
        self.health_edit.setPlainText(json.dumps(data, indent=2))

    def _on_policy_changed(self, text: str) -> None:
        gw = getattr(self.bridge.orchestrator, "intelligence_gateway", None)
        if gw:
            from core.intelligence.types import RoutingPolicy
            try:
                pol = RoutingPolicy[text]
                gw.set_policy(pol)
            except Exception as e:
                print(f"[OptimusUI] Error setting policy: {e}")

    def _reset_cb(self, prov_name: str) -> None:
        gw = getattr(self.bridge.orchestrator, "intelligence_gateway", None)
        if gw and prov_name in gw.providers:
            gw.providers[prov_name].circuit_breaker.reset()
            self.refresh_intelligence()
