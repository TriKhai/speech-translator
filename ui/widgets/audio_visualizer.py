import math
import numpy as np

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore    import Qt, QTimer, QPointF
from PyQt6.QtGui     import (
    QColor, QPainter, QPen, QPolygonF,
)

_BG_COLOR  = QColor(0, 0, 0, 50)
_GRID_LINE = QColor(255, 255, 255, 25)
_GRID_SIZE = 20   # px, khớp với backgroundSize '20px 20px' trong React


class AudioVisualizer(QWidget):

    def __init__(self, parent=None, amplitude_scale: float = 1.0):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.amplitude_scale = amplitude_scale

        self._active  = False
        self._t       = 0.0          # animation clock (idle)

        # Buffers — numpy arrays để tính nhanh
        self._freq_data = np.zeros(256, dtype=np.float32)   # 0.0–1.0
        self._time_data = np.zeros(256, dtype=np.float32)   # -1.0–1.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)   # 20 fps

    # ── Public API ─────────────────────────────────────────
    def set_active(self, active: bool):
        self._active = active

    def push_audio(self, data: bytes):
        try:
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            arr /= 32768.0

            n = len(arr)
            if n < 64:
                return

            # --- Frequency data (FFT magnitude) ---
            mag = np.abs(np.fft.rfft(arr * np.hanning(n)))
            mag = mag[:len(mag) // 2]
            # Downsample → 256 bins
            idx = np.linspace(0, len(mag) - 1, 256, dtype=int)
            mag = mag[idx]
            mx  = mag.max()
            if mx > 0:
                mag /= mx
            # Smooth decay
            self._freq_data = np.maximum(mag, self._freq_data * 0.75)

            # --- Time domain (waveform) ---
            idx2 = np.linspace(0, n - 1, 256, dtype=int)
            self._time_data = arr[idx2]

        except Exception:
            pass

    def push_level(self, level: float):
        v = min(level, 1.0)
        self._freq_data = np.linspace(v, 0, 256, dtype=np.float32)
        self._time_data = np.zeros(256, dtype=np.float32)

    # ── Internal ───────────────────────────────────────────
    def _tick(self):
        if not self._active:
            self._t += 0.05
        self.update()

    # ── Paint ──────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # Background
        p.fillRect(0, 0, W, H, QColor("#0a0a0f"))

        # Grid (opacity 10% white như React)
        p.setPen(QPen(_GRID_LINE, 1))
        x = _GRID_SIZE
        while x < W:
            p.drawLine(x, 0, x, H)
            x += _GRID_SIZE
        y = _GRID_SIZE
        while y < H:
            p.drawLine(0, y, W, y)
            y += _GRID_SIZE

        if self._active:
            self._draw_real(p, W, H)
        else:
            self._draw_simulation(p, W, H)

        p.end()

    # ── Idle simulation — 3 lớp sin (port từ drawSimulation) ──
    def _draw_simulation(self, p: QPainter, W: int, H: int):
        t      = self._t
        center = H / 2
        amp_s  = H / 200.0   # amplitudeScale trong React

        layers = [
            (QColor(34, 211, 238, 153), 1),   # cyan  0.6 opacity
            (QColor(217, 70, 239, 128), 2),   # magenta 0.5
            (QColor(251, 146, 60,  76), 3),   # orange 0.3
        ]

        for idx, (color, j) in enumerate(layers):
            pen = QPen(color, 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            pts = QPolygonF()
            x   = 0
            while x < W:
                y = (center
                     + math.sin(x * 0.01 + t * j) * (30 * amp_s) * math.sin(t * 0.5)
                     + math.sin(x * 0.03 + t * 2) * (10 * amp_s))
                pts.append(QPointF(x, y))
                x += 2

            p.drawPolyline(pts)

            # Fill dưới layer đầu tiên (giống React: fillStyle rgba(34,211,238,0.05))
            if idx == 0 and len(pts) > 0:
                fill_pts = QPolygonF(pts)
                fill_pts.append(QPointF(W, H))
                fill_pts.append(QPointF(0, H))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(34, 211, 238, 13))   # 0.05 * 255 ≈ 13
                p.drawPolygon(fill_pts)

    # ── Real data — bars + waveform (port từ drawReal) ────────
    def _draw_real(self, p: QPainter, W: int, H: int):
        freq = self._freq_data
        n    = len(freq)

        # --- Frequency bars ---
        bar_count = min(n, max(1, W // 6))
        bar_w     = W / bar_count
        step      = max(1, n // bar_count)

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(bar_count):
            normalized = float(freq[min(i * step, n - 1)])
            bar_h      = min(normalized * self.amplitude_scale, 1.0) * H * 0.85

            ratio = i / bar_count
            r     = int(34  + ratio * 183)   # 34→217  (cyan→magenta)
            g     = int(211 - ratio * 141)   # 211→70
            b     = int(238 + ratio * 1)     # 238→239
            alpha = int((0.1 + normalized * 0.2) * 255)

            p.setBrush(QColor(r, g, b, alpha))
            p.drawRect(
                int(i * bar_w + 1),
                int(H - bar_h),
                max(1, int(bar_w - 2)),
                max(1, int(bar_h)),
            )

        # --- Waveform overlay (time domain) ---
        td    = self._time_data
        m     = len(td)
        sw    = W / m
        scale = self.amplitude_scale

        pts = QPolygonF()
        for i in range(m):
            v = float(td[i])                          # -1.0 … 1.0
            y = (((v - 0) * scale + 1) * H) / 2      # port: (v-1)*scale+1 → fix offset
            # port chính xác từ React:
            # v = timeData[i]/128 → ở đây td đã là float -1..1 nên v = td+1 ~ 0..2
            v2 = td[i] + 1.0                          # 0.0 … 2.0  (tương đương /128)
            y  = (((v2 - 1) * scale + 1) * H) / 2
            pts.append(QPointF(i * sw, y))

        pen = QPen(QColor(34, 211, 238, 204), 1.5)   # rgba(34,211,238,0.8)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(pts)