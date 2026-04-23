"""
ui/icons.py — Tập trung tất cả icon dùng qtawesome.
Dùng hàm get(name, color) để lấy icon theo theme.
"""
import qtawesome as qta
from PyQt6.QtCore import QSize

# ── Kích thước chuẩn ──────────────────────────────────
NAV_SIZE  = QSize(18, 18)
BTN_SIZE  = QSize(15, 15)
CARD_SIZE = QSize(14, 14)

# ── Map tên icon → fa5s key ───────────────────────────
_ICONS = {
    # Sidebar nav
    "session":  "fa5s.microphone",
    "history":  "fa5s.history",
    "upload":   "fa5s.upload",
    "settings": "fa5s.cog",

    # Controls
    "record":   "fa5s.circle",
    "stop":     "fa5s.stop-circle",
    "pause":    "fa5s.pause",
    "resume":   "fa5s.play",
    "save":     "fa5s.check",
    "discard":  "fa5s.times",
    "clear":    "fa5s.eraser",

    # History
    "delete":   "fa5s.trash-alt",
    "rename":   "fa5s.pen",
    "reload":   "fa5s.sync-alt",
    "play":     "fa5s.play",

    # Upload
    "browse":   "fa5s.folder-open",
    "process":  "fa5s.bolt",

    # Misc
    "visualizer": "fa5s.wave-square",
    "summary":    "fa5s.star",
    "mindmap":    "fa5s.project-diagram",
    "transcript": "fa5s.align-left",
    "playback":   "fa5s.headphones",
}


def get(name: str, color: str = "#7b82a0") -> "QIcon":
    """Trả về QIcon theo tên và màu."""
    key = _ICONS.get(name)
    if not key:
        return qta.icon("fa5s.circle", color=color)
    return qta.icon(key, color=color)