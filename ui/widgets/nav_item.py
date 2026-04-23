from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore    import Qt, QSize
import qtawesome as qta

from ui.constants import TEXT_SEC, ACCENT

_NAV_ICONS = {
    "Session":  "fa5s.microphone",
    "Meeting":  "fa5s.headphones",
    "History":  "fa5s.history",
    "Upload":   "fa5s.upload",
    "Models":   "fa5s.cube",
    "Settings": "fa5s.cog",
}

_COLOR_ACTIVE   = "#93B8FC"
_COLOR_INACTIVE = TEXT_SEC


class NavItem(QPushButton):
    def __init__(self, icon: str, label: str, active: bool = False):
        super().__init__()
        self._icon_char  = icon
        self._label      = label
        self._collapsed  = False

        self.setCheckable(True)
        self.setChecked(active)
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(20, 20))

        self._apply_style()
        self.toggled.connect(lambda _: self._apply_style())

    def set_collapsed(self, collapsed: bool):
        """Gọi từ MainWindow khi toggle sidebar."""
        self._collapsed = collapsed
        self._apply_style()

    def _apply_style(self):
        on    = self.isChecked()
        color = _COLOR_ACTIVE if on else _COLOR_INACTIVE

        fa_key = _NAV_ICONS.get(self._label)
        if fa_key:
            self.setIcon(qta.icon(fa_key, color=color))

        if self._collapsed:
            # Chỉ icon, căn giữa
            self.setText("")
            self.setToolTip(self._label)
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {"rgba(59,110,245,0.12)" if on else "transparent"};
                    border: none;
                    border-left: 2px solid {"#3B6EF5" if on else "transparent"};
                    border-radius: 0;
                    padding: 0;
                }}
                QPushButton:hover {{
                    background: rgba(59,110,245,0.07);
                }}
            """)
        else:
            # Icon + text, căn trái
            self.setText(f"  {self._label.upper()}")
            self.setToolTip("")
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {"rgba(59,110,245,0.12)" if on else "transparent"};
                    color: {color};
                    border: none;
                    border-left: 2px solid {"#3B6EF5" if on else "transparent"};
                    border-radius: 0;
                    padding-left: 16px;
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