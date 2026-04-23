import os
import threading

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QProgressBar,
    QFileDialog, QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat

from ui.constants import SPEAKER_COLORS
from ui.widgets   import SummaryPanel
import ui.icons as icons

# ══════════════════════════════════════════════════════════════════════════════
# ToggleSwitch
# ══════════════════════════════════════════════════════════════════════════════

class ToggleSwitch(QPushButton):
    """Pill-shaped toggle switch."""

    def __init__(self, label: str = "", parent=None, theme_manager=None):
        super().__init__(parent)
        self._theme = theme_manager
        self._label = label
        self.setCheckable(True)
        self.setChecked(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(32)
        self._update_style()
        self.toggled.connect(lambda _: self._update_style())

    def _update_style(self):
        on  = self.isChecked()
        p   = self._theme.palette if self._theme else {}
        accent   = p.get("ACCENT",   "#3B6EF5")
        bg_card  = p.get("BG_CARD2", "#1E2235")
        text_dim = p.get("TEXT_DIM", "#4A5470")
        text_pri = p.get("TEXT_PRI", "#E2E8F0")
        border   = p.get("BORDER2",  "#2A3050")
        indicator = "●" if on else "○"
        self.setText(f"  {indicator}  {self._label}  ")
        self.setStyleSheet(f"""
            QPushButton {{
                background: {accent if on else bg_card};
                color: {"#fff" if on else text_dim};
                border: 1px solid {"rgba(59,110,245,0.6)" if on else border};
                border-radius: 14px;
                padding: 0 10px;
                font-family: 'JetBrains Mono', monospace;
                font-size: 8pt;
            }}
            QPushButton:hover {{
                border-color: {accent};
                color: {"#fff" if on else text_pri};
            }}
        """)


# ══════════════════════════════════════════════════════════════════════════════
# UploadPage
# ══════════════════════════════════════════════════════════════════════════════

class UploadPage(QWidget):

    _sig_segment  = pyqtSignal(str, str, int, float, float, float, list)
    _sig_progress = pyqtSignal(int, str)
    _sig_error    = pyqtSignal(str)
    _sig_done     = pyqtSignal(str)

    recording_saved = pyqtSignal()

    SUPPORTED = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".webm"}

    def __init__(self, theme_manager, parent=None):
        super().__init__(parent)
        self._theme          = theme_manager
        self.current_file    = None
        self._current_rec_id = None
        self._last_spk_en    = None
        self._last_spk_vi    = None
        self._processing     = False
        self._cancel_flag    = False

        self.whisper_service     = None
        self.translation_service = None
        self.speaker_identifier  = None
        self._db                 = None
        self._diarizer           = None

        self._build_ui()

        self._sig_segment.connect(self._on_segment)
        self._sig_progress.connect(self._on_progress)
        self._sig_error.connect(self._on_error)
        self._sig_done.connect(self._on_done)

    @property
    def _p(self):
        return self._theme.palette

    # ── Services ──────────────────────────────────────────────────────────────

    def set_services(self, whisper_service, translation_service,
                     speaker_identifier=None, db=None, diarizer=None):
        self.whisper_service     = whisper_service
        self.translation_service = translation_service
        self.speaker_identifier  = speaker_identifier
        self._db                 = db
        self._diarizer           = diarizer

    # ── UI Build ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        p = self._p
        self.setStyleSheet(f"background:{p['BG_BASE']};")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        lbl = QLabel("UPLOAD AUDIO")
        lbl.setFont(QFont("JetBrains Mono", 18))
        lbl.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent; letter-spacing:1px;")
        root.addWidget(lbl)

        root.addWidget(self._build_drop_zone())

        prog_col = QVBoxLayout()
        prog_col.setSpacing(4)

        self._lbl_progress = QLabel("")
        self._lbl_progress.setFont(QFont("JetBrains Mono", 8))
        self._lbl_progress.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        self._lbl_progress.setVisible(False)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background:{p['BG_CARD2']}; border:none; border-radius:3px;
            }}
            QProgressBar::chunk {{
                background:{p['ACCENT']}; border-radius:3px;
            }}
        """)
        prog_col.addWidget(self._lbl_progress)
        prog_col.addWidget(self._progress)
        root.addLayout(prog_col)

        content_row = QHBoxLayout()
        content_row.setSpacing(14)

        self._card_en, self._txt_en = self._transcript_card("🇬🇧  ENGLISH",    "#60A5FA")
        self._card_vi, self._txt_vi = self._transcript_card("🇻🇳  TIẾNG VIỆT", "#34D399")
        self._card_summary          = self._build_summary_card()

        content_row.addWidget(self._card_en)
        content_row.addWidget(self._card_vi)
        content_row.addWidget(self._card_summary)
        root.addLayout(content_row, stretch=1)

    def _build_drop_zone(self) -> QFrame:
        p = self._p
        self._drop_card = QFrame()
        self._drop_card.setFixedHeight(150)
        self._drop_card.setAcceptDrops(True)
        self._drop_card.setStyleSheet(f"""
            QFrame {{
                background:{p['BG_CARD']};
                border:2px dashed {p['BORDER2']};
                border-radius:12px;
            }}
        """)
        self._drop_card.dragEnterEvent = self._drag_enter
        self._drop_card.dragLeaveEvent = self._drag_leave
        self._drop_card.dropEvent      = self._drop_event

        lay = QVBoxLayout(self._drop_card)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(6)

        self._lbl_icon = QLabel("⊕")
        self._lbl_icon.setFont(QFont("JetBrains Mono", 28))
        self._lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_icon.setStyleSheet(f"color:{p['ACCENT']}; background:transparent; border:none;")

        self._lbl_hint = QLabel("Kéo thả file audio vào đây")
        self._lbl_hint.setFont(QFont("JetBrains Mono", 11))
        self._lbl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_hint.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent; border:none;")

        self._lbl_sub = QLabel("WAV · MP3 · M4A · FLAC · OGG · AAC · MP4")
        self._lbl_sub.setFont(QFont("JetBrains Mono", 8))
        self._lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_sub.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent; border:none;")

        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_row.setSpacing(10)

        self._btn_browse = QPushButton("  Chọn file  ")
        self._btn_browse.setIcon(icons.get("browse", "#ffffff"))
        self._btn_browse.setFixedHeight(32)
        self._btn_browse.setFont(QFont("JetBrains Mono", 9))
        self._btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_browse.clicked.connect(self._chon_file)
        self._btn_browse.setStyleSheet(f"""
            QPushButton {{
                background:{p['ACCENT']}; color:#fff; border:none;
                border-radius:6px; padding:0 18px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:{p['ACCENT_HO']}; }}
            QPushButton:disabled {{ background:{p['BG_CARD2']}; color:{p['TEXT_DIM']}; }}
        """)

        self._btn_process = QPushButton("  Xử lý  ")
        self._btn_process.setIcon(icons.get("process", "#34D399"))
        self._btn_process.setFixedHeight(32)
        self._btn_process.setEnabled(False)
        self._btn_process.setFont(QFont("JetBrains Mono", 9))
        self._btn_process.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_process.clicked.connect(self._xu_ly)
        self._btn_process.setStyleSheet(f"""
            QPushButton {{
                background:rgba(52,211,153,0.15); color:#34D399;
                border:1px solid rgba(52,211,153,0.35);
                border-radius:6px; padding:0 18px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(52,211,153,0.28); }}
            QPushButton:disabled {{
                background:transparent; color:{p['TEXT_DIM']}; border-color:{p['BORDER']};
            }}
        """)

        self._toggle_diarize = ToggleSwitch(
            label="Nhận diện người nói",
            parent=self,
            theme_manager=self._theme,
        )
        self._toggle_diarize.setToolTip(
            "Bật: chạy diarization song song với ASR\n"
            "Tắt: bỏ qua, transcript không có speaker label"
        )

        self._btn_cancel = QPushButton("  Hủy  ")
        self._btn_cancel.setIcon(icons.get("stop", "#F87171"))
        self._btn_cancel.setFixedHeight(32)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setFont(QFont("JetBrains Mono", 9))
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._huy)
        self._btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background:rgba(248,113,113,0.12); color:#F87171;
                border:1px solid rgba(248,113,113,0.35);
                border-radius:6px; padding:0 18px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(248,113,113,0.25); }}
            QPushButton:disabled {{
                background:transparent; color:{p['TEXT_DIM']}; border-color:{p['BORDER']};
            }}
        """)

        self._btn_reset = QPushButton("  Làm mới  ")
        self._btn_reset.setIcon(icons.get("reset", "#94A3B8"))
        self._btn_reset.setFixedHeight(32)
        self._btn_reset.setEnabled(False)
        self._btn_reset.setFont(QFont("JetBrains Mono", 9))
        self._btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset.clicked.connect(self._lam_moi)
        self._btn_reset.setStyleSheet(f"""
            QPushButton {{
                background:rgba(148,163,184,0.10); color:#94A3B8;
                border:1px solid rgba(148,163,184,0.3);
                border-radius:6px; padding:0 18px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(148,163,184,0.22); }}
            QPushButton:disabled {{
                background:transparent; color:{p['TEXT_DIM']}; border-color:{p['BORDER']};
            }}
        """)

        btn_row.addWidget(self._btn_browse)
        btn_row.addWidget(self._btn_process)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_reset)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._toggle_diarize)

        self._lbl_filename = QLabel("")
        self._lbl_filename.setFont(QFont("JetBrains Mono", 8))
        self._lbl_filename.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_filename.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent; border:none;")

        lay.addWidget(self._lbl_icon)
        lay.addWidget(self._lbl_hint)
        lay.addWidget(self._lbl_sub)
        lay.addLayout(btn_row)
        lay.addWidget(self._lbl_filename)
        return self._drop_card

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
                background:{p['BG_CARD']}; border:1px solid {p['BORDER']}; border-radius:10px;
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

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFrameShape(QFrame.Shape.HLine)
        d.setFixedHeight(1)
        d.setStyleSheet(f"background:{self._p['BORDER']}; border:none;")
        return d

    # ── Drag & Drop ────────────────────────────────────────────────────────────

    def _drag_enter(self, event):
        p = self._p
        if event.mimeData().hasUrls():
            ext = os.path.splitext(event.mimeData().urls()[0].toLocalFile())[1].lower()
            if ext in self.SUPPORTED:
                event.acceptProposedAction()
                self._drop_card.setStyleSheet(f"""
                    QFrame {{
                        background:rgba(59,110,245,0.08);
                        border:2px dashed {p['ACCENT']};
                        border-radius:12px;
                    }}
                """)
                return
        event.ignore()

    def _drag_leave(self, event):
        p = self._p
        self._drop_card.setStyleSheet(f"""
            QFrame {{
                background:{p['BG_CARD']}; border:2px dashed {p['BORDER2']}; border-radius:12px;
            }}
        """)

    def _drop_event(self, event):
        self._drag_leave(event)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.splitext(path)[1].lower() in self.SUPPORTED:
                self._set_file(path)
            event.acceptProposedAction()

    # ── File selection ─────────────────────────────────────────────────────────

    def _chon_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn file audio",
            os.path.expanduser("~"),
            "Audio Files (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.mp4 *.webm)"
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        if self._processing:
            return
        self.current_file = path
        name    = os.path.basename(path)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        self._lbl_hint.setText("✔  File đã sẵn sàng")
        self._lbl_hint.setStyleSheet("color:#34D399; background:transparent; border:none;")
        self._lbl_sub.setText("Nhấn Xử lý để bắt đầu")
        self._lbl_filename.setText(f"📄  {name}  ({size_mb:.1f} MB)")
        self._btn_process.setEnabled(True)
        self._reset_content()

    # ── Processing ─────────────────────────────────────────────────────────────

    def _xu_ly(self):
        if not self.current_file or not self.whisper_service or self._processing:
            return
        self._processing  = True
        self._cancel_flag = False
        self._reset_content()
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._lbl_progress.setVisible(True)
        self._lbl_progress.setText("Đang chuẩn bị…")
        self._btn_process.setEnabled(False)
        self._btn_browse.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._btn_reset.setEnabled(False)
        self._lbl_hint.setText("⏳  Đang xử lý…")
        self._lbl_hint.setStyleSheet(f"color:{self._p['ACCENT']}; background:transparent; border:none;")
        use_diarize = self._toggle_diarize.isChecked()
        threading.Thread(
            target=self._process_background,
            args=(use_diarize,),
            daemon=True,
        ).start()

    def _process_background(self, use_diarize: bool):
        try:
            from core.upload_audio_processor import UploadAudioProcessor

            processor = UploadAudioProcessor(
                whisper_service     = self.whisper_service,
                translation_service = self.translation_service,
                diarizer            = self._diarizer if use_diarize else None,
                db                  = self._db,
            )

            rec_id = processor.process(
                file_path    = self.current_file,
                on_text      = lambda en, vi, spk: self._sig_segment.emit(
                    en, vi, spk, 0.0, 0.0, 0.9, []
                ),
                on_progress  = lambda pct, lbl: self._sig_progress.emit(pct, lbl),
                on_error     = lambda msg: self._sig_error.emit(msg),
                use_diarize  = use_diarize,
                cancel_flag  = self,   # truyền page để processor check _cancel_flag
            )

            if not self._cancel_flag:
                self._sig_done.emit(rec_id or "")
            else:
                self._sig_error.emit("__cancelled__")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._sig_error.emit(str(e))

    # ── Cancel & Reset ────────────────────────────────────────────────────────

    def _huy(self):
        if not self._processing:
            return
        self._cancel_flag = True
        self._btn_cancel.setEnabled(False)
        self._lbl_hint.setText("⏳  Đang hủy…")
        self._lbl_hint.setStyleSheet("color:#F87171; background:transparent; border:none;")
        self._lbl_progress.setText("Đang dừng tiến trình…")

    def _lam_moi(self):
        if self._processing:
            return
        self.current_file    = None
        self._current_rec_id = None
        self._cancel_flag    = False
        self._reset_content()
        self._lbl_hint.setText("Kéo thả file audio vào đây")
        self._lbl_hint.setStyleSheet(f"color:{self._p['TEXT_PRI']}; background:transparent; border:none;")
        self._lbl_sub.setText("WAV · MP3 · M4A · FLAC · OGG · AAC · MP4")
        self._lbl_filename.setText("")
        self._lbl_icon.setText("⊕")
        self._progress.setVisible(False)
        self._progress.setValue(0)
        self._lbl_progress.setVisible(False)
        self._btn_process.setEnabled(False)
        self._btn_cancel.setEnabled(False)
        self._btn_reset.setEnabled(False)

    # ── Signal handlers ────────────────────────────────────────────────────────

    def _on_progress(self, value: int, label: str):
        self._progress.setValue(value)
        self._lbl_progress.setText(label)

    def _on_segment(self, text_en: str, text_vi: str, speaker_id: int,
                    start: float, end: float, conf: float, words: list):
        self._insert_text(self._txt_en, text_en, speaker_id, is_en=True)
        self._insert_text(self._txt_vi, text_vi, speaker_id, is_en=False)

    def _on_done(self, rec_id: str):
        self._processing  = False
        self._cancel_flag = False
        self._btn_process.setEnabled(True)
        self._btn_browse.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._btn_reset.setEnabled(True)
        self._lbl_hint.setText("✔  Hoàn thành!")
        self._lbl_hint.setStyleSheet("color:#34D399; background:transparent; border:none;")
        self._lbl_sub.setText("Kéo thả hoặc chọn file khác để tiếp tục")

        QTimer.singleShot(2000, lambda: (
            self._progress.setVisible(False),
            self._lbl_progress.setVisible(False),
        ))

        self._current_rec_id = rec_id
        if rec_id and self._db:
            segs = self._db.get_recording_segments(rec_id)
            if segs:
                self._summary_panel.load(segs, auto_run=True)
            self.recording_saved.emit()

    def _on_error(self, msg: str):
        self._processing  = False
        self._cancel_flag = False
        self._btn_process.setEnabled(True)
        self._btn_browse.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._btn_reset.setEnabled(True)
        self._progress.setVisible(False)
        self._lbl_progress.setVisible(False)
        if msg == "__cancelled__":
            self._lbl_hint.setText("✕  Đã hủy")
            self._lbl_hint.setStyleSheet("color:#F87171; background:transparent; border:none;")
            self._lbl_sub.setText("Nhấn Làm mới để bắt đầu lại")
        else:
            self._lbl_hint.setText("✕  Lỗi xử lý")
            self._lbl_hint.setStyleSheet("color:#F87171; background:transparent; border:none;")
            self._lbl_sub.setText(msg[:100])

    def _on_summary_ready(self, result):
        if not self._current_rec_id or not self._db:
            return
        if result.error:
            return
        try:
            self._db.save_summary(self._current_rec_id, result)
        except Exception as e:
            print(f"[UPLOAD] Could not save summary: {e}")

    # ── Transcript helpers ─────────────────────────────────────────────────────

    def _insert_text(self, widget: QTextEdit, text: str,
                     speaker_id: int, is_en: bool):
        p     = self._p
        color = SPEAKER_COLORS[(speaker_id - 1) % len(SPEAKER_COLORS)]
        last  = self._last_spk_en if is_en else self._last_spk_vi
        show_speaker = self._toggle_diarize.isChecked()

        cur = widget.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)

        if show_speaker and speaker_id != last:
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
                self._last_spk_en = speaker_id
            else:
                self._last_spk_vi = speaker_id

        fmt_txt = QTextCharFormat()
        fmt_txt.setForeground(QColor(p['TEXT_PRI']))
        fmt_txt.setFontPointSize(11)
        cur.insertText(text + " ", fmt_txt)
        widget.setTextCursor(cur)
        widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum())

    def _reset_content(self):
        self._txt_en.clear()
        self._txt_vi.clear()
        self._last_spk_en = None
        self._last_spk_vi = None
        self._summary_panel.clear()

    # ── Theme ──────────────────────────────────────────────────────────────────

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
        txt_style = f"""
            QTextEdit {{
                background:transparent; color:{p['TEXT_PRI']}; border:none;
            }}
        """
        self._txt_en.setStyleSheet(txt_style)
        self._txt_vi.setStyleSheet(txt_style)
        self._summary_panel.apply_theme()