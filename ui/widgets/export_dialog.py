"""
ui/widgets/export_dialog.py — Dialog chọn format export

Usage:
    dlg = ExportDialog(segments, parent=self)
    dlg.exec()
"""

import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox,
    QFileDialog, QFrame, QWidget,
)
from PyQt6.QtCore  import Qt
from PyQt6.QtGui   import QFont

from core.export_service import ExportService


class ExportDialog(QDialog):

    def __init__(self, segments: list[dict], parent=None):
        super().__init__(parent)
        self._segments = segments
        self.setWindowTitle("Xuất transcript")
        self.setFixedSize(400, 300)
        self.setModal(True)
        self._build_ui()
        self.setStyleSheet("QDialog { background: #0D0F14; } QLabel { background: transparent; }")

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        # Title
        title = QLabel("↗  Xuất transcript")
        title.setFont(QFont("JetBrains Mono", 13))
        title.setStyleSheet("color: #3B6EF5;")
        lay.addWidget(title)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background: #1C2030; border: none; max-height: 1px;")
        lay.addWidget(div)

        # Format row
        row1 = QHBoxLayout()
        lbl1 = QLabel("Định dạng:")
        lbl1.setFont(QFont("JetBrains Mono", 10))
        lbl1.setStyleSheet("color: #D8E0F0;")
        lbl1.setFixedWidth(100)
        row1.addWidget(lbl1)

        self._fmt_box = QComboBox()
        self._fmt_box.addItems([".txt  (Văn bản thuần)", ".srt  (Phụ đề)", ".docx  (Word)"])
        self._fmt_box.setFont(QFont("JetBrains Mono", 10))
        self._fmt_box.setStyleSheet("""
            QComboBox {
                background: #12151D; color: #D8E0F0;
                border: 1px solid #1C2030; border-radius: 6px;
                padding: 6px 10px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #12151D; color: #D8E0F0;
                border: 1px solid #1C2030;
                selection-background-color: rgba(59,110,245,0.2);
            }
        """)
        row1.addWidget(self._fmt_box, 1)
        lay.addLayout(row1)

        # Language row
        row2 = QHBoxLayout()
        lbl2 = QLabel("Ngôn ngữ:")
        lbl2.setFont(QFont("JetBrains Mono", 10))
        lbl2.setStyleSheet("color: #D8E0F0;")
        lbl2.setFixedWidth(100)
        row2.addWidget(lbl2)

        self._lang_box = QComboBox()
        self._lang_box.addItems(["Tiếng Việt (VI)", "Tiếng Anh (EN)", "Song ngữ (VI + EN)"])
        self._lang_box.setFont(QFont("JetBrains Mono", 10))
        self._lang_box.setStyleSheet(self._fmt_box.styleSheet())
        row2.addWidget(self._lang_box, 1)
        lay.addLayout(row2)

        # Info
        seg_count = len(self._segments)
        info = QLabel(f"📄  {seg_count} đoạn transcript sẽ được xuất")
        info.setFont(QFont("JetBrains Mono", 9))
        info.setStyleSheet("color: #4A5470;")
        lay.addWidget(info)

        lay.addStretch()

        # Status
        self._status = QLabel("")
        self._status.setFont(QFont("JetBrains Mono", 9))
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        # Buttons
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch()

        cancel = QPushButton("Hủy")
        cancel.setFixedHeight(34)
        cancel.setFont(QFont("JetBrains Mono", 9))
        cancel.clicked.connect(self.reject)
        cancel.setStyleSheet("""
            QPushButton {
                background: transparent; color: #4A5470;
                border: 1px solid #1C2030; border-radius: 6px; padding: 0 16px;
            }
            QPushButton:hover { color: #D8E0F0; border-color: #232840; }
        """)
        footer.addWidget(cancel)

        export_btn = QPushButton("Xuất file…")
        export_btn.setFixedHeight(34)
        export_btn.setFont(QFont("JetBrains Mono", 9))
        export_btn.clicked.connect(self._on_export)
        export_btn.setStyleSheet("""
            QPushButton {
                background: rgba(59,110,245,0.15);
                color: #93B8FC;
                border: 1px solid rgba(59,110,245,0.4);
                border-radius: 6px; padding: 0 16px;
            }
            QPushButton:hover { background: rgba(59,110,245,0.3); }
        """)
        footer.addWidget(export_btn)
        lay.addLayout(footer)

    def _fmt_ext(self) -> str:
        return [".txt", ".srt", ".docx"][self._fmt_box.currentIndex()]

    def _lang_code(self) -> str:
        return ["vi", "en", "both"][self._lang_box.currentIndex()]

    def _on_export(self):
        if not self._segments:
            self._status.setText("⚠ Không có dữ liệu để xuất.")
            self._status.setStyleSheet("color: #E05252;")
            return

        ext = self._fmt_ext()
        filters = {
            ".txt":  "Text files (*.txt)",
            ".srt":  "SRT subtitle (*.srt)",
            ".docx": "Word Document (*.docx)",
        }[ext]

        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu transcript", f"transcript{ext}", filters
        )
        if not path:
            return

        # Đảm bảo đúng extension
        if not path.endswith(ext):
            path += ext

        try:
            ExportService.export(self._segments, path, lang=self._lang_code())
            self._status.setText(f"✓ Đã lưu: {os.path.basename(path)}")
            self._status.setStyleSheet("color: #34D399;")
        except Exception as e:
            self._status.setText(f"✗ Lỗi: {e}")
            self._status.setStyleSheet("color: #E05252;")