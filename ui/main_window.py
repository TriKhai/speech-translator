import os
import sys
import threading
from datetime import datetime

import numpy as np

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QStackedWidget,
    QFrame, QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui  import (
    QFont, QColor, QPalette, QTextCursor, QTextCharFormat,
)

from core.audio_capture        import AudioCapture
from core.vad_processor        import VADProcessor
from core.whisper_service      import WhisperService
from core.speaker_identifier   import SpeakerIdentifier
from core.translation_service  import TranslationService
from core.transcription_worker import TranscriptionWorker
from core.speaker_diarizer     import SpeakerDiarizer
from db.database_service       import DatabaseService

from ui.constants import (
    AUDIO_DIR,
    SPEAKER_COLORS,
    BG_BASE, BG_SIDEBAR, BG_CARD, BG_CARD2,
    BORDER, BORDER2,
    TEXT_PRI, TEXT_SEC, TEXT_DIM,
    ACCENT, ACCENT_HO, DANGER, DANGER_HO,
)
from ui.signals            import AppSignals
from ui.widgets            import AudioVisualizer, NavItem, AudioPlayer
from ui.pages.history_page import HistoryPage

PLATFORM = sys.platform


def _default_audio_device():
    """
    Trả về device phù hợp với platform hiện tại.
    Windows → None  (sounddevice tự dùng default mic)
    Linux   → "default"  (ALSA default, hoặc set cụ thể như "plughw:0,7")
    """
    if PLATFORM == "win32":
        return None
    else:
        # Thay "plughw:0,7" bằng device ALSA của bạn nếu cần,
        # hoặc để "default" cho ALSA tự chọn.
        return os.environ.get("ALSA_DEVICE", "default")


