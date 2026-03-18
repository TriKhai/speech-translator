import sys
import threading
import wave
import os

PLATFORM = sys.platform  # "linux", "win32", "darwin"

if PLATFORM == "win32":
    import sounddevice as sd
    import numpy as np
else:
    import subprocess


class AudioCapture:
    """
    Cross-platform microphone capture.

    Linux  → ffmpeg subprocess (ALSA / PulseAudio)
    Windows → sounddevice (PortAudio, không cần file ngoài)

    Interface giống hệt bản cũ:
        start(on_data, wav_path)  — bắt đầu capture
        pause()                   — dừng mic, WAV vẫn mở
        resume()                  — mở lại mic
        stop()                    — dừng hẳn + đóng WAV
    """

    SAMPLE_RATE  = 16000
    CHANNELS     = 1
    SAMPLE_WIDTH = 2          # s16le = 2 bytes/sample
    CHUNK_FRAMES = 4096       # frames mỗi lần đọc

    def __init__(self, device=None, sample_rate: int = 16000):
        """
        Args:
            device: Linux  → string ALSA device, ví dụ "plughw:0,7"
                             None → để ffmpeg tự chọn default
                    Windows → int device index, hoặc None → default mic
        """
        self._device      = device
        self._sample_rate = sample_rate
        self._running     = False
        self._paused      = False
        self._wav_file    = None
        self._on_data     = None

        # Linux only
        self._process = None
        self._thread  = None

        # Windows only
        self._sd_stream = None

    # ── Start ───────────────────────────────────────────────
    def start(self, on_data: callable, wav_path: str = None):
        print(f"[AUDIO] Starting ({PLATFORM})...")
        self._on_data = on_data

        if wav_path:
            os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
            self._wav_file = wave.open(wav_path, "wb")
            self._wav_file.setnchannels(self.CHANNELS)
            self._wav_file.setsampwidth(self.SAMPLE_WIDTH)
            self._wav_file.setframerate(self._sample_rate)
            print(f"[AUDIO] Recording to: {wav_path}")

        self._paused  = False
        self._running = True
        self._start_backend(on_data)

    # ── Pause ───────────────────────────────────────────────
    def pause(self):
        if self._paused or not self._running:
            return
        self._paused  = True
        self._running = False
        self._stop_backend()
        print("[AUDIO] Paused.")

    # ── Resume ──────────────────────────────────────────────
    def resume(self):
        if not self._paused:
            return
        self._paused  = False
        self._running = True
        self._start_backend(self._on_data)
        print("[AUDIO] Resumed.")

    # ── Stop ────────────────────────────────────────────────
    def stop(self):
        self._running = False
        self._paused  = False
        self._stop_backend()
        if self._wav_file:
            self._wav_file.close()
            self._wav_file = None
            print("[AUDIO] WAV file saved.")
        print("[AUDIO] Stopped.")

    # ══════════════════════════════════════════════════════════
    # Internal — route đúng backend
    # ══════════════════════════════════════════════════════════
    def _start_backend(self, on_data: callable):
        if PLATFORM == "win32":
            self._start_sounddevice(on_data)
        else:
            self._start_ffmpeg(on_data)

    def _stop_backend(self):
        if PLATFORM == "win32":
            self._stop_sounddevice()
        else:
            self._stop_ffmpeg()

    # ══════════════════════════════════════════════════════════
    # Linux backend — ffmpeg / ALSA
    # ══════════════════════════════════════════════════════════
    def _start_ffmpeg(self, on_data: callable):
        if PLATFORM != "win32":
            # Chọn input device
            alsa_device = self._device if self._device else "default"

            self._process = subprocess.Popen(
                [
                    "ffmpeg",
                    "-f", "alsa", "-i", alsa_device,
                    "-af", "volume=4.0",
                    "-ac", str(self.CHANNELS),
                    "-ar", str(self._sample_rate),
                    "-f", "s16le",
                    "-loglevel", "quiet", "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._thread = threading.Thread(
                target=self._ffmpeg_read_loop, args=(on_data,), daemon=True
            )
            self._thread.start()
            print(f"[AUDIO] ffmpeg PID: {self._process.pid}")

    def _stop_ffmpeg(self):
        if self._process:
            self._process.kill()
            try:
                self._process.wait(timeout=2)
            except Exception:
                pass
            self._process = None

    def _ffmpeg_read_loop(self, on_data: callable):
        chunk_size = self.CHUNK_FRAMES * self.SAMPLE_WIDTH
        while self._running:
            data = self._process.stdout.read(chunk_size)
            if not data:
                break
            if self._wav_file:
                self._wav_file.writeframes(data)
            on_data(data)

    # ══════════════════════════════════════════════════════════
    # Windows backend — sounddevice
    # ══════════════════════════════════════════════════════════
    def _start_sounddevice(self, on_data: callable):
        if PLATFORM != "win32":
            return

        # device=None → default mic của Windows
        device_idx = self._device  # None hoặc int index

        def _sd_callback(indata, frames, time_info, status):
            if not self._running:
                return
            # indata: float32 array shape (frames, channels)
            # Convert float32 → int16 bytes (giống s16le)
            pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
            if self._wav_file:
                self._wav_file.writeframes(pcm)
            on_data(pcm)

        self._sd_stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self.CHANNELS,
            dtype="float32",
            blocksize=self.CHUNK_FRAMES,
            device=device_idx,
            callback=_sd_callback,
        )
        self._sd_stream.start()
        print(f"[AUDIO] sounddevice stream started (device={device_idx})")

    def _stop_sounddevice(self):
        if self._sd_stream:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception as e:
                print(f"[AUDIO] sounddevice stop error: {e}")
            self._sd_stream = None


# ══════════════════════════════════════════════════════════════
# Utility — liệt kê microphone (hữu ích khi cần chọn device)
# ══════════════════════════════════════════════════════════════
def list_microphones() -> list[dict]:
    """
    Trả về danh sách mic input có sẵn trên hệ thống.

    Windows → dùng sounddevice
    Linux   → in thông tin cơ bản

    Ví dụ usage:
        from audio_capture import list_microphones
        for mic in list_microphones():
            print(mic)
        # {'index': 0, 'name': 'Microphone (Realtek HD Audio)', 'channels': 2}
    """
    if PLATFORM == "win32":
        import sounddevice as sd
        devices = sd.query_devices()
        result = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                result.append({
                    "index":    i,
                    "name":     d["name"],
                    "channels": d["max_input_channels"],
                })
        return result
    else:
        # Linux: dùng arecord -l để liệt kê (cần arecord có mặt)
        try:
            out = subprocess.check_output(
                ["arecord", "-l"], stderr=subprocess.DEVNULL, text=True
            )
            print("[AUDIO] ALSA devices:\n" + out)
        except Exception:
            print("[AUDIO] Không tìm thấy arecord. Dùng 'plughw:X,Y'.")
        return []