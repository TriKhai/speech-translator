"""
ui/theme.py — Light / Dark / System mode manager

Light theme redesign — mục tiêu:
  - Nền ấm ivory/cream thay vì xám lạnh
  - Typography hierarchy rõ ràng: 3 mức text khác nhau thực sự
  - Accent xanh dương đậm, tương phản tốt trên nền sáng
  - Card nổi lên khỏi background bằng shadow, không chỉ border
  - Border tinh tế, không "nặng" như bản cũ
"""

from PyQt6.QtCore    import QSettings
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui     import QPalette


DARK = {
    "BG_BASE":    "#0D0F14",
    "BG_SIDEBAR": "#090B0F",
    "BG_CARD":    "#12151D",
    "BG_CARD2":   "#181B25",
    "BORDER":     "#1C2030",
    "BORDER2":    "#232840",
    "TEXT_PRI":   "#D8E0F0",
    "TEXT_SEC":   "#4A5470",
    "TEXT_DIM":   "#2A3050",
    "ACCENT":     "#3B6EF5",
    "ACCENT_HO":  "#4F84FF",
    "DANGER":     "#E05252",
    "GREEN":      "#34D399",
    "INPUT_BG":   "#12151D",

    "TEXT_HOVER": "#000000",
    "BG_HOVER": "#ffffff",
    "BORDER_HOVER": "#ffffff",
    "TEXT_ACTIVE": "#000000",
    "BG_ACTIVE": "#000000",
    "BORDER_ACTIVE": "#ffffff",

    "SB_TEXT_HOVER": "#D8E0F0",   # TEXT_PRI
    "SB_BG_HOVER":   "#181B25",   # BG_CARD2
    "SB_TEXT_ACTIVE": "#93B8FC",
    "SB_BG_ACTIVE":   "rgba(59,110,245,0.12)",
    "SB_BORDER_ACTIVE": "#3B6EF5",
}

# ── Light palette — redesigned ────────────────────────────────────────────────
#
#  Nền chính:  #F5F3EE  — ivory ấm, không gắt mắt như pure white
#  Sidebar:    #EDE9E1  — tone đất nhạt, phân biệt với content area
#  Card:       #FFFFFF  — trắng tinh để card nổi lên
#  Card2:      #F0EDE7  — dùng cho hover state, input fill
#
#  Text:
#    PRI  #1A1714  — gần đen, dễ đọc
#    SEC  #6B6560  — xám ấm, đủ tương phản cho label phụ (WCAG AA)
#    DIM  #B0A89E  — cho placeholder, disabled — nhạt hẳn
#
#  Border:
#    BORDER   #E0D9D0  — viền nhẹ giữa các vùng
#    BORDER2  #C8BFB4  — viền interactive (input focus, hover)
#
#  Accent:  #1B5FD4  — xanh dương đậm hơn bản cũ để tương phản tốt trên nền sáng
#  Hover:   #1450B8
#  Danger:  #C0392B  — đỏ đậm vừa đủ
#  Green:   #1A8C5A  — xanh lá đậm
#
LIGHT = {
    "BG_BASE":    "#EDEEF0",
    "BG_SIDEBAR": "#F7F8FA",
    
    "BG_CARD":    "#FFFFFF",
    "BG_CARD2":   "#F0EDE7",
    "BORDER":     "#E0D9D0",
    "BORDER2":    "#C8BFB4",
    "TEXT_PRI":   "#000000",
    "TEXT_SEC":   "#1A1A1A",
    "TEXT_DIM":   "#333333",
    "ACCENT":     "#1B5FD4",
    "ACCENT_HO":  "#1450B8",
    "DANGER":     "#C0392B",
    "GREEN":      "#1A8C5A",
    "INPUT_BG":   "#FFFFFF",

    "TEXT_HOVER": "#000000",
    "BG_HOVER": "#ffffff",
    "BORDER_HOVER": "#000000",
    "TEXT_ACTIVE": "#000000",
    "BG_ACTIVE": "#ffffff",
    "BORDER_ACTIVE": "#000000",

    "SB_TEXT_HOVER":    "#1A1714",
    "SB_BG_HOVER":      "#E2DED8",   # nhạt hơn

    "SB_TEXT_ACTIVE":   "#1A1714",
    "SB_BG_ACTIVE":     "#D4CFC9",   # đậm hơn hover một chút
    "SB_BORDER_ACTIVE": "#1A1714",

}


def _system_is_dark() -> bool:
    palette = QApplication.instance().palette()
    bg = palette.color(QPalette.ColorRole.Window)
    return bg.lightness() < 128


