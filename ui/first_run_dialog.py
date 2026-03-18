"""
first_run_dialog.py — Token loading với priority order:

1. BUNDLED token (inject lúc build) → dùng ngay, không hỏi user
2. .env file cạnh exe               → dùng nếu có
3. os.environ                       → dùng nếu có (dev mode)
4. Không có gì                      → hiện dialog cho user nhập
"""

import os
import sys

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QWidget,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui  import QFont, QDesktopServices


def _env_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    return os.path.join(base, ".env")


def load_env():
    env_path = _env_path()
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())


def save_token(token: str):
    env_path = _env_path()
    lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()
    lines = [l for l in lines if not l.strip().startswith("HF_TOKEN")]
    lines.append(f"HF_TOKEN={token.strip()}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
    os.environ["HF_TOKEN"] = token.strip()
    print(f"[ENV] Saved HF_TOKEN to {env_path}")


def ensure_hf_token(parent=None) -> bool:
    """
    Load token theo priority:
    1. Bundled token (inject lúc build CI)
    2. .env file
    3. os.environ
    4. Dialog nhập tay
    """

    # Priority 1: Bundled token từ CI build
    try:
        from core._bundled_token import BUNDLED_HF_TOKEN
        if BUNDLED_HF_TOKEN and BUNDLED_HF_TOKEN.startswith("hf_"):
            os.environ["HF_TOKEN"] = BUNDLED_HF_TOKEN
            print("[ENV] Using bundled token from build")
            return True
    except ImportError:
        pass   # Không có bundled token → thử cách khác

    # Priority 2 + 3: .env file hoặc os.environ
    load_env()
    if os.environ.get("HF_TOKEN", "").strip():
        print("[ENV] Using token from .env / environment")
        return True

    # Priority 4: Hiện dialog cho user nhập
    print("[ENV] No token found — showing setup dialog")
    dialog = FirstRunDialog(parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


class FirstRunDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Speech Translator — Setup")
        self.setFixedWidth(520)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background: #0D0F14; }
            QLabel  { background: transparent; color: #D8E0F0; }
            QLineEdit {
                background: #12151D; color: #D8E0F0;
                border: 1px solid #1C2030; border-radius: 6px;
                padding: 8px 12px;
                font-family: 'JetBrains Mono', monospace; font-size: 12px;
            }
            QLineEdit:focus { border-color: #3B6EF5; }
        """)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 24)
        lay.setSpacing(16)

        title = QLabel("◎  HuggingFace Token Required")
        title.setFont(QFont("JetBrains Mono", 13))
        title.setStyleSheet("color: #3B6EF5; background: transparent;")
        lay.addWidget(title)

        desc = QLabel(
            "App cần HuggingFace Access Token để tải model nhận diện giọng nói.\n"
            "Token được lưu vào file .env cạnh app, không gửi đi đâu cả."
        )
        desc.setFont(QFont("JetBrains Mono", 9))
        desc.setStyleSheet("color: #4A5470; background: transparent;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet("background: #1C2030;")
        lay.addWidget(div)

        step1 = QLabel("Bước 1 — Accept model tại HuggingFace:")
        step1.setFont(QFont("JetBrains Mono", 9))
        lay.addWidget(step1)

        for url, label in [
            ("https://huggingface.co/pyannote/speaker-diarization-3.1",   "→ pyannote/speaker-diarization-3.1"),
            ("https://huggingface.co/pyannote/speaker-diarization-community-1", "→ pyannote/speaker-diarization-community-1"),
            ("https://huggingface.co/pyannote/segmentation-3.0",          "→ pyannote/segmentation-3.0"),
        ]:
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; color: #60A5FA; border: none;
                    text-align: left; font-family: 'JetBrains Mono', monospace;
                    font-size: 9px; padding: 2px 0;
                }
                QPushButton:hover { color: #93B8FC; }
            """)
            btn.clicked.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))
            lay.addWidget(btn)

        step2 = QLabel("Bước 2 — Tạo token tại huggingface.co/settings/tokens:")
        step2.setFont(QFont("JetBrains Mono", 9))
        lay.addWidget(step2)

        open_hf = QPushButton("→ Mở trang tạo token")
        open_hf.setCursor(Qt.CursorShape.PointingHandCursor)
        open_hf.setStyleSheet("""
            QPushButton {
                background: transparent; color: #60A5FA; border: none;
                text-align: left; font-family: 'JetBrains Mono', monospace;
                font-size: 9px; padding: 2px 0;
            }
            QPushButton:hover { color: #93B8FC; }
        """)
        open_hf.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://huggingface.co/settings/tokens"))
        )
        lay.addWidget(open_hf)

        step3 = QLabel("Bước 3 — Dán token vào đây:")
        step3.setFont(QFont("JetBrains Mono", 9))
        lay.addWidget(step3)

        self._token_input = QLineEdit()
        self._token_input.setPlaceholderText("hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self._token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._token_input.textChanged.connect(self._on_text_changed)
        lay.addWidget(self._token_input)

        show_row = QHBoxLayout()
        self._show_btn = QPushButton("Hiện token")
        self._show_btn.setCheckable(True)
        self._show_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #4A5470; border: none;
                font-size: 9px; font-family: 'JetBrains Mono', monospace;
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

        self._err_lbl = QLabel("")
        self._err_lbl.setFont(QFont("JetBrains Mono", 8))
        self._err_lbl.setStyleSheet("color: #E05252; background: transparent;")
        lay.addWidget(self._err_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_cancel = QPushButton("Thoát")
        btn_cancel.setFixedHeight(36)
        btn_cancel.setFont(QFont("JetBrains Mono", 9))
        btn_cancel.setStyleSheet("""
            QPushButton {
                background: transparent; color: #4A5470;
                border: 1px solid #1C2030; border-radius: 6px; padding: 0 20px;
            }
            QPushButton:hover { color: #D8E0F0; border-color: #232840; }
        """)
        btn_cancel.clicked.connect(self.reject)

        self._btn_save = QPushButton("Lưu và tiếp tục")
        self._btn_save.setFixedHeight(36)
        self._btn_save.setEnabled(False)
        self._btn_save.setFont(QFont("JetBrains Mono", 9))
        self._btn_save.setStyleSheet("""
            QPushButton {
                background: rgba(59,110,245,0.2); color: #93B8FC;
                border: 1px solid rgba(59,110,245,0.4);
                border-radius: 6px; padding: 0 20px;
            }
            QPushButton:hover { background: rgba(59,110,245,0.35); }
            QPushButton:disabled {
                background: transparent; color: #2A3050; border-color: #1C2030;
            }
        """)
        self._btn_save.clicked.connect(self._on_save)

        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
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
        save_token(self._token_input.text().strip())
        self.accept()