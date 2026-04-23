from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QFrame, QPushButton,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui  import QFont


class SettingsPage(QWidget):

    theme_changed   = pyqtSignal(str)
    sidebar_toggled = pyqtSignal()

    _MODES   = ["system", "light", "dark"]
    _LABELS  = ["Hệ thống", "Sáng", "Tối"]

    def __init__(self, theme_manager, app, parent=None):
        super().__init__(parent)
        self._theme = theme_manager
        self._app   = app
        self._build_ui()
        self._restore()

    @property
    def _p(self) -> dict:
        return self._theme.palette

    def _restore(self):
        mode = self._theme.mode
        idx  = self._MODES.index(mode) if mode in self._MODES else 0
        self._theme_combo.blockSignals(True)
        self._theme_combo.setCurrentIndex(idx)
        self._theme_combo.blockSignals(False)

    def _build_ui(self):
        p = self._p
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(0)

        # Lưu reference để apply_theme() có thể re-apply
        self._title_lbl = QLabel("Settings")
        self._title_lbl.setFont(QFont("JetBrains Mono", 16))
        self._title_lbl.setStyleSheet(
            f"color: {p['TEXT_PRI']}; background: transparent; letter-spacing: 1px;"
        )
        outer.addWidget(self._title_lbl)
        outer.addSpacing(24)

        self._section_appearance = QLabel("APPEARANCE")
        self._section_appearance.setFont(QFont("JetBrains Mono", 10))
        self._section_appearance.setStyleSheet(
            f"color: {p['ACCENT']}; background: transparent; letter-spacing: 3px;"
        )
        outer.addWidget(self._section_appearance)
        outer.addSpacing(12)
        outer.addWidget(self._row_theme())
        outer.addSpacing(16)
        outer.addWidget(self._row_sidebar())
        outer.addSpacing(32)
        outer.addStretch()

    def _row_theme(self) -> QWidget:
        p = self._p
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(4)

        self._lbl_theme_name = QLabel("Theme")
        self._lbl_theme_name.setFont(QFont("JetBrains Mono", 12))
        self._lbl_theme_name.setStyleSheet(
            f"color: {p['TEXT_PRI']}; background: transparent;"
        )
        self._lbl_theme_desc = QLabel("Chọn giao diện sáng, tối, hoặc theo hệ thống")
        self._lbl_theme_desc.setFont(QFont("JetBrains Mono", 10))
        self._lbl_theme_desc.setStyleSheet(
            f"color: {p['TEXT_SEC']}; background: transparent;"
        )
        col.addWidget(self._lbl_theme_name)
        col.addWidget(self._lbl_theme_desc)
        lay.addLayout(col, stretch=1)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(self._LABELS)
        self._theme_combo.setFixedWidth(140)
        self._theme_combo.setFixedHeight(36)
        self._theme_combo.setFont(QFont("JetBrains Mono", 11))
        self._theme_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_combo_style()
        self._theme_combo.currentIndexChanged.connect(self._on_mode_changed)
        lay.addWidget(self._theme_combo)
        return row

    def _row_sidebar(self) -> QWidget:
        p = self._p
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(4)

        self._lbl_sidebar_name = QLabel("Sidebar")
        self._lbl_sidebar_name.setFont(QFont("JetBrains Mono", 12))
        self._lbl_sidebar_name.setStyleSheet(
            f"color: {p['TEXT_PRI']}; background: transparent;"
        )
        self._lbl_sidebar_desc = QLabel("Thu gọn hoặc mở rộng thanh điều hướng")
        self._lbl_sidebar_desc.setFont(QFont("JetBrains Mono", 10))
        self._lbl_sidebar_desc.setStyleSheet(
            f"color: {p['TEXT_SEC']}; background: transparent;"
        )
        col.addWidget(self._lbl_sidebar_name)
        col.addWidget(self._lbl_sidebar_desc)
        lay.addLayout(col, stretch=1)

        self._btn_sidebar = QPushButton("Thu gọn")
        self._btn_sidebar.setFixedWidth(140)
        self._btn_sidebar.setFixedHeight(36)
        self._btn_sidebar.setFont(QFont("JetBrains Mono", 11))
        self._btn_sidebar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_sidebar.clicked.connect(self._on_sidebar_clicked)
        self._apply_sidebar_btn_style()
        lay.addWidget(self._btn_sidebar)
        return row

    # ── Style helpers ─────────────────────────────────────
    def _apply_combo_style(self):
        p = self._p
        self._theme_combo.setStyleSheet(f"""
            QComboBox {{
                background: rgba(232,98,42,0.10);
                color: {p['ACCENT']};
                border: 1px solid rgba(232,98,42,0.4);
                border-radius: 6px;
                padding: 0 10px;
            }}
            QComboBox:hover {{ background: rgba(232,98,42,0.20); }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {p['ACCENT']};
                margin-right: 8px;
            }}
            QComboBox QAbstractItemView {{
                background: {p['BG_CARD']};
                color: {p['TEXT_PRI']};
                border: 1px solid {p['BORDER']};
                border-radius: 6px;
                padding: 4px;
                selection-background-color: rgba(232,98,42,0.15);
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 8px 12px;
                border-radius: 4px;
                font-size: 11pt;
            }}
        """)

    def _apply_sidebar_btn_style(self):
        p = self._p
        self._btn_sidebar.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {p['TEXT_SEC']};
                border: 1px solid {p['BORDER2']};
                border-radius: 6px;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                color: {p['TEXT_PRI']};
                border-color: {p['ACCENT']};
                background: rgba(232,98,42,0.08);
            }}
        """)

    # ── Public: gọi từ MainWindow._on_theme_toggled ───────
    def apply_theme(self):
        """Re-apply TẤT CẢ inline styles — kể cả các label title/desc."""
        p = self._p

        self._title_lbl.setStyleSheet(
            f"color: {p['TEXT_PRI']}; background: transparent; letter-spacing: 1px;"
        )
        self._section_appearance.setStyleSheet(
            f"color: {p['ACCENT']}; background: transparent; letter-spacing: 3px;"
        )
        self._lbl_theme_name.setStyleSheet(
            f"color: {p['TEXT_PRI']}; background: transparent;"
        )
        self._lbl_theme_desc.setStyleSheet(
            f"color: {p['TEXT_SEC']}; background: transparent;"
        )
        self._lbl_sidebar_name.setStyleSheet(
            f"color: {p['TEXT_PRI']}; background: transparent;"
        )
        self._lbl_sidebar_desc.setStyleSheet(
            f"color: {p['TEXT_SEC']}; background: transparent;"
        )
        self._apply_combo_style()
        self._apply_sidebar_btn_style()

    def update_sidebar_btn(self, collapsed: bool):
        self._btn_sidebar.setText("Mở rộng" if collapsed else "Thu gọn")

    def _on_sidebar_clicked(self):
        self.sidebar_toggled.emit()

    def _on_mode_changed(self, idx: int):
        mode = self._MODES[idx]
        self._theme.set_mode(mode, self._app)
        self._apply_combo_style()
        self._apply_sidebar_btn_style()
        self.theme_changed.emit(mode)