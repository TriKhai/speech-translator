"""
transcription_worker.py — parallel speaker + translate, ordered emit

Pipeline:
    Whisper (block trên loop thread)
        → submit [Speaker.identify ║ Translate] song song vào parallel_pool
        → _finish_chunk submit vào serial_pool (max_workers=1)
          → chờ cả 2 future → emit EN + VI đúng thứ tự + lưu DB

Fix so với version trước:
    - speaker_identifier.py đã có Lock nên identify() thread-safe
    - serial_pool đảm bảo thứ tự emit không bị đảo dù speaker/translate
      của chunk sau xong trước chunk trước
"""

import queue
import threading
import concurrent.futures
from dataclasses import dataclass

from core.asr_base            import ASRBase
from core.local_agreement     import LocalAgreement
from db.database_service      import DatabaseService

BYTES_PER_SECOND = 32_000


@dataclass
class _PendingChunk:
    stable:      str
    chunk_start: float
    chunk_end:   float
    confidence:  float
    words:       list
    future_spk:  object = None   # Future[int]
    future_vi:   object = None   # Future[str]


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

        self._mode = self.MODE_BOTH

        # Pool cho Speaker + Translate chạy song song
        self._parallel_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        # Serial executor — emit đúng thứ tự chunk (max_workers=1 là then chốt)
        self._serial_pool   = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        self._queue                          = queue.Queue(maxsize=8)
        self._agreements: dict[int, LocalAgreement] = {}
        self._last_speaker_id: int           = 1

        self._running        = False
        self._thread         = None
        self._bytes_received = 0

    # ══════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════

    def set_mode(self, mode: str):
        assert mode in (self.MODE_BOTH, self.MODE_VI_ONLY)
        self._mode = mode

    def swap_asr(self, engine: ASRBase):
        self._whisper = engine
        print(f"[WORKER] ASR swapped → {engine.name}")

    def start(self):
        self._running        = True
        self._bytes_received = 0
        self._agreements.clear()
        self._last_speaker_id = 1
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def enqueue(self, chunk: bytes, is_final: bool = True, had_draft: bool = False):
        """Đưa VAD chunk vào queue. is_final/had_draft giữ để không break caller."""
        offset = self._bytes_received / BYTES_PER_SECOND
        self._bytes_received += len(chunk)
        try:
            self._queue.put_nowait((chunk, offset))
        except queue.Full:
            print("[WORKER] Queue full — dropped chunk!")

    def stop(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=5)
        self._parallel_pool.shutdown(wait=True, cancel_futures=False)
        self._serial_pool.shutdown(wait=True, cancel_futures=False)
        self._parallel_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self._serial_pool   = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    # ══════════════════════════════════════════════════════
    # Internal helpers
    # ══════════════════════════════════════════════════════

    def _get_agreement(self, speaker_id: int) -> LocalAgreement:
        if speaker_id not in self._agreements:
            self._agreements[speaker_id] = LocalAgreement()
        return self._agreements[speaker_id]

    def _finish_chunk(self, p: _PendingChunk):
        """
        Chạy trong serial_pool (max_workers=1).
        Thứ tự submit = thứ tự emit — không chunk nào bị đảo.
        Chờ future_spk + future_vi (đang chạy song song trong parallel_pool).
        """
        try:
            speaker_id = p.future_spk.result(timeout=10)
        except Exception as e:
            print(f"[WORKER] Speaker error: {e}")
            speaker_id = self._last_speaker_id

        try:
            text_vi = p.future_vi.result(timeout=10)
        except Exception as e:
            print(f"[WORKER] Translate error: {e}")
            text_vi = p.stable

        self._last_speaker_id = speaker_id
        print(f"[WORKER] Speaker {speaker_id} → {p.stable!r}")

        if self._mode == self.MODE_BOTH:
            self._on_english(p.stable, speaker_id)
        self._on_vietnamese(text_vi, speaker_id)

        self._db.save_segment(
            text_en=p.stable, text_vi=text_vi,
            speaker_index=speaker_id,
            start_time=p.chunk_start, end_time=p.chunk_end,
            confidence=p.confidence, words=p.words,
        )

    # ══════════════════════════════════════════════════════
    # Processing
    # ══════════════════════════════════════════════════════

    def _process_chunk(self, chunk: bytes, chunk_offset_sec: float):
        chunk_start = chunk_offset_sec
        chunk_end   = chunk_offset_sec + len(chunk) / BYTES_PER_SECOND

        # ── Bước 1: Whisper (blocking ~0.5-1s) ───────────────────────────
        result = self._whisper.transcribe_full(chunk)

        if not result.text.strip():
            return

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

        agreement = self._get_agreement(self._last_speaker_id)
        stable    = agreement.process(result.text)
        if not stable:
            stable = agreement.flush()
            agreement.reset()

        if not stable:
            return

        # ── Bước 2: Speaker + Translate SONG SONG ────────────────────────
        future_spk = self._parallel_pool.submit(self._speaker.identify, chunk)
        future_vi  = self._parallel_pool.submit(self._translator.translate, stable)

        p = _PendingChunk(
            stable      = stable,
            chunk_start = chunk_start,
            chunk_end   = chunk_end,
            confidence  = result.confidence,
            words       = adjusted_words,
            future_spk  = future_spk,
            future_vi   = future_vi,
        )

        # ── Bước 3: Emit qua serial_pool — đảm bảo thứ tự ───────────────
        self._serial_pool.submit(self._finish_chunk, p)

    # ══════════════════════════════════════════════════════
    # Main loop
    # ══════════════════════════════════════════════════════

    def _process_loop(self):
        while self._running:
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if item is None:
                break

            chunk, chunk_offset_sec = item
            try:
                self._process_chunk(chunk, chunk_offset_sec)
            except Exception as e:
                print(f"[WORKER] Error: {e}")
                import traceback; traceback.print_exc()