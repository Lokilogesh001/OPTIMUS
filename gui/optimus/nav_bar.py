"""gui/optimus/nav_bar.py — Clean, Professional Vertical Command Navigation for OPTIMUS.

Implements the left navigation sidebar of the three-part command interface:
- OPTIMUS brand identity and live connection status dot.
- Logically grouped navigation hierarchy (Execution, Intelligence, Governance).
- Quiet typographic markers and clean line icons (no emojis).
- Live approval and proactive notification badges.
- System health and agent count telemetry footer.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.style import mono_font, px_font, scroll_css
from gui.theme import C


class NavTabButton(QPushButton):
    """Sleek vertical sidebar button with clean left accent indicator and hover effects."""

    def __init__(self, title: str, section_index: int, icon_code: str = "◈", parent=None):
        super().__init__(parent)
        self.section_index = section_index
        self._title = title
        self._icon_code = icon_code
        self._is_active = False
        self._badge_count = 0

        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(px_font(10, QFont.Weight.Medium))
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
            self.setText(f"{self._icon_code}  {self._title}  [{self._badge_count}]")
        else:
            self.setText(f"{self._icon_code}  {self._title}")

    def _apply_style(self) -> None:
        if self._is_active:
            text_color = C.WHITE
            bg_color = "rgba(0, 132, 255, 0.14)"
            border_left = f"3px solid {C.PRI}"
            font_weight = "600"
        elif self._badge_count > 0:
            text_color = C.ACC
            bg_color = "rgba(245, 158, 11, 0.12)"
            border_left = f"3px solid {C.ACC}"
            font_weight = "500"
        else:
            text_color = C.TEXT_MED
            bg_color = "transparent"
            border_left = "3px solid transparent"
            font_weight = "400"

        self.setStyleSheet(f"""
            QPushButton {{
                color: {text_color};
                background: {bg_color};
                border: 1px solid transparent;
                border-left: {border_left};
                border-radius: 4px;
                padding: 0 10px;
                text-align: left;
                font-weight: {font_weight};
            }}
            QPushButton:hover {{
                color: {C.WHITE};
                background: rgba(255, 255, 255, 0.05);
            }}
        """)


class OptimusNavBar(QWidget):
    """Left vertical navigation sidebar providing structured access to OPTIMUS modules."""

    tab_selected = pyqtSignal([str], [int, str])

    # 18 Functional Sections mapped to indices in OptimusControlCenterWidget
    SECTIONS = [
        ("DASHBOARD", "⊞"),       # 0
        ("COMMAND", ">"),         # 1
        ("WORKFLOWS", "⇄"),       # 2
        ("AGENTS", "◈"),          # 3
        ("INTELLIGENCE", "◇"),    # 4
        ("MEMORY", "≡"),          # 5
        ("VERIFICATION", "✓"),    # 6
        ("APPROVALS", "⚡"),       # 7
        ("SYSTEM", "⎈"),          # 8
        ("SETTINGS", "⚙"),        # 9
        ("PLANNING", "◫"),        # 10
        ("DEVELOPER", "</>"),     # 11
        ("SKILLS", "✦"),          # 12
        ("MISSIONS", "◉"),        # 13
        ("PROACTIVE", "▲"),       # 14
        ("PERSONAL", "●"),        # 15
        ("IMPROVE", "↑"),         # 16
        ("VISION", "◎"),          # 17
        ("APPLICATIONS", "⎈"),    # 18
    ]

    # Logical groupings for structured navigation
    _GROUPS = [
        ("EXECUTION & CONTROL", [0, 13, 1, 18, 17, 3, 2, 11]),
        ("INTELLIGENCE & KNOWLEDGE", [4, 15, 5, 12, 10]),
        ("SYSTEM & GOVERNANCE", [7, 6, 14, 16, 8, 9]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(200)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            OptimusNavBar {{
                background: {C.PANEL};
                border-right: 1px solid {C.BORDER};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 10)
        layout.setSpacing(6)

        # ── 1. Top Brand Header ──────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setContentsMargins(4, 0, 4, 4)
        brand = QLabel("OPTIMUS")
        brand.setFont(px_font(12, QFont.Weight.Bold, spacing=1.2))
        brand.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        hdr.addWidget(brand)
        hdr.addStretch()

        status_dot = QLabel("● READY")
        status_dot.setFont(mono_font(8))
        status_dot.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        hdr.addWidget(status_dot)
        layout.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER_A}; margin: 2px 0 6px 0;")
        layout.addWidget(sep)

        # ── 2. Scrollable Navigation List ────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;" + scroll_css())

        nav_container = QWidget()
        nav_container.setStyleSheet("background: transparent;")
        nav_lay = QVBoxLayout(nav_container)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(2)

        self._buttons: list[NavTabButton] = [None] * len(self.SECTIONS)  # type: ignore

        for group_title, section_indices in self._GROUPS:
            grp_lbl = QLabel(group_title)
            grp_lbl.setFont(px_font(8, QFont.Weight.Bold))
            grp_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; padding: 8px 4px 2px 6px;")
            nav_lay.addWidget(grp_lbl)

            for idx in section_indices:
                title, icon = self.SECTIONS[idx]
                btn = NavTabButton(title, idx, icon)
                btn.clicked.connect(lambda checked=False, i=idx, t=title: self._on_button_clicked(i, t))
                nav_lay.addWidget(btn)
                self._buttons[idx] = btn

        nav_lay.addStretch()
        scroll.setWidget(nav_container)
        layout.addWidget(scroll, stretch=1)

        # ── 3. Footer: Telemetry & Badges ────────────────────────────────────
        footer_sep = QFrame()
        footer_sep.setFrameShape(QFrame.Shape.HLine)
        footer_sep.setStyleSheet(f"color: {C.BORDER_A}; margin: 4px 0;")
        layout.addWidget(footer_sep)

        # Approval badge
        self._approval_badge = QLabel("0")
        self._approval_badge.setFont(px_font(9, QFont.Weight.Bold))
        self._approval_badge.setStyleSheet(f"color: {C.WHITE}; background: {C.ACC}; border-radius: 6px; padding: 1px 6px;")
        self._approval_badge.hide()

        # Health badge
        self._online_lbl = QLabel("● HEALTHY  [5 AGENTS]")
        self._online_lbl.setFont(mono_font(8))
        self._online_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; padding: 2px 4px;")
        self._health_badge = self._online_lbl
        layout.addWidget(self._online_lbl)

        # Select Dashboard by default
        self._current_index = 0
        if self._buttons and self._buttons[0]:
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

        if 0 <= index < len(self._buttons) and self._buttons[index] is not None:
            self._current_index = index
            for i, btn in enumerate(self._buttons):
                if btn is not None:
                    btn.set_active(i == index)
            title = self.SECTIONS[index][0].lower()
            self.tab_selected[str].emit(title)
            self.tab_selected[int, str].emit(index, title.upper())

    def set_approval_badge(self, count: int) -> None:
        """Update the pending badge on the APPROVALS tab (Index 7) and navbar indicator."""
        for btn in self._buttons:
            if btn and btn.section_index == 7:
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
            if btn and btn.section_index == 14:
                btn.set_badge(count)
                break

    def update_health(self, status: str, agent_count: int = 5) -> None:
        """Update system health status badge."""
        self._online_lbl.setText(f"● {status.upper()}  [{agent_count} AGENTS]")
        color = C.GREEN if status == "HEALTHY" else C.RED
        self._online_lbl.setStyleSheet(f"color: {color}; background: transparent; padding: 2px 4px;")

    def set_system_status(self, text: str, is_healthy: bool = True) -> None:
        color = C.GREEN if is_healthy else C.RED
        self._online_lbl.setText(f"● {text.upper()}")
        self._online_lbl.setStyleSheet(f"color: {color}; background: transparent; padding: 2px 4px;")
