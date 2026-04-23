from __future__ import annotations

import threading
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QComboBox, QLineEdit,
    QFrame, QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui  import QFont

try:
    import ui.icons as icons
    _HAS_ICONS = True
except ImportError:
    _HAS_ICONS = False


# ── Catalogue ────────────────────────────────────────────────────────────────
WHISPER_MODELS = [
    # Standard
    ("tiny.en",              "tiny.en",          "75 MB",  "Nhanh nhất"),
    # ("base.en",              "base.en",          "142 MB", "Nhanh"),
    # ("small.en",             "small.en",         "466 MB", "Cân bằng"),
    # ("medium.en",            "medium.en",        "1.5 GB", "Chính xác"),
    # ("large-v3",             "large-v3",         "2.9 GB", "Tốt nhất"),
    # Finetune
    # ("./models/tiny.en.fe.ct2",      "Decode",  "local", "Model finetune"),
    # ("./models/tiny.en.lora8",    "LoRA8",      "local", "Model LoRA"),
    # ("./models/tiny.en.lora16",    "LoRA16",      "local", "Model LoRA"),
    ("./models/tiny.en.lora32",    "LoRA 32",      "local", "Model LoRA"),
    ("./models/tiny.en.medical", "Medical",   "local", "Medical domain"),
]


