import os
import sys
import threading

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QStackedWidget, QApplication,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui  import QFont, QColor, QPalette

from core.audio_capture        import AudioCapture
from core.asr_engines          import WhisperASR, Wav2Vec2ASR, VoskASR
from core.speaker_identifier   import SpeakerIdentifier
from core.translation_service  import TranslationService
from core.transcription_worker import TranscriptionWorker
from core.speaker_diarizer     import SpeakerDiarizer
from core.fast_diarizer        import FastDiarizer
from db.database_service       import DatabaseService

from ui.signals             import AppSignals
from ui.widgets             import NavItem
from ui.pages.live_page     import LivePage
from ui.pages.history_page  import HistoryPage
from ui.pages.settings_page import SettingsPage
from ui.pages.upload_page   import UploadPage
from ui.pages.meeting_page  import MeetingPage
from ui.pages.models_page   import ModelsPage

from ui.theme               import ThemeManager

PLATFORM = sys.platform


def _default_audio_device():
    if PLATFORM == "win32":
        return None
    return os.environ.get("ALSA_DEVICE", "default")


# ══════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    """Cửa sổ chính — chỉ lo sidebar, routing, model loading.
    Toàn bộ Session logic nằm trong LivePage.
    """
    # Signal để callback swap model về main thread an toàn
    _model_swap_result = pyqtSignal(str, bool, str)  # (category, success, error_or_model_id)

    def __init__(self):
        super().__init__()
        self._sidebar_collapsed = False
        self._signals           = AppSignals()
        self._theme             = ThemeManager()

        self._signals.status_changed.connect(self._update_status)
        self._signals.model_loaded.connect(self._on_model_loaded)
        self._model_swap_result.connect(self._on_model_swap_result)

        self._build_ui()
        self._apply_theme()
        self._theme.apply(QApplication.instance())
        threading.Thread(target=self._load_model, daemon=True).start()

    @property
    def _p(self) -> dict:
        return self._theme.palette

    # ══════════════════════════════════════════════════════
    # Model loading
    # ══════════════════════════════════════════════════════

    def _load_model(self):
        self._signals.status_changed.emit("Loading Whisper…")
        self._current_asr = WhisperASR(model_path="tiny.en", device="cpu")

        self._signals.status_changed.emit("Loading Speaker…")
        self._speaker = SpeakerIdentifier(device="cpu")

        self._signals.status_changed.emit("Starting Translator…")
        self._translator = TranslationService()

        self._signals.status_changed.emit("Connecting DB…")
        self._db = DatabaseService()

        self._capture = AudioCapture(device=_default_audio_device())

        self._signals.status_changed.emit("Loading Diarizer…")
        try:
            mode = os.environ.get("DIARIZER_MODE", "fast").lower()
            if mode == "pyannote":
                self._diarizer = SpeakerDiarizer()
                print("[UI] Diarizer: pyannote (chính xác, chậm)")
            else:
                self._diarizer = FastDiarizer()
                print("[UI] Diarizer: simple-diarizer (nhanh, phù hợp demo)")
        except Exception as e:
            print(f"[UI] Diarizer not available: {e}")
            self._diarizer = None

        self._signals.model_loaded.emit()

    def _on_model_loaded(self):
        self._stack.setCurrentWidget(self._main_page)
        self._update_status("ready")

        # Inject services vào LivePage — LivePage tự tạo worker bên trong
        self._live_page.set_services(
            whisper    = self._current_asr,
            speaker    = self._speaker,
            translator = self._translator,
            db         = self._db,
            capture    = self._capture,
            diarizer   = self._diarizer,
        )
        self._history_page.db = self._db

        self._upload_page.set_services(
            whisper_service     = self._current_asr._svc,
            translation_service = self._translator,
            speaker_identifier  = self._speaker,
            db                  = self._db,
            diarizer = self._diarizer,
            
        )
        self._meeting_page.set_services(
            whisper_service     = self._current_asr._svc,
            speaker_identifier  = self._speaker,
            translation_service = self._translator,
            db                  = self._db,
            diarizer            = self._diarizer,
        )
        self._models_page.set_active_model("whisper", "tiny.en")

    # ══════════════════════════════════════════════════════
    # UI Build
    # ══════════════════════════════════════════════════════

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
        p = self._p
        self._loading_page = QWidget()
        self._loading_page.setStyleSheet(f"background:{p['BG_BASE']};")
        lay = QVBoxLayout(self._loading_page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("◎")
        icon.setFont(QFont("JetBrains Mono", 56))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color:{p['ACCENT']}; background:transparent;")

        title = QLabel("SPEECH TRANSLATOR")
        title.setFont(QFont("JetBrains Mono", 14))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color:{p['TEXT_PRI']}; letter-spacing:8px; background:transparent;"
        )

        self._lbl_load_sub = QLabel("Initializing…")
        self._lbl_load_sub.setFont(QFont("JetBrains Mono", 9))
        self._lbl_load_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_load_sub.setStyleSheet(
            f"color:{p['TEXT_SEC']}; background:transparent;"
        )
        self._signals.status_changed.connect(lambda s: self._lbl_load_sub.setText(s))

        lay.addWidget(icon)
        lay.addSpacing(10)
        lay.addWidget(title)
        lay.addSpacing(6)
        lay.addWidget(self._lbl_load_sub)
        return self._loading_page

    def _build_main_page(self) -> QWidget:
        p = self._p
        self._main_page = QWidget()
        self._main_page.setStyleSheet(f"background:{p['BG_BASE']};")

        root = QHBoxLayout(self._main_page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = self._build_sidebar()
        root.addWidget(self._sidebar)

        self._page_stack = QStackedWidget()
        self._page_stack.setStyleSheet("background:transparent;")

        self._live_page = LivePage(theme_manager=self._theme)
        self._live_page.status_changed.connect(self._update_status)
        self._page_stack.addWidget(self._live_page)

        self._history_page = HistoryPage(db=None, theme_manager=self._theme)
        self._page_stack.addWidget(self._history_page)

        self._settings_page = SettingsPage(
            theme_manager=self._theme,
            app=QApplication.instance(),
        )
        self._settings_page.theme_changed.connect(self._on_theme_toggled)
        self._settings_page.sidebar_toggled.connect(self._toggle_sidebar)
        self._page_stack.addWidget(self._settings_page)

        self._upload_page = UploadPage(theme_manager=self._theme)
        self._upload_page.recording_saved.connect(self._on_upload_saved)
        self._page_stack.addWidget(self._upload_page)

        self._meeting_page = MeetingPage(theme_manager=self._theme)
        self._meeting_page.status_changed.connect(self._update_status)
        self._page_stack.addWidget(self._meeting_page)

        self._models_page = ModelsPage(theme_manager=self._theme)
        self._models_page.model_load_requested.connect(self._on_model_load_requested)
        # self._models_page.api_config_changed.connect(self._on_api_config_changed)
        self._page_stack.addWidget(self._models_page)

        self._nav_session.clicked.connect(self._show_session_page)
        self._nav_meeting.clicked.connect(self._show_meeting_page)
        self._nav_history.clicked.connect(self._show_history_page)
        self._nav_upload.clicked.connect(self._show_upload_page)
        self._nav_models.clicked.connect(self._show_models_page)
        self._nav_settings.clicked.connect(self._show_settings_page)

        root.addWidget(self._page_stack, stretch=1)
        return self._main_page

    def _build_sidebar(self) -> QWidget:
        p = self._p
        sb = QWidget()
        sb.setFixedWidth(196)
        sb.setStyleSheet(f"""
            QWidget {{
                background:{p['BG_SIDEBAR']};
                border-right:1px solid {p['BORDER']};
            }}
        """)
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_logo())
        lay.addSpacing(12)

        self._nav_session  = NavItem("▶", "Session",  active=True)
        self._nav_meeting  = NavItem("◈", "Meeting")
        self._nav_history  = NavItem("◷", "History")
        self._nav_upload   = NavItem("⊕", "Upload")
        self._nav_models   = NavItem("◈", "Models")
        self._nav_settings = NavItem("◉", "Settings")

        for btn in [self._nav_session, self._nav_meeting,
                    self._nav_history, self._nav_upload,
                    self._nav_models, self._nav_settings]:
            lay.addWidget(btn)

        lay.addStretch()
        lay.addWidget(self._build_sidebar_footer())
        return sb

    def _build_logo(self) -> QWidget:
        p = self._p
        self._logo_widget = QWidget()
        self._logo_widget.setFixedHeight(62)
        self._logo_widget.setStyleSheet(
            f"background:{p['BG_SIDEBAR']}; border-bottom:1px solid {p['BORDER']};"
        )
        ll = QHBoxLayout(self._logo_widget)
        ll.setContentsMargins(12, 0, 8, 0)
        ll.setSpacing(8)

        li = QLabel("◎")
        li.setFont(QFont("JetBrains Mono", 20))
        li.setStyleSheet(f"color:{p['ACCENT']}; border:none;")
        li.setFixedWidth(28)

        self._logo_text_widget = QWidget()
        self._logo_text_widget.setStyleSheet("border:none; background:transparent;")
        ltl = QVBoxLayout(self._logo_text_widget)
        ltl.setContentsMargins(0, 0, 0, 0)
        ltl.setSpacing(0)
        self._logo_l1 = QLabel("SPEECH")
        self._logo_l1.setFont(QFont("JetBrains Mono", 9))
        self._logo_l1.setStyleSheet(
            f"color:{p['TEXT_PRI']}; letter-spacing:3px; background:transparent;"
        )
        self._logo_l2 = QLabel("TRANSLATOR")
        self._logo_l2.setFont(QFont("JetBrains Mono", 7))
        self._logo_l2.setStyleSheet(
            f"color:{p['TEXT_SEC']}; letter-spacing:2px; background:transparent;"
        )
        ltl.addWidget(self._logo_l1)
        ltl.addWidget(self._logo_l2)

        ll.addWidget(li)
        ll.addWidget(self._logo_text_widget, stretch=1)
        return self._logo_widget

    def _build_sidebar_footer(self) -> QWidget:
        p = self._p
        self._sidebar_footer = QWidget()
        self._sidebar_footer.setFixedHeight(50)
        self._sidebar_footer.setStyleSheet(
            f"background:{p['BG_SIDEBAR']}; border-top:1px solid {p['BORDER']};"
        )
        fl = QHBoxLayout(self._sidebar_footer)
        fl.setContentsMargins(18, 0, 18, 0)
        fl.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color:{p['TEXT_SEC']}; font-size:9px; border:none;")
        self._lbl_status = QLabel("Loading…")
        self._lbl_status.setFont(QFont("JetBrains Mono", 8))
        self._lbl_status.setStyleSheet(f"color:{p['TEXT_SEC']}; border:none;")

        fl.addWidget(self._dot)
        fl.addWidget(self._lbl_status)
        fl.addStretch()
        return self._sidebar_footer

    def _toggle_sidebar(self):
        self._sidebar_collapsed = not self._sidebar_collapsed
        target_width = 48 if self._sidebar_collapsed else 196

        self._anim = QPropertyAnimation(self._sidebar, b"minimumWidth")
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.setStartValue(self._sidebar.width())
        self._anim.setEndValue(target_width)
        self._anim.start()

        self._anim2 = QPropertyAnimation(self._sidebar, b"maximumWidth")
        self._anim2.setDuration(180)
        self._anim2.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim2.setStartValue(self._sidebar.width())
        self._anim2.setEndValue(target_width)
        self._anim2.start()

        show = not self._sidebar_collapsed
        self._logo_text_widget.setVisible(show)
        self._lbl_status.setVisible(show)
        self._dot.setVisible(show)

        for btn in [self._nav_session, self._nav_meeting,
                    self._nav_history, self._nav_upload,
                    self._nav_models, self._nav_settings]:
            btn.set_collapsed(self._sidebar_collapsed)

        if hasattr(self, "_settings_page"):
            self._settings_page.update_sidebar_btn(self._sidebar_collapsed)

    # ══════════════════════════════════════════════════════
    # Navigation
    # ══════════════════════════════════════════════════════

    def _uncheck_all(self):
        for btn in [self._nav_session, self._nav_meeting,
                    self._nav_history, self._nav_upload,
                    self._nav_models, self._nav_settings]:
            btn.setChecked(False)

    def _show_session_page(self):
        self._uncheck_all()
        self._nav_session.setChecked(True)
        self._page_stack.setCurrentWidget(self._live_page)

    def _show_meeting_page(self):
        self._uncheck_all()
        self._nav_meeting.setChecked(True)
        self._page_stack.setCurrentWidget(self._meeting_page)

    def _show_history_page(self):
        self._uncheck_all()
        self._nav_history.setChecked(True)
        self._page_stack.setCurrentWidget(self._history_page)
        if hasattr(self, "_db"):
            self._history_page.refresh()

    def _show_upload_page(self):
        self._uncheck_all()
        self._nav_upload.setChecked(True)
        self._page_stack.setCurrentWidget(self._upload_page)

    def _show_models_page(self):
        self._uncheck_all()
        self._nav_models.setChecked(True)
        self._page_stack.setCurrentWidget(self._models_page)

    def _show_settings_page(self):
        self._uncheck_all()
        self._nav_settings.setChecked(True)
        self._page_stack.setCurrentWidget(self._settings_page)

    # ══════════════════════════════════════════════════════
    # Model swap (từ ModelsPage)
    # ══════════════════════════════════════════════════════

    def _on_model_load_requested(self, category: str, model_id: str, extra: dict):
        def _load():
            try:
                if category == "whisper":
                    new_engine = WhisperASR(
                        model_path=model_id,
                        device=extra.get("device", "cpu"),
                        initial_prompt=extra.get("initial_prompt", ""),
                    )
                elif category == "wav2vec2":
                    new_engine = Wav2Vec2ASR(
                        model_id=model_id,
                        device=extra.get("device", "cpu"),
                    )
                elif category == "vosk":
                    new_engine = VoskASR(model_path=model_id)
                elif category == "diarization":
                    if model_id == "fast":
                        new_diarizer = FastDiarizer()
                    else:
                        new_diarizer = SpeakerDiarizer(
                            model_name=model_id,
                            hf_token=extra.get("hf_token", ""),
                        )
                    self._diarizer = new_diarizer
                    # emit signal về main thread để update UI
                    self._model_swap_result.emit(category, True, model_id)
                    return
                else:
                    raise ValueError(f"Unknown category: {category}")

                # Load xong mới swap — không có khoảng trống
                old_engine        = getattr(self, "_current_asr", None)
                self._current_asr = new_engine
                self._live_page.swap_asr(new_engine)

                # Unload cũ SAU KHI swap xong
                if old_engine is not None:
                    old_engine.unload()

                # emit signal về main thread
                self._model_swap_result.emit(category, True, model_id)

            except Exception as exc:
                import traceback; traceback.print_exc()
                self._model_swap_result.emit(category, False, str(exc))

        threading.Thread(target=_load, daemon=True).start()

    def _on_model_swap_result(self, category: str, success: bool, model_id_or_error: str):
        """Chạy trên main thread — an toàn để update UI."""
        print(f"[UI] swap result: category={category} success={success}")
        self._models_page.on_load_done(
            category,
            success=success,
            error=model_id_or_error if not success else "",
        )
        if success:
            self._models_page.set_active_model(category, model_id_or_error)
            # Cập nhật diarizer cho meeting page nếu cần
            if category == "diarization":
                self._meeting_page.set_services(diarizer=self._diarizer)

    # def _on_api_config_changed(self, service: str, config: dict):
    #     if service == "translation":
    #         os.environ["TRANSLATION_PROVIDER"] = config.get("provider", "")
    #         os.environ["TRANSLATION_MODEL"]    = config.get("model", "")
    #         if config.get("api_key"):
    #             os.environ["TRANSLATION_API_KEY"] = config["api_key"]
    #     elif service == "summarize":
    #         os.environ["SUMMARIZE_PROVIDER"] = config.get("provider", "")
    #         os.environ["SUMMARIZE_MODEL"]    = config.get("model", "")
    #         if config.get("api_key"):
    #             os.environ["SUMMARIZE_API_KEY"] = config["api_key"]

    # ══════════════════════════════════════════════════════
    # Misc
    # ══════════════════════════════════════════════════════

    def _update_status(self, status: str):
        self._lbl_status.setText(status)

    def _on_upload_saved(self):
        if hasattr(self, "_history_page") and hasattr(self, "_db"):
            self._history_page.refresh()
            print("[UI] History refreshed after upload")

    def _on_theme_toggled(self, mode: str = None):
        p = self._p

        # 1. Global app stylesheet — đây là nguồn gốc fix bug text color:
        #    QWidget { color: TEXT_PRI } trong _make_stylesheet sẽ cascade
        #    xuống toàn bộ widget tree sau lệnh này.
        self._theme.apply(QApplication.instance())

        # 2. QMainWindow palette — cần set riêng vì QMainWindow không inherit
        #    stylesheet color vào QPalette (Qt quirk)
        self.setStyleSheet(f"QMainWindow {{ background:{p['BG_BASE']}; }}")
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window,     QColor(p['BG_BASE']))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(p['TEXT_PRI']))
        self.setPalette(pal)

        # 3. Các widget có inline setStyleSheet() — cần re-apply thủ công
        #    vì inline style có độ ưu tiên cao hơn global stylesheet
        self._main_page.setStyleSheet(f"background:{p['BG_BASE']};")
        self._loading_page.setStyleSheet(f"background:{p['BG_BASE']};")
        self._sidebar.setStyleSheet(f"""
            QWidget {{
                background:{p['BG_SIDEBAR']};
                border-right:1px solid {p['BORDER']};
            }}
        """)
        self._logo_widget.setStyleSheet(
            f"background:{p['BG_SIDEBAR']}; border-bottom:1px solid {p['BORDER']};"
        )
        self._logo_l1.setStyleSheet(
            f"color:{p['TEXT_PRI']}; letter-spacing:3px; background:transparent;"
        )
        self._logo_l2.setStyleSheet(
            f"color:{p['TEXT_SEC']}; letter-spacing:2px; background:transparent;"
        )
        self._sidebar_footer.setStyleSheet(
            f"background:{p['BG_SIDEBAR']}; border-top:1px solid {p['BORDER']};"
        )
        self._lbl_status.setStyleSheet(f"color:{p['TEXT_SEC']}; border:none;")
        self._dot.setStyleSheet(f"color:{p['TEXT_SEC']}; font-size:9px; border:none;")

        # 4. NavItem — mỗi item có inline style riêng, cần notify
        for btn in [self._nav_session, self._nav_meeting,
                    self._nav_history, self._nav_upload,
                    self._nav_models, self._nav_settings]:
            if hasattr(btn, "apply_theme"):
                btn.apply_theme()

        # 5. Gọi apply_theme() trên TẤT CẢ pages
        for page in [
            self._live_page, self._meeting_page, self._history_page,
            self._upload_page, self._models_page, self._settings_page,
        ]:
            if hasattr(page, "apply_theme"):
                page.apply_theme()

    def _force_repolish(self, widget):
        """
        Đệ quy unpolish + polish toàn bộ widget tree.

        Tại sao cần: Qt stylesheet engine có 3 tầng ưu tiên:
          1. Inline setStyleSheet() trên widget — ưu tiên CAO NHẤT
          2. Stylesheet được set trên parent/ancestor
          3. Global app stylesheet

        Khi một widget đã từng gọi setStyleSheet("color:xxx"), màu đó
        bị "ghim" vào widget — global app stylesheet không thể override.
        unpolish() xóa cache style đó, polish() apply lại từ đầu.

        Lưu ý: chỉ unpolish widget không có inline style quan trọng
        (background card, border...) — những widget đó sẽ được re-apply
        qua apply_theme() riêng của từng page.
        """
        app = QApplication.instance()
        style = app.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
        for child in widget.findChildren(QWidget):
            style.unpolish(child)
            style.polish(child)
            child.update()

    def _apply_theme(self):
        p = self._p
        self.setStyleSheet(f"QMainWindow {{ background:{p['BG_BASE']}; }}")
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window,     QColor(p['BG_BASE']))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(p['TEXT_PRI']))
        self.setPalette(pal)

    def closeEvent(self, event):
        if hasattr(self, "_live_page"):
            self._live_page.cleanup()
        if hasattr(self, "_meeting_page"):
            self._meeting_page.cleanup()
        event.accept()