def _make_stylesheet(p: dict) -> str:
    # Light mode dùng box-shadow để card nổi lên thay vì chỉ dùng border
    is_light = p["BG_BASE"] == LIGHT["BG_BASE"]
    card_shadow = (
        "0 1px 3px rgba(0,0,0,0.07), 0 4px 12px rgba(0,0,0,0.05)"
        if is_light else "none"
    )
    sidebar_border = (
        f"border-right: 1px solid {p['BORDER']};"
    )

    return f"""
    QMainWindow, QDialog {{
        background: {p["BG_BASE"]};
    }}
    QWidget {{
        background: transparent;
        color: {p["TEXT_PRI"]};
        font-family: 'JetBrains Mono', 'Consolas', monospace;
    }}
    QWidget#sidebar {{
        background: {p["BG_SIDEBAR"]};
        {sidebar_border}
    }}
    QWidget#card, QFrame#card {{
        background: {p["BG_CARD"]};
        border: 1px solid {p["BORDER"]};
        border-radius: 8px;
    }}
    QScrollBar:vertical {{
        background: {p["BG_CARD"]}; width: 5px; border-radius: 3px;
    }}
    QScrollBar::handle:vertical {{
        background: {p["BORDER2"]}; border-radius: 3px; min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QTextEdit {{
        background: {p["BG_CARD"]};
        color: {p["TEXT_PRI"]};
        border: none;
        selection-background-color: {"rgba(27,95,212,0.15)" if is_light else "rgba(59,110,245,0.25)"};
    }}
    QLineEdit {{
        background: {p["INPUT_BG"]};
        color: {p["TEXT_PRI"]};
        border: 1px solid {p["BORDER"]};
        border-radius: 6px;
        padding: 6px 10px;
    }}
    QLineEdit:focus {{ border-color: {p["ACCENT"]}; }}
    QLabel {{
        color: {p["TEXT_PRI"]};
        background: transparent;
    }}
    QPushButton {{
        background: {p["BG_CARD2"]};
        color: {p["TEXT_PRI"]};
        border: 1px solid {p["BORDER"]};
        border-radius: 6px;
        padding: 6px 14px;
    }}
    QPushButton:hover {{ background: {p["BORDER"]}; }}
    QPushButton:disabled {{ color: {p["TEXT_DIM"]}; }}
    QPushButton#accent {{
        background: {"rgba(27,95,212,0.1)" if is_light else "rgba(59,110,245,0.15)"};
        color: {p["ACCENT"]};
        border: 1px solid {"rgba(27,95,212,0.35)" if is_light else "rgba(59,110,245,0.4)"};
    }}
    QPushButton#accent:hover {{
        background: {"rgba(27,95,212,0.2)" if is_light else "rgba(59,110,245,0.3)"};
    }}
    QSlider::groove:horizontal {{
        height: 4px; background: {p["BORDER"]}; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 14px; height: 14px; margin: -5px 0;
        border-radius: 7px; background: {p["ACCENT"]};
    }}
    QSlider::sub-page:horizontal {{
        background: {p["ACCENT"]}; border-radius: 2px;
    }}
    QComboBox {{
        background: {p["INPUT_BG"]};
        color: {p["TEXT_PRI"]};
        border: 1px solid {p["BORDER"]};
        border-radius: 6px;
        padding: 4px 10px;
    }}
    QComboBox::drop-down {{ border: none; }}
    QComboBox QAbstractItemView {{
        background: {p["BG_CARD"]};
        color: {p["TEXT_PRI"]};
        border: 1px solid {p["BORDER"]};
        selection-background-color: {"rgba(27,95,212,0.12)" if is_light else "rgba(59,110,245,0.2)"};
    }}
    QToolTip {{
        background: {p["BG_CARD"]};
        color: {p["TEXT_PRI"]};
        border: 1px solid {p["BORDER"]};
        padding: 4px 8px;
        border-radius: 4px;
    }}
    """


class ThemeManager:

    MODE_SYSTEM = "system"
    MODE_DARK   = "dark"
    MODE_LIGHT  = "light"

    _SETTINGS_KEY = "app/theme_mode"

    def __init__(self):
        self._settings = QSettings("SpeechTranslator", "App")
        self._mode = self._settings.value(self._SETTINGS_KEY, self.MODE_SYSTEM)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_dark(self) -> bool:
        if self._mode == self.MODE_SYSTEM:
            return _system_is_dark()
        return self._mode == self.MODE_DARK

    @property
    def palette(self) -> dict:
        return DARK if self.is_dark else LIGHT

    def set_mode(self, mode: str, app: QApplication):
        self._mode = mode
        self._settings.setValue(self._SETTINGS_KEY, mode)
        self.apply(app)

    def apply(self, app: QApplication):
        app.setStyleSheet(_make_stylesheet(self.palette))

    def toggle(self, app: QApplication):
        new = self.MODE_LIGHT if self.is_dark else self.MODE_DARK
        self.set_mode(new, app)