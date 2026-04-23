"""
ui/widgets/toolbar.py — Thanh công cụ phía trên transcript panel

Chứa:
    - Font size slider (10–18px)
    - Theme toggle (🌙 / ☀)
    - Nút Export
    - Nút Tóm tắt (Summary)
    - Search bar toggle (🔍 / Ctrl+F)

Usage trong MainWindow:
    self._toolbar = TranscriptToolbar(
        theme_manager = self._theme,
        app           = QApplication.instance(),
        parent        = panel_widget,
    )
    self._toolbar.export_requested.connect(self._on_export)
    self._toolbar.summary_requested.connect(self._on_summary)
    self._toolbar.font_size_changed.connect(self._transcript.setFontSize)
    self._toolbar.search_toggled.connect(self._search_bar.setVisible)
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout,
    QPushButton, QSlider, QLabel, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui  import QFont

from ui.theme import ThemeManager


class TranscriptToolbar(QWidget):

    font_size_changed = pyqtSignal(int)    # 10–18
    theme_toggled     = pyqtSignal(bool)   # True = dark
    export_requested  = pyqtSignal()
    summary_requested = pyqtSignal()
    search_toggled    = pyqtSignal(bool)   # True = show

    def __init__(
        self,
        theme_manager: ThemeManager,
        app,
        parent: QWidget = None,
    ):
        super().__init__(parent)
        self._theme = theme_manager
        self._app   = app
        self._search_visible = False
        self.setFixedHeight(42)
        self._build_ui()

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(8)

        # ── Font size ───────────────────────────────────────────────────
        font_lbl = QLabel("A")
        font_lbl.setStyleSheet("color: #4A5470; font-size: 9px;")
        font_lbl.setFixedWidth(12)
        lay.addWidget(font_lbl)

        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setRange(10, 18)
        self._font_slider.setValue(11)
        self._font_slider.setFixedWidth(90)
        self._font_slider.setToolTip("Cỡ chữ transcript")
        self._font_slider.valueChanged.connect(self.font_size_changed)
        lay.addWidget(self._font_slider)

        font_lbl2 = QLabel("A")
        font_lbl2.setStyleSheet("color: #4A5470; font-size: 14px;")
        font_lbl2.setFixedWidth(16)
        lay.addWidget(font_lbl2)

        lay.addWidget(self._sep())

        # ── Search toggle ────────────────────────────────────────────────
        self._search_btn = self._make_btn("🔍", "Tìm kiếm (Ctrl+F)")
        self._search_btn.setCheckable(True)
        self._search_btn.toggled.connect(self._on_search_toggled)
        lay.addWidget(self._search_btn)

        lay.addStretch()

        # ── Export ───────────────────────────────────────────────────────
        export_btn = self._make_btn("↗", "Xuất transcript")
        export_btn.clicked.connect(self.export_requested)
        lay.addWidget(export_btn)

        # ── Summary ──────────────────────────────────────────────────────
        summary_btn = self._make_btn("✦", "Tóm tắt nội dung")
        summary_btn.clicked.connect(self.summary_requested)
        lay.addWidget(summary_btn)

        lay.addWidget(self._sep())

        # ── Theme toggle ─────────────────────────────────────────────────
        self._theme_btn = self._make_btn(
            "☀" if self._theme.is_dark else "🌙",
            "Chuyển Light/Dark mode",
        )
        self._theme_btn.clicked.connect(self._on_theme_toggle)
        lay.addWidget(self._theme_btn)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _make_btn(self, icon: str, tooltip: str) -> QPushButton:
        btn = QPushButton(icon)
        btn.setFixedSize(QSize(30, 30))
        btn.setToolTip(tooltip)
        btn.setFont(QFont("Segoe UI Emoji", 12))
        btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #1C2030;
                border-radius: 6px;
                color: #4A5470;
            }
            QPushButton:hover {
                background: rgba(59,110,245,0.12);
                color: #93B8FC;
                border-color: rgba(59,110,245,0.4);
            }
            QPushButton:checked {
                background: rgba(59,110,245,0.2);
                color: #3B6EF5;
                border-color: rgba(59,110,245,0.5);
            }
        """)
        return btn

    def _sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedHeight(20)
        sep.setStyleSheet("background: #1C2030; border: none; max-width: 1px;")
        return sep

    def _on_theme_toggle(self):
        self._theme.toggle(self._app)
        self._theme_btn.setText("☀" if self._theme.is_dark else "🌙")
        self.theme_toggled.emit(self._theme.is_dark)

    def _on_search_toggled(self, checked: bool):
        self._search_visible = checked
        self.search_toggled.emit(checked)

    def get_font_size(self) -> int:
        return self._font_slider.value()