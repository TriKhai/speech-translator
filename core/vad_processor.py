

import numpy as np
import torch
from silero_vad import load_silero_vad, VADIterator


class VADProcessor:

    SAMPLE_RATE  = 16000
    WINDOW_SIZE  = 512

    def __init__(
        self,
        on_speech_chunk: callable,
        on_draft_chunk:  callable = None,   # giữ param để không break caller
        threshold:       float    = 0.5,
        min_speech_ms:   int      = 200,
        min_silence_ms:  int      = 300,
        max_speech_ms:   int      = 30000,
    ):
        self._on_speech_chunk = on_speech_chunk
        self._threshold       = threshold
        self._min_speech_ms   = min_speech_ms
        self._min_silence_ms  = min_silence_ms
        self._max_speech_ms   = max_speech_ms

        print("[VAD] Loading Silero VAD...")
        self._model = load_silero_vad()
        self._iterator = VADIterator(
            self._model,
            threshold=threshold,
            sampling_rate=self.SAMPLE_RATE,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=100,
        )
        print("[VAD] Silero VAD loaded.")

        self._buffer      = bytearray()
        self._speech_buf  = []
        self._is_speaking = False

    def emit_draft(self):
        """No-op — draft bị tắt."""
        pass

    def add(self, data: bytes):
        self._buffer.extend(data)
        bytes_per_window = self.WINDOW_SIZE * 2

        while len(self._buffer) >= bytes_per_window:
            window_bytes = bytes(self._buffer[:bytes_per_window])
            del self._buffer[:bytes_per_window]

            window = np.frombuffer(window_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(window)
            result = self._iterator(tensor, return_seconds=False)

            if result is None:
                if self._is_speaking:
                    self._speech_buf.append(window)
                    total_ms = len(self._speech_buf) * self.WINDOW_SIZE / self.SAMPLE_RATE * 1000
                    if total_ms >= self._max_speech_ms:
                        self._emit()

            elif "start" in result:
                self._is_speaking = True
                self._speech_buf  = [window]

            elif "end" in result:
                if self._is_speaking and self._speech_buf:
                    self._speech_buf.append(window)
                    total_ms = len(self._speech_buf) * self.WINDOW_SIZE / self.SAMPLE_RATE * 1000
                    if total_ms >= self._min_speech_ms:
                        self._emit()
                    else:
                        print(f"[VAD] Bỏ qua chunk ngắn ({total_ms:.0f}ms)")

                self._is_speaking = False
                self._speech_buf  = []
                self._iterator.reset_states()

    def _emit(self):
        if not self._speech_buf:
            return

        audio = np.concatenate(self._speech_buf)
        rms   = np.sqrt(np.mean(audio ** 2))

        if rms < 0.01:
            print(f"[VAD] Bỏ qua chunk noise (RMS={rms:.4f})")
            self._speech_buf = []
            return

        pcm              = (audio * 32768).astype(np.int16).tobytes()
        self._speech_buf = []
        self._on_speech_chunk(pcm)

    def reset(self):
        self._iterator.reset_states()
        self._buffer.clear()
        self._speech_buf  = []
        self._is_speaking = False