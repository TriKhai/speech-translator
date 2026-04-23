"""
ui/widgets/mindmap.py — Horizontal mindmap widget

Horizontal tree layout (left → right), zoom/pan, smooth rendering,
rounded nodes, bezier curves, theme-aware.

Usage:
    w = MindmapWidget(theme_manager)
    layout.addWidget(w)
    w.load(db, recording_id, segments, title="...")
    w.apply_theme()   # khi đổi light/dark
"""

import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLabel, QPushButton, QGraphicsView, QGraphicsScene,
    QGraphicsItem, QGraphicsPathItem, QGraphicsDropShadowEffect,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui  import (
    QFont, QColor, QPen, QBrush, QPainter,
    QFontMetrics, QPainterPath, QWheelEvent,
    QLinearGradient, QRadialGradient,
)

from core.summary_service import MindmapWorker
import qtawesome as qta
from PyQt6.QtCore import QSize


# ── Branch colors ─────────────────────────────────────────────────────────────
_BRANCH_COLORS = ["#60A5FA", "#34D399", "#F59E0B", "#A78BFA", "#F87171"]

# ── Layout constants ──────────────────────────────────────────────────────────
_H_GAP       = 90    # horizontal gap between levels
_V_GAP       = 20    # vertical gap between siblings
_PAD_X       = 20    # node horizontal padding
_PAD_Y       = 10    # node vertical padding
_MAX_W       = 180   # max text wrap width
_CENTER_MINW = 120   # min width of center node
_CORNER      = 10    # border radius for nodes


def _palette_or_default(tm, key: str, fallback: str) -> str:
    if tm is None:
        return fallback
    return tm.palette.get(key, fallback)


# ── Node data ─────────────────────────────────────────────────────────────────

class _Node:
    def __init__(self, label, color, font_size, bold, children=None, level=0):
        self.label     = label
        self.color     = color
        self.font_size = font_size
        self.bold      = bold
        self.children  = children or []
        self.level     = level
        # Set by layout
        self.x = self.y = self.w = self.h = self.subtree_h = 0.0

    def measure(self):
        font = QFont("JetBrains Mono", self.font_size)
        font.setBold(self.bold)
        fm    = QFontMetrics(font)
        lines = _wrap(self.label, fm, _MAX_W)
        lh    = fm.height()
        tw    = max(fm.horizontalAdvance(l) for l in lines)
        th    = lh * len(lines)
        self.w = max(_CENTER_MINW if self.level == 0 else 0, tw + _PAD_X * 2)
        self.h = th + _PAD_Y * 2
        return lines, lh, th