# ══════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    """Cửa sổ chính – khởi tạo UI rồi load model ở background."""

    def __init__(self):
        super().__init__()
        self._is_recording    = False
        self._is_paused       = False
        self._current_wav     = None
        self._saved_rec_id    = None
        self._last_speaker_en = None
        self._last_speaker_vi = None
        self._vi_only_mode    = False
        self._signals         = AppSignals()

        self._signals.english_received.connect(self._append_english)
        self._signals.vietnamese_received.connect(self._append_vietnamese)
        self._signals.status_changed.connect(self._update_status)
        self._signals.model_loaded.connect(self._on_model_loaded)
        self._signals.audio_level.connect(self._on_audio_level)
        self._signals.diarization_done.connect(self._on_diarization_done)

        self._build_ui()
        self._apply_theme()
        threading.Thread(target=self._load_model, daemon=True).start()

    # ── Model loading ──────────────────────────────────────
    def _load_model(self):
        self._signals.status_changed.emit("Loading Whisper…")
        self._whisper = WhisperService(model_path="tiny.en", device="cpu")

        self._signals.status_changed.emit("Loading Speaker…")
        self._speaker = SpeakerIdentifier(device="cpu")

        self._signals.status_changed.emit("Starting Translator…")
        self._translator = TranslationService()

        self._signals.status_changed.emit("Connecting DB…")
        self._db = DatabaseService()

        # ← Thay đổi chính: dùng _default_audio_device()
        self._capture = AudioCapture(device=_default_audio_device())

        self._signals.status_changed.emit("Loading Diarizer…")
        try:
            self._diarizer = SpeakerDiarizer()
        except Exception as e:
            print(f"[UI] Diarizer not available: {e}")
            self._diarizer = None
        self._worker  = TranscriptionWorker(
            whisper       = self._whisper,
            speaker       = self._speaker,
            translator    = self._translator,
            db            = self._db,
            on_english    = self._on_english,
            on_vietnamese = self._on_vietnamese,
        )
        self._signals.model_loaded.emit()

    def _on_model_loaded(self):
        self._stack.setCurrentWidget(self._main_page)
        self._history_page.db = self._db
        self._update_status("ready")
        self._btn_record.setEnabled(True)

    # ── UI build ───────────────────────────────────────────
    def _build_ui(self):
        self.setWindowTitle("Speech Translator")
        self.setMinimumSize(1080, 660)
        self.resize(1280, 780)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)
        self._stack.addWidget(self._build_loading_page())
        self._stack.addWidget(self._build_main_page())
        self._stack.setCurrentWidget(self._loading_page)

    def _build_loading_page(self) -> QWidget:
        self._loading_page = QWidget()
        self._loading_page.setStyleSheet(f"background:{BG_BASE};")
        lay = QVBoxLayout(self._loading_page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("◎")
        icon.setFont(QFont("JetBrains Mono", 56))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color:{ACCENT}; background:transparent;")

        title = QLabel("SPEECH TRANSLATOR")
        title.setFont(QFont("JetBrains Mono", 14))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color:{TEXT_PRI}; letter-spacing:8px; background:transparent;")

        self._lbl_load_sub = QLabel("Initializing…")
        self._lbl_load_sub.setFont(QFont("JetBrains Mono", 9))
        self._lbl_load_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_load_sub.setStyleSheet(f"color:{TEXT_SEC}; background:transparent;")
        self._signals.status_changed.connect(lambda s: self._lbl_load_sub.setText(s))

        lay.addWidget(icon)
        lay.addSpacing(10)
        lay.addWidget(title)
        lay.addSpacing(6)
        lay.addWidget(self._lbl_load_sub)
        return self._loading_page

    def _build_main_page(self) -> QWidget:
        self._main_page = QWidget()
        self._main_page.setStyleSheet(f"background:{BG_BASE};")

        root = QHBoxLayout(self._main_page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        self._page_stack = QStackedWidget()
        self._page_stack.setStyleSheet("background:transparent;")
        self._page_stack.addWidget(self._build_content())
        self._history_page = HistoryPage(db=None)
        self._page_stack.addWidget(self._history_page)

        self._nav_session.clicked.connect(self._show_session_page)
        self._nav_history.clicked.connect(self._show_history_page)
        root.addWidget(self._page_stack, stretch=1)
        return self._main_page

    def _build_sidebar(self) -> QWidget:
        sb = QWidget()
        sb.setFixedWidth(196)
        sb.setStyleSheet(f"""
            QWidget {{
                background:{BG_SIDEBAR};
                border-right:1px solid {BORDER};
            }}
        """)
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_logo())
        lay.addSpacing(12)

        self._nav_session  = NavItem("▶", "Session",  active=True)
        self._nav_history  = NavItem("◷", "History")
        self._nav_models   = NavItem("◈", "Models")
        self._nav_settings = NavItem("◉", "Settings")
        for btn in [self._nav_session, self._nav_history,
                    self._nav_models,  self._nav_settings]:
            lay.addWidget(btn)

        lay.addStretch()
        lay.addWidget(self._build_sidebar_footer())
        return sb

    def _build_logo(self) -> QWidget:
        logo = QWidget()
        logo.setFixedHeight(62)
        logo.setStyleSheet(f"background:{BG_SIDEBAR}; border-bottom:1px solid {BORDER};")
        ll = QHBoxLayout(logo)
        ll.setContentsMargins(18, 0, 18, 0)
        ll.setSpacing(10)

        li = QLabel("◎")
        li.setFont(QFont("JetBrains Mono", 20))
        li.setStyleSheet(f"color:{ACCENT}; border:none;")

        lt  = QWidget()
        ltl = QVBoxLayout(lt)
        ltl.setContentsMargins(0, 0, 0, 0)
        ltl.setSpacing(0)
        lt.setStyleSheet("border:none; background:transparent;")
        l1 = QLabel("SPEECH")
        l1.setFont(QFont("JetBrains Mono", 9))
        l1.setStyleSheet(f"color:{TEXT_PRI}; letter-spacing:3px; background:transparent;")
        l2 = QLabel("TRANSLATOR")
        l2.setFont(QFont("JetBrains Mono", 7))
        l2.setStyleSheet(f"color:{TEXT_SEC}; letter-spacing:2px; background:transparent;")
        ltl.addWidget(l1)
        ltl.addWidget(l2)

        ll.addWidget(li)
        ll.addWidget(lt)
        ll.addStretch()
        return logo

    def _build_sidebar_footer(self) -> QWidget:
        foot = QWidget()
        foot.setFixedHeight(50)
        foot.setStyleSheet(f"background:{BG_SIDEBAR}; border-top:1px solid {BORDER};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 0, 18, 0)
        fl.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color:{TEXT_SEC}; font-size:9px; border:none;")
        self._lbl_status = QLabel("Loading…")
        self._lbl_status.setFont(QFont("JetBrains Mono", 8))
        self._lbl_status.setStyleSheet(f"color:{TEXT_SEC}; border:none;")

        fl.addWidget(self._dot)
        fl.addWidget(self._lbl_status)
        fl.addStretch()
        return foot

    def _build_content(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background:{BG_BASE};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        title_row = QHBoxLayout()
        pt = QLabel("Session")
        pt.setFont(QFont("JetBrains Mono", 18))
        pt.setStyleSheet(f"color:{TEXT_PRI}; background:transparent; letter-spacing:1px;")
        title_row.addWidget(pt)
        title_row.addStretch()

        self._btn_toggle_en = self._toggle_pill("🇬🇧  EN", active=True)
        self._btn_toggle_vi = self._toggle_pill("🇻🇳  VI", active=True)
        self._btn_toggle_en.clicked.connect(self._toggle_en_panel)
        self._btn_toggle_vi.clicked.connect(self._toggle_vi_panel)
        title_row.addWidget(self._btn_toggle_en)
        title_row.addSpacing(6)
        title_row.addWidget(self._btn_toggle_vi)
        lay.addLayout(title_row)

        lay.addWidget(self._build_visualizer_card())

        self._transcript_row = QWidget()
        self._transcript_row.setStyleSheet("background:transparent;")
        bl = QHBoxLayout(self._transcript_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(14)
        self._card_en, self._txt_en = self._transcript_panel_card("🇬🇧  ENGLISH",    "#60A5FA")
        self._card_vi, self._txt_vi = self._transcript_panel_card("🇻🇳  TIẾNG VIỆT", "#34D399")
        bl.addWidget(self._card_en)
        bl.addWidget(self._card_vi)
        # Diarize overlay — hiện lên trên transcript khi đang xử lý
        self._diarize_overlay = self._build_diarize_overlay()
        self._diarize_overlay.setVisible(False)

        # Wrap transcript_row + overlay vào container
        transcript_container = QWidget()
        transcript_container.setStyleSheet("background:transparent;")
        tc_lay = QVBoxLayout(transcript_container)
        tc_lay.setContentsMargins(0, 0, 0, 0)
        tc_lay.setSpacing(0)
        tc_lay.addWidget(self._transcript_row, stretch=1)
        tc_lay.addWidget(self._diarize_overlay)
        lay.addWidget(transcript_container, stretch=1)
        return w

    def _build_visualizer_card(self) -> QFrame:
        viz_card = QFrame()
        viz_card.setStyleSheet(f"""
            QFrame {{
                background:{BG_CARD};
                border:1px solid {BORDER};
                border-radius:10px;
            }}
        """)
        vc_lay = QVBoxLayout(viz_card)
        vc_lay.setContentsMargins(18, 14, 18, 14)
        vc_lay.setSpacing(10)

        ch = QHBoxLayout()
        ch_icon = QLabel("◈")
        ch_icon.setFont(QFont("JetBrains Mono", 11))
        ch_icon.setStyleSheet(f"color:{ACCENT}; background:transparent;")
        ch_title = QLabel("Audio Visualizer")
        ch_title.setFont(QFont("JetBrains Mono", 11))
        ch_title.setStyleSheet(f"color:{TEXT_PRI}; background:transparent;")
        self._lbl_rec_status = QLabel("Idle — awaiting input")
        self._lbl_rec_status.setFont(QFont("JetBrains Mono", 8))
        self._lbl_rec_status.setStyleSheet(f"color:{TEXT_SEC}; background:transparent;")
        ch.addWidget(ch_icon)
        ch.addSpacing(6)
        ch.addWidget(ch_title)
        ch.addStretch()
        ch.addWidget(self._lbl_rec_status)
        vc_lay.addLayout(ch)
        vc_lay.addWidget(self._divider())

        self._viz_stack = QStackedWidget()
        self._viz_stack.setStyleSheet("background:transparent;")
        self._visualizer = AudioVisualizer()
        self._player     = AudioPlayer()
        self._player.new_recording_requested.connect(self._on_new_recording)
        self._viz_stack.addWidget(self._visualizer)
        self._viz_stack.addWidget(self._player)
        self._viz_stack.setCurrentIndex(0)
        vc_lay.addWidget(self._viz_stack)
        vc_lay.addWidget(self._divider())
        vc_lay.addLayout(self._build_controls())
        return viz_card

    def _build_controls(self) -> QHBoxLayout:
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        def _ghost_btn(text):
            b = QPushButton(text)
            b.setFixedHeight(38)
            b.setFont(QFont("JetBrains Mono", 9))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background:transparent; color:{TEXT_SEC};
                    border:1px solid {BORDER2}; border-radius:6px;
                    padding:0 16px; letter-spacing:1px;
                }}
                QPushButton:hover {{ color:{TEXT_PRI}; border-color:#384060; }}
                QPushButton:disabled {{ color:{TEXT_DIM}; border-color:{BORDER}; }}
            """)
            return b

        self._btn_record = QPushButton("⬤   START RECORDING")
        self._btn_record.setFixedHeight(38)
        self._btn_record.setEnabled(False)
        self._btn_record.setFont(QFont("JetBrains Mono", 9))
        self._btn_record.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_record.clicked.connect(self._on_btn_record)
        self._set_btn_start()

        self._btn_pause = _ghost_btn("⏸   PAUSE")
        self._btn_pause.setEnabled(False)
        self._btn_pause.clicked.connect(self._on_btn_pause)

        self._btn_save = QPushButton("✔   SAVE")
        self._btn_save.setFixedHeight(38)
        self._btn_save.setEnabled(False)
        self._btn_save.setFont(QFont("JetBrains Mono", 9))
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(self._on_btn_save)
        self._btn_save.setStyleSheet(f"""
            QPushButton {{
                background:rgba(52,211,153,0.15); color:#34D399;
                border:1px solid rgba(52,211,153,0.3); border-radius:6px;
                padding:0 16px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(52,211,153,0.25); }}
            QPushButton:disabled {{ background:transparent; color:{TEXT_DIM};
                                    border-color:{BORDER}; }}
        """)

        self._btn_discard = _ghost_btn("✕   DISCARD")
        self._btn_discard.setEnabled(False)
        self._btn_discard.clicked.connect(self._on_btn_discard)

        self._btn_clear = _ghost_btn("⊘   CLEAR")
        self._btn_clear.clicked.connect(self._clear_transcript)

        ctrl.addWidget(self._btn_record)
        ctrl.addWidget(self._btn_pause)
        ctrl.addWidget(self._btn_save)
        ctrl.addWidget(self._btn_discard)
        ctrl.addStretch()
        ctrl.addWidget(self._btn_clear)
        return ctrl

    def _build_diarize_overlay(self) -> QWidget:
        """Overlay loading hiện lên khi đang chạy post-save diarization."""
        w = QWidget()
        w.setStyleSheet(f"""
            QWidget {{
                background: rgba(13, 15, 20, 0.92);
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
        """)
        w.setFixedHeight(80)

        lay = QHBoxLayout(w)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(16)

        # Spinner (dots animation via QTimer)
        self._spinner_lbl = QLabel("◉")
        self._spinner_lbl.setFont(QFont("JetBrains Mono", 18))
        self._spinner_lbl.setStyleSheet(f"color:{ACCENT}; background:transparent; border:none;")
        self._spinner_lbl.setFixedWidth(32)

        # Text + progress bar
        text_col = QWidget()
        text_col.setStyleSheet("background:transparent; border:none;")
        tc = QVBoxLayout(text_col)
        tc.setContentsMargins(0, 0, 0, 0)
        tc.setSpacing(6)

        self._diarize_lbl = QLabel("Re-analyzing speakers…")
        self._diarize_lbl.setFont(QFont("JetBrains Mono", 9))
        self._diarize_lbl.setStyleSheet(f"color:{TEXT_PRI}; background:transparent; border:none; letter-spacing:1px;")

        self._diarize_sub = QLabel("pyannote diarization pipeline running")
        self._diarize_sub.setFont(QFont("JetBrains Mono", 8))
        self._diarize_sub.setStyleSheet(f"color:{TEXT_SEC}; background:transparent; border:none;")

        self._diarize_bar = QProgressBar()
        self._diarize_bar.setRange(0, 0)   # indeterminate / marquee
        self._diarize_bar.setFixedHeight(3)
        self._diarize_bar.setTextVisible(False)
        self._diarize_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {BORDER};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {ACCENT};
                border-radius: 2px;
            }}
        """)

        tc.addWidget(self._diarize_lbl)
        tc.addWidget(self._diarize_sub)
        tc.addWidget(self._diarize_bar)

        lay.addWidget(self._spinner_lbl)
        lay.addWidget(text_col, stretch=1)

        # Spinner animation
        self._spinner_frames = ["◉", "◎", "○", "◎"]
        self._spinner_idx    = 0
        self._spinner_timer  = QTimer()
        self._spinner_timer.timeout.connect(self._tick_spinner)

        return w

    def _tick_spinner(self):
        self._spinner_idx = (self._spinner_idx + 1) % len(self._spinner_frames)
        self._spinner_lbl.setText(self._spinner_frames[self._spinner_idx])

    def _show_diarize_overlay(self):
        self._diarize_overlay.setVisible(True)
        self._spinner_timer.start(300)

    def _hide_diarize_overlay(self):
        self._spinner_timer.stop()
        self._diarize_overlay.setVisible(False)

    def _toggle_pill(self, label: str, active: bool = True) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setFixedHeight(28)
        btn.setFont(QFont("JetBrains Mono", 8))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        def _style():
            on = btn.isChecked()
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {"rgba(59,110,245,0.15)" if on else "transparent"};
                    color: {"#93B8FC" if on else TEXT_SEC};
                    border: 1px solid {"rgba(59,110,245,0.4)" if on else BORDER2};
                    border-radius: 14px;
                    padding: 0 12px;
                    letter-spacing: 1px;
                }}
                QPushButton:hover {{
                    background: rgba(59,110,245,0.1);
                    color: #7AA8F8;
                }}
            """)
        _style()
        btn.toggled.connect(lambda _: _style())
        return btn

    def _transcript_panel_card(self, title: str, color: str):
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background:{BG_CARD};
                border:1px solid {BORDER};
                border-radius:10px;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        hdr = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setFont(QFont("JetBrains Mono", 9))
        lbl.setStyleSheet(f"color:{color}; background:transparent; letter-spacing:2px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addWidget(self._divider())

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("JetBrains Mono", 11))
        txt.setPlaceholderText("Awaiting transcript…")
        txt.setStyleSheet(f"""
            QTextEdit {{
                background:transparent; color:{TEXT_PRI};
                border:none; line-height:1.7;
                selection-background-color:rgba(59,110,245,0.25);
            }}
            QScrollBar:vertical {{
                background:{BG_CARD}; width:5px; border-radius:3px;
            }}
            QScrollBar::handle:vertical {{
                background:{BORDER2}; border-radius:3px; min-height:20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height:0;
            }}
        """)
        lay.addWidget(txt, stretch=1)
        return card, txt

    def _toggle_en_panel(self):
        self._card_en.setVisible(self._btn_toggle_en.isChecked())

    def _toggle_vi_panel(self):
        self._card_vi.setVisible(self._btn_toggle_vi.isChecked())

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.HLine)
        d.setFixedHeight(1)
        d.setStyleSheet(f"background:{BORDER}; border:none;")
        return d

    def _apply_theme(self):
        self.setStyleSheet(f"QMainWindow {{ background:{BG_BASE}; }}")
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window,     QColor(BG_BASE))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRI))
        self.setPalette(pal)

    # ── Button state helpers ───────────────────────────────
    def _set_btn_start(self):
        self._btn_record.setText("⬤   START RECORDING")
        self._btn_record.setStyleSheet(f"""
            QPushButton {{
                background:{ACCENT}; color:#fff; border:none;
                border-radius:6px; padding:0 24px; letter-spacing:2px;
            }}
            QPushButton:hover   {{ background:{ACCENT_HO}; }}
            QPushButton:pressed {{ background:#2A5CE0; }}
            QPushButton:disabled {{ background:{BG_CARD2}; color:{TEXT_SEC}; }}
        """)

    def _set_btn_resume(self):
        self._btn_record.setText("⬤   RESUME")
        self._btn_record.setStyleSheet(f"""
            QPushButton {{
                background:rgba(59,110,245,0.2); color:{ACCENT_HO};
                border:1px solid {ACCENT}; border-radius:6px;
                padding:0 24px; letter-spacing:2px;
            }}
            QPushButton:hover {{ background:rgba(59,110,245,0.35); }}
        """)

    def _set_btn_recording(self):
        self._btn_record.setText("◼   RECORDING…")
        self._btn_record.setStyleSheet(f"""
            QPushButton {{
                background:{DANGER}; color:#fff; border:none;
                border-radius:6px; padding:0 24px; letter-spacing:2px;
            }}
            QPushButton:hover {{ background:{DANGER_HO}; }}
        """)

    # ── Button handlers ────────────────────────────────────
    def _on_btn_record(self):
        if not self._is_recording and not self._is_paused:
            self._start_recording()
        elif self._is_paused:
            self._resume_recording()

    def _on_btn_pause(self):
        if self._is_recording and not self._is_paused:
            self._pause_recording()

    def _on_btn_save(self):
        self._save_recording()

    def _on_btn_discard(self):
        self._discard_recording()

    # ── Recording FSM ──────────────────────────────────────
    def _start_recording(self):
        self._is_recording    = True
        self._is_paused       = False
        self._last_speaker_en = None
        self._last_speaker_vi = None

        self._set_btn_recording()
        self._btn_pause.setEnabled(True)
        self._btn_save.setEnabled(True)
        self._btn_discard.setEnabled(True)
        self._btn_record.setEnabled(True)

        self._lbl_rec_status.setText("● Recording — live")
        self._lbl_rec_status.setStyleSheet("color:#F87171; background:transparent;")
        self._dot.setStyleSheet("color:#F87171; font-size:9px; border:none;")
        self._update_status("recording")
        self._visualizer.set_active(True)

        os.makedirs(AUDIO_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._current_wav = os.path.join(AUDIO_DIR, f"{timestamp}.wav")
        self._db.start_recording(audio_path=self._current_wav)

        self._vad = VADProcessor(
            on_speech_chunk=self._on_speech_chunk,
            threshold=0.25, min_speech_ms=200,
            min_silence_ms=600, max_speech_ms=1500,
        )
        self._speaker.reset()
        self._worker.start()
        self._capture.start(on_data=self._on_audio_data, wav_path=self._current_wav)

    def _pause_recording(self):
        self._is_paused = True
        self._capture.pause()
        self._vad.reset()
        self._set_btn_resume()
        self._btn_pause.setEnabled(False)
        self._lbl_rec_status.setText("⏸ Paused")
        self._lbl_rec_status.setStyleSheet(f"color:{TEXT_SEC}; background:transparent;")
        self._dot.setStyleSheet(f"color:{TEXT_SEC}; font-size:9px; border:none;")
        self._update_status("paused")
        self._visualizer.set_active(False)

    def _resume_recording(self):
        self._is_paused = False
        self._capture.resume()
        self._set_btn_recording()
        self._btn_pause.setEnabled(True)
        self._lbl_rec_status.setText("● Recording — live")
        self._lbl_rec_status.setStyleSheet("color:#F87171; background:transparent;")
        self._dot.setStyleSheet("color:#F87171; font-size:9px; border:none;")
        self._update_status("recording")
        self._visualizer.set_active(True)

    def _save_recording(self):
        self._is_recording = False
        self._is_paused    = False
        self._capture.stop()
        if hasattr(self, "_vad"):
            self._vad.reset()
        self._worker.stop()

        self._saved_rec_id = self._db._current_rec_id
        self._db.stop_recording()

        self._set_btn_start()
        self._btn_pause.setEnabled(False)
        self._btn_save.setEnabled(False)
        self._btn_discard.setEnabled(False)
        self._dot.setStyleSheet(f"color:{TEXT_SEC}; font-size:9px; border:none;")

        if self._current_wav:
            self._player.load(self._current_wav)
            self._viz_stack.setCurrentIndex(1)
            self._btn_record.setEnabled(False)
        print(f"[UI] Saved: {self._current_wav}")
        if hasattr(self, "_history_page") and hasattr(self, "_db"):
            self._history_page.refresh()

        # Kick off diarization + show overlay
        if (hasattr(self, "_diarizer") and self._diarizer
                and self._current_wav and self._saved_rec_id):
            self._lbl_rec_status.setText("✔ Saved — re-analyzing speakers…")
            self._lbl_rec_status.setStyleSheet("color:#FBBF24; background:transparent;")
            self._update_status("diarizing")
            self._show_diarize_overlay()
            import threading
            threading.Thread(
                target=self._run_diarization,
                args=(self._saved_rec_id, self._current_wav),
                daemon=True,
            ).start()
        else:
            self._lbl_rec_status.setText("✔ Saved — playing back")
            self._lbl_rec_status.setStyleSheet("color:#34D399; background:transparent;")
            self._update_status("ready")

    def _run_diarization(self, recording_id: str, wav_path: str):
        print(f"[DIARIZE] Starting post-save diarization: {wav_path}")
        try:
            turns = self._diarizer.diarize(wav_path)
            if not turns:
                self._signals.diarization_done.emit(recording_id, False)
                return
            db_segs     = self._db.get_recording_segments(recording_id)
            assignments = self._diarizer.assign_speakers(turns, db_segs)
            if assignments:
                self._db.update_segment_speakers(recording_id, assignments)
            self._signals.diarization_done.emit(recording_id, bool(assignments))
        except Exception as e:
            print(f"[DIARIZE] Error: {e}")
            import traceback; traceback.print_exc()
            self._signals.diarization_done.emit(recording_id, False)

    def _on_diarization_done(self, recording_id: str, success: bool):
        self._hide_diarize_overlay()
        if success:
            self._lbl_rec_status.setText("✔ Saved — speaker labels updated")
            self._lbl_rec_status.setStyleSheet("color:#34D399; background:transparent;")
            self._reload_transcript(recording_id)
            if hasattr(self, "_history_page"):
                self._history_page.refresh()
        else:
            self._lbl_rec_status.setText("✔ Saved — playing back")
            self._lbl_rec_status.setStyleSheet("color:#34D399; background:transparent;")
        self._update_status("ready")

    def _reload_transcript(self, recording_id: str):
        self._clear_transcript()
        segments = self._db.get_recording_segments(recording_id)
        for seg in segments:
            spk_idx = seg["speaker_index"]
            self._insert_text(self._txt_en, seg["text_en"], spk_idx, is_en=True)
            self._insert_text(self._txt_vi, seg["text_vi"], spk_idx, is_en=False)

    def _on_new_recording(self):
        self._player.stop()
        self._viz_stack.setCurrentIndex(0)
        self._lbl_rec_status.setText("Idle — awaiting input")
        self._lbl_rec_status.setStyleSheet(f"color:{TEXT_SEC}; background:transparent;")
        self._btn_record.setEnabled(True)

    def _discard_recording(self):
        self._is_recording = False
        self._is_paused    = False
        self._capture.stop()
        if hasattr(self, "_vad"):
            self._vad.reset()
        self._worker.stop()
        self._db.stop_recording()
        if self._current_wav and os.path.exists(self._current_wav):
            os.remove(self._current_wav)
            print(f"[UI] Discarded: {self._current_wav}")

        self._set_btn_start()
        self._btn_pause.setEnabled(False)
        self._btn_save.setEnabled(False)
        self._btn_discard.setEnabled(False)
        self._lbl_rec_status.setText("Idle — awaiting input")
        self._lbl_rec_status.setStyleSheet(f"color:{TEXT_SEC}; background:transparent;")
        self._dot.setStyleSheet(f"color:{TEXT_SEC}; font-size:9px; border:none;")
        self._update_status("ready")
        self._visualizer.set_active(False)
        self._clear_transcript()

    # ── Callbacks ──────────────────────────────────────────
    def _on_audio_data(self, data: bytes):
        self._vad.add(data)
        self._visualizer.push_audio(data)

    def _on_speech_chunk(self, chunk: bytes):
        self._worker.enqueue(chunk, is_final=True)

    def _on_english(self, text: str, speaker_id: int):
        self._signals.english_received.emit(text, speaker_id)

    def _on_vietnamese(self, text: str, speaker_id: int):
        self._signals.vietnamese_received.emit(text, speaker_id)

    def _on_audio_level(self, level: float):
        self._visualizer.push_level(level)

    # ── UI updates ─────────────────────────────────────────
    def _append_english(self, text: str, speaker_id: int):
        self._insert_text(self._txt_en, text, speaker_id, is_en=True)

    def _append_vietnamese(self, text: str, speaker_id: int):
        self._insert_text(self._txt_vi, text, speaker_id, is_en=False)

    def _insert_text(self, widget: QTextEdit, text: str, speaker_id: int, is_en: bool):
        color = SPEAKER_COLORS[(speaker_id - 1) % len(SPEAKER_COLORS)]
        last  = self._last_speaker_en if is_en else self._last_speaker_vi

        cur = widget.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)

        if speaker_id != last:
            if last is not None:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(TEXT_PRI))
                cur.insertText("\n", fmt)
            fmt_spk = QTextCharFormat()
            fmt_spk.setForeground(QColor(color))
            fmt_spk.setFontWeight(QFont.Weight.Bold)
            fmt_spk.setFontPointSize(8)
            cur.insertText(f"SPEAKER {speaker_id}\n", fmt_spk)
            if is_en:
                self._last_speaker_en = speaker_id
            else:
                self._last_speaker_vi = speaker_id

        fmt_txt = QTextCharFormat()
        fmt_txt.setForeground(QColor(TEXT_PRI))
        fmt_txt.setFontPointSize(11)
        cur.insertText(text + " ", fmt_txt)
        widget.setTextCursor(cur)
        widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum())

    def _clear_transcript(self):
        self._txt_en.clear()
        self._txt_vi.clear()
        self._last_speaker_en = None
        self._last_speaker_vi = None

    def _update_status(self, status: str):
        self._lbl_status.setText(status)

    def closeEvent(self, event):
        if self._is_recording or self._is_paused:
            self._capture.stop()
            if hasattr(self, "_vad"):
                self._vad.reset()
            if hasattr(self, "_worker"):
                self._worker.stop()
            self._db.stop_recording()
        event.accept()

    def _show_session_page(self):
        self._nav_session.setChecked(True)
        self._nav_history.setChecked(False)
        self._page_stack.setCurrentIndex(0)

    def _show_history_page(self):
        self._nav_session.setChecked(False)
        self._nav_history.setChecked(True)
        self._page_stack.setCurrentIndex(1)
        if hasattr(self, "_history_page") and hasattr(self, "_db"):
            self._history_page.refresh()