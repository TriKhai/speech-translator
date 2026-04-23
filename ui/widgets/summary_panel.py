"""
ui/widgets/summary_panel.py — Collapsible summary panel

Dùng được trong cả Session page lẫn History page.
Tự động chạy SummaryWorker trong background, không block UI.

Usage:
    panel = SummaryPanel(theme_manager)
    layout.addWidget(panel)

    # Khi có segments mới (sau save / khi load history):
    panel.load(segments)

    # Khi đổi theme:
    panel.apply_theme()
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QFrame,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui  import QFont, QColor

from core.summary_service import SummaryWorker, SummaryResult


# ── Sentiment badge colors ────────────────────────────────────────────────────
_SENTIMENT = {
    "positive": ("#1E9654", "rgba(30,150,84,0.12)",  "rgba(30,150,84,0.3)"),
    "negative": ("#E05252", "rgba(224,82,82,0.12)",  "rgba(224,82,82,0.3)"),
    "neutral":  ("#4A80C4", "rgba(74,128,196,0.12)", "rgba(74,128,196,0.3)"),
}

_SENTIMENT_LABEL = {
    "positive": "Tích cực",
    "negative": "Tiêu cực",
    "neutral":  "Trung tính",
}


class SummaryPanel(QWidget):
    """
    Collapsible panel hiển thị summary + action items.
    Mặc định thu gọn, bấm header để expand/collapse.
    """

    summary_ready = pyqtSignal(object)   # SummaryResult

    def __init__(self, theme_manager, parent=None, embedded: bool = False):
        super().__init__(parent)
        self._embedded = embedded
        self._theme    = theme_manager
        self._expanded = False
        self._segments = []
        self._worker   = None
        self._running  = False
        self._result   = None

        self._build_ui()
        self.apply_theme()

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, segments: list[dict], auto_run: bool = True):
        self._segments = segments or []
        self._result   = None
        self._set_state("idle")
        if auto_run and self._segments:
            self._run_summary()

    def clear(self):
        """Xóa sạch UI — gọi khi discard/reset recording."""
        # Dừng worker nếu đang chạy
        if self._running and self._worker:
            try:
                self._worker.done.disconnect()
                self._worker.error.disconnect()
            except Exception:
                pass
            self._worker  = None
            self._running = False

        self._segments = []
        self._result   = None
        self._set_state("idle")

        # Xóa summary text
        self._lbl_summary.setText("")
        self._lbl_summary.setVisible(False)

        # Xóa key points (giữ lại title label ở index 0)
        while self._points_layout.count() > 1:
            item = self._points_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._points_widget.setVisible(False)
        self._divider_points.setVisible(False)

        # Xóa action items (giữ lại title label ở index 0)
        while self._actions_layout.count() > 1:
            item = self._actions_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._actions_widget.setVisible(False)
        self._divider_actions.setVisible(False)

    def apply_theme(self):
        p = self._theme.palette
        if not self._embedded:
            self._header.setStyleSheet(f"""
                QWidget {{
                    background: {p['BG_CARD2']};
                    border: 1px solid {p['BORDER']};
                    border-radius: 8px;
                }}
                QWidget:hover {{ border-color: {p['BORDER2']}; }}
            """)
            self._lbl_title.setStyleSheet(f"color: {p['TEXT_SEC']}; background: transparent;")
            self._lbl_chevron.setStyleSheet(f"color: {p['TEXT_DIM']}; background: transparent;")
            self._btn_regen.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {p['TEXT_DIM']};
                    border: none; padding: 0 4px;
                    font-family: 'JetBrains Mono', monospace; font-size: 11px;
                }}
                QPushButton:hover {{ color: {p['ACCENT']}; }}
            """)
        self._body.setStyleSheet(f"""
            QWidget {{
                background: transparent;
                border: none;
            }}
        """ if self._embedded else f"""
            QWidget {{
                background: {p['BG_CARD']};
                border: 1px solid {p['BORDER']};
                border-top: none;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }}
        """)
        self._lbl_summary.setStyleSheet(
            f"color: {p['TEXT_PRI']}; background: transparent; line-height: 1.6;"
        )
        self._lbl_points_title.setStyleSheet(
            f"color: {p['TEXT_SEC']}; background: transparent; letter-spacing: 2px;"
        )
        self._lbl_actions_title.setStyleSheet(
            f"color: {p['TEXT_SEC']}; background: transparent; letter-spacing: 2px;"
        )
        self._divider_points.setStyleSheet(f"background: {p['BORDER']}; border: none;")
        self._divider_actions.setStyleSheet(f"background: {p['BORDER']}; border: none;")
        # Re-render nếu đang có kết quả
        if self._result and not self._result.error:
            self._render_result(self._result)

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ──────────────────────────────────────────
        self._header = QWidget()
        self._header.setFixedHeight(38)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.mousePressEvent = lambda _: self._toggle()

        # FIX #1: Đặt visibility ngay sau khi tạo header, trước khi build nội dung
        self._header.setVisible(not self._embedded)
        # Khi embedded, header bị ẩn — đặt SizePolicy Fixed/Fixed với height=0
        # để nó không chiếm bất kỳ vertical space nào trong layout
        if self._embedded:
            self._header.setFixedHeight(0)
            self._header.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )

        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(14, 0, 10, 0)
        hl.setSpacing(8)

        icon = QLabel("✦")
        icon.setFont(QFont("JetBrains Mono", 9))
        icon.setStyleSheet("color: #3B6EF5; background: transparent;")

        self._lbl_title = QLabel("Summary")
        self._lbl_title.setFont(QFont("JetBrains Mono", 9))

        self._lbl_status = QLabel("")
        self._lbl_status.setFont(QFont("JetBrains Mono", 8))
        self._lbl_status.setStyleSheet("color: #4A5470; background: transparent;")

        self._btn_regen = QPushButton("↺")
        self._btn_regen.setFixedSize(24, 24)
        self._btn_regen.setToolTip("Tóm tắt lại")
        self._btn_regen.clicked.connect(self._on_regen)
        # FIX #4: Dùng mousePressEvent để chặn bubble lên header._toggle()
        self._btn_regen.mousePressEvent = self._regen_press

        self._lbl_chevron = QLabel("▸")
        self._lbl_chevron.setFont(QFont("JetBrains Mono", 9))

        hl.addWidget(icon)
        hl.addWidget(self._lbl_title)
        hl.addSpacing(8)
        hl.addWidget(self._lbl_status)
        hl.addStretch()
        hl.addWidget(self._btn_regen)
        hl.addWidget(self._lbl_chevron)
        root.addWidget(self._header)

        # ── Body ─────────────────────────────────────────────
        self._body = QWidget()
        self._body.setVisible(True if self._embedded else False)

        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0) if self._embedded else bl.setContentsMargins(14, 12, 14, 14)
        bl.setSpacing(0)

        # ── Overview (summary text) ──────────────────────────
        self._lbl_summary = QLabel("")
        self._lbl_summary.setFont(QFont("JetBrains Mono", 10))
        self._lbl_summary.setWordWrap(True)
        self._lbl_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._lbl_summary.setVisible(False)
        bl.addWidget(self._lbl_summary)

        # ── Divider + Key Points ─────────────────────────────
        # Dùng addSpacing trực tiếp, không dùng wrapper widget
        # để tránh khoảng trắng thừa khi ẩn/hiện
        self._divider_points = QFrame()
        self._divider_points.setFixedHeight(1)
        self._divider_points.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._divider_points.setVisible(False)
        self._sp_before_pts = bl.count()  # lưu index để toggle spacing
        bl.addWidget(self._divider_points)

        self._points_widget = QWidget()
        self._points_widget.setStyleSheet("background: transparent;")
        self._points_widget.setVisible(False)
        self._points_layout = QVBoxLayout(self._points_widget)
        self._points_layout.setContentsMargins(0, 8, 0, 0)
        self._points_layout.setSpacing(4)

        self._lbl_points_title = QLabel("KEY POINTS")
        self._lbl_points_title.setFont(QFont("JetBrains Mono", 8))
        self._points_layout.addWidget(self._lbl_points_title)
        bl.addWidget(self._points_widget)

        # ── Divider + Action Items ────────────────────────────
        self._divider_actions = QFrame()
        self._divider_actions.setFixedHeight(1)
        self._divider_actions.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._divider_actions.setVisible(False)
        bl.addWidget(self._divider_actions)

        self._actions_widget = QWidget()
        self._actions_widget.setStyleSheet("background: transparent;")
        self._actions_widget.setVisible(False)
        self._actions_layout = QVBoxLayout(self._actions_widget)
        self._actions_layout.setContentsMargins(0, 8, 0, 0)
        self._actions_layout.setSpacing(6)

        self._lbl_actions_title = QLabel("ACTION ITEMS")
        self._lbl_actions_title.setFont(QFont("JetBrains Mono", 8))
        self._actions_layout.addWidget(self._lbl_actions_title)
        bl.addWidget(self._actions_widget)

        # Đẩy nội dung lên trên, không để khoảng trắng thừa ở giữa
        bl.addStretch(1)

        root.addWidget(self._body)

    # ── State machine ─────────────────────────────────────────────────────────

    def _set_state(self, state: str, msg: str = ""):
        p = self._theme.palette
        if self._embedded:
            # Không có header/status/regen trong embedded mode
            return
        if state == "idle":
            self._lbl_status.setText("—")
            self._lbl_status.setStyleSheet(f"color: {p['TEXT_DIM']}; background: transparent;")
            self._btn_regen.setVisible(bool(self._segments))
        elif state == "loading":
            self._lbl_status.setText("đang tóm tắt…")
            self._lbl_status.setStyleSheet("color: #4A80C4; background: transparent;")
            self._btn_regen.setVisible(False)
        elif state == "done":
            self._lbl_status.setText("✔")
            self._lbl_status.setStyleSheet(f"color: {p['GREEN']}; background: transparent;")
            self._btn_regen.setVisible(True)
        elif state == "error":
            self._lbl_status.setText(f"✕ {msg}")
            self._lbl_status.setStyleSheet(f"color: {p['DANGER']}; background: transparent;")
            self._btn_regen.setVisible(True)

    # ── Toggle ────────────────────────────────────────────────────────────────

    def _toggle(self):
        if self._embedded:
            return

        self._expanded = not self._expanded
        self._lbl_chevron.setText("▾" if self._expanded else "▸")
        self._body.setVisible(self._expanded)

        p = self._theme.palette
        if self._expanded:
            self._header.setStyleSheet(f"""
                QWidget {{
                    background: {p['BG_CARD2']};
                    border: 1px solid {p['BORDER']};
                    border-bottom: none;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 0px;
                }}
            """)
        else:
            self._header.setStyleSheet(f"""
                QWidget {{
                    background: {p['BG_CARD2']};
                    border: 1px solid {p['BORDER']};
                    border-radius: 8px;
                }}
                QWidget:hover {{ border-color: {p['BORDER2']}; }}
            """)

    # ── Summary runner ────────────────────────────────────────────────────────

    def _run_summary(self):
        if not self._segments or self._running:
            return
        self._running = True
        self._set_state("loading")
        self._worker = SummaryWorker(self._segments, lang="vi", style="bullet")
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_regen(self):
        self._running = False
        self._worker  = None
        self._run_summary()

    def _regen_press(self, event):
        # FIX #4: Gọi accept() và KHÔNG gọi super() để chặn event bubble
        # lên mousePressEvent của _header (tránh gọi _toggle() không mong muốn)
        event.accept()
        self._on_regen()

    def _on_done(self, result: SummaryResult):
        self._running = False
        self._worker  = None
        self._result  = result
        if result.error and result.error == "empty_transcript":
            self._set_state("error", "Không có nội dung")
            return
        self._set_state("done")
        self._render_result(result)
        self.summary_ready.emit(result)
        if not self._embedded and not self._expanded:
            self._toggle()

    def _on_error(self, msg: str):
        self._running = False
        self._worker  = None
        self._set_state("error", msg[:40])

    # ── Render ────────────────────────────────────────────────────────────────

    def _render_result(self, r: SummaryResult):
        p = self._theme.palette

        # Summary text
        self._lbl_summary.setVisible(bool(r.summary))
        self._lbl_summary.setText(r.summary)

        # ── Key Points ───────────────────────────────────────
        while self._points_layout.count() > 1:
            item = self._points_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        for pt in r.key_points[:6]:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rl  = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)

            dot = QLabel("·")
            dot.setFont(QFont("JetBrains Mono", 12))
            dot.setStyleSheet(f"color: {p['ACCENT']}; background: transparent;")
            dot.setFixedWidth(12)

            txt = QLabel(pt)
            txt.setFont(QFont("JetBrains Mono", 9))
            txt.setStyleSheet(f"color: {p['TEXT_PRI']}; background: transparent;")
            txt.setWordWrap(True)
            txt.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

            rl.addWidget(dot)
            rl.addWidget(txt, stretch=1)
            self._points_layout.addWidget(row)

        self._points_widget.setVisible(bool(r.key_points))
        self._divider_points.setVisible(bool(r.key_points))

        # ── Action Items ─────────────────────────────────────
        while self._actions_layout.count() > 1:
            item = self._actions_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        for action in r.action_items[:8]:
            cb = QCheckBox(action)
            cb.setFont(QFont("JetBrains Mono", 9))
            cb.setStyleSheet(f"""
                QCheckBox {{
                    color: {p['TEXT_PRI']};
                    background: transparent;
                    spacing: 8px;
                }}
                QCheckBox::indicator {{
                    width: 14px;
                    height: 14px;
                    border: 1px solid {p['BORDER2']};
                    border-radius: 3px;
                    background: transparent;
                }}
                QCheckBox::indicator:hover {{
                    border-color: {p['ACCENT']};
                }}
                QCheckBox::indicator:checked {{
                    background: {p['ACCENT']};
                    border-color: {p['ACCENT']};
                    image: none;
                }}
                QCheckBox:checked {{
                    color: {p['TEXT_DIM']};
                    text-decoration: line-through;
                }}
            """)
            self._actions_layout.addWidget(cb)

        self._actions_widget.setVisible(bool(r.action_items))
        self._divider_actions.setVisible(bool(r.action_items))