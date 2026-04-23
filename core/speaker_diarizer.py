import os
import time
import subprocess
import tempfile


class SpeakerDiarizer:

    def __init__(self, hf_token: str = None):
        self._pipeline = None   # lazy load
        self._hf_token = (
            hf_token
            or os.environ.get("HF_TOKEN", "")
            or os.environ.get("HUGGINGFACE_TOKEN", "")
        )
        if not self._hf_token:
            print("[DIARIZE] WARNING: HF_TOKEN không tìm thấy — "
                  "pyannote sẽ lỗi khi load model.")
        print("[DIARIZE] SpeakerDiarizer (pyannote 3.1) ready — lazy load")

    # ══════════════════════════════════════════════════════
    # Lazy load
    # ══════════════════════════════════════════════════════

    def _ensure_pipeline(self):
        if self._pipeline is not None:
            return

        print("[DIARIZE] Loading pyannote/speaker-diarization-3.1…")
        from pyannote.audio import Pipeline
        import torch

        # pyannote >= 3.x đổi use_auth_token → token
        # thử token trước, fallback use_auth_token cho version cũ
        try:
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=self._hf_token,
            )
        except TypeError:
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self._hf_token,
            )

        self._pipeline = self._pipeline.to(torch.device("cpu"))
        print("[DIARIZE] pyannote loaded on cpu.")

    # ══════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════

    def diarize(self, wav_path: str) -> list[dict]:
        """
        Chạy diarization trên file audio.
        Trả về list[dict]: [{"start": float, "end": float, "speaker": str}]
        """
        t0 = time.time()
        print(f"[DIARIZE] Processing: {wav_path}")

        try:
            self._ensure_pipeline()

            # Đảm bảo file là WAV 16kHz mono
            clean_wav = self._ensure_wav_16k(wav_path)
            is_tmp    = (clean_wav != wav_path)

            print("[DIARIZE] Running pyannote pipeline…")
            diarization = self._pipeline(clean_wav)

            if is_tmp and os.path.exists(clean_wav):
                os.remove(clean_wav)

            turns = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                turns.append({
                    "start":   round(turn.start, 3),
                    "end":     round(turn.end,   3),
                    "speaker": speaker,   # "SPEAKER_00", "SPEAKER_01", ...
                })

            elapsed = time.time() - t0
            n_spk   = len(set(t["speaker"] for t in turns))
            print(f"[DIARIZE] Done in {elapsed:.1f}s: "
                  f"{len(turns)} turns, {n_spk} speakers")
            for t in turns[:20]:
                print(f"  [{t['start']:7.2f}–{t['end']:7.2f}s]  {t['speaker']}")
            if len(turns) > 20:
                print(f"  … ({len(turns) - 20} turns more)")

            return turns

        except Exception as e:
            print(f"[DIARIZE] Error: {e}")
            import traceback; traceback.print_exc()
            return []

    def diarize_array(self, audio_f32: "np.ndarray", sample_rate: int = 16000) -> list[tuple]:
        """
        Nhận numpy float32 array (đã decode sẵn) thay vì wav_path.
        Truyền thẳng vào pyannote pipeline — không cần ghi file tmp.

        Trả về list[tuple]: [(start_sec, end_sec, speaker_label), …]
        """
        import numpy as np
        import torch

        t0 = time.time()

        try:
            self._ensure_pipeline()

            # 1. Đảm bảo dtype float32 và contiguous trong memory
            if audio_f32.dtype != np.float32:
                audio_f32 = audio_f32.astype(np.float32)
            if not audio_f32.flags["C_CONTIGUOUS"]:
                audio_f32 = np.ascontiguousarray(audio_f32)

            # 2. Clip [-1, 1] phòng decode lệch
            audio_f32 = np.clip(audio_f32, -1.0, 1.0)

            # 3. Tensor shape (1, N) — pyannote yêu cầu (channels, samples)
            waveform = torch.from_numpy(audio_f32).unsqueeze(0)

            # 4. Pipeline đã pin về CPU trong _ensure_pipeline
            waveform = waveform.to(torch.device("cpu"))
            device   = "cpu"

            duration = audio_f32.shape[0] / sample_rate
            print(f"[DIARIZE] diarize_array: {duration:.1f}s, "
                  f"sr={sample_rate}, device={device}, dtype={waveform.dtype}")

            # 5. Chạy pipeline
            diarization = self._pipeline({
                "waveform":    waveform,
                "sample_rate": sample_rate,
            })

            turns = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                turns.append((round(turn.start, 3), round(turn.end, 3), speaker))

            elapsed = time.time() - t0
            n_spk   = len(set(t[2] for t in turns))
            print(f"[DIARIZE] diarize_array done in {elapsed:.1f}s: "
                  f"{len(turns)} turns, {n_spk} speakers")
            for t in turns[:20]:
                print(f"  [{t[0]:7.2f}s–{t[1]:7.2f}s]  {t[2]}")
            if len(turns) > 20:
                print(f"  … ({len(turns) - 20} turns more)")

            return turns

        except Exception as e:
            print(f"[DIARIZE] diarize_array error: {e}")
            import traceback; traceback.print_exc()
            return []

    def assign_speakers(
        self,
        diarization_segments: list[dict],
        db_segments:          list[dict],
    ) -> dict[int, int]:
        """
        Map diarization turns → DB segment_index → speaker_index (1-based).
        Dùng overlap-first, fallback nearest neighbor.
        """
        if not diarization_segments:
            return {}

        speaker_map: dict[str, int] = {}
        next_idx = 1
        result:  dict[int, int] = {}

        for seg in db_segments:
            seg_start = seg["start_time"]
            seg_end   = seg["end_time"]
            seg_mid   = (seg_start + seg_end) / 2
            if seg_end - seg_start <= 0:
                continue

            best_speaker     = None
            best_overlap     = 0.0
            nearest_speaker  = None
            nearest_distance = float("inf")

            for turn in diarization_segments:
                overlap = min(turn["end"], seg_end) - max(turn["start"], seg_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = turn["speaker"]

                turn_mid = (turn["start"] + turn["end"]) / 2
                dist = abs(turn_mid - seg_mid)
                if dist < nearest_distance:
                    nearest_distance = dist
                    nearest_speaker  = turn["speaker"]

            chosen = best_speaker if best_overlap > 0 else nearest_speaker
            method = "overlap" if best_overlap > 0 else f"nearest({nearest_distance:.1f}s)"

            if chosen is None:
                continue

            if chosen not in speaker_map:
                speaker_map[chosen] = next_idx
                next_idx += 1

            result[seg["segment_index"]] = speaker_map[chosen]
            print(f"[DIARIZE] Seg #{seg['segment_index']:2d} "
                  f"[{seg_start:.1f}–{seg_end:.1f}s] → "
                  f"{chosen} ({method}) → Speaker {result[seg['segment_index']]}")

        return result

    # ══════════════════════════════════════════════════════
    # Helper — convert sang WAV 16kHz mono nếu cần
    # ══════════════════════════════════════════════════════

    def _ensure_wav_16k(self, path: str) -> str:
        """
        Nếu file đã là WAV 16kHz mono → trả về path gốc.
        Nếu không → convert bằng ffmpeg → trả về path tmp.
        """
        import wave

        try:
            with wave.open(path, "rb") as f:
                sr   = f.getframerate()
                n_ch = f.getnchannels()
            if sr == 16000 and n_ch == 1:
                return path
        except Exception:
            pass

        tmp = tempfile.mktemp(suffix="_16k.wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", path,
            "-ac", "1",
            "-ar", "16000",
            "-loglevel", "quiet",
            tmp,
        ]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"[DIARIZE] ffmpeg convert thất bại: {path}")

        print(f"[DIARIZE] Converted to 16kHz mono WAV: {tmp}")
        return tmp