def _wrap(text: str, fm: QFontMetrics, max_w: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if fm.horizontalAdvance(test) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


def _layout(node: _Node, x: float, y_top: float) -> float:
    """Returns subtree height. Sets node.x, node.y."""
    node.measure()
    if not node.children:
        node.x         = x
        node.y         = y_top + node.h / 2
        node.subtree_h = node.h
        return node.h

    child_x  = x + node.w + _H_GAP
    total_h  = 0
    for i, child in enumerate(node.children):
        if i > 0:
            total_h += _V_GAP
        ch = _layout(child, child_x, y_top + total_h)
        total_h += ch

    first_cy = node.children[0].y
    last_cy  = node.children[-1].y
    node.x         = x
    node.y         = (first_cy + last_cy) / 2
    node.subtree_h = total_h
    return total_h


# ── Canvas with zoom / pan ────────────────────────────────────────────────────

class _Canvas(QGraphicsView):

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.TextAntialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._zoom = 1.0

    def wheelEvent(self, event: QWheelEvent):
        delta  = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        new    = self._zoom * factor
        if 0.1 <= new <= 6.0:
            self._zoom = new
            self.scale(factor, factor)

    def fit(self):
        """Fit toàn bộ mindmap vào view."""
        rect = self.scene().itemsBoundingRect()
        if rect.isNull():
            return
        self.fitInView(rect.adjusted(-60, -60, 60, 60),
                       Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()

    def default_view(self):
        """
        Zoom mặc định khi mới load — 80% scale, center vào root node.
        Không fit toàn bộ (tránh bị quá nhỏ khi mindmap lớn).
        """
        self.resetTransform()
        self._zoom = 0.8
        self.scale(0.8, 0.8)
        # Center vào gốc (0, 0) — nơi root node được đặt
        self.centerOn(QPointF(200, 0))

    def zoom_in(self):
        if self._zoom < 6.0:
            self._zoom *= 1.2
            self.scale(1.2, 1.2)

    def zoom_out(self):
        if self._zoom > 0.1:
            self._zoom /= 1.2
            self.scale(1 / 1.2, 1 / 1.2)

    def reset_zoom(self):
        self.resetTransform()
        self._zoom = 1.0
        self.fit()


# ── Renderer ──────────────────────────────────────────────────────────────────

class _Renderer:
    """Draws horizontal tree mindmap onto a QGraphicsScene."""

    def __init__(self, scene: QGraphicsScene, tm):
        self._scene = scene
        self._tm    = tm

    def _p(self, key: str, fallback: str) -> str:
        return _palette_or_default(self._tm, key, fallback)

    def render(self, data: dict):
        self._scene.clear()
        self._draw_background()

        central  = data.get("central", "Mindmap")
        branches = data.get("branches", [])

        if not branches:
            t = self._scene.addText("Không có nội dung.")
            t.setDefaultTextColor(QColor(self._p("TEXT_DIM", "#3A4060")))
            return

        # Build tree
        children = []
        for bi, b in enumerate(branches):
            color = b.get("color", _BRANCH_COLORS[bi % len(_BRANCH_COLORS)])
            gc_list = []
            for c in b.get("children", []):
                gcs = [_Node(gc.get("label", ""), color, 7, False, level=3)
                       for gc in c.get("children", [])]
                gc_list.append(_Node(c.get("label", ""), color, 9, False, gcs, level=2))
            children.append(_Node(b.get("label", ""), color, 10, True, gc_list, level=1))

        root = _Node(central, self._p("ACCENT", "#3B6EF5"), 12, True, children, level=0)

        # Layout — start x so center node is at x=0
        _layout(root, 0, 0)

        # Center root vertically at y=0
        offset_y = -root.y
        self._shift_y(root, offset_y)

        self._draw_tree(root)

    def _shift_y(self, node: _Node, dy: float):
        node.y += dy
        for c in node.children:
            self._shift_y(c, dy)

    def _draw_tree(self, node: _Node, parent: _Node = None):
        if parent is not None:
            self._draw_edge(parent, node)
        self._draw_node(node)
        for child in node.children:
            self._draw_tree(child, node)

    def _draw_node(self, node: _Node):
        x, y, w, h = node.x, node.y, node.w, node.h
        lines, lh, th = node.measure()

        is_root = node.level == 0

        # ── Path with rounded corners ─────────────────────────
        path = QPainterPath()
        rect = QRectF(x, y - h / 2, w, h)
        path.addRoundedRect(rect, _CORNER, _CORNER)

        item = QGraphicsPathItem(path)

        if is_root:
            # Gradient fill for center
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0, QColor(node.color))
            grad.setColorAt(1, QColor(node.color).darker(140))
            item.setBrush(QBrush(grad))
            item.setPen(QPen(QColor(node.color).lighter(130), 0))
        elif node.level == 1:
            # Branch nodes — filled accent with slight transparency
            c = QColor(node.color)
            c.setAlpha(220)
            item.setBrush(QBrush(c))
            item.setPen(QPen(Qt.PenStyle.NoPen))
        else:
            # Child / grandchild — dark bg with colored border
            bg = QColor(self._p("BG_CARD2", "#1A1F2E"))
            item.setBrush(QBrush(bg))
            pen_c = QColor(node.color)
            pen_c.setAlpha(120)
            item.setPen(QPen(pen_c, 1.2))

        self._scene.addItem(item)

        # ── Text ──────────────────────────────────────────────
        font = QFont("JetBrains Mono", node.font_size)
        font.setBold(node.bold)
        fm = QFontMetrics(font)

        if is_root or node.level == 1:
            text_color = QColor("#FFFFFF")
        else:
            text_color = QColor(self._p("TEXT_PRI", "#D8E0F0"))

        for i, line in enumerate(lines):
            ti = self._scene.addText(line)
            ti.setFont(font)
            ti.setDefaultTextColor(text_color)
            lw = fm.horizontalAdvance(line)
            ti.setPos(x + w / 2 - lw / 2, y - th / 2 + i * lh)

    def _draw_edge(self, parent: _Node, child: _Node):
        # Connect right-center of parent to left-center of child
        x1 = parent.x + parent.w
        y1 = parent.y
        x2 = child.x
        y2 = child.y

        ctrl_x = (x1 + x2) / 2

        path = QPainterPath()
        path.moveTo(x1, y1)
        path.cubicTo(ctrl_x, y1, ctrl_x, y2, x2, y2)

        pen_c = QColor(child.color)
        alpha = 180 if child.level == 1 else 100
        pen_c.setAlpha(alpha)
        width = 2.0 if child.level == 1 else 1.5

        edge = QGraphicsPathItem(path)
        edge.setPen(QPen(pen_c, width))
        edge.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._scene.addItem(edge)

    def _draw_background(self):
        """Subtle dot grid."""
        dot_c = QColor(self._p("BG_CARD", "#131720"))
        dot_c.setAlpha(180)
        pen = QPen(dot_c, 1.5)
        brush = QBrush(dot_c)
        step, size = 44, 2
        for x in range(-2200, 2200, step):
            for y in range(-2200, 2200, step):
                self._scene.addEllipse(x - size / 2, y - size / 2,
                                       size, size, pen, brush)


# ── Public MindmapWidget ──────────────────────────────────────────────────────

class MindmapWidget(QWidget):
    """
    Drop-in mindmap panel. Theme-aware, zoom/pan, lazy Groq generation.

    Usage:
        w = MindmapWidget(theme_manager)
        layout.addWidget(w)
        w.load(db, recording_id, segments, title="...")
        w.apply_theme()
    """

    def __init__(self, theme_manager=None, parent=None):
        super().__init__(parent)
        self._theme     = theme_manager
        self._db        = None
        self._rec_id    = None
        self._segments  = []
        self._worker    = None
        self._running   = False
        self._last_data = None
        self._build_ui()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ───────────────────────────────────────────
        self._toolbar = QWidget()
        self._toolbar.setFixedHeight(42)
        self._apply_toolbar_style()

        tl = QHBoxLayout(self._toolbar)
        tl.setContentsMargins(14, 0, 14, 0)
        tl.setSpacing(6)

        self._lbl_badge = QLabel("✦  MINDMAP")
        self._lbl_badge.setFont(QFont("JetBrains Mono", 8))

        self._lbl_title = QLabel("")
        self._lbl_title.setFont(QFont("JetBrains Mono", 8))

        self._btn_zin   = self._tbtn("+",  "Zoom in",  "fa5s.search-plus")
        self._btn_zout  = self._tbtn("−",  "Zoom out", "fa5s.search-minus")
        self._btn_fit   = self._tbtn("⊡",  "Fit to screen", "fa5s.compress-arrows-alt")
        self._btn_regen = self._tbtn("↺",  "Tạo lại mindmap", "fa5s.sync-alt")
        self._btn_regen.setVisible(False)

        self._toolbar_btns = [self._btn_zin, self._btn_zout,
                               self._btn_fit, self._btn_regen]

        self._btn_zin.clicked.connect(lambda: self._canvas.zoom_in())
        self._btn_zout.clicked.connect(lambda: self._canvas.zoom_out())
        self._btn_fit.clicked.connect(lambda: self._canvas.fit())
        self._btn_regen.clicked.connect(self._force_regen)

        tl.addWidget(self._lbl_badge)
        tl.addSpacing(6)
        tl.addWidget(self._lbl_title)
        tl.addStretch()
        tl.addWidget(self._btn_zin)
        tl.addWidget(self._btn_zout)
        tl.addWidget(self._btn_fit)
        tl.addSpacing(4)
        tl.addWidget(self._btn_regen)
        root.addWidget(self._toolbar)

        # ── Stack ─────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._apply_stack_style()

        # 0: loading
        self._loading_w = self._build_loading()
        self._stack.addWidget(self._loading_w)

        # 1: canvas
        self._scene  = QGraphicsScene(self)
        self._canvas = _Canvas(self._scene)
        self._apply_canvas_style()
        self._stack.addWidget(self._canvas)

        # 2: error
        self._error_w, self._lbl_err = self._build_error()
        self._stack.addWidget(self._error_w)

        root.addWidget(self._stack, stretch=1)

        # Spinner
        self._spin_frames = ["◉", "◎", "○", "◎"]
        self._spin_idx    = 0
        self._spin_timer  = QTimer(self)
        self._spin_timer.timeout.connect(self._tick)

    def _build_loading(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(16)

        self._spinner_lbl = QLabel("◉")
        self._spinner_lbl.setFont(QFont("JetBrains Mono", 36))
        self._spinner_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner_lbl.setStyleSheet(
            f"color:{self._p('ACCENT','#3B6EF5')}; background:transparent;"
        )
        lbl1 = QLabel("Đang phân tích và tạo mindmap…")
        lbl1.setFont(QFont("JetBrains Mono", 10))
        lbl1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl1.setStyleSheet(
            f"color:{self._p('TEXT_SEC','#4A5470')}; background:transparent;"
        )
        lbl2 = QLabel("(thường mất 5–15 giây)")
        lbl2.setFont(QFont("JetBrains Mono", 8))
        lbl2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl2.setStyleSheet(
            f"color:{self._p('TEXT_DIM','#2A3050')}; background:transparent;"
        )
        lay.addWidget(self._spinner_lbl)
        lay.addWidget(lbl1)
        lay.addWidget(lbl2)
        return w

    def _build_error(self):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(12)

        icon = QLabel("✕")
        icon.setFont(QFont("JetBrains Mono", 32))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"color:{self._p('TEXT_DIM','#2A3050')}; background:transparent;"
        )
        lbl_err = QLabel("")
        lbl_err.setFont(QFont("JetBrains Mono", 10))
        lbl_err.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_err.setStyleSheet(
            f"color:{self._p('TEXT_SEC','#4A5470')}; background:transparent;"
        )
        btn = QPushButton("↺  Thử lại")
        btn.setFixedSize(130, 34)
        btn.setFont(QFont("JetBrains Mono", 9))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent;
                color:{self._p('ACCENT','#3B6EF5')};
                border:1px solid {self._p('ACCENT','#3B6EF5')};
                border-radius:6px;
            }}
            QPushButton:hover {{
                background:rgba(59,110,245,0.15);
            }}
        """)
        btn.clicked.connect(self._force_regen)
        lay.addWidget(icon)
        lay.addWidget(lbl_err)
        lay.addSpacing(4)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return w, lbl_err

    def _tbtn(self, text, tip, icon_name: str = "") -> QPushButton:
        b = QPushButton()
        b._icon_name = icon_name  # lưu để _apply_tbtn_style dùng
        b.setFixedSize(28, 28)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_name:
            b.setIcon(qta.icon(icon_name, color=self._p('TEXT_SEC','#4A5470')))
            b.setIconSize(QSize(14, 14))
        else:
            b.setText(text)
            b.setFont(QFont("JetBrains Mono", 11))
        self._apply_tbtn_style(b)
        return b

    # ── Theme helpers ─────────────────────────────────────────────────────────

    def _p(self, key: str, fallback: str) -> str:
        return _palette_or_default(self._theme, key, fallback)

    def _apply_toolbar_style(self):
        self._toolbar.setStyleSheet(
            f"background:{self._p('BG_CARD','#131720')};"
            f"border-bottom:1px solid {self._p('BORDER','#1C2030')};"
        )

    def _apply_stack_style(self):
        self._stack.setStyleSheet(
            f"background:{self._p('BG_BASE','#0D0F14')};"
        )

    def _apply_canvas_style(self):
        self._canvas.setStyleSheet(
            f"background:{self._p('BG_BASE','#0D0F14')}; border:none;"
        )

    def _apply_tbtn_style(self, b: QPushButton):
        color_sec = self._p('TEXT_SEC','#4A5470')
        icon_name = getattr(b, '_icon_name', '')
        if icon_name:
            b.setIcon(qta.icon(icon_name, color=color_sec))
            b.setIconSize(QSize(14, 14))
        b.setStyleSheet(f"""
            QPushButton {{
                background:transparent;
                color:{color_sec};
                border:1px solid {self._p('BORDER','#1C2030')};
                border-radius:5px;
            }}
            QPushButton:hover {{
                color:{self._p('TEXT_PRI','#D8E0F0')};
                background:{self._p('BG_CARD2','#181B25')};
                border-color:{self._p('ACCENT','#3B6EF5')};
            }}
        """)

    def apply_theme(self):
        """Gọi từ MainWindow._on_theme_toggled."""
        self._apply_toolbar_style()
        self._apply_stack_style()
        self._apply_canvas_style()
        self._lbl_badge.setStyleSheet(
            f"color:{self._p('ACCENT','#3B6EF5')}; background:transparent; letter-spacing:3px;"
        )
        self._lbl_title.setStyleSheet(
            f"color:{self._p('TEXT_SEC','#4A5470')}; background:transparent;"
        )
        for b in self._toolbar_btns:
            self._apply_tbtn_style(b)
        # Re-render với màu mới
        if self._last_data and self._stack.currentIndex() == 1:
            self._render(self._last_data)

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, db, rec_id: str, segments: list[dict], title: str = ""):
        self._db       = db
        self._rec_id   = rec_id
        self._segments = segments
        self._lbl_title.setText(title)
        self._btn_regen.setVisible(False)

        cached = None
        try:
            cached = db.get_mindmap(rec_id)
        except Exception:
            pass

        if cached and cached.get("branches"):
            self._render(cached)
        else:
            self._generate()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _generate(self):
        if self._running:
            return
        if not self._segments:
            self._lbl_err.setText("Không có transcript để tạo mindmap.")
            self._stack.setCurrentIndex(2)
            return
        self._running = True
        self._stack.setCurrentIndex(0)
        self._spin_timer.start(250)

        self._worker = MindmapWorker(self._segments)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _force_regen(self):
        self._running   = False
        self._worker    = None
        self._last_data = None
        self._generate()

    def _on_done(self, result):
        self._running = False
        self._spin_timer.stop()

        if result.error or not result.branches:
            msg = result.error or "Groq không trả về mindmap hợp lệ."
            self._lbl_err.setText(msg[:100])
            self._stack.setCurrentIndex(2)
            return

        data = {"central": result.central, "branches": result.branches}
        if self._db and self._rec_id:
            try:
                self._db.save_mindmap(self._rec_id, result)
            except Exception as e:
                print(f"[MINDMAP] Save error: {e}")

        self._render(data)

    def _on_error(self, msg: str):
        self._running = False
        self._spin_timer.stop()
        self._lbl_err.setText(f"Lỗi kết nối: {msg[:80]}")
        self._stack.setCurrentIndex(2)

    def _render(self, data: dict):
        self._last_data = data
        renderer = _Renderer(self._scene, self._theme)
        renderer.render(data)
        self._stack.setCurrentIndex(1)
        self._btn_regen.setVisible(True)
        self._lbl_title.setText(data.get("central", ""))

        # Delay để đảm bảo widget đã có kích thước thực, rồi dùng default zoom
        QTimer.singleShot(80, self._canvas.default_view)

    def _tick(self):
        self._spin_idx = (self._spin_idx + 1) % len(self._spin_frames)
        self._spinner_lbl.setText(self._spin_frames[self._spin_idx])

    def showEvent(self, event):
        """Re-center khi widget được hiển thị."""
        super().showEvent(event)
        if self._stack.currentIndex() == 1:
            QTimer.singleShot(50, self._canvas.default_view)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Không auto-fit khi resize — giữ nguyên zoom của user