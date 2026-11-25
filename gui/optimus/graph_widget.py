"""gui/optimus/graph_widget.py — Reusable Interactive PyQt6 Node-Graph Canvas & Inspector.

Design system:
- Midnight navy canvas (#070b14), elevated panel (#0c1322), hairline borders (#1e293b).
- Restrained electric blue (#0084ff) connections & selection highlights, cool white (#f8fafc) text.
- Supports node selection, mouse-wheel/button zoom, drag pan, live search filtering, and
  explicit visual distinction between direct (solid) and derived (dashed) relationships.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.style import btn_css, mono_font, px_font, scroll_css
from gui.theme import C


@dataclass
class GraphNode:
    """Single node in an OPTIMUS interactive graph."""

    node_id: str
    label: str
    category: str
    status: str = "idle"
    subtitle: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    x: float | None = None
    y: float | None = None


@dataclass
class GraphEdge:
    """Directed relationship between two GraphNodes."""

    source_id: str
    target_id: str
    label: str = ""
    relation_kind: str = "direct"  # "direct" (solid) | "derived" (dashed) | "retry" (amber)


def _status_color(status: str) -> str:
    st = str(status or "").upper()
    if any(k in st for k in ("CONNECTED", "COMPLETED", "VERIFIED", "ACTIVE", "AVAILABLE", "PASS", "HEALTHY", "DONE")):
        return C.GREEN
    if any(k in st for k in ("WAITING", "APPROVAL", "RECOVERING", "PAUSED", "EXPIR", "PARTIAL", "DERIVED")):
        return C.ACC
    if any(k in st for k in ("FAILED", "ERROR", "BLOCKED", "DENIED", "UNAVAILABLE", "CANCELLED", "REVOKED")):
        return C.RED
    if any(k in st for k in ("RUNNING", "EXECUTING", "PLANNING", "VERIFYING", "QUEUED", "CORE")):
        return C.PRI
    return C.BORDER_B


class _NodeItem(QGraphicsRectItem):
    """Clickable, selectable QGraphicsRectItem representing a GraphNode."""

    WIDTH = 176.0
    HEIGHT = 60.0

    def __init__(self, node: GraphNode, on_select) -> None:
        super().__init__(0.0, 0.0, self.WIDTH, self.HEIGHT)
        self.node = node
        self._on_select = on_select
        self._highlighted = True
        self._selected = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if node.x is not None and node.y is not None:
            self.setPos(float(node.x), float(node.y))

        # Title
        self._title = QGraphicsTextItem(self._truncate(node.label, 22), self)
        self._title.setDefaultTextColor(QColor(C.WHITE))
        self._title.setFont(px_font(9, QFont.Weight.DemiBold))
        self._title.setPos(8.0, 6.0)

        # Category + Status subtitle
        sub = f"{node.category} · {node.status}" if node.status else node.category
        self._sub = QGraphicsTextItem(self._truncate(sub, 27), self)
        self._sub.setDefaultTextColor(QColor(_status_color(node.status)))
        self._sub.setFont(mono_font(7))
        self._sub.setPos(8.0, 28.0)

        if node.subtitle:
            self._extra = QGraphicsTextItem(self._truncate(node.subtitle, 27), self)
            self._extra.setDefaultTextColor(QColor(C.TEXT_DIM))
            self._extra.setFont(mono_font(7))
            self._extra.setPos(8.0, 41.0)

        self._apply_style()

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        t = str(text or "").replace("\n", " ").strip()
        return t if len(t) <= limit else t[: limit - 1] + "…"

    def set_search_match(self, matched: bool) -> None:
        self._highlighted = matched
        self.setOpacity(1.0 if matched else 0.25)

    def set_selected_state(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        accent = _status_color(self.node.status)
        border = C.PRI if self._selected else accent
        width = 2.0 if self._selected else 1.0
        bg = QColor(C.PANEL2 if self._selected else C.PANEL)
        self.setBrush(QBrush(bg))
        self.setPen(QPen(QColor(border), width))

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if self._on_select:
            self._on_select(self.node)


class _ZoomPanView(QGraphicsView):
    """QGraphicsView with smooth drag-pan and Ctrl/wheel zoom."""

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setStyleSheet(f"QGraphicsView {{ background: {C.BG}; border: 1px solid {C.BORDER}; border-radius: 4px; }}")
        self._zoom_factor = 1.0

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_by(1.12)
        elif delta < 0:
            self.zoom_by(1.0 / 1.12)

    def zoom_by(self, factor: float) -> None:
        new_z = max(0.3, min(2.5, self._zoom_factor * factor))
        applied = new_z / self._zoom_factor
        self._zoom_factor = new_z
        self.scale(applied, applied)

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom_factor = 1.0
        if self.scene() and not self.scene().itemsBoundingRect().isNull():
            self.fitInView(self.scene().itemsBoundingRect().adjusted(-30, -30, 30, 30), Qt.AspectRatioMode.KeepAspectRatio)


class InteractiveGraphView(QWidget):
    """Reusable interactive graph canvas + node inspector + search/zoom toolbar."""

    node_selected = pyqtSignal(dict)

    def __init__(self, title: str, empty_message: str = "No graph data available.", parent=None):
        super().__init__(parent)
        self._title = title
        self._empty_message = empty_message
        self._nodes: list[GraphNode] = []
        self._edges: list[GraphEdge] = []
        self._node_items: dict[str, _NodeItem] = {}
        self._selected_node_id: str | None = None

        self.setStyleSheet(f"background: {C.BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Toolbar
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.lbl_title = QLabel(title)
        self.lbl_title.setFont(px_font(10, QFont.Weight.Bold, spacing=1.0))
        self.lbl_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        bar.addWidget(self.lbl_title)

        self.lbl_counts = QLabel("0 nodes · 0 edges")
        self.lbl_counts.setFont(mono_font(8))
        self.lbl_counts.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        bar.addWidget(self.lbl_counts)
        bar.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter nodes...")
        self.search_input.setFixedWidth(180)
        self.search_input.setFont(px_font(9))
        self.search_input.setStyleSheet(
            f"QLineEdit {{ background: {C.PANEL2}; color: {C.WHITE}; border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 6px; }}"
        )
        self.search_input.textChanged.connect(self._apply_search_filter)
        bar.addWidget(self.search_input)

        for label, slot in (("+", lambda: self.view.zoom_by(1.15)), ("-", lambda: self.view.zoom_by(1.0 / 1.15)), ("FIT", lambda: self.view.reset_zoom())):
            btn = QPushButton(label)
            btn.setFont(mono_font(8, QFont.Weight.Bold))
            btn.setStyleSheet(btn_css("dim"))
            btn.clicked.connect(slot)
            bar.addWidget(btn)

        root.addLayout(bar)

        # Main Splitter: Canvas (left) + Inspector (right)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet(f"QSplitter::handle {{ background: {C.BORDER}; }}")

        self.scene = QGraphicsScene(self)
        self.view = _ZoomPanView(self.scene, self)
        split.addWidget(self.view)

        # Inspector panel
        insp = QFrame()
        insp.setMinimumWidth(250)
        insp.setStyleSheet(f"QFrame {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 4px; }}")
        insp_lay = QVBoxLayout(insp)
        insp_lay.setContentsMargins(10, 10, 10, 10)
        insp_lay.setSpacing(6)

        self.insp_header = QLabel("NODE INSPECTOR")
        self.insp_header.setFont(px_font(9, QFont.Weight.Bold, spacing=1.0))
        self.insp_header.setStyleSheet(f"color: {C.PRI}; border: none;")
        insp_lay.addWidget(self.insp_header)

        self.insp_title = QLabel("Select a node to inspect")
        self.insp_title.setFont(px_font(10, QFont.Weight.DemiBold))
        self.insp_title.setWordWrap(True)
        self.insp_title.setStyleSheet(f"color: {C.WHITE}; border: none;")
        insp_lay.addWidget(self.insp_title)

        self.insp_status = QLabel("")
        self.insp_status.setFont(mono_font(8))
        self.insp_status.setStyleSheet(f"color: {C.TEXT_DIM}; border: none;")
        insp_lay.addWidget(self.insp_status)

        self.insp_body = QTextEdit()
        self.insp_body.setReadOnly(True)
        self.insp_body.setFont(mono_font(8))
        self.insp_body.setStyleSheet(
            f"QTextEdit {{ background: {C.PANEL2}; color: {C.WHITE}; border: 1px solid {C.BORDER}; border-radius: 3px; padding: 6px; }}"
            + scroll_css()
        )
        insp_lay.addWidget(self.insp_body, stretch=1)

        split.addWidget(insp)
        split.setStretchFactor(0, 7)
        split.setStretchFactor(1, 3)
        root.addWidget(split, stretch=1)

    def set_graph(self, nodes: list[GraphNode], edges: list[GraphEdge], empty_message: str | None = None) -> None:
        """Render nodes and edges cleanly with deterministic layout if positions are unset."""
        self._nodes = list(nodes)
        self._edges = list(edges)
        self._node_items.clear()
        self._selected_node_id = None
        self.scene.clear()

        if not self._nodes:
            msg = empty_message or self._empty_message
            txt = self.scene.addText(msg, px_font(10))
            txt.setDefaultTextColor(QColor(C.TEXT_DIM))
            self.lbl_counts.setText("0 nodes · 0 edges")
            self.insp_title.setText("No data")
            self.insp_status.setText("")
            self.insp_body.setPlainText(msg)
            return

        self._assign_layered_positions(self._nodes)

        # Draw edges first (z-order behind nodes)
        node_map = {n.node_id: n for n in self._nodes}
        for edge in self._edges:
            src = node_map.get(edge.source_id)
            dst = node_map.get(edge.target_id)
            if not src or not dst or src.x is None or dst.x is None or src.y is None or dst.y is None:
                continue
            x1 = src.x + _NodeItem.WIDTH / 2.0
            y1 = src.y + _NodeItem.HEIGHT
            x2 = dst.x + _NodeItem.WIDTH / 2.0
            y2 = dst.y
            line = QGraphicsLineItem(x1, y1, x2, y2)
            if edge.relation_kind == "derived":
                pen = QPen(QColor(C.ACC), 1.2, Qt.PenStyle.DashLine)
            elif edge.relation_kind == "retry":
                pen = QPen(QColor(C.ACC), 1.5, Qt.PenStyle.DotLine)
            else:
                pen = QPen(QColor(C.PRI_DIM), 1.3, Qt.PenStyle.SolidLine)
            line.setPen(pen)
            line.setZValue(0)
            self.scene.addItem(line)

        # Draw nodes
        for n in self._nodes:
            item = _NodeItem(n, self._on_node_clicked)
            item.setZValue(1)
            self.scene.addItem(item)
            self._node_items[n.node_id] = item

        self.lbl_counts.setText(f"{len(self._nodes)} nodes · {len(self._edges)} edges")
        self._apply_search_filter(self.search_input.text())
        self.select_node(self._nodes[0].node_id)

    @staticmethod
    def _assign_layered_positions(nodes: list[GraphNode]) -> None:
        """Assign clean non-overlapping layered grid positions for nodes without explicit (x, y)."""
        unpositioned = [n for n in nodes if n.x is None or n.y is None]
        if not unpositioned:
            return
        groups: dict[str, list[GraphNode]] = {}
        for n in unpositioned:
            groups.setdefault(n.category, []).append(n)
        x_step = 205.0
        y_step = 96.0
        for row_idx, (_cat, grp) in enumerate(groups.items()):
            cols = max(1, min(6, math.ceil(math.sqrt(len(grp)) * 1.5)))
            for idx, node in enumerate(grp):
                col = idx % cols
                sub_row = idx // cols
                node.x = col * x_step
                node.y = (row_idx * 1.4 + sub_row) * y_step

    def _on_node_clicked(self, node: GraphNode) -> None:
        self.select_node(node.node_id)

    def select_node(self, node_id: str) -> None:
        self._selected_node_id = node_id
        for nid, item in self._node_items.items():
            item.set_selected_state(nid == node_id)
        item = self._node_items.get(node_id)
        if not item:
            return
        node = item.node
        self.insp_title.setText(node.label)
        self.insp_status.setText(f"{node.category.upper()} · {node.status}")
        lines = []
        if node.subtitle:
            lines.append(f"Summary: {node.subtitle}")
        for k, v in (node.details or {}).items():
            lines.append(f"{k}: {v}")
        self.insp_body.setPlainText("\n".join(lines) if lines else "No additional metadata.")
        self.node_selected.emit({
            "node_id": node.node_id,
            "label": node.label,
            "category": node.category,
            "status": node.status,
            "details": node.details,
        })

    def _apply_search_filter(self, text: str) -> None:
        q = str(text or "").strip().lower()
        for item in self._node_items.values():
            if not q:
                item.set_search_match(True)
            else:
                hay = f"{item.node.label} {item.node.category} {item.node.status} {item.node.subtitle}".lower()
                item.set_search_match(q in hay)
