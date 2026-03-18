from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore    import Qt

from ui.constants import TEXT_SEC, ACCENT


class NavItem(QPushButton):
    def __init__(self, icon: str, label: str, active: bool = False):
        super().__init__()
        self._icon  = icon
        self._label = label
        self.setCheckable(True)
        self.setChecked(active)
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_style()
        self.toggled.connect(lambda _: self._apply_style())

    def _apply_style(self):
        on = self.isChecked()
        self.setStyleSheet(f"""
            QPushButton {{
                background: {"rgba(59,110,245,0.12)" if on else "transparent"};
                color: {"#93B8FC" if on else TEXT_SEC};
                border: none;
                border-left: 2px solid {"#3B6EF5" if on else "transparent"};
                border-radius: 0;
                padding-left: 22px;
                text-align: left;
                font-family: 'JetBrains Mono', 'Consolas', monospace;
                font-size: 12px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background: rgba(59,110,245,0.07);
                color: #7AA8F8;
            }}
        """)
        self.setText(f"{self._icon}   {self._label.upper()}")