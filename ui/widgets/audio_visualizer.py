import math
import numpy as np

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore    import Qt, QTimer, QPointF
from PyQt6.QtGui     import QColor, QPainter, QPen, QPolygonF

from ui.theme import DARK as _THEME_FALLBACK

_GRID_SIZE = 20


def _p(obj):
    t = getattr(obj, "_theme", None)
    return t.palette if t is not None else _THEME_FALLBACK


def _is_light(pal: dict) -> bool:
    return QColor(pal["BG_BASE"]).lightness() > 128


class AudioVisualizer(QWidget):

    def __init__(self, theme=None, parent=None, amplitude_scale: float = 1.0):
        super().__init__(parent)
        self._theme = theme
        self.setMinimumHeight(140)
        self.amplitude_scale = amplitude_scale

        self._active    = False
        self._t         = 0.0
        self._freq_data = np.zeros(256, dtype=np.float32)
        self._time_data = np.zeros(256, dtype=np.float32)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def set_active(self, active: bool):
        self._active = active

    def push_audio(self, data: bytes):
        try:
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            arr /= 32768.0
            n = len(arr)
            if n < 64:
                return
            mag = np.abs(np.fft.rfft(arr * np.hanning(n)))
            mag = mag[:len(mag) // 2]
            idx = np.linspace(0, len(mag) - 1, 256, dtype=int)
            mag = mag[idx]
            mx  = mag.max()
            if mx > 0:
                mag /= mx
            self._freq_data = np.maximum(mag, self._freq_data * 0.75)
            idx2 = np.linspace(0, n - 1, 256, dtype=int)
            self._time_data = arr[idx2]
        except Exception:
            pass

    def push_level(self, level: float):
        v = min(level, 1.0)
        self._freq_data = np.linspace(v, 0, 256, dtype=np.float32)
        self._time_data = np.zeros(256, dtype=np.float32)

    def _tick(self):
        if not self._active:
            self._t += 0.05
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        pal = _p(self)
        painter.fillRect(0, 0, W, H, QColor(pal["BG_CARD"]))

        # Grid: đen mờ trên light, trắng mờ trên dark
        bg_lum = QColor(pal['BG_BASE']).lightness()
        grid_color = QColor(0, 0, 0, 30) if bg_lum > 128 else QColor(255, 255, 255, 25)
        painter.setPen(QPen(grid_color, 1))
        x = _GRID_SIZE
        while x < W:
            painter.drawLine(x, 0, x, H)
            x += _GRID_SIZE
        y = _GRID_SIZE
        while y < H:
            painter.drawLine(0, y, W, y)
            y += _GRID_SIZE

        if self._active:
            self._draw_real(painter, W, H, pal)
        else:
            self._draw_simulation(painter, W, H, pal)

        painter.end()

    def _draw_simulation(self, p: QPainter, W: int, H: int, pal: dict):
        t      = self._t
        center = H / 2
        amp_s  = H / 200.0
        light  = _is_light(pal)

        if light:
            # Light mode: màu đậm hơn, opacity cao hơn để nổi trên nền trắng
            layers = [
                (QColor(0,  160, 190, 230), 1),   # cyan đậm
                (QColor(160, 30, 200, 200), 2),   # tím đậm
                (QColor(210, 90,  10, 170), 3),   # cam đậm
            ]
            fill_color = QColor(0, 160, 190, 35)
        else:
            # Dark mode: giữ nguyên màu cũ
            layers = [
                (QColor(34, 211, 238, 153), 1),
                (QColor(217, 70, 239, 128), 2),
                (QColor(251, 146, 60,  76), 3),
            ]
            fill_color = QColor(34, 211, 238, 13)

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

            if idx == 0 and len(pts) > 0:
                fill_pts = QPolygonF(pts)
                fill_pts.append(QPointF(W, H))
                fill_pts.append(QPointF(0, H))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill_color)
                p.drawPolygon(fill_pts)

    def _draw_real(self, p: QPainter, W: int, H: int, pal: dict):
        freq      = self._freq_data
        n         = len(freq)
        bar_count = min(n, max(1, W // 6))
        bar_w     = W / bar_count
        step      = max(1, n // bar_count)
        light     = _is_light(pal)

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(bar_count):
            normalized = float(freq[min(i * step, n - 1)])
            bar_h      = min(normalized * self.amplitude_scale, 1.0) * H * 0.85
            ratio = i / bar_count

            if light:
                # Light mode: màu đậm hơn (bắt đầu từ cyan đậm → tím đậm)
                # và alpha cao hơn đáng kể
                r = int(0   + ratio * 160)
                g = int(160 - ratio * 130)
                b = int(190 + ratio * 10)
                alpha = int((0.45 + normalized * 0.50) * 255)
            else:
                # Dark mode: giữ nguyên
                r = int(34  + ratio * 183)
                g = int(211 - ratio * 141)
                b = int(238 + ratio * 1)
                alpha = int((0.1 + normalized * 0.2) * 255)

            p.setBrush(QColor(r, g, b, alpha))
            p.drawRect(int(i * bar_w + 1), int(H - bar_h),
                       max(1, int(bar_w - 2)), max(1, int(bar_h)))

        td    = self._time_data
        m     = len(td)
        sw    = W / m
        scale = self.amplitude_scale
        pts   = QPolygonF()
        for i in range(m):
            v2 = td[i] + 1.0
            y  = (((v2 - 1) * scale + 1) * H) / 2
            pts.append(QPointF(i * sw, y))

        # Waveform line: đậm hơn ở light mode
        if light:
            wave_color = QColor(0, 140, 180, 240)
            wave_width = 2.0
        else:
            wave_color = QColor(34, 211, 238, 204)
            wave_width = 1.5

        pen = QPen(wave_color, wave_width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(pts)