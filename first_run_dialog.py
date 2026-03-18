"""
first_run_dialog.py — Dialog nhập HF_TOKEN lần đầu chạy app

Đặt tại: ui/first_run_dialog.py

Logic:
    1. Tìm .env cùng thư mục exe (hoặc thư mục project khi dev)
    2. Nếu có HF_TOKEN → load vào os.environ, không hiện dialog
    3. Nếu không có → hiện dialog cho nhập token
    4. Sau khi nhập → lưu vào .env cạnh exe để lần sau tự load
"""

import os
import sys

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui  import QFont, QDesktopServices
from PyQt6.QtCore import QUrl


def _env_path() -> str:
    """Tìm đường dẫn .env — cạnh exe khi đóng gói, cạnh main.py khi dev."""
    if getattr(sys, "frozen", False):
        # Đang chạy từ PyInstaller exe
        base = os.path.dirname(sys.executable)
    else:
        # Đang chạy từ source
        base = os.path.dirname(os.path.abspath(__file__))
        base = os.path.join(base, "..")   # lên thư mục gốc project

    return os.path.join(base, ".env")


def load_env() -> bool:
    """
    Load .env vào os.environ.
    Returns True nếu HF_TOKEN đã có, False nếu cần hỏi user.
    """
    env_path = _env_path()
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

    return bool(os.environ.get("HF_TOKEN", "").strip())


