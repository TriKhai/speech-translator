"""
speaker_diarizer.py — Fix DiarizeOutput API pyannote 4.x

pyannote 4.x đổi output:
    - Cũ (3.x): Annotation object → .itertracks(yield_label=True)
    - Mới (4.x): DiarizeOutput object → iterate trực tiếp hoặc dùng .to_annotation()

Fix: thử nhiều cách parse output, fallback graceful.
"""

import os
import wave
import numpy as np


class SpeakerDiarizer:

    def __init__(self):
        self._hf_token = os.environ.get("HF_TOKEN", "").strip()
        if not self._hf_token:
            raise RuntimeError(
                "\n[DIARIZE] Thiếu HF_TOKEN!\n"
                "  Thêm vào .env: HF_TOKEN=hf_xxxxxxxxxxxx\n"
            )

        print("[DIARIZE] Loading pyannote/speaker-diarization-3.1...")
        try:
            from pyannote.audio import Pipeline
            import pyannote.audio as pa

            major  = int(pa.__version__.split(".")[0])
            kwargs = {"token": self._hf_token} if major >= 3 \
                     else {"use_auth_token": self._hf_token}

            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                **kwargs,
            )
            print(f"[DIARIZE] Pipeline loaded OK (pyannote {pa.__version__})")

        except ImportError:
            raise RuntimeError("[DIARIZE] pip install pyannote.audio")
        except Exception as e:
            raise RuntimeError(f"[DIARIZE] Không load pipeline: {e}")

    def _load_wav_as_tensor(self, wav_path: str):
        """Đọc WAV thủ công → torch tensor, bypass torchcodec."""
        import torch

        with wave.open(wav_path, 'rb') as f:
            n_channels = f.getnchannels()
            sampwidth  = f.getsampwidth()
            framerate  = f.getframerate()
            frames     = f.readframes(f.getnframes())

        if sampwidth == 2:
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        elif sampwidth == 4:
            audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
        else:
            audio = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0

        if n_channels > 1:
            audio = audio[::n_channels]

        return torch.tensor(audio).unsqueeze(0), framerate

    def _parse_diarization(self, diarization) -> list[dict]:
        """
        Parse diarization output — handle cả pyannote 3.x và 4.x API.

        pyannote 3.x: Annotation → .itertracks(yield_label=True)
        pyannote 4.x: DiarizeOutput → .to_annotation() hoặc iterate trực tiếp
        """
        segments = []

        # Cách 1: API cũ 3.x — Annotation.itertracks()
        if hasattr(diarization, 'itertracks'):
            print("[DIARIZE] Using itertracks() API (pyannote 3.x)")
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segments.append({
                    "start":   round(turn.start, 3),
                    "end":     round(turn.end,   3),
                    "speaker": speaker,
                })
            return segments

        # Cách 2: pyannote 4.x — DiarizeOutput.speaker_diarization
        if hasattr(diarization, 'speaker_diarization'):
            print("[DIARIZE] Using speaker_diarization attr (pyannote 4.x)")
            annotation = diarization.speaker_diarization
            for turn, _, speaker in annotation.itertracks(yield_label=True):
                segments.append({
                    "start":   round(turn.start, 3),
                    "end":     round(turn.end,   3),
                    "speaker": speaker,
                })
            return segments

        # Cách 3: pyannote 4.x — iterate DiarizeOutput trực tiếp
        # DiarizeOutput thường là list of (Segment, track, label) hoặc namedtuple
        if hasattr(diarization, '__iter__'):
            print("[DIARIZE] Using direct iteration API (pyannote 4.x)")
            try:
                for item in diarization:
                    # item có thể là (segment, label) hoặc object với .start/.end/.speaker
                    if hasattr(item, 'start') and hasattr(item, 'end'):
                        speaker = getattr(item, 'speaker', getattr(item, 'label', 'SPEAKER_00'))
                        segments.append({
                            "start":   round(item.start, 3),
                            "end":     round(item.end,   3),
                            "speaker": str(speaker),
                        })
                    elif isinstance(item, (tuple, list)) and len(item) >= 2:
                        seg_obj  = item[0]
                        speaker  = item[-1]   # label thường ở cuối
                        if hasattr(seg_obj, 'start') and hasattr(seg_obj, 'end'):
                            segments.append({
                                "start":   round(seg_obj.start, 3),
                                "end":     round(seg_obj.end,   3),
                                "speaker": str(speaker),
                            })
                if segments:
                    return segments
            except Exception as e:
                print(f"[DIARIZE] Direct iteration failed: {e}")

        # Cách 4: Debug — in ra type và attrs để biết cần xử lý gì
        print(f"[DIARIZE] Unknown output type: {type(diarization)}")
        print(f"[DIARIZE] Attrs: {[a for a in dir(diarization) if not a.startswith('_')]}")
        return []

    def diarize(self, wav_path: str) -> list[dict]:
        print(f"[DIARIZE] Processing: {wav_path}")
        try:
            waveform, sample_rate = self._load_wav_as_tensor(wav_path)
            print(f"[DIARIZE] WAV loaded: {waveform.shape}, "
                  f"{sample_rate}Hz, {waveform.shape[1]/sample_rate:.1f}s")

            audio_input  = {"waveform": waveform, "sample_rate": sample_rate}
            diarization  = self._pipeline(audio_input)
            segments     = self._parse_diarization(diarization)

            if not segments:
                return []

            segments.sort(key=lambda x: x["start"])
            n_speakers = len(set(s["speaker"] for s in segments))
            print(f"[DIARIZE] Done: {len(segments)} turns, {n_speakers} speakers")
            for s in segments:
                print(f"  [{s['start']:6.1f}–{s['end']:6.1f}s]  {s['speaker']}")
            return segments

        except Exception as e:
            print(f"[DIARIZE] Error: {e}")
            import traceback; traceback.print_exc()
            return []

    def assign_speakers(
        self,
        diarization_segments: list[dict],
        db_segments:          list[dict],
    ) -> dict[int, int]:
        """
        Map diarization turns → DB segment_index → speaker_index (1-based).

        Ưu tiên overlap. Nếu không overlap → dùng speaker của turn gần nhất
        (nearest neighbor) để không bỏ sót segment nào.
        """
        if not diarization_segments:
            return {}

        speaker_map: dict[str, int] = {}
        next_idx = 1
        result:   dict[int, int]   = {}

        for seg in db_segments:
            seg_start  = seg["start_time"]
            seg_end    = seg["end_time"]
            seg_mid    = (seg_start + seg_end) / 2   # midpoint để tìm nearest
            if seg_end - seg_start <= 0:
                continue

            best_speaker = None
            best_overlap = 0.0
            nearest_speaker  = None
            nearest_distance = float("inf")

            for turn in diarization_segments:
                # Overlap
                overlap = min(turn["end"], seg_end) - max(turn["start"], seg_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = turn["speaker"]

                # Nearest neighbor (dùng khi không có overlap)
                turn_mid = (turn["start"] + turn["end"]) / 2
                dist = abs(turn_mid - seg_mid)
                if dist < nearest_distance:
                    nearest_distance = dist
                    nearest_speaker  = turn["speaker"]

            # Chọn speaker: overlap > 0 thì dùng overlap, không thì dùng nearest
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