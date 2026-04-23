"""
ui/widgets/search_bar.py — Thanh tìm kiếm nổi (Ctrl+F) cho ClickableTranscript

Features:
    - Highlight tất cả kết quả tìm thấy
    - Navigate Prev / Next (Enter / Shift+Enter)
    - Hiện số kết quả: "3 / 12"
    - Esc hoặc nút X để đóng
    - Tự động focus vào ô tìm khi hiện

Usage (trong MainWindow hoặc panel chứa transcript):
    self._search = TranscriptSearchBar(self._transcript_widget, parent=panel)
    # Bắt Ctrl+F ở window:
    QShortcut(QKeySequence("Ctrl+F"), self, self._search.show_and_focus)
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit,
    QPushButton, QLabel,
)
from PyQt6.QtCore    import Qt, QTimer
from PyQt6.QtGui     import (
    QTextCursor, QTextCharFormat,
    QColor, QKeySequence, QShortcut,
)


class TranscriptSearchBar(QWidget):

    def __init__(self, transcript: "QTextEdit", parent: QWidget = None):
        super().__init__(parent)
        self._transcript = transcript
        self._matches:   list[QTextCursor] = []
        self._current    = -1

        self._build_ui()
        self.hide()

        # Shortcuts
        QShortcut(QKeySequence("Escape"),  self, self.hide_and_clear)
        QShortcut(QKeySequence("Return"),  self._input, self._next)
        QShortcut(QKeySequence("Shift+Return"), self._input, self._prev)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Tìm kiếm…")
        self._input.setFixedHeight(30)
        self._input.textChanged.connect(self._on_text_changed)
        lay.addWidget(self._input, 1)

        self._count_lbl = QLabel("")
        self._count_lbl.setFixedWidth(54)
        self._count_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._count_lbl.setStyleSheet("color: #6B7799; font-size: 10px;")
        lay.addWidget(self._count_lbl)

        for symbol, slot, tip in [
            ("↑", self._prev, "Kết quả trước (Shift+Enter)"),
            ("↓", self._next, "Kết quả tiếp (Enter)"),
        ]:
            btn = QPushButton(symbol)
            btn.setFixedSize(28, 28)
            btn.setToolTip(tip)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 1px solid #1C2030;
                    border-radius: 5px;
                    color: #D8E0F0;
                    font-size: 13px;
                }
                QPushButton:hover { background: rgba(59,110,245,0.15); }
            """)
            btn.clicked.connect(slot)
            lay.addWidget(btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setToolTip("Đóng (Esc)")
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                color: #4A5470; font-size: 12px;
            }
            QPushButton:hover { color: #E05252; }
        """)
        close_btn.clicked.connect(self.hide_and_clear)
        lay.addWidget(close_btn)

        self.setStyleSheet("""
            TranscriptSearchBar {
                background: #12151D;
                border: 1px solid #1C2030;
                border-radius: 8px;
            }
        """)
        self.setFixedHeight(46)

    # ── Public API ───────────────────────────────────────────────────────────

    def show_and_focus(self):
        self.show()
        self._input.setFocus()
        self._input.selectAll()

    def hide_and_clear(self):
        self._clear_highlights()
        self._input.clear()
        self._matches.clear()
        self._current = -1
        self._count_lbl.setText("")
        self.hide()
        self._transcript.setFocus()

    # ── Search logic ─────────────────────────────────────────────────────────

    def _on_text_changed(self, text: str):
        # Debounce nhẹ 150ms để không search từng ký tự khi gõ nhanh
        if hasattr(self, "_debounce"):
            self._debounce.stop()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(lambda: self._run_search(text))
        self._debounce.start(150)

    def _run_search(self, query: str):
        self._clear_highlights()
        self._matches.clear()
        self._current = -1

        if not query.strip():
            self._count_lbl.setText("")
            return

        doc = self._transcript.document()
        cursor = QTextCursor(doc)

        # Highlight format — vàng nhạt
        hi_fmt = QTextCharFormat()
        hi_fmt.setBackground(QColor(251, 191, 36, 120))   # amber

        while True:
            cursor = doc.find(query, cursor, QTextDocument_flag())
            if cursor.isNull():
                break
            cursor.mergeCharFormat(hi_fmt)
            self._matches.append(QTextCursor(cursor))

        total = len(self._matches)
        if total == 0:
            self._count_lbl.setText("0 kết quả")
            self._count_lbl.setStyleSheet("color: #E05252; font-size: 10px;")
        else:
            self._count_lbl.setStyleSheet("color: #6B7799; font-size: 10px;")
            self._go_to(0)

    def _go_to(self, idx: int):
        if not self._matches:
            return
        idx = idx % len(self._matches)
        self._current = idx

        # Highlight active — xanh dương đậm hơn
        active_fmt = QTextCharFormat()
        active_fmt.setBackground(QColor(59, 110, 245, 180))
        active_fmt.setForeground(QColor("#FFFFFF"))

        # Reset màu của match trước
        normal_fmt = QTextCharFormat()
        normal_fmt.setBackground(QColor(251, 191, 36, 120))
        normal_fmt.setForeground(QColor())   # reset

        for i, cur in enumerate(self._matches):
            cur.mergeCharFormat(active_fmt if i == idx else normal_fmt)

        # Scroll tới match
        self._transcript.setTextCursor(self._matches[idx])
        self._transcript.ensureCursorVisible()

        total = len(self._matches)
        self._count_lbl.setText(f"{idx + 1} / {total}")

    def _next(self):
        self._go_to(self._current + 1)

    def _prev(self):
        self._go_to(self._current - 1)

    def _clear_highlights(self):
        """Xóa toàn bộ highlight — reset về format mặc định."""
        doc    = self._transcript.document()
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(0, 0, 0, 0))   # transparent
        fmt.setForeground(QColor())              # default color
        cursor.mergeCharFormat(fmt)


def QTextDocument_flag():
    """Helper — trả về flag tìm kiếm case-insensitive."""
    from PyQt6.QtGui import QTextDocument
    return QTextDocument.FindFlag(0)   # 0 = no flags = case-insensitive by default