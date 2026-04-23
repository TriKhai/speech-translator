import os
import shutil
import threading
import concurrent.futures
import numpy as np
from datetime import datetime


class UploadAudioProcessor:
    """
    Core logic xử lý file upload.

    Kiến trúc — Diarization First:
        1. Decode audio → f32
        2. Diarization toàn file → turns [(start, end, speaker), ...]
        3. ASR song song từng turn → text đúng người nói
        4. Dịch song song
        5. Emit + lưu DB

    Fallback (khi không có diarizer):
        - ASR chunk 30s, speaker=1
    """

    SAMPLE_RATE  = 16_000
    MAX_WORKERS  = 4
    MIN_TURN_SEC = 0.5
    CHUNK_SEC    = 30

    def __init__(self, whisper_service, translation_service,
                 diarizer=None, db=None):
        self._whisper    = whisper_service
        self._translator = translation_service
        self._diarizer   = diarizer
        self._db         = db

    # ──────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────

    def process(self, file_path, on_text, on_progress, on_error=None,
                use_diarize=True, min_speakers=None, max_speakers=None,
                cancel_flag=None):
        try:
            # ── 0. Decode audio ────────────────────────────────────────
            on_progress(2, "Đang decode audio…")
            audio_f32 = self._load_audio(file_path)
            total_sec = len(audio_f32) / self.SAMPLE_RATE
            on_progress(5, f"Audio {total_sec:.0f}s — bắt đầu xử lý…")

            dest_path = self._copy_to_audio_dir(file_path)

            rec_id = None
            if self._db:
                rec_id = self._db.start_recording(
                    audio_path=dest_path,
                    title=os.path.splitext(os.path.basename(file_path))[0],
                    duration=total_sec,
                    source="upload",
                )

            # ── 1. Diarization toàn file ───────────────────────────────
            if use_diarize and self._diarizer:
                on_progress(8, "Đang phân tích người nói…")
                turns = self._run_diarization(
                    audio_f32, min_speakers, max_speakers
                )
                on_progress(35, f"Tìm thấy {len(turns)} lượt nói — bắt đầu ASR…")
            else:
                turns = None

            if cancel_flag and getattr(cancel_flag, "_cancel_flag", False):
                return None

            # ── 2. ASR ────────────────────────────────────────────────
            if turns:
                all_segments = self._asr_by_turns(
                    audio_f32, turns, on_progress, cancel_flag
                )
            else:
                all_segments = self._asr_by_chunks(
                    audio_f32, on_progress, cancel_flag
                )

            if cancel_flag and getattr(cancel_flag, "_cancel_flag", False):
                return None

            # ── 3. Dịch song song ──────────────────────────────────────
            on_progress(88, "Đang dịch…")
            all_segments = self._translate_segments(
                all_segments, on_progress, cancel_flag
            )

            if cancel_flag and getattr(cancel_flag, "_cancel_flag", False):
                return None

            # ── 4. Emit + lưu DB ───────────────────────────────────────
            on_progress(94, "Lưu kết quả…")
            total_segs = len(all_segments)
            for i, seg in enumerate(all_segments):
                on_text(seg["text_en"], seg["text_vi"], seg["speaker_index"])

                if self._db and rec_id:
                    self._db.save_segment(
                        text_en       = seg["text_en"],
                        text_vi       = seg["text_vi"],
                        speaker_index = seg["speaker_index"],
                        start_time    = seg["start_time"],
                        end_time      = seg["end_time"],
                        confidence    = seg["confidence"],
                        words         = seg["words"],
                    )

                on_progress(
                    94 + int(5 * (i + 1) / max(total_segs, 1)),
                    f"Lưu {i + 1}/{total_segs}…"
                )

            if self._db and rec_id:
                self._db.stop_recording()

            on_progress(100, f"Hoàn thành — {total_segs} đoạn")
            return rec_id

        except Exception as e:
            if self._db:
                try:
                    self._db.stop_recording()
                except Exception:
                    pass
            if on_error:
                on_error(str(e))
            else:
                raise

    # ──────────────────────────────────────────────────────────────────
    # Bước 1: Diarization
    # ──────────────────────────────────────────────────────────────────

    def _run_diarization(self, audio_f32, min_speakers, max_speakers):
        try:
            turns = self._diarizer.diarize_array(
                audio_f32, self.SAMPLE_RATE,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            # Lọc turn quá ngắn
            turns = [t for t in turns if (t[1] - t[0]) >= self.MIN_TURN_SEC]
            print(f"[PROCESSOR] Diarization: {len(turns)} turns")
            return turns
        except Exception as ex:
            print(f"[PROCESSOR] Diarization error: {ex}")
            return []

    # ──────────────────────────────────────────────────────────────────
    # Bước 2a: ASR theo turns (diarization first)
    # ──────────────────────────────────────────────────────────────────

    def _asr_by_turns(self, audio_f32, turns, on_progress, cancel_flag):
        """ASR song song từng speaker turn — mỗi turn đúng 1 speaker.
        Turn dài hơn CHUNK_SEC sẽ được cắt thành sub-chunks trước khi ASR.
        """
        # Expand turn dài thành sub-chunks <= CHUNK_SEC
        expanded = []  # list of (start_sec, end_sec, speaker_label)
        for start_sec, end_sec, speaker_label in turns:
            dur = end_sec - start_sec
            if dur <= self.CHUNK_SEC:
                expanded.append((start_sec, end_sec, speaker_label))
            else:
                cur = start_sec
                while cur < end_sec:
                    chunk_end = min(cur + self.CHUNK_SEC, end_sec)
                    expanded.append((cur, chunk_end, speaker_label))
                    cur = chunk_end

        n_turns  = len(expanded)
        results  = [None] * n_turns
        done_cnt = [0]
        lock     = threading.Lock()

        def _asr_turn(idx, start_sec, end_sec, speaker_label):
            try:
                start_sample = int(start_sec * self.SAMPLE_RATE)
                end_sample   = int(end_sec   * self.SAMPLE_RATE)
                chunk_f32    = audio_f32[start_sample:end_sample]

                if len(chunk_f32) == 0:
                    return idx, None

                pcm = (np.clip(chunk_f32, -1.0, 1.0) * 32768
                       ).astype(np.int16).tobytes()

                r       = self._whisper.transcribe_full(pcm)
                text_en = r.text.strip()
                if not text_en:
                    return idx, None

                words = [
                    {
                        "word":        w["word"],
                        "start":       round(w["start"] + start_sec, 3),
                        "end":         round(w["end"]   + start_sec, 3),
                        "probability": w["probability"],
                    }
                    for w in (r.words or [])
                ]

                return idx, {
                    "text_en":       text_en,
                    "text_vi":       text_en,
                    "speaker_index": self._label_to_int(speaker_label),
                    "start_time":    start_sec,
                    "end_time":      end_sec,
                    "confidence":    r.confidence,
                    "words":         words,
                }
            except Exception as ex:
                print(f"[PROCESSOR] Turn {idx} ASR error: {ex}")
                return idx, None

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.MAX_WORKERS
        ) as pool:
            futures = {
                pool.submit(_asr_turn, idx, s, e, spk): idx
                for idx, (s, e, spk) in enumerate(expanded)
            }
            for fut in concurrent.futures.as_completed(futures):
                idx, res = fut.result()
                results[idx] = res
                with lock:
                    done_cnt[0] += 1
                    pct = 35 + int(done_cnt[0] / n_turns * 50)
                on_progress(pct, f"ASR {done_cnt[0]}/{n_turns} turns")

                if cancel_flag and getattr(cancel_flag, "_cancel_flag", False):
                    pool.shutdown(wait=False, cancel_futures=True)
                    return []

        return [r for r in results if r is not None]

    # ──────────────────────────────────────────────────────────────────
    # Bước 2b: ASR fallback theo chunks 30s
    # ──────────────────────────────────────────────────────────────────

    def _asr_by_chunks(self, audio_f32, on_progress, cancel_flag):
        """Fallback khi không có diarizer — chunk 30s, speaker=1."""
        raw_s16     = (audio_f32 * 32768).astype(np.int16).tobytes()
        total_bytes = len(raw_s16)
        chunk_bytes = self.CHUNK_SEC * self.SAMPLE_RATE * 2

        chunks = []
        offset = 0
        while offset < total_bytes:
            end  = min(offset + chunk_bytes, total_bytes)
            end -= (end - offset) % 2
            chunks.append((
                len(chunks),
                raw_s16[offset:end],
                offset / (self.SAMPLE_RATE * 2),
                end    / (self.SAMPLE_RATE * 2),
            ))
            offset = end

        n_chunks = len(chunks)
        results  = [None] * n_chunks
        done_cnt = [0]
        lock     = threading.Lock()

        def _asr_chunk(idx, pcm, start_sec, end_sec):
            try:
                r       = self._whisper.transcribe_full(pcm)
                text_en = r.text.strip()
                if not text_en:
                    return idx, None
                words = [
                    {
                        "word":        w["word"],
                        "start":       round(w["start"] + start_sec, 3),
                        "end":         round(w["end"]   + start_sec, 3),
                        "probability": w["probability"],
                    }
                    for w in (r.words or [])
                ]
                return idx, {
                    "text_en":       text_en,
                    "text_vi":       text_en,
                    "speaker_index": 1,
                    "start_time":    start_sec,
                    "end_time":      end_sec,
                    "confidence":    r.confidence,
                    "words":         words,
                }
            except Exception as ex:
                print(f"[PROCESSOR] Chunk {idx} error: {ex}")
                return idx, None

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.MAX_WORKERS
        ) as pool:
            futures = {
                pool.submit(_asr_chunk, idx, pcm, s, e): idx
                for idx, pcm, s, e in chunks
            }
            for fut in concurrent.futures.as_completed(futures):
                idx, res = fut.result()
                results[idx] = res
                with lock:
                    done_cnt[0] += 1
                    pct = 8 + int(done_cnt[0] / n_chunks * 78)
                on_progress(pct, f"ASR {done_cnt[0]}/{n_chunks} chunks")

                if cancel_flag and getattr(cancel_flag, "_cancel_flag", False):
                    pool.shutdown(wait=False, cancel_futures=True)
                    return []

        return [r for r in results if r is not None]

    # ──────────────────────────────────────────────────────────────────
    # Bước 3: Dịch song song
    # ──────────────────────────────────────────────────────────────────

    def _translate_segments(self, segments, on_progress, cancel_flag):
        if not self._translator or not segments:
            return segments

        n       = len(segments)
        done    = [0]
        results = [None] * n
        lock    = threading.Lock()

        def _translate(idx, text_en):
            try:
                return idx, self._translator.translate(text_en)
            except Exception:
                return idx, text_en

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.MAX_WORKERS
        ) as pool:
            futures = {
                pool.submit(_translate, i, seg["text_en"]): i
                for i, seg in enumerate(segments)
            }
            for fut in concurrent.futures.as_completed(futures):
                idx, text_vi = fut.result()
                results[idx] = text_vi
                with lock:
                    done[0] += 1
                on_progress(
                    88 + int(5 * done[0] / max(n, 1)),
                    f"Dịch {done[0]}/{n}…"
                )
                if cancel_flag and getattr(cancel_flag, "_cancel_flag", False):
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

        for i, seg in enumerate(segments):
            seg["text_vi"] = results[i] or seg["text_en"]
        return segments

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _label_to_int(label, default: int = 1) -> int:
        if isinstance(label, int):
            return label
        if isinstance(label, str) and label.startswith("SPEAKER_"):
            try:
                return int(label.split("_")[1]) + 1
            except (IndexError, ValueError):
                pass
        return default

    def _load_audio(self, file_path: str) -> np.ndarray:
        import subprocess
        cmd = [
            "ffmpeg", "-i", file_path,
            "-f", "f32le", "-ac", "1",
            "-ar", str(self.SAMPLE_RATE),
            "-loglevel", "quiet", "pipe:1",
        ]
        out = subprocess.run(cmd, capture_output=True)
        if out.returncode != 0 or not out.stdout:
            raise RuntimeError(f"ffmpeg decode thất bại: {file_path}")
        return np.frombuffer(out.stdout, dtype=np.float32)

    def _copy_to_audio_dir(self, file_path: str) -> str:
        try:
            from ui.constants import AUDIO_DIR
            os.makedirs(AUDIO_DIR, exist_ok=True)
            ext       = os.path.splitext(file_path)[1]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest      = os.path.join(AUDIO_DIR, f"upload_{timestamp}{ext}")
            shutil.copy2(file_path, dest)
            print(f"[PROCESSOR] Copied to: {dest}")
            return dest
        except Exception as ex:
            print(f"[PROCESSOR] Copy warning: {ex}")
            return file_path