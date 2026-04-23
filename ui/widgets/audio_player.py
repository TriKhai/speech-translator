import sys
import wave
import threading
import numpy as np

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider
from PyQt6.QtCore    import Qt, QTimer, QThread, pyqtSignal, QObject, QRectF
from PyQt6.QtGui     import QColor, QPainter, QPen, QBrush, QFont

from ui.constants import SPEAKER_COLORS  # chỉ còn SPEAKER_COLORS trong constants
from ui.theme     import DARK as _THEME_FALLBACK

PLATFORM = sys.platform

if PLATFORM == "win32":
    import sounddevice as sd
else:
    import subprocess

_GRID = QColor(255, 255, 255, 13)


def _fmt(sec: float) -> str:
    s = int(max(0, sec))
    return f"{s // 60:02d}:{s % 60:02d}"


def _p(obj):
    """Lấy palette từ obj._theme nếu có, fallback DARK."""
    t = getattr(obj, "_theme", None)
    return t.palette if t is not None else _THEME_FALLBACK


# ── Waveform canvas ────────────────────────────────────────
class _WaveCanvas(QWidget):
    seek_requested = pyqtSignal(float)   # ratio 0.0–1.0

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setMinimumHeight(110)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._peaks    = np.array([], dtype=np.float32)
        self._progress = 0.0

    def set_peaks(self, peaks: np.ndarray):
        self._peaks = peaks
        self.update()

    def set_progress(self, ratio: float):
        self._progress = max(0.0, min(1.0, ratio))
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and len(self._peaks):
            self.seek_requested.emit(e.position().x() / self.width())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and len(self._peaks):
            self.seek_requested.emit(
                max(0.0, min(1.0, e.position().x() / self.width()))
            )

    def paintEvent(self, event):
        p   = QPainter(self)
        pal = _p(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(pal["BG_CARD"]))

        p.setPen(QPen(_GRID, 1))
        for i in range(1, 9):
            p.drawLine(W * i // 8, 0, W * i // 8, H)
        p.drawLine(0, H // 2, W, H // 2)

        if not len(self._peaks):
            p.setPen(QPen(QColor(pal["TEXT_SEC"]), 1))
            p.setFont(QFont("JetBrains Mono", 9))
            p.drawText(0, 0, W, H, Qt.AlignmentFlag.AlignCenter, "Loading waveform…")
            p.end()
            return

        n     = len(self._peaks)
        mid   = H / 2
        cut_x = int(self._progress * W)
        bar_w = max(1.0, W / n)

        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(self._peaks):
            x  = i * W / n
            bh = max(1.0, float(abs(v)) * mid * 0.88)
            color = (QColor(0, 229, 255, 190) if x <= cut_x
                     else QColor(0, 229, 255, 42))
            p.setBrush(QBrush(color))
            p.drawRect(QRectF(x, mid - bh, max(1.0, bar_w - 0.5), bh * 2))

        if self._progress > 0.001:
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
            p.drawLine(cut_x, 0, cut_x, H)

        p.end()


# ── WAV loader (background thread) ────────────────────────
class _Loader(QObject):
    done = pyqtSignal(object, float)   # peaks ndarray, duration

    def __init__(self, path: str, n: int = 600):
        super().__init__()
        self._path = path
        self._n    = n

    def run(self):
        try:
            with wave.open(self._path, "rb") as wf:
                frames   = wf.getnframes()
                sr       = wf.getframerate()
                raw      = wf.readframes(frames)
                duration = frames / sr
            arr  = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            step = max(1, len(arr) // self._n)
            peaks = np.array(
                [arr[i * step:(i + 1) * step].max() for i in range(self._n)],
                dtype=np.float32,
            )
            self.done.emit(peaks, duration)
        except Exception as ex:
            print(f"[PLAYER] Load error: {ex}")
            self.done.emit(np.array([], dtype=np.float32), 0.0)


# ── Windows playback engine ────────────────────────────────
class _WinPlayback(QObject):
    finished = pyqtSignal()

    def __init__(self, wav_path: str, start_sec: float):
        super().__init__()
        self._path       = wav_path
        self._start_sec  = start_sec
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            with wave.open(self._path, "rb") as wf:
                sr       = wf.getframerate()
                n_ch     = wf.getnchannels()
                n_frames = wf.getnframes()
                start_frame = max(0, min(int(self._start_sec * sr), n_frames - 1))
                wf.setpos(start_frame)
                stream = sd.OutputStream(samplerate=sr, channels=n_ch, dtype="int16")
                stream.start()
                while not self._stop_event.is_set():
                    raw = wf.readframes(4096)
                    if not raw:
                        break
                    stream.write(np.frombuffer(raw, dtype=np.int16))
                stream.stop()
                stream.close()
        except Exception as e:
            print(f"[PLAYER-WIN] Playback error: {e}")
        finally:
            self.finished.emit()


# ── Main AudioPlayer widget ────────────────────────────────
class AudioPlayer(QWidget):
    new_recording_requested = pyqtSignal()
    _playback_ended         = pyqtSignal()

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme    = theme
        self._wav_path = None
        self._duration = 0.0
        self._position = 0.0
        self._playing  = False

        self._process      = None   # Linux: ffplay
        self._thread       = None
        self._win_thread   = None   # Windows: sounddevice
        self._win_playback = None

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)
        self._playback_ended.connect(self._on_playback_ended)

    def _p(self):
        return self._theme.palette if self._theme else _THEME_FALLBACK

    # ── Build UI ───────────────────────────────────────────
    def _build_ui(self):
        p   = self._p()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self._canvas = _WaveCanvas(theme=self._theme)
        self._canvas.seek_requested.connect(self._on_seek_ratio)
        lay.addWidget(self._canvas, stretch=1)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 1000)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._slider.sliderMoved.connect(self._on_slider)
        self._slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background:{p['BORDER2']}; height:3px; border-radius:2px;
            }}
            QSlider::sub-page:horizontal {{
                background:{p['ACCENT']}; height:3px; border-radius:2px;
            }}
            QSlider::handle:horizontal {{
                background:#fff; width:12px; height:12px;
                margin:-5px 0; border-radius:6px;
            }}
        """)
        lay.addWidget(self._slider)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        self._btn_play = self._mk_btn("▶  PLAY", p["ACCENT"], p["ACCENT_HO"])
        self._btn_play.clicked.connect(self._toggle_play)

        self._btn_stop = self._mk_ghost("◼  STOP")
        self._btn_stop.clicked.connect(self.stop)

        self._lbl_time = QLabel("00:00 / 00:00")
        self._lbl_time.setFont(QFont("JetBrains Mono", 8))
        self._lbl_time.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")

        self._btn_new = QPushButton("⬤  NEW RECORDING")
        self._btn_new.setFixedHeight(34)
        self._btn_new.setFont(QFont("JetBrains Mono", 9))
        self._btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_new.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{p['TEXT_SEC']};
                border:1px solid {p['BORDER2']}; border-radius:6px;
                padding:0 14px; letter-spacing:1px;
                font-family:'JetBrains Mono','Consolas',monospace;
                font-size:9px;
            }}
            QPushButton:hover {{ color:{p['TEXT_PRI']}; border-color:{p['ACCENT']}; }}
        """)
        self._btn_new.clicked.connect(self.new_recording_requested)

        ctrl.addWidget(self._btn_play)
        ctrl.addWidget(self._btn_stop)
        ctrl.addSpacing(10)
        ctrl.addWidget(self._lbl_time)
        ctrl.addStretch()
        ctrl.addWidget(self._btn_new)
        lay.addLayout(ctrl)

    def _mk_btn(self, text, bg, hover):
        p = self._p()
        b = QPushButton(text)
        b.setFixedHeight(34)
        b.setFont(QFont("JetBrains Mono", 9))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background:{bg}; color:#fff; border:none;
                border-radius:6px; padding:0 16px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:{hover}; }}
            QPushButton:disabled {{ background:{p['BG_CARD2']}; color:{p['TEXT_DIM']}; }}
        """)
        return b

    def _mk_ghost(self, text):
        p = self._p()
        b = QPushButton(text)
        b.setFixedHeight(34)
        b.setFont(QFont("JetBrains Mono", 9))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{p['TEXT_SEC']};
                border:1px solid {p['BORDER2']}; border-radius:6px;
                padding:0 16px; letter-spacing:1px;
            }}
            QPushButton:hover {{ color:{p['TEXT_PRI']}; border-color:{p['BORDER2']}; }}
        """)
        return b

    def apply_theme(self):
        """Gọi từ parent khi đổi theme."""
        p = self._p()
        self._canvas._theme = self._theme
        self._canvas.update()
        self._slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background:{p['BORDER2']}; height:3px; border-radius:2px;
            }}
            QSlider::sub-page:horizontal {{
                background:{p['ACCENT']}; height:3px; border-radius:2px;
            }}
            QSlider::handle:horizontal {{
                background:#fff; width:12px; height:12px;
                margin:-5px 0; border-radius:6px;
            }}
        """)
        self._lbl_time.setStyleSheet(f"color:{p['TEXT_SEC']}; background:transparent;")
        self._btn_play.setStyleSheet(f"""
            QPushButton {{
                background:{p['ACCENT']}; color:#fff; border:none;
                border-radius:6px; padding:0 16px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:{p['ACCENT_HO']}; }}
            QPushButton:disabled {{ background:{p['BG_CARD2']}; color:{p['TEXT_DIM']}; }}
        """)
        self._btn_stop.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{p['TEXT_SEC']};
                border:1px solid {p['BORDER2']}; border-radius:6px;
                padding:0 16px; letter-spacing:1px;
            }}
            QPushButton:hover {{ color:{p['TEXT_PRI']}; border-color:{p['BORDER2']}; }}
        """)
        self._btn_new.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{p['TEXT_SEC']};
                border:1px solid {p['BORDER2']}; border-radius:6px;
                padding:0 14px; letter-spacing:1px;
                font-family:'JetBrains Mono','Consolas',monospace;
                font-size:9px;
            }}
            QPushButton:hover {{ color:{p['TEXT_PRI']}; border-color:{p['ACCENT']}; }}
        """)

    # ── Public API ─────────────────────────────────────────
    def load(self, wav_path: str):
        self.stop()
        self._wav_path = wav_path
        self._position = 0.0
        self._canvas.set_progress(0.0)
        self._slider.setValue(0)
        self._lbl_time.setText("00:00 / 00:00")

        self._loader_thread = QThread()
        self._loader        = _Loader(wav_path)
        self._loader.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader.run)
        self._loader.done.connect(self._on_loaded)
        self._loader.done.connect(self._loader_thread.quit)
        self._loader_thread.start()

    def _on_loaded(self, peaks: np.ndarray, duration: float):
        self._duration = duration
        self._canvas.set_peaks(peaks)
        self._lbl_time.setText(f"00:00 / {_fmt(duration)}")

    def _toggle_play(self):
        if self._playing:
            self.pause()
        else:
            self.play()

    def _kill_all(self):
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                pass
            self._process = None
        if self._win_playback:
            self._win_playback.stop()
            self._win_playback = None
        if self._win_thread and self._win_thread.isRunning():
            self._win_thread.quit()
            self._win_thread.wait(2000)
            self._win_thread = None

    def play(self):
        if not self._wav_path or self._playing:
            return
        self._kill_all()
        self._playing = True
        self._btn_play.setText("⏸  PAUSE")
        if PLATFORM == "win32":
            self._play_windows()
        else:
            self._play_linux()
        self._timer.start()

    def _play_linux(self):
        self._process = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-ss", str(self._position), self._wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._thread = threading.Thread(target=self._watch_linux_process, daemon=True)
        self._thread.start()

    def _watch_linux_process(self):
        if self._process:
            self._process.wait()
        if self._playing:
            self._playback_ended.emit()

    def _play_windows(self):
        self._win_thread   = QThread()
        self._win_playback = _WinPlayback(self._wav_path, self._position)
        self._win_playback.moveToThread(self._win_thread)
        self._win_thread.started.connect(self._win_playback.run)
        self._win_playback.finished.connect(self._win_thread.quit)
        self._win_playback.finished.connect(
            lambda: self._playback_ended.emit() if self._playing else None
        )
        self._win_thread.start()

    def pause(self):
        if not self._playing:
            return
        self._playing = False
        self._btn_play.setText("▶  PLAY")
        self._timer.stop()
        self._kill_all()

    def stop(self):
        self._playing  = False
        self._position = 0.0
        self._timer.stop()
        self._kill_all()
        self._btn_play.setText("▶  PLAY")
        self._canvas.set_progress(0.0)
        self._slider.setValue(0)
        if self._duration:
            self._lbl_time.setText(f"00:00 / {_fmt(self._duration)}")

    def _on_seek_ratio(self, ratio: float):
        self._position = ratio * self._duration
        was_playing    = self._playing
        self._playing  = False
        self._timer.stop()
        self._kill_all()
        self._canvas.set_progress(ratio)
        self._slider.setValue(int(ratio * 1000))
        self._lbl_time.setText(f"{_fmt(self._position)} / {_fmt(self._duration)}")
        if was_playing:
            self.play()

    def _on_slider(self, value: int):
        self._on_seek_ratio(value / 1000)

    def _tick(self):
        if not self._playing:
            return
        self._position = min(self._position + 0.25, self._duration)
        ratio = self._position / self._duration if self._duration else 0
        self._canvas.set_progress(ratio)
        self._slider.setValue(int(ratio * 1000))
        self._lbl_time.setText(f"{_fmt(self._position)} / {_fmt(self._duration)}")

    def _on_playback_ended(self):
        self._playing  = False
        self._position = self._duration
        self._timer.stop()
        self._btn_play.setText("▶  PLAY")
        self._canvas.set_progress(1.0)
        self._slider.setValue(1000)