import numpy as np
import torch
from silero_vad import load_silero_vad, VADIterator


class VADProcessor:
    """
    Silero VAD — chỉ emit audio chunk khi có tiếng nói thật.
    Thay thế AudioChunker cứng 3s.

    Hoạt động:
    - Nhận PCM bytes liên tục từ ffmpeg
    - Phát hiện đoạn có tiếng nói (speech segment)
    - Emit chunk khi người dùng ngừng nói (silence detected)
    """

    SAMPLE_RATE  = 16000
    WINDOW_SIZE  = 512   # samples mỗi lần VAD check (32ms @ 16kHz)

    def __init__(
        self,
        on_speech_chunk: callable,
        threshold: float       = 0.5,   # độ nhạy VAD (0-1), cao = nhạy hơn
        min_speech_ms: int     = 300,   # bỏ qua tiếng nói < 300ms
        min_silence_ms: int    = 600,   # im lặng 600ms → kết thúc 1 câu
        max_speech_ms: int     = 8000,  # tối đa 8s/chunk, tránh Whisper quá tải
    ):
        self._on_speech_chunk = on_speech_chunk
        self._threshold       = threshold
        self._min_speech_ms   = min_speech_ms
        self._min_silence_ms  = min_silence_ms
        self._max_speech_ms   = max_speech_ms

        # Load Silero VAD model
        print("[VAD] Loading Silero VAD...")
        self._model = load_silero_vad()
        self._iterator = VADIterator(
            self._model,
            threshold=threshold,
            sampling_rate=self.SAMPLE_RATE,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=100,   # thêm 100ms padding 2 đầu để không bị cắt câu
        )
        print("[VAD] Silero VAD loaded.")

        self._buffer       = bytearray()  # raw bytes chờ xử lý
        self._speech_buf   = []           # float32 frames đang là speech
        self._is_speaking  = False

    def add(self, data: bytes):
        """Nhận raw PCM s16le bytes từ ffmpeg."""
        self._buffer.extend(data)

        # Xử lý từng window 512 samples (1024 bytes)
        bytes_per_window = self.WINDOW_SIZE * 2  # s16le = 2 bytes/sample

        while len(self._buffer) >= bytes_per_window:
            window_bytes = bytes(self._buffer[:bytes_per_window])
            del self._buffer[:bytes_per_window]

            # Convert s16le → float32
            window = np.frombuffer(window_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(window)

            # Chạy VAD
            result = self._iterator(tensor, return_seconds=False)

            if result is None:
                # Đang trong trạng thái speech → tích lũy
                if self._is_speaking:
                    self._speech_buf.append(window)

                    # Nếu quá max_speech_ms → emit luôn tránh Whisper quá tải
                    total_ms = len(self._speech_buf) * self.WINDOW_SIZE / self.SAMPLE_RATE * 1000
                    if total_ms >= self._max_speech_ms:
                        self._emit()

            elif 'start' in result:
                # Bắt đầu tiếng nói
                self._is_speaking = True
                self._speech_buf  = [window]

            elif 'end' in result:
                if self._is_speaking and self._speech_buf:
                    self._speech_buf.append(window)
                    total_ms = len(self._speech_buf) * self.WINDOW_SIZE / self.SAMPLE_RATE * 1000
                    if total_ms >= self._min_speech_ms:
                        self._emit()
                    else:
                        print(f"[VAD] Bỏ qua chunk ngắn ({total_ms:.0f}ms)")
                self._is_speaking = False
                self._speech_buf  = []
                self._iterator.reset_states()  # ← thêm dòng này

    def _emit(self):
        audio = np.concatenate(self._speech_buf)
        
        # Filter chunk toàn noise/silence trước khi gọi Whisper
        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 0.01:
            print(f"[VAD] Bỏ qua chunk noise (RMS={rms:.4f})")
            self._speech_buf = []
            self._iterator.reset_states()
            return
        
        pcm = (audio * 32768).astype(np.int16).tobytes()
        self._speech_buf = []
        self._iterator.reset_states()
        self._on_speech_chunk(pcm)

    def reset(self):
        """Reset state khi stop recording."""
        self._iterator.reset_states()
        self._buffer.clear()
        self._speech_buf  = []
        self._is_speaking = False