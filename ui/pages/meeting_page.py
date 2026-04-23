import os
import threading
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel,
    QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat
import qtawesome as qta

from ui.constants import AUDIO_DIR, SPEAKER_COLORS
from ui.signals   import AppSignals
from ui.widgets   import SummaryPanel
import ui.icons as icons


class MeetingPage(QWidget):

    status_changed = pyqtSignal(str)

    def __init__(self, theme_manager, parent=None):
        super().__init__(parent)
        self._theme = theme_manager

        # Services
        self._whisper    = None
        self._speaker    = None
        self._translator = None
        self._db         = None

        # State
        self._is_recording    = False
        self._current_wav     = None
        self._saved_rec_id    = None
        self._record_seconds  = 0
        self._last_speaker_en = None
        self._last_speaker_vi = None
        self._capture         = None

        # Signals thread-safe
        self._signals = AppSignals()
        self._signals.english_received.connect(self._append_english)
        self._signals.vietnamese_received.connect(self._append_vietnamese)
        self._signals.audio_level.connect(self._on_level_sys)

        # Timers
        self._record_timer = QTimer()
        self._record_timer.timeout.connect(self._tick_record)

        self._build_ui()

    @property
    def _p(self):
        return self._theme.palette

    # ══════════════════════════════════════════════════════
    # Services
    # ══════════════════════════════════════════════════════

    def set_services(self, whisper_service, speaker_identifier,
                     translation_service, db=None, diarizer=None):
        self._whisper    = whisper_service
        self._speaker    = speaker_identifier
        self._translator = translation_service
        self._db         = db
        # diarizer bỏ — không dùng nữa
        self._summary_panel.summary_ready.connect(self._on_summary_ready)
        self._btn_record.setEnabled(True)

    # ══════════════════════════════════════════════════════
    # UI Build
    # ══════════════════════════════════════════════════════

    def _build_ui(self):
        p = self._p
        self.setStyleSheet(f"background:{p['BG_BASE']};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        # Title row
        title_row = QHBoxLayout()
        self._lbl_page_title = QLabel("MEETING")
        self._lbl_page_title.setFont(QFont("JetBrains Mono", 18))
        self._lbl_page_title.setStyleSheet(
            f"color:{p['TEXT_PRI']}; background:transparent; letter-spacing:1px;")
        title_row.addWidget(self._lbl_page_title)
        title_row.addStretch()

        self._btn_toggle_en      = self._toggle_pill("🇬🇧  EN",     active=True)
        self._btn_toggle_vi      = self._toggle_pill("🇻🇳  VI",     active=True)
        self._btn_toggle_summary = self._toggle_pill("✦  Tóm tắt", active=True)
        self._btn_toggle_en.clicked.connect(
            lambda: self._card_en.setVisible(self._btn_toggle_en.isChecked()))
        self._btn_toggle_vi.clicked.connect(
            lambda: self._card_vi.setVisible(self._btn_toggle_vi.isChecked()))
        self._btn_toggle_summary.clicked.connect(
            lambda: self._card_summary.setVisible(
                self._btn_toggle_summary.isChecked()))

        title_row.addWidget(self._btn_toggle_en)
        title_row.addSpacing(6)
        title_row.addWidget(self._btn_toggle_vi)
        title_row.addSpacing(6)
        title_row.addWidget(self._btn_toggle_summary)
        lay.addLayout(title_row)

        lay.addWidget(self._build_control_card())

        transcript_row = QWidget()
        transcript_row.setStyleSheet("background:transparent;")
        bl = QHBoxLayout(transcript_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(14)

        self._card_en, self._txt_en = self._transcript_card(
            "🔊  ENGLISH",    "#60A5FA")
        self._card_vi, self._txt_vi = self._transcript_card(
            "🇻🇳  TIẾNG VIỆT", "#34D399")
        self._card_summary          = self._build_summary_card()

        bl.addWidget(self._card_en)
        bl.addWidget(self._card_vi)
        bl.addWidget(self._card_summary)
        lay.addWidget(transcript_row, stretch=1)

    def _build_control_card(self) -> QFrame:
        p = self._p
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background:{p['BG_CARD']};
                border:1px solid {p['BORDER']};
                border-radius:10px;
            }}
        """)
        vc_lay = QVBoxLayout(card)
        vc_lay.setContentsMargins(18, 14, 18, 14)
        vc_lay.setSpacing(10)

        # Header
        ch = QHBoxLayout()
        ch_icon = QLabel()
        ch_icon.setPixmap(
            qta.icon("fa5s.headphones", color=p['ACCENT']).pixmap(QSize(14, 14)))
        ch_icon.setStyleSheet("background:transparent;")
        self._lbl_card_title = QLabel("System Audio Capture")
        self._lbl_card_title.setFont(QFont("JetBrains Mono", 11))
        self._lbl_card_title.setStyleSheet(
            f"color:{p['TEXT_PRI']}; background:transparent;")

        self._lbl_rec_status = QLabel("Idle — chưa bắt đầu")
        self._lbl_rec_status.setFont(QFont("JetBrains Mono", 8))
        self._lbl_rec_status.setStyleSheet(
            f"color:{p['TEXT_SEC']}; background:transparent;")

        ch.addWidget(ch_icon)
        ch.addSpacing(6)
        ch.addWidget(self._lbl_card_title)
        ch.addStretch()
        ch.addWidget(self._lbl_rec_status)
        vc_lay.addLayout(ch)
        vc_lay.addWidget(self._divider())

        # Single level bar — system audio only
        self._level_bar_sys = QLabel()
        self._level_bar_sys.setFixedHeight(4)
        self._level_bar_sys.setStyleSheet(
            f"background:{p['BORDER']}; border-radius:2px;")
        vc_lay.addWidget(self._level_bar_sys)
        vc_lay.addWidget(self._divider())

        # Controls
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        def _ghost_btn(text, icon_name):
            b = QPushButton(text)
            b.setIcon(icons.get(icon_name, p['TEXT_SEC']))
            b.setIconSize(icons.BTN_SIZE)
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

        self._btn_record = QPushButton("  Bắt đầu")
        self._btn_record.setIcon(icons.get("record", "#ffffff"))
        self._btn_record.setIconSize(icons.BTN_SIZE)
        self._btn_record.setFixedHeight(38)
        self._btn_record.setEnabled(False)
        self._btn_record.setFont(QFont("JetBrains Mono", 9))
        self._btn_record.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_record.clicked.connect(self._on_btn_record)
        self._set_btn_start()

        self._btn_save = QPushButton("  LƯU")
        self._btn_save.setIcon(icons.get("save", "#34D399"))
        self._btn_save.setIconSize(icons.BTN_SIZE)
        self._btn_save.setFixedHeight(38)
        self._btn_save.setEnabled(False)
        self._btn_save.setFont(QFont("JetBrains Mono", 9))
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.clicked.connect(self._on_btn_stop)
        self._btn_save.setStyleSheet(f"""
            QPushButton {{
                background:rgba(52,211,153,0.15); color:#34D399;
                border:1px solid rgba(52,211,153,0.3); border-radius:6px;
                padding:0 16px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(52,211,153,0.25); }}
            QPushButton:disabled {{
                background:transparent; color:{p['TEXT_DIM']};
                border-color:{p['BORDER']};
            }}
        """)

        self._btn_discard = _ghost_btn("  HỦY", "discard")
        self._btn_discard.setEnabled(False)
        self._btn_discard.clicked.connect(self._on_btn_discard)

        self._btn_clear = _ghost_btn("  CLEAR", "clear")
        self._btn_clear.clicked.connect(self._clear_transcript)

        ctrl.addWidget(self._btn_record)
        ctrl.addWidget(self._btn_save)
        ctrl.addWidget(self._btn_discard)
        ctrl.addStretch()
        ctrl.addWidget(self._btn_clear)
        vc_lay.addLayout(ctrl)
        return card

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
        lbl.setStyleSheet(
            f"color:{p['ACCENT']}; background:transparent; letter-spacing:2px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addWidget(self._divider())
        self._summary_panel = SummaryPanel(self._theme, embedded=True)
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
        lbl.setStyleSheet(
            f"color:{color}; background:transparent; letter-spacing:2px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        lay.addLayout(hdr)
        lay.addWidget(self._divider())
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("JetBrains Mono", 11))
        txt.setPlaceholderText("Transcript sẽ hiện ở đây…")
        txt.setStyleSheet(f"""
            QTextEdit {{
                background:transparent; color:{p['TEXT_PRI']};
                border:none; line-height:1.7;
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
                    color: {"#93B8FC" if on else self._p['TEXT_SEC']};
                    border: 1px solid {"rgba(59,110,245,0.4)" if on else self._p['BORDER2']};
                    border-radius: 14px; padding: 0 12px;
                }}
                QPushButton:hover {{ background:rgba(59,110,245,0.1); color:#7AA8F8; }}
            """)
        _style()
        btn.toggled.connect(lambda _: _style())
        return btn

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.HLine)
        d.setFixedHeight(1)
        d.setStyleSheet(f"background:{self._p['BORDER']}; border:none;")
        return d

    # ══════════════════════════════════════════════════════
    # Button helpers
    # ══════════════════════════════════════════════════════

    def _set_btn_start(self):
        p = self._p
        self._btn_record.setText("  Bắt đầu")
        self._btn_record.setEnabled(True)
        self._btn_record.setIcon(icons.get("record", "#ffffff"))
        self._btn_record.setStyleSheet(f"""
            QPushButton {{
                background:{p['ACCENT']}; color:#fff; border:none;
                border-radius:6px; padding:0 24px; letter-spacing:2px;
            }}
            QPushButton:hover   {{ background:{p['ACCENT_HO']}; }}
            QPushButton:disabled {{ background:{p['BG_CARD2']}; color:{p['TEXT_SEC']}; }}
        """)

    def _set_btn_recording(self):
        p = self._p
        self._btn_record.setText("  Đang capture…")
        self._btn_record.setEnabled(False)
        self._btn_record.setIcon(icons.get("stop", "#ffffff"))
        self._btn_record.setStyleSheet(f"""
            QPushButton {{
                background:{p['DANGER']}; color:#fff; border:none;
                border-radius:6px; padding:0 24px; letter-spacing:2px;
            }}
            QPushButton:disabled {{ background:{p['DANGER']}; color:#fff; }}
        """)

    # ══════════════════════════════════════════════════════
    # Button handlers
    # ══════════════════════════════════════════════════════

    def _on_btn_record(self):
        if not self._is_recording:
            self._start_capture()

    def _on_btn_stop(self):
        if self._is_recording:
            self._stop_capture()

    def _on_btn_discard(self):
        p = self._p
        self._is_recording = False
        self._record_timer.stop()
        if self._capture:
            try:
                self._capture.stop()
            except Exception:
                pass
            self._capture = None
        if self._current_wav and os.path.exists(self._current_wav):
            try:
                os.remove(self._current_wav)
            except Exception:
                pass
        self._current_wav  = None
        self._saved_rec_id = None
        self._set_btn_start()
        self._btn_save.setEnabled(False)
        self._btn_discard.setEnabled(False)
        self._level_bar_sys.setStyleSheet(
            f"background:{p['BORDER']}; border-radius:2px;")
        self._lbl_rec_status.setText("Idle — chưa bắt đầu")
        self._lbl_rec_status.setStyleSheet(
            f"color:{p['TEXT_SEC']}; background:transparent;")
        self.status_changed.emit("ready")
        self._clear_transcript()
        self._summary_panel.clear()

    # ══════════════════════════════════════════════════════
    # Capture
    # ══════════════════════════════════════════════════════

    def _start_capture(self):
        from core.meeting_capture import MeetingCapture

        self._is_recording    = True
        self._record_seconds  = 0
        self._last_speaker_en = None
        self._last_speaker_vi = None

        os.makedirs(AUDIO_DIR, exist_ok=True)
        ts                = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._current_wav = os.path.join(AUDIO_DIR, f"meet_{ts}.wav")

        self._capture = MeetingCapture(
            whisper_service     = self._whisper,
            speaker_identifier  = self._speaker,
            translation_service = self._translator,
            db_service          = self._db,
            on_english    = lambda t, spk: self._signals.english_received.emit(t, spk),
            on_vietnamese = lambda t, spk: self._signals.vietnamese_received.emit(t, spk),
            on_level_sys  = lambda rms: self._signals.audio_level.emit(rms),
        )
        self._capture.start(wav_path=self._current_wav)
        self._saved_rec_id = self._capture.recording_id

        self._set_btn_recording()
        self._btn_save.setEnabled(True)
        self._btn_discard.setEnabled(True)
        self._lbl_rec_status.setText("● Đang capture system audio")
        self._lbl_rec_status.setStyleSheet("color:#F87171; background:transparent;")
        self._record_timer.start(1000)
        self.status_changed.emit("meeting")

    def _stop_capture(self):
        p = self._p
        self._is_recording = False
        self._record_timer.stop()

        if self._capture:
            self._capture.stop()

        self._set_btn_start()
        self._btn_save.setEnabled(False)
        self._btn_discard.setEnabled(False)
        self._level_bar_sys.setStyleSheet(
            f"background:{p['BORDER']}; border-radius:2px;")
        self._lbl_rec_status.setText("✔ Đã dừng")
        self._lbl_rec_status.setStyleSheet(
            f"color:{p['GREEN']}; background:transparent;")
        self.status_changed.emit("ready")
        self._load_summary()

    # ══════════════════════════════════════════════════════
    # Level bar — thread-safe qua AppSignals.audio_level
    # ══════════════════════════════════════════════════════

    def _on_level_sys(self, rms: float):
        p   = self._p
        pct = min(int(rms * 800), 100)
        if pct > 0:
            self._level_bar_sys.setStyleSheet(f"""
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #60A5FA,
                    stop:{max(pct/100, 0.01):.2f} #60A5FA,
                    stop:{min(pct/100 + 0.01, 1):.2f} {p['BORDER']},
                    stop:1 {p['BORDER']}
                ); border-radius:2px;
            """)
        else:
            self._level_bar_sys.setStyleSheet(
                f"background:{p['BORDER']}; border-radius:2px;")

    # ══════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════

    def _load_summary(self):
        if self._db and self._saved_rec_id:
            segs = self._db.get_recording_segments(self._saved_rec_id)
            self._summary_panel.load(segs, auto_run=True)

    def _on_summary_ready(self, result):
        if not self._saved_rec_id or not self._db or result.error:
            return
        try:
            self._db.save_summary(self._saved_rec_id, result)
        except Exception as e:
            print(f"[MEETING] Could not save summary: {e}")

    # ══════════════════════════════════════════════════════
    # Transcript
    # ══════════════════════════════════════════════════════

    def _append_english(self, text: str, speaker_id: int):
        self._insert_text(self._txt_en, text, speaker_id, is_en=True)

    def _append_vietnamese(self, text: str, speaker_id: int):
        self._insert_text(self._txt_vi, text, speaker_id, is_en=False)

    def _insert_text(self, widget: QTextEdit, text: str,
                     speaker_id: int, is_en: bool):
        p     = self._p
        color = SPEAKER_COLORS[(speaker_id - 1) % len(SPEAKER_COLORS)]
        last  = self._last_speaker_en if is_en else self._last_speaker_vi
        cur   = widget.textCursor()
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
        widget.verticalScrollBar().setValue(
            widget.verticalScrollBar().maximum())

    def _clear_transcript(self):
        self._txt_en.clear()
        self._txt_vi.clear()
        self._last_speaker_en = None
        self._last_speaker_vi = None

    def _tick_record(self):
        self._record_seconds += 1
        m = self._record_seconds // 60
        s = self._record_seconds % 60
        self._lbl_rec_status.setText(f"● Đang capture — {m:02d}:{s:02d}")

    # ══════════════════════════════════════════════════════
    # Theme & cleanup
    # ══════════════════════════════════════════════════════

    def apply_theme(self):
        p = self._p
        self.setStyleSheet(f"background:{p['BG_BASE']};")
        for attr, style in [
            ("_lbl_page_title", f"color:{p['TEXT_PRI']}; background:transparent; letter-spacing:1px;"),
            ("_lbl_card_title", f"color:{p['TEXT_PRI']}; background:transparent;"),
            ("_lbl_rec_status", f"color:{p['TEXT_SEC']}; background:transparent;"),
        ]:
            if hasattr(self, attr):
                getattr(self, attr).setStyleSheet(style)
        card_style = (f"QFrame {{ background:{p['BG_CARD']}; "
                      f"border:1px solid {p['BORDER']}; border-radius:10px; }}")
        for attr in ("_card_en", "_card_vi", "_card_summary"):
            if hasattr(self, attr):
                getattr(self, attr).setStyleSheet(card_style)
        if hasattr(self, "_level_bar_sys"):
            self._level_bar_sys.setStyleSheet(
                f"background:{p['BORDER']}; border-radius:2px;")
        if hasattr(self, "_summary_panel"):
            self._summary_panel.apply_theme()

    def cleanup(self):
        self._record_timer.stop()
        if self._is_recording and self._capture:
            try:
                self._capture.stop()
            except Exception:
                pass