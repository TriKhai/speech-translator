"""
vad_processor.py  —  FIX #4

Vấn đề gốc:
    _emit() gọi self._iterator.reset_states() SAU mỗi lần emit chunk hợp lệ.
    Điều này phá vỡ internal state của Silero VAD Iterator → bỏ sót đầu câu
    ngay sau chunk vừa emit, vì VAD nghĩ mình đang ở trạng thái "mới bắt đầu".

    Ngoài ra _emit() còn được gọi trong 2 nhánh:
        (a) chunk đủ điều kiện → không nên reset
        (b) chunk noise/rms thấp → không nên reset (VAD cần nhớ state để detect tiếp)

Fix đã áp dụng:
    - Xóa reset_states() khỏi _emit() hoàn toàn
    - reset_states() chỉ còn ở 2 chỗ đúng:
        1. Trong nhánh 'end' của add() khi utterance thực sự kết thúc
        2. Trong reset() khi user bấm Stop/Pause
    - Thêm comment giải thích rõ tại sao không reset trong _emit()
"""

import numpy as np
import torch
from silero_vad import load_silero_vad, VADIterator


class VADProcessor:
    """
    Silero VAD — chỉ emit audio chunk khi có tiếng nói thật.
    Thay thế AudioChunker cứng 3s.

    Hoạt động:
    - Nhận PCM bytes liên tục từ AudioCapture
    - Phát hiện đoạn có tiếng nói (speech segment)
    - Emit chunk khi người dùng ngừng nói (silence detected)
    """

    SAMPLE_RATE  = 16000
    WINDOW_SIZE  = 512   # samples mỗi lần VAD check (32ms @ 16kHz)

    def __init__(
        self,
        on_speech_chunk: callable,
        threshold: float    = 0.5,    # độ nhạy VAD (0–1), cao = nhạy hơn
        min_speech_ms: int  = 300,    # bỏ qua tiếng nói < 300ms
        min_silence_ms: int = 600,    # im lặng 600ms → kết thúc 1 câu
        max_speech_ms: int  = 8000,   # tối đa 8s/chunk, tránh Whisper quá tải
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
            speech_pad_ms=100,   # thêm 100ms padding 2 đầu để không bị cắt câu
        )
        print("[VAD] Silero VAD loaded.")

        self._buffer      = bytearray()
        self._speech_buf  = []         # float32 frames đang là speech
        self._is_speaking = False

    def add(self, data: bytes):
        """Nhận raw PCM s16le bytes từ AudioCapture."""
        self._buffer.extend(data)

        bytes_per_window = self.WINDOW_SIZE * 2  # s16le = 2 bytes/sample

        while len(self._buffer) >= bytes_per_window:
            window_bytes = bytes(self._buffer[:bytes_per_window])
            del self._buffer[:bytes_per_window]

            # Convert s16le → float32
            window = np.frombuffer(window_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(window)

            result = self._iterator(tensor, return_seconds=False)

            if result is None:
                # Đang trong speech → tích lũy frames
                if self._is_speaking:
                    self._speech_buf.append(window)

                    # Nếu quá max_speech_ms → emit sớm, tránh Whisper quá tải
                    total_ms = len(self._speech_buf) * self.WINDOW_SIZE / self.SAMPLE_RATE * 1000
                    if total_ms >= self._max_speech_ms:
                        self._emit()
                        # FIX #4: KHÔNG reset_states() ở đây — VAD tiếp tục tracking
                        # người nói vẫn đang nói, chỉ emit vì quá dài

            elif "start" in result:
                self._is_speaking = True
                self._speech_buf  = [window]

            elif "end" in result:
                # Utterance kết thúc — đây là nơi DUY NHẤT được phép reset_states()
                if self._is_speaking and self._speech_buf:
                    self._speech_buf.append(window)
                    total_ms = len(self._speech_buf) * self.WINDOW_SIZE / self.SAMPLE_RATE * 1000
                    if total_ms >= self._min_speech_ms:
                        self._emit()
                    else:
                        print(f"[VAD] Bỏ qua chunk ngắn ({total_ms:.0f}ms)")

                self._is_speaking = False
                self._speech_buf  = []

                # FIX #4: reset_states() CHỈ khi utterance thực sự kết thúc (có 'end')
                # KHÔNG đặt trong _emit() vì sau max_speech_ms người vẫn còn nói
                self._iterator.reset_states()

    def _emit(self):
        """
        Gộp speech_buf thành PCM bytes và gọi callback.

        FIX #4: KHÔNG gọi reset_states() ở đây.
            - Nếu emit vì max_speech_ms: người vẫn đang nói, VAD phải nhớ state
            - Nếu emit từ nhánh 'end': reset_states() đã được gọi ở add() rồi
        """
        if not self._speech_buf:
            return

        audio = np.concatenate(self._speech_buf)

        # Lọc chunk toàn noise/silence trước khi gọi Whisper
        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 0.01:
            print(f"[VAD] Bỏ qua chunk noise (RMS={rms:.4f})")
            self._speech_buf = []
            # FIX #4: Không reset_states() — VAD tiếp tục tracking bình thường
            return

        pcm = (audio * 32768).astype(np.int16).tobytes()
        self._speech_buf = []
        self._on_speech_chunk(pcm)

    def reset(self):
        """Reset toàn bộ state khi Stop/Pause recording."""
        self._iterator.reset_states()   # đúng chỗ: user chủ động dừng
        self._buffer.clear()
        self._speech_buf  = []
        self._is_speaking = False