def save_token(token: str):
    """Lưu HF_TOKEN vào .env cạnh exe."""
    env_path = _env_path()
    lines = []

    # Đọc .env cũ nếu có
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    # Xóa dòng HF_TOKEN cũ nếu có
    lines = [l for l in lines if not l.strip().startswith("HF_TOKEN")]

    # Thêm token mới
    lines.append(f"HF_TOKEN={token.strip()}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)

    os.environ["HF_TOKEN"] = token.strip()
    print(f"[ENV] Saved HF_TOKEN to {env_path}")


class FirstRunDialog(QDialog):
    """Dialog nhập HF_TOKEN — hiện khi chưa có token."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Speech Translator — Setup")
        self.setFixedWidth(520)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog {
                background: #0D0F14;
            }
            QLabel {
                background: transparent;
                color: #D8E0F0;
            }
            QLineEdit {
                background: #12151D;
                color: #D8E0F0;
                border: 1px solid #1C2030;
                border-radius: 6px;
                padding: 8px 12px;
                font-family: 'JetBrains Mono', monospace;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #3B6EF5;
            }
        """)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 24)
        lay.setSpacing(16)

        # Icon + Title
        title = QLabel("◎  HuggingFace Token Required")
        title.setFont(QFont("JetBrains Mono", 13))
        title.setStyleSheet("color: #3B6EF5; background: transparent;")
        lay.addWidget(title)

        # Mô tả
        desc = QLabel(
            "App cần HuggingFace Access Token để tải model nhận diện giọng nói.\n"
            "Token được lưu vào file .env cạnh app, không gửi đi đâu cả."
        )
        desc.setFont(QFont("JetBrains Mono", 9))
        desc.setStyleSheet("color: #4A5470; background: transparent;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # Divider
        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet("background: #1C2030;")
        lay.addWidget(div)

        # Step 1
        step1 = QLabel("Bước 1 — Tạo tài khoản và accept model:")
        step1.setFont(QFont("JetBrains Mono", 9))
        step1.setStyleSheet("color: #D8E0F0; background: transparent;")
        lay.addWidget(step1)

        links_layout = QVBoxLayout()
        links_layout.setSpacing(4)
        for url, label in [
            ("https://huggingface.co/pyannote/speaker-diarization-3.1",
             "→ pyannote/speaker-diarization-3.1"),
            ("https://huggingface.co/pyannote/speaker-diarization-community-1",
             "→ pyannote/speaker-diarization-community-1"),
            ("https://huggingface.co/pyannote/segmentation-3.0",
             "→ pyannote/segmentation-3.0"),
        ]:
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #60A5FA;
                    border: none;
                    text-align: left;
                    font-family: 'JetBrains Mono', monospace;
                    font-size: 9px;
                    padding: 2px 0;
                }
                QPushButton:hover { color: #93B8FC; }
            """)
            btn.clicked.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))
            links_layout.addWidget(btn)
        lay.addLayout(links_layout)

        # Step 2
        step2 = QLabel("Bước 2 — Tạo token tại huggingface.co/settings/tokens:")
        step2.setFont(QFont("JetBrains Mono", 9))
        step2.setStyleSheet("color: #D8E0F0; background: transparent;")
        lay.addWidget(step2)

        open_hf = QPushButton("→ Mở trang tạo token")
        open_hf.setCursor(Qt.CursorShape.PointingHandCursor)
        open_hf.setStyleSheet("""
            QPushButton {
                background: transparent; color: #60A5FA;
                border: none; text-align: left;
                font-family: 'JetBrains Mono', monospace;
                font-size: 9px; padding: 2px 0;
            }
            QPushButton:hover { color: #93B8FC; }
        """)
        open_hf.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://huggingface.co/settings/tokens")
            )
        )
        lay.addWidget(open_hf)

        # Step 3 — Input token
        step3 = QLabel("Bước 3 — Dán token vào đây:")
        step3.setFont(QFont("JetBrains Mono", 9))
        step3.setStyleSheet("color: #D8E0F0; background: transparent;")
        lay.addWidget(step3)

        self._token_input = QLineEdit()
        self._token_input.setPlaceholderText("hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self._token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_input.textChanged.connect(self._on_text_changed)
        lay.addWidget(self._token_input)

        # Show/hide token
        show_row = QHBoxLayout()
        self._show_btn = QPushButton("Hiện token")
        self._show_btn.setCheckable(True)
        self._show_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #4A5470;
                border: none; font-size: 9px;
                font-family: 'JetBrains Mono', monospace;
            }
            QPushButton:checked { color: #60A5FA; }
        """)
        self._show_btn.toggled.connect(
            lambda on: self._token_input.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        show_row.addWidget(self._show_btn)
        show_row.addStretch()
        lay.addLayout(show_row)

        # Error label
        self._err_lbl = QLabel("")
        self._err_lbl.setFont(QFont("JetBrains Mono", 8))
        self._err_lbl.setStyleSheet("color: #E05252; background: transparent;")
        lay.addWidget(self._err_lbl)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_cancel = QPushButton("Thoát")
        self._btn_cancel.setFixedHeight(36)
        self._btn_cancel.setFont(QFont("JetBrains Mono", 9))
        self._btn_cancel.setStyleSheet("""
            QPushButton {
                background: transparent; color: #4A5470;
                border: 1px solid #1C2030; border-radius: 6px;
                padding: 0 20px;
            }
            QPushButton:hover { color: #D8E0F0; border-color: #232840; }
        """)
        self._btn_cancel.clicked.connect(self.reject)

        self._btn_save = QPushButton("Lưu và tiếp tục")
        self._btn_save.setFixedHeight(36)
        self._btn_save.setEnabled(False)
        self._btn_save.setFont(QFont("JetBrains Mono", 9))
        self._btn_save.setStyleSheet("""
            QPushButton {
                background: rgba(59,110,245,0.2); color: #93B8FC;
                border: 1px solid rgba(59,110,245,0.4); border-radius: 6px;
                padding: 0 20px;
            }
            QPushButton:hover { background: rgba(59,110,245,0.35); }
            QPushButton:disabled { background: transparent; color: #2A3050;
                                   border-color: #1C2030; }
        """)
        self._btn_save.clicked.connect(self._on_save)

        btn_row.addStretch()
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_save)
        lay.addLayout(btn_row)

    def _on_text_changed(self, text: str):
        valid = text.strip().startswith("hf_") and len(text.strip()) > 10
        self._btn_save.setEnabled(valid)
        self._err_lbl.setText(
            "" if valid or not text.strip()
            else "Token phải bắt đầu bằng 'hf_' và đủ dài"
        )

    def _on_save(self):
        token = self._token_input.text().strip()
        save_token(token)
        self.accept()


def ensure_hf_token(parent=None) -> bool:
    """
    Gọi hàm này trước khi load model.
    Returns True nếu token OK, False nếu user bấm Cancel/đóng dialog.
    """
    if load_env():
        return True   # Đã có token, không cần hỏi

    dialog = FirstRunDialog(parent)
    result = dialog.exec()
    return result == QDialog.DialogCode.Accepted