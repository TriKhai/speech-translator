from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtCore    import Qt, pyqtSignal
from PyQt6.QtGui     import (
    QTextCursor, QTextCharFormat, QTextBlockUserData,
    QColor, QFont,
)
from ui.constants import TEXT_PRI, BORDER2, BG_CARD


class _TS(QTextBlockUserData):
    def __init__(self, ts: float):
        super().__init__()
        self.ts = ts


class ClickableTranscript(QTextEdit):
    seek_requested = pyqtSignal(float)   # seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("JetBrains Mono", 11))
        self.setPlaceholderText("Awaiting transcript…")
        self._seek_enabled   = False
        self._last_speaker   = None
        self._active_block_n = -1
        self.setStyleSheet(f"""
            QTextEdit {{
                background: transparent; color: {TEXT_PRI};
                border: none; line-height: 1.7;
                selection-background-color: rgba(59,110,245,0.25);
            }}
            QScrollBar:vertical {{
                background: {BG_CARD}; width: 5px; border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER2}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def set_seek_enabled(self, enabled: bool):
        self._seek_enabled = enabled
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if enabled
            else Qt.CursorShape.IBeamCursor
        )

    def insert_segment(self, text: str, speaker_id: int, color: str, ts: float):
        """Chèn segment kèm timestamp — dùng khi reload từ DB sau Save."""
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)

        if speaker_id != self._last_speaker:
            if self._last_speaker is not None:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(TEXT_PRI))
                cur.insertText("\n", fmt)
            fmt_spk = QTextCharFormat()
            fmt_spk.setForeground(QColor(color))
            fmt_spk.setFontWeight(QFont.Weight.Bold)
            fmt_spk.setFontPointSize(8)
            cur.insertText(f"SPEAKER {speaker_id}\n", fmt_spk)
            self._last_speaker = speaker_id

        # Gắn timestamp vào block
        cur.block().setUserData(_TS(ts))

        fmt_txt = QTextCharFormat()
        fmt_txt.setForeground(QColor(TEXT_PRI))
        fmt_txt.setFontPointSize(11)
        cur.insertText(text + " ", fmt_txt)

        self.setTextCursor(cur)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def highlight_at(self, pos: float):
        """Highlight block gần nhất có ts <= pos."""
        doc   = self.document()
        block = doc.begin()
        best_n, best_ts = -1, -1.0
        while block.isValid():
            d = block.userData()
            if isinstance(d, _TS) and d.ts <= pos and d.ts > best_ts:
                best_ts = d.ts
                best_n  = block.blockNumber()
            block = block.next()
        if best_n == self._active_block_n:
            return
        self._active_block_n = best_n
        sels = []
        if best_n >= 0:
            b = self.document().findBlockByNumber(best_n)
            if b.isValid():
                sel = QTextEdit.ExtraSelection()
                fmt = QTextCharFormat()
                fmt.setBackground(QColor(59, 110, 245, 35))
                fmt.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
                sel.format = fmt
                c = QTextCursor(b)
                c.select(QTextCursor.SelectionType.BlockUnderCursor)
                sel.cursor = c
                sels.append(sel)
        self.setExtraSelections(sels)

    def clear(self):
        self._last_speaker   = None
        self._active_block_n = -1
        self.setExtraSelections([])
        super().clear()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or not self._seek_enabled:
            return
        block = self.cursorForPosition(event.pos()).block()
        while block.isValid():
            d = block.userData()
            if isinstance(d, _TS):
                self.seek_requested.emit(d.ts)
                return
            block = block.previous()