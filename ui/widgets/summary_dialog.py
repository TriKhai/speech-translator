"""
ui/widgets/summary_dialog.py — Dialog hiển thị kết quả tóm tắt

Hiện:
    - Spinner khi đang gọi API
    - Summary + key points + topics + sentiment
    - Nút Copy và Close

Usage:
    dlg = SummaryDialog(segments, parent=self)
    dlg.show()
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit,
    QWidget, QFrame, QComboBox,
)
from PyQt6.QtCore  import Qt, QTimer
from PyQt6.QtGui   import QFont, QClipboard
from PyQt6.QtWidgets import QApplication

from core.summary_service import SummaryWorker, SummaryResult


SENTIMENT_EMOJI = {
    "positive": "😊 Tích cực",
    "neutral":  "😐 Trung tính",
    "negative": "😟 Tiêu cực",
}


class SummaryDialog(QDialog):

    def __init__(self, segments: list[dict], parent=None):
        super().__init__(parent)
        self._segments = segments
        self._result:  SummaryResult | None = None
        self._worker:  SummaryWorker | None = None
        self._dot_count = 0

        self.setWindowTitle("Tóm tắt nội dung")
        self.setMinimumSize(560, 480)
        self.setModal(True)
        self._build_ui()
        self._start_summary()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        # Header
        header = QHBoxLayout()
        title  = QLabel("✦  Tóm tắt nội dung")
        title.setFont(QFont("JetBrains Mono", 13))
        title.setStyleSheet("color: #3B6EF5;")
        header.addWidget(title)
        header.addStretch()

        # Lang selector
        self._lang_box = QComboBox()
        self._lang_box.addItems(["Tiếng Việt", "Tiếng Anh", "Song ngữ"])
        self._lang_box.setFixedWidth(120)
        self._lang_box.currentIndexChanged.connect(self._on_lang_changed)
        header.addWidget(self._lang_box)

        lay.addLayout(header)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background: #1C2030; border: none; max-height: 1px;")
        lay.addWidget(div)

        # Status label (spinner)
        self._status = QLabel("Đang phân tích…")
        self._status.setFont(QFont("JetBrains Mono", 9))
        self._status.setStyleSheet("color: #4A5470;")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._status)

        # Content area
        self._content = QTextEdit()
        self._content.setReadOnly(True)
        self._content.setFont(QFont("JetBrains Mono", 10))
        self._content.setStyleSheet("""
            QTextEdit {
                background: #12151D;
                color: #D8E0F0;
                border: 1px solid #1C2030;
                border-radius: 8px;
                padding: 12px;
                line-height: 1.6;
            }
        """)
        lay.addWidget(self._content, 1)

        # Footer buttons
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self._copy_btn = QPushButton("📋 Copy")
        self._copy_btn.setEnabled(False)
        self._copy_btn.setFixedHeight(34)
        self._copy_btn.setFont(QFont("JetBrains Mono", 9))
        self._copy_btn.clicked.connect(self._copy)
        self._copy_btn.setStyleSheet("""
            QPushButton {
                background: rgba(59,110,245,0.1);
                color: #60A5FA;
                border: 1px solid rgba(59,110,245,0.3);
                border-radius: 6px; padding: 0 16px;
            }
            QPushButton:hover { background: rgba(59,110,245,0.25); }
            QPushButton:disabled { color: #2A3050; border-color: #1C2030; background: transparent; }
        """)
        footer.addWidget(self._copy_btn)
        footer.addStretch()

        close_btn = QPushButton("Đóng")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("JetBrains Mono", 9))
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #4A5470;
                border: 1px solid #1C2030; border-radius: 6px; padding: 0 16px;
            }
            QPushButton:hover { color: #D8E0F0; border-color: #232840; }
        """)
        footer.addWidget(close_btn)
        lay.addLayout(footer)

        self.setStyleSheet("QDialog { background: #0D0F14; } QLabel { background: transparent; }")

    # ── Summary logic ────────────────────────────────────────────────────────

    def _lang_code(self) -> str:
        return ["vi", "en", "both"][self._lang_box.currentIndex()]

    def _on_lang_changed(self):
        if self._result:
            # Re-run với ngôn ngữ khác
            self._result = None
            self._start_summary()

    def _start_summary(self):
        self._copy_btn.setEnabled(False)
        self._content.clear()

        # Spinner animation
        self._dot_count = 0
        self._spinner = QTimer(self)
        self._spinner.timeout.connect(self._tick_spinner)
        self._spinner.start(400)

        self._worker = SummaryWorker(self._segments, lang=self._lang_code())
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _tick_spinner(self):
        dots = "." * (self._dot_count % 4)
        self._status.setText(f"Đang phân tích{dots}")
        self._dot_count += 1

    def _on_done(self, result: SummaryResult):
        self._spinner.stop()
        self._result = result

        if result.error and result.error == "empty_transcript":
            self._status.setText("Không có nội dung.")
            return

        self._status.setText("")
        sentiment = SENTIMENT_EMOJI.get(result.sentiment, "😐 Trung tính")

        lines = []

        # Summary
        lines.append("━━  TÓM TẮT  ━━")
        lines.append(result.summary)
        lines.append("")

        # Key points
        if result.key_points:
            lines.append("━━  ĐIỂM CHÍNH  ━━")
            for pt in result.key_points:
                lines.append(f"  •  {pt}")
            lines.append("")

        # Topics
        if result.topics:
            lines.append("━━  CHỦ ĐỀ  ━━")
            lines.append("  " + "  ·  ".join(result.topics))
            lines.append("")

        # Speakers
        if result.speakers:
            lines.append("━━  NGƯỜI NÓI  ━━")
            for spk_id, desc in result.speakers.items():
                lines.append(f"  Speaker {spk_id}:  {desc}")
            lines.append("")

        # Sentiment
        lines.append(f"Cảm xúc chung:  {sentiment}")

        self._content.setPlainText("\n".join(lines))
        self._copy_btn.setEnabled(True)

    def _on_error(self, msg: str):
        self._spinner.stop()
        self._status.setText(f"Lỗi: {msg}")

    def _copy(self):
        QApplication.clipboard().setText(self._content.toPlainText())
        original = self._copy_btn.text()
        self._copy_btn.setText("✓ Đã copy!")
        QTimer.singleShot(1500, lambda: self._copy_btn.setText(original))