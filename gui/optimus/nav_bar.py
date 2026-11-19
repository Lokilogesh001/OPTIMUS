"""gui/optimus/nav_bar.py — Cinematic JARVIS Navigation Bar for OPTIMUS.

Provides sleek, command-oriented section navigation across:
- Dashboard (Home)
- Command Center
- Workflows
- Agents
- Intelligence
- Memory
- Verification & Evidence
- Approvals (with live pending badge counter)
- System Monitor
- Settings
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from gui.style import px_font
from gui.theme import C


class NavTabButton(QPushButton):
    """Sleek HUD button with active glow border and hover effects."""

    def __init__(self, title: str, section_index: int, icon_code: str = "◈", parent=None):
        super().__init__(parent)
        self.section_index = section_index
        self._title = title
        self._icon_code = icon_code
        self._is_active = False
        self._badge_count = 0

        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(px_font(11, QFont.Weight.DemiBold, spacing=1.2))
        self._update_text()
        self._apply_style()

    def set_active(self, active: bool) -> None:
        self._is_active = active
        self._apply_style()
        self.update()

    def set_badge(self, count: int) -> None:
        self._badge_count = count
        self._update_text()
        self._apply_style()

    def _update_text(self) -> None:
        if self._badge_count > 0:
            self.setText(f"{self._icon_code}  {self._title} [{self._badge_count}]")
        else:
            self.setText(f"{self._icon_code}  {self._title}")

    def _apply_style(self) -> None:
        if self._is_active:
            text_color = C.WHITE
            bg_color = "rgba(0, 212, 255, 0.14)"
            border_bottom = f"2px solid {C.PRI}"
            border = f"1px solid {C.PRI}"
        elif self._badge_count > 0:
            text_color = C.ACC
            bg_color = "rgba(255, 107, 0, 0.12)"
            border_bottom = f"2px solid {C.ACC}"
            border = f"1px solid {C.ACC}"
        else:
            text_color = C.TEXT_DIM
            bg_color = "transparent"
            border_bottom = "2px solid transparent"
            border = "1px solid transparent"

        self.setStyleSheet(f"""
            QPushButton {{
                color: {text_color};
                background: {bg_color};
                border: {border};
                border-bottom: {border_bottom};
                border-radius: 3px;
                padding: 0 12px;
                text-align: center;
            }}
            QPushButton:hover {{
                color: {C.PRI};
                background: rgba(0, 212, 255, 0.08);
                border: 1px solid {C.BORDER_B};
            }}
        """)


class OptimusNavBar(QWidget):
    """Top horizontal bar providing fast section switching."""

    tab_selected = pyqtSignal([str], [int, str])

    SECTIONS = [
        ("DASHBOARD", "◈"),
        ("COMMAND", "⬡"),
        ("WORKFLOWS", "☊"),
        ("AGENTS", "⌬"),
        ("INTELLIGENCE", "🧠"),
        ("MEMORY", "🗄"),
        ("VERIFICATION", "🛡"),
        ("APPROVALS", "⚡"),
        ("SYSTEM", "📊"),
        ("SETTINGS", "⚙"),
        ("PLANNING", "📐"),
        ("DEVELOPER", "⌨"),
        ("SKILLS", "🧩"),
        ("MISSIONS", "🎯"),
        ("PROACTIVE", "🔔"),
        ("PERSONAL", "👤"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            OptimusNavBar {{
                background: {C.PANEL};
                border-bottom: 1px solid {C.BORDER_A};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        # Brand prefix
        brand = QLabel("OPTIMUS")
        brand.setFont(px_font(12, QFont.Weight.Bold, spacing=2.5))
        brand.setStyleSheet(f"color: {C.PRI}; background: transparent; padding-right: 6px;")
        layout.addWidget(brand)

        sep = QLabel("│")
        sep.setFont(px_font(12, QFont.Weight.Light))
        sep.setStyleSheet(f"color: {C.BORDER_B}; background: transparent; padding-right: 6px;")
        layout.addWidget(sep)

        self._buttons: list[NavTabButton] = []
        for idx, (title, icon) in enumerate(self.SECTIONS):
            btn = NavTabButton(title, idx, icon)
            btn.clicked.connect(lambda checked=False, i=idx, t=title: self._on_button_clicked(i, t))
            layout.addWidget(btn)
            self._buttons.append(btn)

        layout.addStretch()

        # Approval badge indicator
        self._approval_badge = QLabel("0")
        self._approval_badge.setFont(px_font(9, QFont.Weight.Bold))
        self._approval_badge.setStyleSheet(f"color: {C.WHITE}; background: {C.ACC}; border-radius: 8px; padding: 1px 6px;")
        self._approval_badge.hide()
        layout.addWidget(self._approval_badge)

        # Status badge indicator
        self._online_lbl = QLabel("● SYSTEM ONLINE")
        self._online_lbl.setFont(px_font(9, QFont.Weight.DemiBold, spacing=1.5))
        self._online_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; padding: 0 8px;")
        self._health_badge = self._online_lbl
        layout.addWidget(self._online_lbl)

        # Select Dashboard by default
        self._current_index = 0
        if self._buttons:
            self._buttons[0].set_active(True)

    @property
    def active_tab(self) -> str:
        if 0 <= self._current_index < len(self.SECTIONS):
            return self.SECTIONS[self._current_index][0].lower()
        return "dashboard"

    def _on_button_clicked(self, index: int, title: str) -> None:
        self.select_tab(index)
        self.tab_selected[str].emit(title.lower())
        self.tab_selected[int, str].emit(index, title)

    def select_tab(self, index: int | str) -> None:
        if isinstance(index, str):
            target = index.strip().upper()
            idx_found = None
            for i, (title, _) in enumerate(self.SECTIONS):
                if title == target or title.startswith(target):
                    idx_found = i
                    break
            if idx_found is not None:
                index = idx_found
            else:
                return

        if 0 <= index < len(self._buttons):
            self._current_index = index
            for i, btn in enumerate(self._buttons):
                btn.set_active(i == index)
            title = self.SECTIONS[index][0].lower()
            self.tab_selected[str].emit(title)
            self.tab_selected[int, str].emit(index, title.upper())

    def set_approval_badge(self, count: int) -> None:
        """Update the pending badge on the APPROVALS tab (Index 7) and navbar indicator."""
        for btn in self._buttons:
            if btn.section_index == 7:
                btn.set_badge(count)
                break
        if count > 0:
            self._approval_badge.setText(str(count))
            self._approval_badge.show()
        else:
            self._approval_badge.hide()

    def set_proactive_badge(self, count: int) -> None:
        """Update the notification/recommendation badge on the PROACTIVE tab (Index 14)."""
        for btn in self._buttons:
            if btn.section_index == 14:
                btn.set_badge(count)
                break

    def update_health(self, status: str, agent_count: int = 5) -> None:
        """Update system health status badge."""
        self._online_lbl.setText(f"● {status.upper()}  [{agent_count} AGENTS]")
        color = C.GREEN if status == "HEALTHY" else C.RED
        self._online_lbl.setStyleSheet(f"color: {color}; background: transparent; padding: 0 8px;")

    def set_system_status(self, text: str, is_healthy: bool = True) -> None:
        color = C.GREEN if is_healthy else C.RED
        self._online_lbl.setText(f"● {text.upper()}")
        self._online_lbl.setStyleSheet(f"color: {color}; background: transparent; padding: 0 8px;")
