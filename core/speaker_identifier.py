import threading
import numpy as np

WARMUP_CHUNKS = 2


class SpeakerIdentifier:

    # SIMILARITY_THRESHOLD = 0.72
    # MIN_CONFIDENCE_SCORE = 0.40
    # NOISE_RMS_THRESHOLD  = 0.014
    # MOVING_AVG_ALPHA     = 0.10
    # MIN_CHUNK_SECONDS    = 0.8
    # SAMPLE_RATE          = 16000

    SIMILARITY_THRESHOLD = 0.72   
    MIN_CONFIDENCE_SCORE = 0.40   
    NOISE_RMS_THRESHOLD  = 0.008  
    MOVING_AVG_ALPHA     = 0.10
    MIN_CHUNK_SECONDS    = 1.0    
    SAMPLE_RATE          = 16000

    def __init__(self, device: str = "cpu", max_speakers: int = 4):
        self._max_speakers = max_speakers
        self._lock         = threading.Lock()   # ← thread-safe

        print("[SPEAKER] Loading resemblyzer encoder...")
        try:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder(device="cpu")
            print("[SPEAKER] resemblyzer loaded OK")
            print(f"[SPEAKER] Threshold={self.SIMILARITY_THRESHOLD} | "
                  f"MinConf={self.MIN_CONFIDENCE_SCORE} | "
                  f"NoiseRMS={self.NOISE_RMS_THRESHOLD} | "
                  f"MinChunk={self.MIN_CHUNK_SECONDS}s | "
                  f"Warmup={WARMUP_CHUNKS} chunks | "
                  f"MaxSpeakers={self._max_speakers}")
        except ImportError:
            raise RuntimeError("\n[SPEAKER] pip install resemblyzer\n")
        except Exception as e:
            raise RuntimeError(f"[SPEAKER] Không load model: {e}")

        self._speakers: dict[int, np.ndarray] = {}
        self._next_id  = 1
        self._last_id  = None

        self._warmup_buf:  list[bytes] = []
        self._warmup_done: bool        = False

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
        with self._lock:   # ← serialize tất cả calls
            return self._identify_locked(pcm_bytes)

    def _identify_locked(self, pcm_bytes: bytes) -> int:
        audio    = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        rms      = float(np.sqrt(np.mean(audio ** 2)))
        duration = len(audio) / self.SAMPLE_RATE

        if rms < self.NOISE_RMS_THRESHOLD:
            fallback = self._last_id or 1
            print(f"[SPEAKER] Noise (RMS={rms:.4f}) → giữ Speaker {fallback}")
            return fallback

        if duration < self.MIN_CHUNK_SECONDS:
            fallback = self._last_id or 1
            print(f"[SPEAKER] Quá ngắn ({duration:.2f}s) → giữ Speaker {fallback}")
            return fallback

        # ── Warmup phase ────────────────────────────────────────────────
        if not self._warmup_done:
            self._warmup_buf.append(pcm_bytes)
            print(f"[SPEAKER] Warmup {len(self._warmup_buf)}/{WARMUP_CHUNKS} "
                  f"(rms={rms:.3f}, dur={duration:.2f}s) → Speaker 1")

            if len(self._warmup_buf) >= WARMUP_CHUNKS:
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
                    last_audio = np.frombuffer(self._warmup_buf[-1],
                                               dtype=np.int16).astype(np.float32) / 32768.0
                    emb = self._extract_embedding(last_audio)
                    if emb is not None:
                        self._speakers[1] = emb
                        self._next_id     = 2
                self._warmup_done = True
                self._last_id     = 1

            return 1

        # ── Normal phase ────────────────────────────────────────────────
        embedding = self._extract_embedding(audio)
        if embedding is None:
            fallback = self._last_id or 1
            print(f"[SPEAKER] Embed thất bại → giữ Speaker {fallback}")
            return fallback

        if not self._speakers:
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
            if len(self._speakers) < self._max_speakers:
                new_id = self._next_id
                self._next_id += 1
                self._speakers[new_id] = embedding
                print(f"[SPEAKER] New → Speaker {new_id} "
                      f"(best={best_score:.3f}, rms={rms:.3f}, dur={duration:.2f}s) "
                      f"[{len(self._speakers)}/{self._max_speakers}]")
                self._last_id = new_id
                return new_id
            else:
                updated = (self.MOVING_AVG_ALPHA * embedding
                           + (1 - self.MOVING_AVG_ALPHA) * self._speakers[best_id])
                self._speakers[best_id] = updated / (np.linalg.norm(updated) + 1e-8)
                print(f"[SPEAKER] Max reached ({self._max_speakers}) → "
                      f"gán Speaker {best_id} "
                      f"(score={best_score:.3f}, rms={rms:.3f}, dur={duration:.2f}s)")
                self._last_id = best_id
                return best_id

    def reset(self):
        with self._lock:
            self._speakers.clear()
            self._next_id     = 1
            self._last_id     = None
            self._warmup_buf  = []
            self._warmup_done = False