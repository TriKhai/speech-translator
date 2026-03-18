import time
import queue
import threading
import concurrent.futures
from core.whisper_service     import WhisperService
from core.local_agreement     import LocalAgreement
from core.speaker_identifier  import SpeakerIdentifier
from core.translation_service import TranslationService
from db.database_service      import DatabaseService


class TranscriptionWorker:
    """
    Pipeline với 2 mode:

    Mode "both" (mặc định):
        Whisper + Speaker song song
        → submit translate NGAY khi có text (song song với emit EN)
        → emit EN ngay
        → emit VI khi dịch xong (delay ≈ 0 vì đã chạy song song)

    Mode "vi_only":
        Giống "both" nhưng KHÔNG emit EN
        → chỉ emit VI khi dịch xong

    Cải thiện delay so với bản cũ:
        Cũ:  whisper(2s) → emit EN → translate(1s) → emit VI   = 3s total
        Mới: whisper(2s) song song translate(1s) → emit EN+VI  = 2s total
    """

    MODE_BOTH    = "both"
    MODE_VI_ONLY = "vi_only"

    def __init__(
        self,
        whisper:       WhisperService,
        speaker:       SpeakerIdentifier,
        translator:    TranslationService,
        db:            DatabaseService,
        on_english:    callable,
        on_vietnamese: callable,
    ):
        self._whisper       = whisper
        self._speaker       = speaker
        self._translator    = translator
        self._db            = db
        self._on_english    = on_english
        self._on_vietnamese = on_vietnamese

        self._mode          = self.MODE_BOTH
        self._queue         = queue.Queue(maxsize=2)
        self._bg_pool       = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self._agreement     = LocalAgreement()
        self._running       = False
        self._thread        = None
        self._session_start = None

    def set_mode(self, mode: str):
        """
        Đổi mode realtime (hiệu lực từ chunk tiếp theo).
            "both"    → hiện EN + VI
            "vi_only" → chỉ hiện VI
        """
        assert mode in (self.MODE_BOTH, self.MODE_VI_ONLY)
        self._mode = mode
        print(f"[WORKER] Mode → {mode}")

    def start(self):
        self._running        = True
        self._session_start  = time.time()
        self._bytes_received = 0   # tổng bytes PCM đã nhận, dùng để tính offset chính xác
        self._agreement.reset()
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def enqueue(self, chunk: bytes, is_final: bool = False):
        # Tính offset của chunk này trong WAV dựa trên bytes đã nhận
        # BYTES_PER_SECOND = 16000 Hz * 2 bytes (s16le) = 32000
        BYTES_PER_SECOND = 32000
        chunk_offset_sec = self._bytes_received / BYTES_PER_SECOND
        self._bytes_received += len(chunk)

        if self._queue.full():
            try:
                self._queue.get_nowait()
                print("[WORKER] Queue full — dropped oldest chunk.")
            except queue.Empty:
                pass
        self._queue.put_nowait((chunk, is_final, chunk_offset_sec))

    def stop(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=5)

    # ── Helpers 
    def _save_to_db(self, text_en, text_vi, speaker_id,
                    start_time, end_time, confidence, words):
        self._db.save_segment(
            text_en       = text_en,
            text_vi       = text_vi,
            speaker_index = speaker_id,
            start_time    = start_time,
            end_time      = end_time,
            confidence    = confidence,
            words         = words,
        )

    def _finish_vi(self, stable, speaker_id, chunk_start, chunk_end,
                   confidence, words, future_vi):
        """
        Chạy trên bg_pool — đợi future_vi (đã đang chạy song song)
        rồi emit VI + lưu DB.
        """
        try:
            text_vi = future_vi.result(timeout=10)
        except Exception as e:
            print(f"[WORKER] Translate error: {e}")
            text_vi = stable    # fallback: hiện EN nếu dịch lỗi

        self._on_vietnamese(text_vi, speaker_id)
        self._save_to_db(stable, text_vi, speaker_id,
                         chunk_start, chunk_end, confidence, words)

    # ── Main loop 
    def _process_loop(self):
        while self._running:
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if item is None:
                break

            chunk, is_final, chunk_offset_sec = item

            try:
                # 1. Whisper + Speaker song song
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                    f_asr = ex.submit(self._whisper.transcribe_full, chunk)
                    f_spk = ex.submit(self._speaker.identify, chunk)
                    result     = f_asr.result()
                    speaker_id = f_spk.result()

                if not result.text.strip():
                    continue

                # start_time = offset chunk trong WAV
                # end_time   = offset + duration chunk (bytes → giây)
                BYTES_PER_SECOND = 32000
                chunk_start = chunk_offset_sec
                chunk_end   = chunk_offset_sec + len(chunk) / BYTES_PER_SECOND
                print(f"[WORKER] Speaker {speaker_id} [{chunk_start:.1f}s→{chunk_end:.1f}s]: {result.text}")

                # Adjust word timestamps: Whisper trả về offset trong chunk
                # → cộng chunk_offset_sec để ra offset trong toàn bộ WAV
                adjusted_words = []
                for w in (result.words or []):
                    adjusted_words.append({
                        "word":        w["word"],
                        "start":       round(w["start"] + chunk_offset_sec, 3),
                        "end":         round(w["end"]   + chunk_offset_sec, 3),
                        "probability": w["probability"],
                    })

                # 2. LocalAgreement → lấy stable text
                if is_final:
                    self._agreement.process(result.text)
                    stable = self._agreement.flush()
                    self._agreement.reset()
                else:
                    stable = self._agreement.process(result.text)

                if not stable:
                    continue

                # 3. Submit translate NGAY — chạy song song với emit EN + UI
                future_vi = self._bg_pool.submit(
                    self._translator.translate, stable
                )

                # 4. Emit EN (chỉ mode both)
                if self._mode == self.MODE_BOTH:
                    self._on_english(stable, speaker_id)

                # 5. Đợi VI + lưu DB (nền) — future_vi đang chạy song song
                self._bg_pool.submit(
                    self._finish_vi,
                    stable, speaker_id,
                    chunk_start, chunk_end,
                    result.confidence, adjusted_words,
                    future_vi,
                )

            except Exception as e:
                print(f"[WORKER] Error: {e}")