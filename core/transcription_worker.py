"""
transcription_worker.py — Fix SpeakerBuffer không gộp chunk

Bugs từ log:
    1. Buffer luôn "gán cho 1 chunk" vì LocalAgreement trả None cho chunk 2, 3
       → code `if not stable: continue` bỏ qua chunk → buffer không tích lũy
       Fix: tích lũy PCM vào buffer TRƯỚC khi check stable text

    2. Speaker 3 tạo tại score=0.141, 0.253 (quá thấp = chunk bị cắt/nhiễu)
       Fix: MIN_CONFIDENCE_SCORE — nếu best_score < ngưỡng này thì giữ speaker cũ
       thay vì tạo speaker mới (xử lý trong speaker_identifier.py)
"""

import time
import queue
import threading
import concurrent.futures
from dataclasses import dataclass

from core.whisper_service     import WhisperService
from core.local_agreement     import LocalAgreement
from core.speaker_identifier  import SpeakerIdentifier
from core.translation_service import TranslationService
from db.database_service      import DatabaseService

BYTES_PER_SECOND      = 32000
SPEAKER_BUFFER_CHUNKS = 3   # gộp 3 chunk PCM (~4.5s) rồi identify 1 lần


@dataclass
class _PendingChunk:
    stable:      str
    chunk_start: float
    chunk_end:   float
    confidence:  float
    words:       list
    future_vi:   object = None


class TranscriptionWorker:

    MODE_BOTH    = "both"
    MODE_VI_ONLY = "vi_only"

    def __init__(self, whisper, speaker, translator, db, on_english, on_vietnamese):
        self._whisper       = whisper
        self._speaker       = speaker
        self._translator    = translator
        self._db            = db
        self._on_english    = on_english
        self._on_vietnamese = on_vietnamese

        self._mode    = self.MODE_BOTH
        self._queue   = queue.Queue(maxsize=4)
        self._bg_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self._agreements: dict[int, LocalAgreement] = {}

        # Speaker buffer
        self._spk_pcm_buf:     list[bytes]         = []
        self._spk_pending:     list[_PendingChunk] = []
        self._last_speaker_id: int                 = 1

        self._running        = False
        self._thread         = None
        self._bytes_received = 0

    def set_mode(self, mode: str):
        assert mode in (self.MODE_BOTH, self.MODE_VI_ONLY)
        self._mode = mode

    def start(self):
        self._running        = True
        self._bytes_received = 0
        self._agreements.clear()
        self._spk_pcm_buf.clear()
        self._spk_pending.clear()
        self._last_speaker_id = 1
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def enqueue(self, chunk: bytes, is_final: bool = False):
        offset = self._bytes_received / BYTES_PER_SECOND
        self._bytes_received += len(chunk)
        if self._queue.full():
            try:
                self._queue.get_nowait()
                print("[WORKER] Queue full — dropped oldest chunk.")
            except queue.Empty:
                pass
        self._queue.put_nowait((chunk, is_final, offset))

    def stop(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=5)

    def _get_agreement(self, speaker_id: int) -> LocalAgreement:
        if speaker_id not in self._agreements:
            self._agreements[speaker_id] = LocalAgreement()
        return self._agreements[speaker_id]

    def _flush_speaker_buffer(self, is_final: bool = False):
        """
        Identify speaker từ PCM đã gộp rồi emit tất cả pending chunks.
        Chỉ gọi khi buffer đủ SPEAKER_BUFFER_CHUNKS hoặc is_final.
        """
        if not self._spk_pcm_buf:
            return

        combined_pcm = b"".join(self._spk_pcm_buf)
        speaker_id   = self._speaker.identify(combined_pcm)
        self._last_speaker_id = speaker_id
        print(f"[WORKER] Speaker {speaker_id} → "
              f"gán cho {len(self._spk_pending)} pending chunk(s)")

        for p in self._spk_pending:
            if self._mode == self.MODE_BOTH:
                self._on_english(p.stable, speaker_id)
            self._bg_pool.submit(
                self._finish_vi,
                p.stable, speaker_id,
                p.chunk_start, p.chunk_end,
                p.confidence, p.words,
                p.future_vi,
            )

        self._spk_pending.clear()
        self._spk_pcm_buf.clear()

    def _finish_vi(self, stable, speaker_id, chunk_start, chunk_end,
                   confidence, words, future_vi):
        try:
            text_vi = future_vi.result(timeout=10)
        except Exception as e:
            print(f"[WORKER] Translate error: {e}")
            text_vi = stable
        self._on_vietnamese(text_vi, speaker_id)
        self._db.save_segment(
            text_en=stable, text_vi=text_vi,
            speaker_index=speaker_id,
            start_time=chunk_start, end_time=chunk_end,
            confidence=confidence, words=words,
        )

    def _process_loop(self):
        while self._running:
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if item is None:
                # Flush còn lại khi stop
                if self._spk_pcm_buf:
                    print(f"[WORKER] Stop flush: {len(self._spk_pending)} chunk(s)")
                    self._flush_speaker_buffer(is_final=True)
                break

            chunk, is_final, chunk_offset_sec = item

            try:
                chunk_start = chunk_offset_sec
                chunk_end   = chunk_offset_sec + len(chunk) / BYTES_PER_SECOND

                # ── FIX: Tích lũy PCM vào buffer TRƯỚC mọi thứ khác ─────────
                # Không để LocalAgreement/stable check chặn việc gom PCM
                self._spk_pcm_buf.append(chunk)

                # ── Whisper ─────────────────────────────────────────────────
                result = self._whisper.transcribe_full(chunk)

                if not result.text.strip():
                    # Chunk không có text — vẫn đếm vào buffer PCM
                    # Khi đủ chunk thì flush với speaker trước đó
                    if len(self._spk_pcm_buf) >= SPEAKER_BUFFER_CHUNKS or is_final:
                        if self._spk_pending:
                            self._flush_speaker_buffer(is_final)
                        else:
                            # Không có pending text, chỉ xoá PCM buffer
                            self._spk_pcm_buf.clear()
                    continue

                print(f"[WORKER] [{chunk_start:.1f}s→{chunk_end:.1f}s]: {result.text}")

                adjusted_words = [
                    {
                        "word":        w["word"],
                        "start":       round(w["start"] + chunk_offset_sec, 3),
                        "end":         round(w["end"]   + chunk_offset_sec, 3),
                        "probability": w["probability"],
                    }
                    for w in (result.words or [])
                ]

                # LocalAgreement dùng last_speaker_id tạm
                agreement = self._get_agreement(self._last_speaker_id)
                if is_final:
                    agreement.process(result.text)
                    stable = agreement.flush()
                    agreement.reset()
                else:
                    stable = agreement.process(result.text)

                if not stable:
                    # Chưa stable — vẫn giữ chunk trong PCM buffer, chờ flush sau
                    continue

                # Submit dịch ngay (song song)
                future_vi = self._bg_pool.submit(self._translator.translate, stable)

                # Thêm vào pending — sẽ emit khi flush_speaker_buffer
                self._spk_pending.append(_PendingChunk(
                    stable      = stable,
                    chunk_start = chunk_start,
                    chunk_end   = chunk_end,
                    confidence  = result.confidence,
                    words       = adjusted_words,
                    future_vi   = future_vi,
                ))

                # Flush khi buffer đủ hoặc là chunk cuối
                if len(self._spk_pcm_buf) >= SPEAKER_BUFFER_CHUNKS or is_final:
                    self._flush_speaker_buffer(is_final)

            except Exception as e:
                print(f"[WORKER] Error: {e}")
                import traceback; traceback.print_exc()