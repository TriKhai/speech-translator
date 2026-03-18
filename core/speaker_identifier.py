"""
speaker_identifier.py — resemblyzer + warmup fix

Vấn đề:
    Chunk đầu tiên (Speaker 1) có embedding chưa ổn định vì centroid mới được
    tạo từ 1 sample duy nhất. Chunk thứ 2 của cùng giọng có score ~0.65 → tạo
    Speaker 2 nhầm.

Fix — Warmup buffer:
    Gom 2 chunk đầu tiên vào warmup_buf, gộp PCM lại, tạo centroid từ audio
    dài hơn (~3s) trước khi bắt đầu so sánh. Từ chunk thứ 3 mới identify bình thường.
    Trong thời gian warmup: tất cả chunk gán là Speaker 1.
"""

import numpy as np

WARMUP_CHUNKS = 2   # gom 2 chunk đầu (~3s) để build centroid ổn định


class SpeakerIdentifier:

    SIMILARITY_THRESHOLD = 0.72
    MIN_CONFIDENCE_SCORE = 0.40
    NOISE_RMS_THRESHOLD  = 0.014
    MOVING_AVG_ALPHA     = 0.10
    MIN_CHUNK_SECONDS    = 0.8
    SAMPLE_RATE          = 16000

    def __init__(self, device: str = "cpu"):
        print("[SPEAKER] Loading resemblyzer encoder...")
        try:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder(device="cpu")
            print("[SPEAKER] resemblyzer loaded OK")
            print(f"[SPEAKER] Threshold={self.SIMILARITY_THRESHOLD} | "
                  f"MinConf={self.MIN_CONFIDENCE_SCORE} | "
                  f"NoiseRMS={self.NOISE_RMS_THRESHOLD} | "
                  f"MinChunk={self.MIN_CHUNK_SECONDS}s | "
                  f"Warmup={WARMUP_CHUNKS} chunks")
        except ImportError:
            raise RuntimeError("\n[SPEAKER] pip install resemblyzer\n")
        except Exception as e:
            raise RuntimeError(f"[SPEAKER] Không load model: {e}")

        self._speakers: dict[int, np.ndarray] = {}
        self._next_id  = 1
        self._last_id  = None

        # Warmup buffer — gom PCM trước khi identify
        self._warmup_buf:    list[bytes] = []
        self._warmup_done:   bool        = False

    def _extract_embedding(self, audio: np.ndarray) -> np.ndarray | None:
        try:
            from resemblyzer import preprocess_wav
            wav = preprocess_wav(audio, source_sr=self.SAMPLE_RATE)
            if len(wav) < 240:
                return None
            emb  = self._encoder.embed_utterance(wav)
            norm = np.linalg.norm(emb)
            if norm < 1e-8:
                return None
            return emb / norm
        except Exception as e:
            print(f"[SPEAKER] Embed error: {e}")
            return None

    def identify(self, pcm_bytes: bytes) -> int:
        audio    = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        rms      = float(np.sqrt(np.mean(audio ** 2)))
        duration = len(audio) / self.SAMPLE_RATE

        # Lọc noise
        if rms < self.NOISE_RMS_THRESHOLD:
            fallback = self._last_id or 1
            print(f"[SPEAKER] Noise (RMS={rms:.4f}) → giữ Speaker {fallback}")
            return fallback

        # Lọc chunk quá ngắn
        if duration < self.MIN_CHUNK_SECONDS:
            fallback = self._last_id or 1
            print(f"[SPEAKER] Quá ngắn ({duration:.2f}s) → giữ Speaker {fallback}")
            return fallback

        # ── Warmup phase: gom chunk đầu để build centroid ổn định ──────────
        if not self._warmup_done:
            self._warmup_buf.append(pcm_bytes)
            print(f"[SPEAKER] Warmup {len(self._warmup_buf)}/{WARMUP_CHUNKS} "
                  f"(rms={rms:.3f}, dur={duration:.2f}s) → Speaker 1")

            if len(self._warmup_buf) >= WARMUP_CHUNKS:
                # Gộp tất cả warmup PCM, tạo centroid từ audio dài hơn
                combined  = np.frombuffer(b"".join(self._warmup_buf),
                                          dtype=np.int16).astype(np.float32) / 32768.0
                embedding = self._extract_embedding(combined)
                if embedding is not None:
                    self._speakers[1] = embedding
                    self._next_id     = 2
                    print(f"[SPEAKER] Warmup done → Speaker 1 centroid built "
                          f"({len(self._warmup_buf)} chunks, "
                          f"{len(combined)/self.SAMPLE_RATE:.1f}s audio)")
                else:
                    # Fallback: dùng chunk cuối nếu gộp thất bại
                    last_audio = np.frombuffer(self._warmup_buf[-1],
                                               dtype=np.int16).astype(np.float32) / 32768.0
                    emb = self._extract_embedding(last_audio)
                    if emb is not None:
                        self._speakers[1] = emb
                        self._next_id     = 2
                self._warmup_done = True
                self._last_id     = 1

            return 1

        # ── Normal phase: so sánh với speakers đã biết ─────────────────────
        embedding = self._extract_embedding(audio)
        if embedding is None:
            fallback = self._last_id or 1
            print(f"[SPEAKER] Embed thất bại → giữ Speaker {fallback}")
            return fallback

        if not self._speakers:
            # Fallback nếu warmup build thất bại
            self._speakers[1] = embedding
            self._next_id     = 2
            self._last_id     = 1
            return 1

        best_id    = None
        best_score = -1.0
        for spk_id, spk_emb in self._speakers.items():
            score = float(np.dot(embedding, spk_emb))
            if score > best_score:
                best_score = score
                best_id    = spk_id

        if best_score >= self.SIMILARITY_THRESHOLD:
            updated = (self.MOVING_AVG_ALPHA * embedding
                       + (1 - self.MOVING_AVG_ALPHA) * self._speakers[best_id])
            self._speakers[best_id] = updated / (np.linalg.norm(updated) + 1e-8)
            print(f"[SPEAKER] Speaker {best_id} "
                  f"(score={best_score:.3f}, rms={rms:.3f}, dur={duration:.2f}s)")
            self._last_id = best_id
            return best_id

        elif best_score < self.MIN_CONFIDENCE_SCORE:
            fallback = self._last_id or 1
            print(f"[SPEAKER] Score thấp ({best_score:.3f}) → giữ Speaker {fallback}")
            return fallback

        else:
            new_id = self._next_id
            self._next_id += 1
            self._speakers[new_id] = embedding
            print(f"[SPEAKER] New → Speaker {new_id} "
                  f"(best={best_score:.3f}, rms={rms:.3f}, dur={duration:.2f}s)")
            self._last_id = new_id
            return new_id

    def reset(self):
        self._speakers.clear()
        self._next_id    = 1
        self._last_id    = None
        self._warmup_buf  = []
        self._warmup_done = False