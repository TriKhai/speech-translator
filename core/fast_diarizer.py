"""
fast_diarizer.py — simple-diarizer (xvec + spectral clustering)

Dùng cho demo / CPU-only thay thế pyannote:
  - Không cần HF token
  - Không cần GPU
  - Tốc độ: ~3-8x realtime trên CPU (file 90s → ~10-30s)
  - DER: ~0.64 (tốt hơn resemblyzer, đủ cho demo)

API giống SpeakerDiarizer (pyannote):
  - diarize_array(audio_f32, sample_rate, ...) → list[tuple]
  - diarize(wav_path, ...)                     → list[dict]

Cài đặt:
  pip install simple-diarizer
"""

import os
import time
import tempfile
import subprocess
import numpy as np


class FastDiarizer:

    SAMPLE_RATE = 16_000

    def __init__(self,
                 embed_model: str    = "xvec",
                 cluster_method: str = "sc",
                 window: float       = 1.5,
                 period: float       = 0.75):
        """
        embed_model   : "xvec" (nhanh) hoặc "ecapa" (chính xác hơn, chậm hơn)
        cluster_method: "sc" (spectral, tốt hơn) hoặc "ahc" (nhanh hơn)
        window        : độ dài cửa sổ embedding (giây)
        period        : bước nhảy giữa các cửa sổ (giây)
        """
        self._embed_model    = embed_model
        self._cluster_method = cluster_method
        self._window         = window
        self._period         = period
        self._diarizer       = None   # lazy load
        print(f"[FAST_DIARIZE] FastDiarizer (simple-diarizer/{embed_model}) ready — lazy load")

    # ──────────────────────────────────────────────────────────────────
    # Lazy load
    # ──────────────────────────────────────────────────────────────────

    def _ensure_diarizer(self):
        if self._diarizer is not None:
            return
        print(f"[FAST_DIARIZE] Loading simple-diarizer ({self._embed_model})…")
        from simple_diarizer.diarizer import Diarizer
        self._diarizer = Diarizer(
            embed_model    = self._embed_model,
            cluster_method = self._cluster_method,
            window         = self._window,
            period         = self._period,
        )
        print("[FAST_DIARIZE] Loaded.")

    # ──────────────────────────────────────────────────────────────────
    # Public API — khớp với SpeakerDiarizer
    # ──────────────────────────────────────────────────────────────────

    def diarize_array(self, audio_f32: np.ndarray,
                      sample_rate: int  = 16_000,
                      min_speakers: int = None,
                      max_speakers: int = None) -> list[tuple]:
        """
        Nhận numpy float32 array → trả về list[tuple]:
            [(start_sec, end_sec, "SPEAKER_00"), …]
        Bypass torchaudio.load() của simple-diarizer để tránh bug torchcodec.
        """
        import torch
        t0 = time.time()
        try:
            self._ensure_diarizer()

            duration = len(audio_f32) / max(sample_rate, 1)
            print(f"[FAST_DIARIZE] diarize_array: {duration:.1f}s audio")

            # Resample về 16kHz nếu cần
            if sample_rate != self.SAMPLE_RATE:
                audio_f32 = self._resample(audio_f32, sample_rate, self.SAMPLE_RATE)

            # Clip và convert sang tensor (1, N) — bypass torchaudio.load()
            audio_f32 = np.clip(audio_f32, -1.0, 1.0)
            signal = torch.from_numpy(audio_f32).unsqueeze(0)   # (1, N)
            fs     = self.SAMPLE_RATE

            num_spk = self._resolve_num_speakers(min_speakers, max_speakers)

            # Gọi trực tiếp các bước bên trong simple-diarizer
            # thay vì gọi .diarize(wav_file) để tránh bước load file
            d = self._diarizer

            speech_ts = d.vad(signal[0])
            assert len(speech_ts) >= 1, "VAD không tìm thấy speech"

            embeds, segs = d.recording_embeds(signal, fs, speech_ts)

            cluster_labels = d.cluster(
                embeds,
                n_clusters   = num_spk,
                threshold    = None,
                enhance_sim  = True,
            )

            cleaned = d.join_segments(cluster_labels, segs)
            cleaned = d.make_output_seconds(cleaned, fs)
            cleaned = d.join_samespeaker_segments(cleaned, silence_tolerance=0.2)

            turns = self._segments_to_turns(cleaned)

            elapsed = time.time() - t0
            n_spk_actual = len(set(t[2] for t in turns))
            print(f"[FAST_DIARIZE] diarize_array done in {elapsed:.1f}s: "
                  f"{len(turns)} turns, {n_spk_actual} speakers")
            for t in turns[:20]:
                print(f"  [{t[0]:7.2f}s–{t[1]:7.2f}s]  {t[2]}")
            if len(turns) > 20:
                print(f"  … ({len(turns) - 20} turns more)")

            return turns

        except Exception as e:
            print(f"[FAST_DIARIZE] diarize_array error: {e}")
            import traceback; traceback.print_exc()
            return []

    def diarize(self, wav_path: str,
                min_speakers: int = None,
                max_speakers: int = None) -> list[dict]:
        """
        Nhận wav_path → trả về list[dict]:
            [{"start": float, "end": float, "speaker": str}, …]
        Giống format SpeakerDiarizer.diarize()
        """
        t0 = time.time()
        try:
            self._ensure_diarizer()

            clean_wav = self._ensure_wav_16k(wav_path)
            is_tmp    = (clean_wav != wav_path)

            num_spk  = self._resolve_num_speakers(min_speakers, max_speakers)
            segments = self._diarizer.diarize(
                wav_file     = clean_wav,
                num_speakers = num_spk,
                enhance_sim  = True,
                extra_info   = False,
            )

            if is_tmp and os.path.exists(clean_wav):
                os.remove(clean_wav)

            turns = [
                {
                    "start":   round(s["start"], 3),
                    "end":     round(s["end"],   3),
                    "speaker": f"SPEAKER_{int(s['label']):02d}",
                }
                for s in segments
            ]

            elapsed = time.time() - t0
            n_spk   = len(set(t["speaker"] for t in turns))
            print(f"[FAST_DIARIZE] diarize done in {elapsed:.1f}s: "
                  f"{len(turns)} turns, {n_spk} speakers")
            return turns

        except Exception as e:
            print(f"[FAST_DIARIZE] diarize error: {e}")
            import traceback; traceback.print_exc()
            return []

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_num_speakers(min_speakers, max_speakers) -> int:
        """
        simple-diarizer nhận num_speakers duy nhất (int).
        Nếu biết cả 2 đầu → lấy trung bình, ưu tiên max.
        Nếu không biết → mặc định 2.
        """
        if min_speakers and max_speakers:
            return max_speakers          # ưu tiên max để không bỏ sót speaker
        if max_speakers:
            return max_speakers
        if min_speakers:
            return min_speakers
        return 2                         # fallback

    def _array_to_tmp_wav(self, audio_f32: np.ndarray,
                          sample_rate: int) -> str:
        """Ghi numpy array ra file WAV tmp 16kHz mono."""
        import wave, struct

        # Resample nếu không phải 16kHz
        if sample_rate != self.SAMPLE_RATE:
            audio_f32 = self._resample(audio_f32, sample_rate, self.SAMPLE_RATE)

        # Clip và convert sang int16
        audio_f32 = np.clip(audio_f32, -1.0, 1.0)
        pcm_int16 = (audio_f32 * 32768).astype(np.int16)

        tmp = tempfile.mktemp(suffix="_fast_diar.wav")
        with wave.open(tmp, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(pcm_int16.tobytes())
        return tmp

    @staticmethod
    def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
        """Resample đơn giản bằng scipy nếu có, fallback linear interp."""
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(src_sr, dst_sr)
            return resample_poly(audio, dst_sr // g, src_sr // g).astype(np.float32)
        except ImportError:
            ratio  = dst_sr / src_sr
            n_out  = int(len(audio) * ratio)
            x_old  = np.linspace(0, 1, len(audio))
            x_new  = np.linspace(0, 1, n_out)
            return np.interp(x_new, x_old, audio).astype(np.float32)

    @staticmethod
    def _segments_to_turns(segments: list) -> list[tuple]:
        """
        Chuyển output của simple-diarizer sang list[tuple].
        segments: [{"start": float, "end": float, "label": int}, …]
        """
        turns = []
        for s in segments:
            label = f"SPEAKER_{int(s['label']):02d}"
            turns.append((round(s["start"], 3), round(s["end"], 3), label))
        return turns

    def _ensure_wav_16k(self, path: str) -> str:
        """Convert về WAV 16kHz mono nếu cần."""
        import wave
        try:
            with wave.open(path, "rb") as f:
                if f.getframerate() == 16000 and f.getnchannels() == 1:
                    return path
        except Exception:
            pass

        tmp = tempfile.mktemp(suffix="_16k.wav")
        result = subprocess.run([
            "ffmpeg", "-y", "-i", path,
            "-ac", "1", "-ar", "16000",
            "-loglevel", "quiet", tmp,
        ])
        if result.returncode != 0:
            raise RuntimeError(f"[FAST_DIARIZE] ffmpeg convert thất bại: {path}")
        return tmp