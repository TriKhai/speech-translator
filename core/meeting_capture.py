"""
core/meeting_capture.py — v3

System audio only — bỏ mic worker.
Mic chỉ dùng để detect MIC ON/OFF (badge UI), không transcribe.

Lý do: loopback capture đã chứa tiếng mic rồi → 2 worker
       transcribe cùng 1 câu → duplicate segment trong DB.
"""

import os
import sys
import wave
import subprocess
import threading
import numpy as np

from core.vad_processor        import VADProcessor
from core.transcription_worker import TranscriptionWorker

DEFAULT_MONITOR = (
    "alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic"
    ".HiFi__hw_sofhdadsp__sink.monitor"
)

SAMPLE_RATE  = 16000
CHUNK_FRAMES = 4096

MIC_MUTE_THRESHOLD = 0.005
MIC_MUTE_FRAMES    = 15


class MicStatusMonitor:
    """Chỉ detect tắt/bật mic — không transcribe."""

    def __init__(self, on_muted: callable, on_active: callable):
        self._on_muted     = on_muted
        self._on_active    = on_active
        self._silent_count = 0
        self._is_muted     = False

    def feed(self, pcm_bytes: bytes):
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        rms   = float(np.sqrt(np.mean(audio ** 2))) if len(audio) > 0 else 0.0
        if rms < MIC_MUTE_THRESHOLD:
            self._silent_count += 1
            if not self._is_muted and self._silent_count >= MIC_MUTE_FRAMES:
                self._is_muted = True
                self._on_muted()
        else:
            if self._is_muted:
                self._is_muted = False
                self._on_active()
            self._silent_count = 0

    def reset(self):
        self._silent_count = 0
        self._is_muted     = False


