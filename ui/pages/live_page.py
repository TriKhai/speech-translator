from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel,
    QFrame, QSizePolicy, QInputDialog, QLineEdit,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat

from core.vad_processor        import VADProcessor
from core.transcription_worker import TranscriptionWorker

from ui.constants import SPEAKER_COLORS
from ui.signals   import AppSignals
from ui.widgets   import AudioVisualizer, SummaryPanel
import ui.icons as icons



class LivePage(QWidget):
    """Trang Session — recording realtime."""

    status_changed = pyqtSignal(str)
    _swap_asr_done = pyqtSignal()

    def __init__(self, theme_manager, parent=None):
        super().__init__(parent)
        self._theme = theme_manager

        self._record_seconds = 0
        self._record_timer = QTimer()
        self._record_timer.timeout.connect(self._tick_record)

        # Services
        self._whisper    = None
        self._speaker    = None
        self._translator = None
        self._db         = None
        self._capture    = None
        self._worker     = None

        # State
        self._is_recording    = False
        self._is_paused       = False
        self._saved_rec_id    = None
        self._last_speaker_en = None
        self._last_speaker_vi = None

        # Signals nội bộ (thread-safe UI update)
        self._signals = AppSignals()
        self._signals.english_received.connect(self._append_english)
        self._signals.vietnamese_received.connect(self._append_vietnamese)
        self._signals.audio_level.connect(self._on_audio_level)

        self._build_ui()

    @property
    def _p(self):
        return self._theme.palette

    # ══════════════════════════════════════════════════════
    # Services
    # ══════════════════════════════════════════════════════
    def set_services(self, *, whisper, speaker, translator, db, capture, **kwargs):
        self._whisper    = whisper
        self._speaker    = speaker
        self._translator = translator
        self._db         = db
        self._capture    = capture
        self._worker = TranscriptionWorker(
            whisper       = whisper,
            speaker       = speaker,
            translator    = translator,
            db            = db,
            on_english    = self._on_english,
            on_vietnamese = self._on_vietnamese,
        )
        self._btn_record.setEnabled(True)

    def swap_asr(self, engine):
        """Hot-swap ASR engine từ ModelsPage — gọi từ background thread an toàn."""
        self._whisper = engine
        if self._worker:
            self._worker.swap_asr(engine)

    # ══════════════════════════════════════════════════════
    # UI Build  (giữ nguyên hoàn toàn)
    # ══════════════════════════════════════════════════════
    def _build_ui(self):
        p = self._p
        self.setStyleSheet(f"background:{p['BG_BASE']};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        title_row = QHBoxLayout()
        self._lbl_page_title = QLabel("GHI ÂM TRỰC TIẾP")
        self._lbl_page_title.setFont(QFont("JetBrains Mono", 18))
        self._lbl_page_title.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent; letter-spacing:1px;")
        title_row.addWidget(self._lbl_page_title)
        title_row.addStretch()

        self._btn_toggle_en      = self._toggle_pill("🇬🇧  Văn bản EN",     active=True)
        self._btn_toggle_vi      = self._toggle_pill("🇻🇳  Văn bản VI",     active=True)
        self._btn_toggle_summary = self._toggle_pill("✦  Tóm tắt", active=True)
        self._btn_toggle_en.clicked.connect(self._toggle_en_panel)
        self._btn_toggle_vi.clicked.connect(self._toggle_vi_panel)
        self._btn_toggle_summary.clicked.connect(self._toggle_summary_panel)

        title_row.addWidget(self._btn_toggle_en)
        title_row.addSpacing(6)
        title_row.addWidget(self._btn_toggle_vi)
        title_row.addSpacing(6)
        title_row.addWidget(self._btn_toggle_summary)
        lay.addLayout(title_row)

        self._viz_card = self._build_visualizer_card()
        lay.addWidget(self._viz_card)

        transcript_row = QWidget()
        transcript_row.setStyleSheet("background:transparent;")
        bl = QHBoxLayout(transcript_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(14)

        self._card_en, self._txt_en = self._transcript_card("🇬🇧  ENGLISH",    "#60A5FA")
        self._card_vi, self._txt_vi = self._transcript_card("🇻🇳  TIẾNG VIỆT", "#34D399")
        self._card_summary          = self._build_summary_card()

        bl.addWidget(self._card_en)
        bl.addWidget(self._card_vi)
        bl.addWidget(self._card_summary)
        lay.addWidget(transcript_row, stretch=1)

    def _build_visualizer_card(self) -> QFrame:
        p = self._p
        self._viz_card = QFrame()
        self._viz_card.setStyleSheet(f"""
            QFrame {{
                background:{p['BG_CARD']};
                border:1px solid {p['BORDER']};
                border-radius:10px;
            }}
        """)
        vc_lay = QVBoxLayout(self._viz_card)
        vc_lay.setContentsMargins(18, 14, 18, 14)
        vc_lay.setSpacing(10)

        ch = QHBoxLayout()
        ch_icon = QLabel()
        ch_icon.setPixmap(icons.get("visualizer", p['ACCENT']).pixmap(icons.CARD_SIZE))
        ch_icon.setStyleSheet("background:transparent;")
        self._lbl_viz_title = QLabel("Audio Visualizer")
        self._lbl_viz_title.setFont(QFont("JetBrains Mono", 11))
        self._lbl_viz_title.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent;")
        ch_title = self._lbl_viz_title
        self._lbl_rec_status = QLabel("Idle — awaiting input")
        self._lbl_rec_status.setFont(QFont("JetBrains Mono", 8))
        self._lbl_rec_status.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        ch.addWidget(ch_icon)
        ch.addSpacing(6)
        ch.addWidget(ch_title)
        ch.addStretch()
        ch.addWidget(self._lbl_rec_status)
        vc_lay.addLayout(ch)
        vc_lay.addWidget(self._divider())

        self._visualizer = AudioVisualizer(theme=self._theme)
        vc_lay.addWidget(self._visualizer)
        vc_lay.addWidget(self._divider())
        vc_lay.addLayout(self._build_controls())
        return self._viz_card

    def _build_controls(self) -> QHBoxLayout:
        p = self._p
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        def _ghost_btn(text):
            b = QPushButton(text)
            b.setFixedHeight(38)
            b.setFont(QFont("JetBrains Mono", 9))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background:transparent; color:{p['TEXT_SEC']};
                    border:1px solid {p['BORDER2']}; border-radius:6px;
                    padding:0 16px; letter-spacing:1px;
                }}
                QPushButton:hover {{ color:{p['TEXT_PRI']}; border-color:#384060; }}
                QPushButton:disabled {{ color:{p['TEXT_DIM']}; border-color:{p['BORDER']}; }}
            """)
            return b

        self._btn_record = QPushButton("  BẮT ĐẦU")
        self._btn_record.setIcon(icons.get("record", "#ffffff"))
        self._btn_record.setIconSize(icons.BTN_SIZE)
        self._btn_record.setFixedHeight(38)
        self._btn_record.setEnabled(False)
        self._btn_record.setFont(QFont("JetBrains Mono", 9))
        self._btn_record.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_record.clicked.connect(self._on_btn_record)
        self._set_btn_start()

        self._btn_pause = _ghost_btn("  TẠM DỪNG")
        self._btn_pause.setIcon(icons.get("pause", p['TEXT_SEC']))
        self._btn_pause.setIconSize(icons.BTN_SIZE)
        self._btn_pause.clicked.connect(self._on_btn_pause)
        self._btn_pause.setVisible(False)

        self._btn_save = QPushButton("  LƯU")
        self._btn_save.setIcon(icons.get("save", "#34D399"))
        self._btn_save.setIconSize(icons.BTN_SIZE)
        self._btn_save.setFixedHeight(38)
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
        """)
        self._btn_save.setVisible(False)

        self._btn_discard = _ghost_btn("  HỦY")
        self._btn_discard.setIcon(icons.get("discard", p['TEXT_SEC']))
        self._btn_discard.setIconSize(icons.BTN_SIZE)
        self._btn_discard.clicked.connect(self._on_btn_discard)
        self._btn_discard.setVisible(False)

        self._btn_clear = _ghost_btn("  CLEAR")
        self._btn_clear.setIcon(icons.get("clear", p['TEXT_SEC']))
        self._btn_clear.setIconSize(icons.BTN_SIZE)
        self._btn_clear.clicked.connect(self._clear_transcript)

        self._diarize_spinner_widget = self._build_inline_spinner()
        self._diarize_spinner_widget.setVisible(False)

        ctrl.addWidget(self._btn_record)
        ctrl.addWidget(self._btn_pause)
        ctrl.addWidget(self._btn_save)
        ctrl.addWidget(self._btn_discard)
        ctrl.addWidget(self._diarize_spinner_widget)
        ctrl.addStretch()
        ctrl.addWidget(self._btn_clear)
        return ctrl

    def _build_inline_spinner(self) -> QWidget:
        p = self._p
        w = QWidget()
        w.setFixedHeight(38)
        w.setFixedWidth(160)
        w.setStyleSheet("background: transparent;")
        w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 0, 12, 0)
        lay.setSpacing(6)

        self._inline_spinner_lbl = QLabel("◉")
        self._inline_spinner_lbl.setFont(QFont("JetBrains Mono", 11))
        self._inline_spinner_lbl.setStyleSheet(
            f"color:{p['ACCENT']}; background:transparent; border:none;"
        )
        self._inline_spinner_text = QLabel("Re-analyzing…")
        self._inline_spinner_text.setFont(QFont("JetBrains Mono", 8))
        self._inline_spinner_text.setStyleSheet(
            f"color:{p['TEXT_SEC']}; background:transparent; border:none;"
        )
        lay.addWidget(self._inline_spinner_lbl)
        lay.addWidget(self._inline_spinner_text)

        self._inline_spinner_frames = ["◉", "◎", "○", "◎"]
        self._inline_spinner_idx    = 0
        self._inline_spinner_timer  = QTimer()
        self._inline_spinner_timer.timeout.connect(self._tick_inline_spinner)
        return w

    def _tick_inline_spinner(self):
        self._inline_spinner_idx = (self._inline_spinner_idx + 1) % len(self._inline_spinner_frames)
        self._inline_spinner_lbl.setText(self._inline_spinner_frames[self._inline_spinner_idx])

    def _tick_record(self):
        self._record_seconds += 1
        m = self._record_seconds // 60
        s = self._record_seconds % 60
        self._lbl_rec_status.setText(f"● Đang ghi âm — {m:02d}:{s:02d}")

    # ── MỚI: tick draft mỗi 1s ────────────────────────────
    def _tick_draft(self):
        """Gọi mỗi 1s → VAD emit draft nếu đang có speech."""
        if hasattr(self, "_vad"):
            self._vad.emit_draft()

    def _build_summary_card(self) -> QFrame:
        p = self._p
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background:{p['BG_CARD']};
                border:1px solid {p['BORDER']};
                border-radius:10px;
            }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        hdr = QHBoxLayout()
        lbl = QLabel("✦  TÓM TẮT")
        lbl.setFont(QFont("JetBrains Mono", 9))
        lbl.setStyleSheet(f"color:{p['ACCENT']}; background:transparent; letter-spacing:2px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addWidget(self._divider())

        self._summary_panel = SummaryPanel(self._theme, embedded=True)
        self._summary_panel.summary_ready.connect(self._on_summary_ready)
        lay.addWidget(self._summary_panel, stretch=1)
        return card

    def _transcript_card(self, title: str, color: str):
        p = self._p
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background:{p['BG_CARD']};
                border:1px solid {p['BORDER']};
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
                background:transparent; color:{p['TEXT_PRI']};
                border:none; line-height:1.7;
                selection-background-color:rgba(59,110,245,0.25);
            }}
            QScrollBar:vertical {{
                background:{p['BG_CARD']}; width:5px; border-radius:3px;
            }}
            QScrollBar::handle:vertical {{
                background:{p['BORDER2']}; border-radius:3px; min-height:20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        lay.addWidget(txt, stretch=1)
        return card, txt

    def _toggle_pill(self, text: str, active: bool = True) -> QPushButton:
        p   = self._p
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setFixedHeight(28)
        btn.setFont(QFont("JetBrains Mono", 8))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_pill_style(btn)
        btn.toggled.connect(lambda _: self._apply_pill_style(btn))
        return btn

    def _apply_pill_style(self, btn: QPushButton):
        p = self._p
        if btn.isChecked():
            btn.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(96,165,250,0.15); color:#60A5FA;
                    border:1px solid rgba(96,165,250,0.3); border-radius:14px;
                    padding:0 12px;
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background:transparent; color:{p['TEXT_DIM']};
                    border:1px solid {p['BORDER']}; border-radius:14px;
                    padding:0 12px;
                }}
                QPushButton:hover {{ color:{p['TEXT_SEC']}; }}
            """)

    def _toggle_en_panel(self):
        self._card_en.setVisible(self._btn_toggle_en.isChecked())

    def _toggle_vi_panel(self):
        self._card_vi.setVisible(self._btn_toggle_vi.isChecked())

    def _toggle_summary_panel(self):
        self._card_summary.setVisible(self._btn_toggle_summary.isChecked())

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.HLine)
        d.setStyleSheet(f"color:{self._p['BORDER']}; background:{self._p['BORDER']}; max-height:1px;")
        return d

    def _set_btn_start(self):
        p = self._p
        self._btn_record.setText("  BẮT ĐẦU")
        self._btn_record.setIcon(icons.get("record", "#ffffff"))
        self._btn_record.setIconSize(icons.BTN_SIZE)
        self._btn_record.setEnabled(True)
        self._btn_record.setStyleSheet(f"""
            QPushButton {{
                background:{p['ACCENT']}; color:#fff; border:none;
                border-radius:6px; padding:0 24px; letter-spacing:2px;
            }}
            QPushButton:hover   {{ background:{p['ACCENT_HO']}; }}
            QPushButton:pressed {{ background:#2A5CE0; }}
            QPushButton:disabled {{ background:{p['BG_CARD2']}; color:{p['TEXT_SEC']}; }}
        """)

    def _set_btn_recording(self):
        p = self._p
        self._btn_record.setText("  ĐANG GHI")
        self._btn_record.setIcon(icons.get("record", "#F87171"))
        self._btn_record.setIconSize(icons.BTN_SIZE)
        self._btn_record.setEnabled(False)
        self._btn_record.setStyleSheet(f"""
            QPushButton {{
                background:{p['BG_CARD2']}; color:#F87171;
                border:1px solid rgba(248,113,113,0.3); border-radius:6px;
                padding:0 24px; letter-spacing:2px;
            }}
            QPushButton:disabled {{ background:{p['BG_CARD2']}; color:#F87171;
                                    border:1px solid rgba(248,113,113,0.3); }}
        """)

    def _set_btn_paused(self):
        p = self._p
        self._btn_pause.setText("  TIẾP TỤC")
        self._btn_pause.setIcon(icons.get("resume", p['ACCENT_HO']))
        self._btn_pause.setIconSize(icons.BTN_SIZE)
        self._btn_pause.setStyleSheet(f"""
            QPushButton {{
                background:rgba(59,110,245,0.15); color:{p['ACCENT_HO']};
                border:1px solid {p['ACCENT']}; border-radius:6px;
                padding:0 16px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(59,110,245,0.3); }}
        """)

    def _set_btn_pause_default(self):
        p = self._p
        self._btn_pause.setText("  TẠM DỪNG")
        self._btn_pause.setIcon(icons.get("pause", p['TEXT_SEC']))
        self._btn_pause.setIconSize(icons.BTN_SIZE)

    def _show_action_btns(self):
        self._btn_record.setVisible(True)
        self._btn_pause.setVisible(True)
        self._btn_save.setVisible(True)
        self._btn_discard.setVisible(True)

    def _hide_action_btns(self):
        self._btn_pause.setVisible(False)
        self._btn_save.setVisible(False)
        self._btn_discard.setVisible(False)
        self._set_btn_pause_default()

    # ══════════════════════════════════════════════════════
    # Button handlers
    # ══════════════════════════════════════════════════════
    def _on_btn_record(self):
        self._start_recording()

    def _on_btn_pause(self):
        if not self._is_paused:
            self._pause_recording()
        else:
            self._resume_recording()

    def _on_btn_save(self):
        self._save_recording()

    def _on_btn_discard(self):
        self._discard_recording()

    # ══════════════════════════════════════════════════════
    # Recording FSM
    # ══════════════════════════════════════════════════════
    def _start_recording(self):
        self._is_recording    = True
        self._is_paused       = False
        self._last_speaker_en = None
        self._last_speaker_vi = None

        self._set_btn_recording()
        self._show_action_btns()
        self._lbl_rec_status.setText("● Recording — live")
        self._lbl_rec_status.setStyleSheet("color:#F87171; background:transparent;")
        self.status_changed.emit("recording")
        self._visualizer.set_active(True)

        self._db.start_recording(audio_path=None)

        # Tạo lại worker mới mỗi lần start — tránh dùng lại worker đã stop/shutdown
        self._worker = TranscriptionWorker(
            whisper       = self._whisper,
            speaker       = self._speaker,
            translator    = self._translator,
            db            = self._db,
            on_english    = self._on_english,
            on_vietnamese = self._on_vietnamese,
        )

        self._vad = VADProcessor(
            on_speech_chunk = self._on_speech_chunk,
            threshold       = 0.25,
            min_speech_ms   = 150,
            min_silence_ms  = 150,
            max_speech_ms   = 1500,
        )
        self._speaker.reset()
        self._worker.start()
        self._capture.start(on_data=self._on_audio_data)

        self._record_seconds = 0
        self._record_timer.start(1000)

    def _pause_recording(self):
        p = self._p
        self._is_paused = True
        self._capture.pause()
        self._vad.reset()
        self._set_btn_paused()
        self._lbl_rec_status.setText("⏸ Tạm dừng")
        self._lbl_rec_status.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        self.status_changed.emit("paused")
        self._visualizer.set_active(False)
        self._record_timer.stop()

    def _resume_recording(self):
        self._is_paused = False
        self._capture.resume()
        self._set_btn_recording()
        self._set_btn_pause_default()
        self._lbl_rec_status.setText("● Recording — live")
        self._lbl_rec_status.setStyleSheet("color:#F87171; background:transparent;")
        self.status_changed.emit("recording")
        self._visualizer.set_active(True)
        self._record_timer.start(1000)

    def _save_recording(self):
        p = self._p
        self._is_recording = False
        self._is_paused    = False
        self._capture.stop()
        if hasattr(self, "_vad"):
            self._vad.reset()
        self._worker.stop()
        self._record_timer.stop()
        self.status_changed.emit("saving")

        default_name = datetime.now().strftime("Bản ghi %d/%m/%Y %H:%M")
        name, ok = QInputDialog.getText(
            self, "Đặt tên cuộc hội thoại", "Tên:",
            QLineEdit.EchoMode.Normal, default_name,
        )
        if not ok or not name.strip():
            self._db.stop_recording()
            self._reset_ui()
            return

        self._saved_rec_id = self._db._current_rec_id
        self._db.stop_recording()
        if self._saved_rec_id:
            self._db.update_recording_title(self._saved_rec_id, name.strip())

        self._btn_record.setText("  ✔ ĐÃ KẾT THÚC")
        self._btn_record.setEnabled(False)
        self._btn_record.setStyleSheet(f"""
            QPushButton {{
                background:{p['BG_CARD2']}; color:{p['GREEN']};
                border:1px solid rgba(52,211,153,0.3); border-radius:6px;
                padding:0 24px; letter-spacing:2px;
            }}
            QPushButton:disabled {{ background:{p['BG_CARD2']}; color:{p['GREEN']};
                                    border:1px solid rgba(52,211,153,0.3); }}
        """)
        self._btn_pause.setVisible(False)
        self._btn_save.setVisible(False)

        self._lbl_rec_status.setText(f"✔ Đã lưu: {name.strip()}")
        self._lbl_rec_status.setStyleSheet(f"color:{p['GREEN']}; background:transparent;")
        self.status_changed.emit("ready")

        if self._saved_rec_id:
            segs = self._db.get_recording_segments(self._saved_rec_id)
            self._summary_panel.load(segs, auto_run=True)

    def _discard_recording(self):
        self._is_recording = False
        self._is_paused    = False
        # Chỉ stop nếu còn đang chạy (tránh double-stop sau khi đã save)
        if self._capture:
            try:
                self._capture.stop()
            except Exception:
                pass
        if hasattr(self, "_vad"):
            self._vad.reset()
        if self._worker:
            try:
                self._worker.stop()
            except Exception:
                pass
        if self._db:
            try:
                self._db.stop_recording()
            except Exception:
                pass
        self._record_timer.stop()
        self._reset_ui()

    def _reset_ui(self):
        p = self._p
        self._hide_action_btns()
        self._set_btn_start()
        self._visualizer.set_active(False)
        self._lbl_rec_status.setText("Idle — awaiting input")
        self._lbl_rec_status.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        self.status_changed.emit("ready")
        self._clear_transcript()
        self._summary_panel.clear()

    # ══════════════════════════════════════════════════════
    # Callbacks từ audio pipeline
    # ══════════════════════════════════════════════════════
    def _on_audio_data(self, data: bytes):
        self._vad.add(data)
        self._visualizer.push_audio(data)

    def _on_speech_chunk(self, chunk: bytes):
        """VAD phát hiện câu hoàn chỉnh → enqueue."""
        self._worker.enqueue(chunk)

    def _on_english(self, text: str, speaker_id: int):
        self._signals.english_received.emit(text, speaker_id)

    def _on_vietnamese(self, text: str, speaker_id: int):
        self._signals.vietnamese_received.emit(text, speaker_id)

    def _on_audio_level(self, level: float):
        self._visualizer.push_level(level)

    # ══════════════════════════════════════════════════════
    # Transcript helpers
    # ══════════════════════════════════════════════════════
    def _append_english(self, text: str, speaker_id: int):
        self._insert_text(self._txt_en, text, speaker_id, is_en=True)

    def _append_vietnamese(self, text: str, speaker_id: int):
        self._insert_text(self._txt_vi, text, speaker_id, is_en=False)

    def _insert_text(self, widget: QTextEdit, text: str, speaker_id: int, is_en: bool):
        p     = self._p
        color = SPEAKER_COLORS[(speaker_id - 1) % len(SPEAKER_COLORS)]
        last  = self._last_speaker_en if is_en else self._last_speaker_vi

        cur = widget.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)

        if speaker_id != last:
            if last is not None:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(p['TEXT_PRI']))
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
        fmt_txt.setForeground(QColor(p['TEXT_PRI']))
        fmt_txt.setFontPointSize(11)
        cur.insertText(text + " ", fmt_txt)
        widget.setTextCursor(cur)
        widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum())

    def _clear_transcript(self):
        self._txt_en.clear()
        self._txt_vi.clear()
        self._last_speaker_en = None
        self._last_speaker_vi = None

    def _on_summary_ready(self, result):
        if not self._saved_rec_id or not hasattr(self, "_db"):
            return
        if result.error:
            return
        try:
            self._db.save_summary(self._saved_rec_id, result)
        except Exception as e:
            print(f"[UI] Could not save summary: {e}")

    # ══════════════════════════════════════════════════════
    # Theme
    # ══════════════════════════════════════════════════════
    def apply_theme(self):
        p = self._p
        self.setStyleSheet(f"background:{p['BG_BASE']};")

        card_style = f"""
            QFrame {{
                background:{p['BG_CARD']};
                border:1px solid {p['BORDER']};
                border-radius:10px;
            }}
        """
        self._card_en.setStyleSheet(card_style)
        self._card_vi.setStyleSheet(card_style)
        self._card_summary.setStyleSheet(card_style)
        self._viz_card.setStyleSheet(card_style)

        txt_style = f"""
            QTextEdit {{
                background:transparent; color:{p['TEXT_PRI']};
                border:none;
                selection-background-color:rgba(59,110,245,0.25);
            }}
            QScrollBar:vertical {{
                background:{p['BG_CARD']}; width:5px; border-radius:3px;
            }}
            QScrollBar::handle:vertical {{
                background:{p['BORDER2']}; border-radius:3px; min-height:20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """
        self._txt_en.setStyleSheet(txt_style)
        self._txt_vi.setStyleSheet(txt_style)

        self._lbl_rec_status.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        if hasattr(self, '_lbl_page_title'):
            self._lbl_page_title.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent; letter-spacing:1px;")
        if hasattr(self, '_lbl_viz_title'):
            self._lbl_viz_title.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent;")
        self._inline_spinner_lbl.setStyleSheet(
            f"color:{p['ACCENT']}; background:transparent; border:none;"
        )
        self._inline_spinner_text.setStyleSheet(
            f"color:{p['TEXT_SEC']}; background:transparent; border:none;"
        )
        self._set_btn_start()
        self._summary_panel.apply_theme()
        if hasattr(self, '_visualizer'):
            self._visualizer._theme = self._theme
            self._visualizer.update()

    # ══════════════════════════════════════════════════════
    # closeEvent helper
    # ══════════════════════════════════════════════════════
    def cleanup(self):
        """Gọi khi app đóng để dừng recording an toàn."""
        if self._is_recording or self._is_paused:
            if self._capture:
                self._capture.stop()
            if hasattr(self, "_vad"):
                self._vad.reset()
            if self._worker:
                self._worker.stop()
            if self._db:
                self._db.stop_recording()