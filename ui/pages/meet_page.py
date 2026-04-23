import os
import threading
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel,
    QFrame, QLineEdit,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat

from core.vad_processor        import VADProcessor
from core.transcription_worker import TranscriptionWorker
from core.meet_capture         import MeetCapture, MeetStatus

from ui.constants import AUDIO_DIR, SPEAKER_COLORS
from ui.signals   import AppSignals
from ui.widgets   import SummaryPanel
import ui.icons as icons


_STREAM_FLUSH_MS  = 3000
_SAMPLE_RATE      = 16_000
_BYTES_PER_SAMPLE = 2
_FLUSH_BYTES      = _SAMPLE_RATE * _BYTES_PER_SAMPLE * 3


class MeetPage(QWidget):
    """Tab ghi âm Google Meet — độc lập với LivePage."""

    status_changed = pyqtSignal(str)

    def __init__(self, theme_manager, parent=None):
        super().__init__(parent)
        self._theme = theme_manager

        # Services — inject từ ngoài
        self._whisper    = None
        self._speaker    = None
        self._translator = None
        self._db         = None
        self._diarizer   = None
        self._worker     = None
        self._meet       = None
        self._vad        = None

        # State
        self._is_recording     = False
        self._current_wav      = None
        self._saved_rec_id     = None
        self._last_speaker_en  = None
        self._last_speaker_vi  = None
        self._record_seconds   = 0
        self._vad_just_flushed = False

        # Streaming buffer
        self._stream_buf      = bytearray()
        self._stream_buf_lock = threading.Lock()

        # Timers
        self._flush_timer = QTimer()
        self._flush_timer.setInterval(_STREAM_FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush_stream_buf)

        self._record_timer = QTimer()
        self._record_timer.timeout.connect(self._tick_record)

        # Signals (thread-safe UI update)
        self._signals = AppSignals()
        self._signals.english_received.connect(self._append_english)
        self._signals.vietnamese_received.connect(self._append_vietnamese)
        self._signals.diarization_done.connect(self._on_diarization_done)

        self._build_ui()

    @property
    def _p(self):
        return self._theme.palette

    # ══════════════════════════════════════════════════════
    # Services inject — tên param khớp với main_window.py
    # ══════════════════════════════════════════════════════

    def set_services(
        self, *,
        whisper_service=None,
        speaker_identifier=None,
        translation_service=None,
        db=None,
        diarizer=None,
    ):
        """
        Tên param khớp với main_window.py:
            whisper_service     = self._current_asr._svc
            speaker_identifier  = self._speaker
            translation_service = self._translator
            db                  = self._db
            diarizer            = self._diarizer
        Có thể gọi lại với chỉ diarizer= để update diarizer sau khi swap model.
        """
        if whisper_service    is not None: self._whisper    = whisper_service
        if speaker_identifier is not None: self._speaker    = speaker_identifier
        if translation_service is not None: self._translator = translation_service
        if db                 is not None: self._db         = db
        if diarizer           is not None: self._diarizer   = diarizer

        # Tạo worker khi đủ services lần đầu
        if (self._worker is None
                and self._whisper is not None
                and self._speaker is not None
                and self._translator is not None
                and self._db is not None):
            self._worker = TranscriptionWorker(
                whisper       = self._whisper,
                speaker       = self._speaker,
                translator    = self._translator,
                db            = self._db,
                on_english    = self._on_english,
                on_vietnamese = self._on_vietnamese,
            )
            self._btn_join.setEnabled(True)

    # ══════════════════════════════════════════════════════
    # UI Build
    # ══════════════════════════════════════════════════════

    def _build_ui(self):
        p = self._p
        self.setStyleSheet(f"background:{p['BG_BASE']};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        # ── Title ──────────────────────────────────────
        title_row = QHBoxLayout()
        pt = QLabel("GHI ÂM CUỘC HỌP")
        pt.setFont(QFont("JetBrains Mono", 18))
        pt.setStyleSheet(
            f"color:{p['TEXT_PRI']}; background:transparent; letter-spacing:1px;"
        )
        title_row.addWidget(pt)
        title_row.addStretch()
        lay.addLayout(title_row)

        # ── Join card ───────────────────────────────────
        lay.addWidget(self._build_join_card())

        # ── Transcript + Summary row ────────────────────
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

    def _build_join_card(self) -> QFrame:
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
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        icon_lbl = QLabel("🎙")
        icon_lbl.setFont(QFont("JetBrains Mono", 14))
        icon_lbl.setStyleSheet("background:transparent;")
        title_lbl = QLabel("Google Meet")
        title_lbl.setFont(QFont("JetBrains Mono", 11))
        title_lbl.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent;")
        self._lbl_status = QLabel("Chưa kết nối")
        self._lbl_status.setFont(QFont("JetBrains Mono", 8))
        self._lbl_status.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(6)
        hdr.addWidget(title_lbl)
        hdr.addStretch()
        hdr.addWidget(self._lbl_status)
        lay.addLayout(hdr)
        lay.addWidget(self._divider())

        # URL input + buttons
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._input_url = QLineEdit()
        self._input_url.setPlaceholderText(
            "Dán link Google Meet vào đây… (meet.google.com/xxx-yyy-zzz)"
        )
        self._input_url.setFont(QFont("JetBrains Mono", 9))
        self._input_url.setFixedHeight(36)
        self._input_url.setStyleSheet(f"""
            QLineEdit {{
                background:{p['BG_BASE']};
                color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER2']};
                border-radius:6px;
                padding:0 12px;
            }}
            QLineEdit:focus {{ border-color:{p['ACCENT']}; }}
        """)
        self._input_url.returnPressed.connect(self._on_btn_join)

        self._btn_join = QPushButton("  THAM GIA")
        self._btn_join.setFixedHeight(36)
        self._btn_join.setEnabled(False)
        self._btn_join.setFont(QFont("JetBrains Mono", 9))
        self._btn_join.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_join.setStyleSheet(f"""
            QPushButton {{
                background:{p['ACCENT']}; color:#fff;
                border:none; border-radius:6px;
                padding:0 20px; letter-spacing:1px;
            }}
            QPushButton:hover    {{ background:{p['ACCENT_HO']}; }}
            QPushButton:disabled {{ background:{p['BG_CARD2']}; color:{p['TEXT_SEC']}; }}
        """)
        self._btn_join.clicked.connect(self._on_btn_join)

        self._btn_leave = QPushButton("  DỪNG & LƯU")
        self._btn_leave.setFixedHeight(36)
        self._btn_leave.setEnabled(False)
        self._btn_leave.setFont(QFont("JetBrains Mono", 9))
        self._btn_leave.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_leave.setStyleSheet(f"""
            QPushButton {{
                background:rgba(52,211,153,0.15); color:#34D399;
                border:1px solid rgba(52,211,153,0.3);
                border-radius:6px; padding:0 20px; letter-spacing:1px;
            }}
            QPushButton:hover    {{ background:rgba(52,211,153,0.25); }}
            QPushButton:disabled {{ background:transparent; color:{p['TEXT_DIM']};
                                    border-color:{p['BORDER']}; }}
        """)
        self._btn_leave.clicked.connect(self._on_btn_leave)

        input_row.addWidget(self._input_url, stretch=1)
        input_row.addWidget(self._btn_join)
        input_row.addWidget(self._btn_leave)
        lay.addLayout(input_row)

        # Info label
        self._lbl_info = QLabel(
            "💡 Bot sẽ tham gia với tên \"Meeting Recorder\". "
            "Host cần bấm Admit để cho vào phòng."
        )
        self._lbl_info.setFont(QFont("JetBrains Mono", 8))
        self._lbl_info.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        self._lbl_info.setWordWrap(True)
        lay.addWidget(self._lbl_info)

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
            f"color:{color}; background:transparent; letter-spacing:2px;"
        )
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
                border:none;
                selection-background-color:rgba(59,110,245,0.25);
            }}
            QScrollBar:vertical {{
                background:{p['BG_CARD']}; width:5px; border-radius:3px;
            }}
            QScrollBar::handle:vertical {{
                background:{p['BORDER2']}; border-radius:3px; min-height:20px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        lay.addWidget(txt, stretch=1)
        return card, txt

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
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._summary_panel = SummaryPanel(self._theme, embedded=True)
        lay.addWidget(self._summary_panel)
        return card

    def _divider(self) -> QFrame:
        p = self._p
        d = QFrame()
        d.setFrameShape(QFrame.Shape.HLine)
        d.setFixedHeight(1)
        d.setStyleSheet(f"background:{p['BORDER']}; border:none;")
        return d

    # ══════════════════════════════════════════════════════
    # Button handlers
    # ══════════════════════════════════════════════════════

    def _on_btn_join(self):
        url = self._input_url.text().strip()
        if not url:
            self._set_status_label("⚠ Vui lòng nhập link Meet", "#F87171")
            return
        if "meet.google.com" not in url:
            self._set_status_label("⚠ Link không hợp lệ", "#F87171")
            return
        self._start_meeting(url)

    def _on_btn_leave(self):
        self._stop_meeting()

    # ══════════════════════════════════════════════════════
    # Meeting FSM
    # ══════════════════════════════════════════════════════

    def _start_meeting(self, url: str):
        # Setup DB recording
        os.makedirs(AUDIO_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._current_wav = os.path.join(AUDIO_DIR, f"meet_{timestamp}.wav")
        self._db.start_recording(audio_path=self._current_wav)

        # Setup VAD + Worker
        self._vad = VADProcessor(
            on_speech_chunk=self._on_speech_chunk,
            threshold=0.25, min_speech_ms=200,
            min_silence_ms=600, max_speech_ms=1500,
        )
        self._speaker.reset()
        self._worker.start()

        # Reset stream buffer
        self._vad_just_flushed = False
        with self._stream_buf_lock:
            self._stream_buf.clear()

        # Setup + start MeetCapture
        self._meet = MeetCapture(
            on_data   = self._on_audio_data,
            on_status = self._on_meet_status,
        )
        self._meet.join(url)

        # UI
        self._is_recording = True
        self._btn_join.setEnabled(False)
        self._btn_leave.setEnabled(True)
        self._input_url.setEnabled(False)
        self._record_seconds = 0
        self._record_timer.start(1000)
        self.status_changed.emit("recording")

    def _stop_meeting(self):
        self._is_recording = False
        self._flush_timer.stop()
        self._record_timer.stop()

        if self._meet:
            self._meet.stop()
            self._meet = None

        if self._vad:
            self._vad.reset()

        if self._worker:
            self._worker.stop()

        self._saved_rec_id = self._db._current_rec_id
        self._db.stop_recording()

        # UI
        self._btn_join.setEnabled(True)
        self._btn_leave.setEnabled(False)
        self._input_url.setEnabled(True)
        self._set_status_label("✔ Đã lưu", "#34D399")
        self.status_changed.emit("ready")

        # Diarization sau khi lưu
        if self._diarizer and self._current_wav and self._saved_rec_id:
            threading.Thread(
                target=self._run_diarization,
                args=(self._saved_rec_id, self._current_wav),
                daemon=True,
            ).start()
        else:
            if self._saved_rec_id:
                segs = self._db.get_recording_segments(self._saved_rec_id)
                self._summary_panel.load(segs, auto_run=True)

    # ══════════════════════════════════════════════════════
    # Audio callbacks
    # ══════════════════════════════════════════════════════

    def _on_audio_data(self, data: bytes):
        if not self._is_recording:
            return
        if self._vad:
            self._vad.add(data)
        with self._stream_buf_lock:
            self._stream_buf.extend(data)

    def _on_speech_chunk(self, chunk: bytes):
        with self._stream_buf_lock:
            self._stream_buf.clear()
            self._vad_just_flushed = True
        if self._worker:
            self._worker.enqueue(chunk, is_final=True)

    def _flush_stream_buf(self):
        if not self._is_recording:
            return
        with self._stream_buf_lock:
            if self._vad_just_flushed:
                self._vad_just_flushed = False
                return
            if len(self._stream_buf) < _FLUSH_BYTES:
                return
            chunk = bytes(self._stream_buf)
            self._stream_buf.clear()
        if self._worker:
            self._worker.enqueue(chunk, is_final=False)

    def _on_english(self, text: str, speaker_id: int):
        self._signals.english_received.emit(text, speaker_id)

    def _on_vietnamese(self, text: str, speaker_id: int):
        self._signals.vietnamese_received.emit(text, speaker_id)

    # ══════════════════════════════════════════════════════
    # Meet status callback (từ background thread)
    # ══════════════════════════════════════════════════════

    def _on_meet_status(self, status: MeetStatus, msg: str):
        """Gọi từ MeetCapture thread → QTimer.singleShot để về main thread."""
        QTimer.singleShot(0, lambda: self._apply_meet_status(status, msg))

    def _apply_meet_status(self, status: MeetStatus, msg: str):
        if status == MeetStatus.CONNECTING:
            self._set_status_label("⏳ Đang kết nối…", "#FBBF24")

        elif status == MeetStatus.WAITING:
            self._set_status_label("⏳ Chờ host cho phép vào…", "#FBBF24")
            self._lbl_info.setText(
                "💡 Vào Google Meet của bạn và bấm Admit cho \"Meeting Recorder\""
            )

        elif status == MeetStatus.IN_MEETING:
            self._set_status_label("● Đang ghi âm", "#F87171")
            self._lbl_info.setText(
                "🎙 Bot đang trong phòng họp và ghi âm. "
                "Bấm \"DỪNG & LƯU\" khi xong."
            )
            self._flush_timer.start()
            self.status_changed.emit("recording")

        elif status == MeetStatus.ENDED:
            self._set_status_label("✔ Cuộc họp đã kết thúc", "#34D399")
            self._stop_meeting()

        elif status == MeetStatus.ERROR:
            self._set_status_label(f"⚠ {msg}", "#F87171")
            self._btn_join.setEnabled(True)
            self._btn_leave.setEnabled(False)
            self._input_url.setEnabled(True)
            self._is_recording = False
            self.status_changed.emit("ready")

    # ══════════════════════════════════════════════════════
    # Diarization
    # ══════════════════════════════════════════════════════

    def _run_diarization(self, recording_id: str, wav_path: str):
        try:
            turns = self._diarizer.diarize(wav_path)
            if not turns:
                self._signals.diarization_done.emit(recording_id, False)
                return
            db_segs           = self._db.get_recording_segments(recording_id)
            pyannote_speakers = len(set(t["speaker"] for t in turns))
            live_speakers     = len(set(s["speaker_index"] for s in db_segs))
            if pyannote_speakers < live_speakers:
                print(f"[DIARIZE] Skip — pyannote({pyannote_speakers}) < live({live_speakers})")
                self._signals.diarization_done.emit(recording_id, False)
                return
            assignments = self._diarizer.assign_speakers(turns, db_segs)
            if assignments:
                self._db.update_segment_speakers(recording_id, assignments)
            self._signals.diarization_done.emit(recording_id, bool(assignments))
        except Exception as e:
            print(f"[DIARIZE] Error: {e}")
            self._signals.diarization_done.emit(recording_id, False)

    def _on_diarization_done(self, recording_id: str, success: bool):
        if success:
            self._reload_transcript(recording_id)
        if self._saved_rec_id:
            segs = self._db.get_recording_segments(recording_id)
            self._summary_panel.load(segs, auto_run=True)

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

    def _reload_transcript(self, recording_id: str):
        self._txt_en.clear()
        self._txt_vi.clear()
        self._last_speaker_en = None
        self._last_speaker_vi = None
        segments = self._db.get_recording_segments(recording_id)
        for seg in segments:
            spk_idx = seg["speaker_index"]
            self._insert_text(self._txt_en, seg["text_en"], spk_idx, is_en=True)
            self._insert_text(self._txt_vi, seg["text_vi"], spk_idx, is_en=False)

    # ══════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════

    def _set_status_label(self, text: str, color: str):
        self._lbl_status.setText(text)
        self._lbl_status.setStyleSheet(f"color:{color}; background:transparent;")

    def _tick_record(self):
        self._record_seconds += 1
        m = self._record_seconds // 60
        s = self._record_seconds % 60
        self._set_status_label(f"● {m:02d}:{s:02d} — đang ghi âm", "#F87171")

    def apply_theme(self):
        pass  # TODO nếu cần re-apply theme động

    def cleanup(self):
        """Gọi khi app đóng."""
        self._flush_timer.stop()
        self._record_timer.stop()
        if self._meet:
            self._meet.stop()
        if self._vad:
            self._vad.reset()
        if self._worker:
            self._worker.stop()
        if self._db and self._is_recording:
            self._db.stop_recording()