# ─────────────────────────────────────────────────────────────────────────────
class ModelsPage(QWidget):

    model_load_requested = pyqtSignal(str, str, dict)   # (category, model_id, extra)

    def __init__(self, theme_manager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._theme = theme_manager

        self._active:  dict[str, str]  = {"whisper": "tiny.en", "wav2vec2": "", "vosk": ""}
        self._loading: dict[str, bool] = {k: False for k in self._active}

        # ── Unified ASR state ───────────────────────────────────────────
        self._sel_asr_engine  = "whisper"                  # engine đang chọn
        self._sel_asr_model   = WHISPER_MODELS[0][0]       # model_id
        self._sel_asr_custom  = ""                         # path browse tay

        self._spin_frames = ["◐", "◓", "◑", "◒"]
        self._spin_timers: dict[str, QTimer] = {}

        self._progress_bars: dict[str, QProgressBar] = {}
        self._status_labels: dict[str, QLabel]       = {}
        self._active_badges: dict[str, QLabel]       = {}
        self._spin_labels:   dict[str, QLabel]       = {}

        self._build_ui()

    @property
    def _p(self) -> dict:
        return self._theme.palette

    # ═════════════════════════════════════════════════════════════════════
    # Public API — gọi từ MainWindow
    # ═════════════════════════════════════════════════════════════════════

    def on_load_done(self, category: str, success: bool, error: str = ""):
        """Gọi sau khi MainWindow swap model xong (trong main thread qua signal)."""
        self._loading[category] = False
        self._stop_spinner(category)

        bar = self._progress_bars.get(category)
        if bar:
            bar.setVisible(False)

        lbl = self._status_labels.get(category)
        if lbl:
            p = self._p
            if success:
                lbl.setText("✔  Tải thành công")
                lbl.setStyleSheet(f"color:{p['GREEN']}; background:transparent;")
            else:
                lbl.setText(f"✖  {error or 'Lỗi không xác định'}")
                lbl.setStyleSheet("color:#F87171; background:transparent;")
            lbl.setVisible(True)
            QTimer.singleShot(4000, lambda: lbl.setVisible(False))

        self._refresh_badge(category)

    def set_active_model(self, category: str, model_id: str):
        """Cập nhật badge khi swap thành công."""
        self._active[category] = model_id
        self._refresh_badge(category)

    # ═════════════════════════════════════════════════════════════════════
    # UI Build
    # ═════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        p = self._p
        self.setStyleSheet(f"background:{p['BG_BASE']};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(0)

        self._lbl_page_title = QLabel("Models")
        self._lbl_page_title.setFont(QFont("JetBrains Mono", 18))
        self._lbl_page_title.setStyleSheet(
            f"color:{p['TEXT_PRI']}; background:transparent; letter-spacing:1px;"
        )
        outer.addWidget(self._lbl_page_title)
        outer.addSpacing(24)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background:transparent; border:none; }}
            QScrollBar:vertical {{
                background:{p['BG_CARD']}; width:5px; border-radius:3px;
            }}
            QScrollBar::handle:vertical {{
                background:{p['BORDER2']}; border-radius:3px; min-height:20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)

        body = QWidget()
        body.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 12, 0)
        bl.setSpacing(0)

        bl.addWidget(self._build_asr_card())
        bl.addSpacing(24)
        bl.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

    # ─── Local model cards ────────────────────────────────────────────

    def _build_asr_card(self) -> QFrame:
        """Card ASR Engine — chỉ hiển thị Model selector."""
        card, body = self._card_shell(
            "ASR Engine", "whisper",
            "Chọn model nhận dạng giọng nói"
        )
        p = self._p

        def _lbl(text: str, w: int = 70) -> QLabel:
            l = QLabel(text)
            l.setFont(QFont("JetBrains Mono", 10))
            l.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
            l.setFixedWidth(w)
            return l

        # ── Model combo ───────────────────────────────────────────────
        row_model = QHBoxLayout()
        row_model.setSpacing(12)

        model_combo = self._make_combo(
            [(m[0], f"{m[1]}  ({m[2]})  —  {m[3]}") for m in WHISPER_MODELS],
            width=420,
        )
        model_combo.currentIndexChanged.connect(
            lambda i: setattr(self, "_sel_asr_model", WHISPER_MODELS[i][0])
        )

        row_model.addWidget(_lbl("Model:"))
        row_model.addWidget(model_combo)
        row_model.addStretch()
        body.addLayout(row_model)

        body.addSpacing(4)
        body.addLayout(self._apply_row("whisper", self._do_load_asr))
        return card

    def _build_wav2vec2_card(self) -> None:
        pass  # đã gộp vào _build_asr_card

    def _build_whisper_card(self) -> None:
        pass  # đã gộp vào _build_asr_card

    def _build_vosk_card(self) -> None:
        pass  # đã gộp vào _build_asr_card

    # ─── Reusable builders ───────────────────────────────────────────

    def _card_shell(self, title: str, cat: str, desc: str,
                    is_api: bool = False) -> tuple[QFrame, QVBoxLayout]:
        p = self._p
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background:{p['BG_CARD']};
                border:1px solid {p['BORDER']};
                border-radius:10px;
            }}
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(12)

        hdr = QHBoxLayout()
        hdr.setSpacing(10)

        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("JetBrains Mono", 11))
        title_lbl.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent;")

        desc_lbl = QLabel(desc)
        desc_lbl.setFont(QFont("JetBrains Mono", 8))
        desc_lbl.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(title_lbl)
        title_col.addWidget(desc_lbl)
        hdr.addLayout(title_col)
        hdr.addStretch()

        if not is_api:
            badge = QLabel("")
            badge.setFont(QFont("JetBrains Mono", 8))
            badge.setStyleSheet(
                f"color:{p['ACCENT']}; background:rgba(59,110,245,0.1);"
                f"border:1px solid rgba(59,110,245,0.25); border-radius:4px; padding:2px 8px;"
            )
            badge.setVisible(False)
            hdr.addWidget(badge)
            self._active_badges[cat] = badge

        cl.addLayout(hdr)
        cl.addWidget(self._divider())

        body = QVBoxLayout()
        body.setSpacing(8)
        cl.addLayout(body)
        return card, body

    def _device_row(self, cat: str) -> QHBoxLayout:
        p = self._p
        row = QHBoxLayout()
        row.setSpacing(10)
        lbl = QLabel("Device:")
        lbl.setFont(QFont("JetBrains Mono", 9))
        lbl.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        lbl.setFixedWidth(60)
        combo = self._make_combo([(d, d) for d in DEVICES], width=110)
        combo.currentIndexChanged.connect(
            lambda i: setattr(self, f"_sel_{cat}_device", DEVICES[i])
        )
        row.addWidget(lbl)
        row.addWidget(combo)
        row.addStretch()
        return row

    def _apply_row(self, cat: str, callback) -> QHBoxLayout:
        p = self._p
        row = QHBoxLayout()
        row.setSpacing(12)

        btn = QPushButton("Tải model")
        btn.setFixedHeight(34)
        btn.setFont(QFont("JetBrains Mono", 10))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(59,110,245,0.12); color:{p['ACCENT']};
                border:1px solid rgba(59,110,245,0.3); border-radius:6px; padding:0 16px;
            }}
            QPushButton:hover {{ background:rgba(59,110,245,0.22); }}
        """)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setFixedHeight(4)
        bar.setVisible(False)
        bar.setStyleSheet(f"""
            QProgressBar {{ background:{p['BORDER']}; border:none; border-radius:2px; }}
            QProgressBar::chunk {{ background:{p['ACCENT']}; border-radius:2px; }}
        """)

        spin_lbl = QLabel("")
        spin_lbl.setFont(QFont("JetBrains Mono", 10))
        spin_lbl.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        spin_lbl.setVisible(False)

        status_lbl = QLabel("")
        status_lbl.setFont(QFont("JetBrains Mono", 10))
        status_lbl.setStyleSheet(f"color:{p['GREEN']}; background:transparent;")
        status_lbl.setVisible(False)

        self._progress_bars[cat] = bar
        self._status_labels[cat] = status_lbl
        self._spin_labels[cat]   = spin_lbl

        btn.clicked.connect(
            lambda: self._on_load_clicked(cat, spin_lbl, bar, callback)
        )

        row.addWidget(btn)
        row.addWidget(bar, stretch=1)
        row.addWidget(spin_lbl)
        row.addWidget(status_lbl)
        row.addStretch()
        return row

    # ─── Widget factories ────────────────────────────────────────────

    def _make_combo(self, items: list[tuple[str, str]], width: int = 200) -> QComboBox:
        p = self._p
        combo = QComboBox()
        combo.setFixedHeight(34)
        combo.setFixedWidth(width)
        combo.setFont(QFont("JetBrains Mono", 10))
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for _id, label in items:
            combo.addItem(label, userData=_id)
        combo.setStyleSheet(f"""
            QComboBox {{
                background:rgba(59,110,245,0.07); color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER2']}; border-radius:6px; padding:0 10px;
            }}
            QComboBox:hover {{ background:rgba(59,110,245,0.13); }}
            QComboBox::drop-down {{ border:none; width:20px; }}
            QComboBox::down-arrow {{
                border-left:4px solid transparent; border-right:4px solid transparent;
                border-top:5px solid {p['ACCENT']}; margin-right:8px;
            }}
            QComboBox QAbstractItemView {{
                background:{p['BG_CARD']}; color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER']}; border-radius:6px; padding:4px;
                selection-background-color:rgba(59,110,245,0.25); outline:none;
            }}
            QComboBox QAbstractItemView::item {{ padding:6px 12px; border-radius:4px; }}
        """)
        return combo

    def _make_lineedit(self, placeholder: str = "", password: bool = False) -> QLineEdit:
        p  = self._p
        le = QLineEdit()
        le.setFixedHeight(34)
        le.setFont(QFont("JetBrains Mono", 10))
        le.setPlaceholderText(placeholder)
        if password:
            le.setEchoMode(QLineEdit.EchoMode.Password)
        le.setStyleSheet(f"""
            QLineEdit {{
                background:rgba(59,110,245,0.07); color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER2']}; border-radius:6px; padding:0 10px;
            }}
            QLineEdit:focus {{
                border-color:{p['ACCENT']}; background:rgba(59,110,245,0.12);
            }}
        """)
        return le

    def _browse_btn(self, text: str = "Browse…") -> QPushButton:
        p   = self._p
        btn = QPushButton(text)
        btn.setFixedHeight(34)
        btn.setFixedWidth(90)
        btn.setFont(QFont("JetBrains Mono", 9))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{p['TEXT_SEC']};
                border:1px solid {p['BORDER2']}; border-radius:6px;
            }}
            QPushButton:hover {{
                color:{p['TEXT_PRI']}; border-color:{p['ACCENT']};
                background:rgba(59,110,245,0.08);
            }}
        """)
        return btn

    def _path_label(self) -> QLabel:
        p   = self._p
        lbl = QLabel("")
        lbl.setFont(QFont("JetBrains Mono", 8))
        lbl.setStyleSheet(f"color:{p['ACCENT']}; background:transparent;")
        lbl.setVisible(False)
        return lbl

    def _section_label(self, text: str) -> QLabel:
        p   = self._p
        lbl = QLabel(text)
        lbl.setFont(QFont("JetBrains Mono", 10))
        lbl.setStyleSheet(
            f"color:{p['ACCENT']}; background:transparent; letter-spacing:3px;"
        )
        return lbl

    def _divider(self) -> QFrame:
        p   = self._p
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1)
        div.setStyleSheet(f"background:{p['BORDER']}; border:none;")
        return div

    # ─── Load event handlers ─────────────────────────────────────────

    def _on_load_clicked(self, cat: str, spin_lbl: QLabel,
                         bar: QProgressBar, callback):
        """Bật spinner → gọi callback trong thread → MainWindow xử lý thực."""
        if self._loading.get(cat):
            return
        self._loading[cat] = True
        bar.setVisible(True)

        idx   = [0]
        timer = QTimer(self)
        self._spin_timers[cat] = timer

        def _tick():
            idx[0] = (idx[0] + 1) % len(self._spin_frames)
            spin_lbl.setText(f"{self._spin_frames[idx[0]]}  Đang tải…")

        spin_lbl.setText("◐  Đang tải…")
        spin_lbl.setVisible(True)
        timer.timeout.connect(_tick)
        timer.start(150)

        threading.Thread(target=callback, daemon=True).start()

    def _stop_spinner(self, cat: str):
        t = self._spin_timers.pop(cat, None)
        if t:
            t.stop()
        lbl = self._spin_labels.get(cat)
        if lbl:
            lbl.setVisible(False)

    # ─── Emit signals (không load trực tiếp — MainWindow lo) ─────────

    def _do_load_asr(self):
        mid      = self._sel_asr_custom or self._sel_asr_model
        category = self._sel_asr_engine
        self.model_load_requested.emit(category, mid, {})

    def _refresh_badge(self, cat: str):
        badge = self._active_badges.get(cat)
        if not badge:
            return
        mid = self._active.get(cat, "")
        badge.setText(f"active: {mid}" if mid else "")
        badge.setVisible(bool(mid))

    # ═════════════════════════════════════════════════════════════════════
    # Theme 
    # ═════════════════════════════════════════════════════════════════════

    def apply_theme(self):
        p = self._p
        self.setStyleSheet(f"background:{p['BG_BASE']};")
        if hasattr(self, '_lbl_page_title'):
            self._lbl_page_title.setStyleSheet(
                f"color:{p['TEXT_PRI']}; background:transparent; letter-spacing:1px;"
            )