class MeetingCapture:
    """
    Capture system audio (loopback) → VAD → TranscriptionWorker.
    Mic capture riêng chỉ để detect MIC ON/OFF, không đưa vào pipeline.
    """

    def __init__(
        self,
        whisper_service,
        speaker_identifier,
        translation_service,
        db_service,
        on_english:      callable,
        on_vietnamese:   callable,
        on_level_sys:    callable = None,
        on_level_mic:    callable = None,
        on_mic_muted:    callable = None,
        on_mic_active:   callable = None,
        # compat params cũ
        on_level:        callable = None,
        on_audio_data:   callable = None,
        on_speech_chunk: callable = None,
        monitor_device:  str      = None,
    ):
        self._whisper      = whisper_service
        self._speaker      = speaker_identifier
        self._translator   = translation_service
        self._db           = db_service
        self._on_english   = on_english
        self._on_vi        = on_vietnamese
        self._on_level_sys = on_level_sys or on_level
        self._on_level_mic = on_level_mic
        self._on_mic_muted  = on_mic_muted
        self._on_mic_active = on_mic_active
        self._device = monitor_device or DEFAULT_MONITOR

        self._running     = False
        self._wav_file    = None
        self._process_sys = None
        self._thread_sys  = None
        self._vad_sys     = None
        self._worker_sys  = None

        # Mic — chỉ cho status badge
        self._process_mic = None
        self._sd_stream   = None
        self._mic_monitor = None

        self.recording_id = None

    # ══════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════

    def start(self, wav_path: str = None):
        self._running = True

        if wav_path:
            os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
            self._wav_file = wave.open(wav_path, "wb")
            self._wav_file.setnchannels(1)
            self._wav_file.setsampwidth(2)
            self._wav_file.setframerate(SAMPLE_RATE)

        if self._db:
            self._db.start_recording(audio_path=wav_path, source="meeting")
            self.recording_id = self._db._current_rec_id

        self._speaker.reset()

        # System audio pipeline (transcript)
        self._worker_sys = TranscriptionWorker(
            whisper       = self._whisper,
            speaker       = self._speaker,
            translator    = self._translator,
            db            = self._db,
            on_english    = self._on_english,
            on_vietnamese = self._on_vi,
        )
        self._vad_sys = VADProcessor(
            on_speech_chunk = self._worker_sys.enqueue,
            threshold       = 0.3,
            min_speech_ms   = 200,
            min_silence_ms  = 400,
            max_speech_ms   = 8000,
        )
        self._worker_sys.start()

        self._thread_sys = threading.Thread(
            target=self._capture_sys_loop, daemon=True)
        self._thread_sys.start()

        # Mic pipeline — chỉ status, không transcribe
        if self._on_mic_muted or self._on_mic_active or self._on_level_mic:
            self._mic_monitor = MicStatusMonitor(
                on_muted  = self._on_mic_muted  or (lambda: None),
                on_active = self._on_mic_active or (lambda: None),
            )
            self._start_mic_capture()

        print(f"[MEETING] Started: {self._device[:55]}…")

    def stop(self):
        self._running = False

        if self._process_sys:
            try:
                self._process_sys.kill()
                self._process_sys.wait(timeout=2)
            except Exception:
                pass
            self._process_sys = None

        if self._vad_sys:
            self._vad_sys.reset()
        if self._worker_sys:
            self._worker_sys.stop()
            self._worker_sys = None

        self._stop_mic_capture()
        if self._mic_monitor:
            self._mic_monitor.reset()

        if self._wav_file:
            self._wav_file.close()
            self._wav_file = None
        if self._db:
            self._db.stop_recording()

        print("[MEETING] Stopped.")

    # ══════════════════════════════════════════════════════
    # System audio loop
    # ══════════════════════════════════════════════════════

    def _capture_sys_loop(self):
        cmd = [
            "ffmpeg", "-f", "pulse", "-i", self._device,
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "-loglevel", "quiet", "pipe:1",
        ]
        self._process_sys = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        chunk_size = CHUNK_FRAMES * 2
        while self._running:
            data = self._process_sys.stdout.read(chunk_size)
            if not data:
                break
            if self._wav_file:
                self._wav_file.writeframes(data)
            if self._on_level_sys:
                audio = np.frombuffer(
                    data, dtype=np.int16).astype(np.float32) / 32768.0
                self._on_level_sys(float(np.sqrt(np.mean(audio ** 2))))
            if self._vad_sys:
                self._vad_sys.add(data)

    # ══════════════════════════════════════════════════════
    # Mic — status only, no transcription
    # ══════════════════════════════════════════════════════

    def _start_mic_capture(self):
        if sys.platform == "win32":
            self._start_mic_sounddevice()
        else:
            self._start_mic_ffmpeg()

    def _stop_mic_capture(self):
        if self._sd_stream:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None
        if self._process_mic:
            try:
                self._process_mic.kill()
                self._process_mic.wait(timeout=2)
            except Exception:
                pass
            self._process_mic = None

    def _start_mic_sounddevice(self):
        try:
            import sounddevice as sd
            def _cb(indata, frames, time_info, status):
                if not self._running:
                    return
                pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
                self._handle_mic_data(pcm)
            self._sd_stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1,
                dtype="float32", blocksize=CHUNK_FRAMES, callback=_cb)
            self._sd_stream.start()
        except Exception as e:
            print(f"[MEETING] Mic sounddevice error: {e}")

    def _start_mic_ffmpeg(self):
        cmd = [
            "ffmpeg", "-f", "alsa", "-i", "default",
            "-af", "volume=4.0", "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "-loglevel", "quiet", "pipe:1",
        ]
        self._process_mic = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._mic_loop, daemon=True).start()

    def _mic_loop(self):
        chunk_size = CHUNK_FRAMES * 2
        while self._running:
            data = self._process_mic.stdout.read(chunk_size)
            if not data:
                break
            self._handle_mic_data(data)

    def _handle_mic_data(self, data: bytes):
        """Mic data → level bar + mic status monitor. KHÔNG vào VAD/worker."""
        if self._on_level_mic:
            audio = np.frombuffer(
                data, dtype=np.int16).astype(np.float32) / 32768.0
            self._on_level_mic(float(np.sqrt(np.mean(audio ** 2))))
        if self._mic_monitor:
            self._mic_monitor.feed(data)