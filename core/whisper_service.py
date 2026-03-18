import numpy as np
from dataclasses import dataclass
from faster_whisper import WhisperModel


@dataclass
class TranscribeResult:
    text:       str
    confidence: float
    words:      list[dict]   # [{"word": str, "start": float, "end": float, "probability": float}]


class WhisperService:

    def __init__(self, model_path: str = "./models/small.en", device: str = "cpu"):
        print(f"[WHISPER] Loading model: {model_path} on {device}...")
        self._model = WhisperModel(
            model_path,
            device=device,
            compute_type="int8",
            cpu_threads=8,
        )
        print("[WHISPER] Model loaded.")

    def transcribe(self, pcm_bytes: bytes) -> str:
        """Interface cũ — trả về text string. Dùng cho pipeline hiện tại."""
        result = self.transcribe_full(pcm_bytes)
        return result.text

    def transcribe_full(self, pcm_bytes: bytes) -> TranscribeResult:
        """
        Trả về TranscribeResult gồm text + confidence + word timestamps.
        Dùng khi cần lưu DB với word-level timestamps.
        """
        print("[WHISPER] Processing audio...")

        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        segments, _ = self._model.transcribe(
            audio,
            language="en",
            beam_size=5,
            word_timestamps=True,       # bật word-level timestamps
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        all_words  = []
        all_text   = []
        avg_logprob = 0.0
        seg_count   = 0

        for seg in segments:
            all_text.append(seg.text.strip())
            avg_logprob += seg.avg_logprob
            seg_count   += 1

            if seg.words:
                for w in seg.words:
                    all_words.append({
                        "word":        w.word.strip(),
                        "start":       w.start,
                        "end":         w.end,
                        "probability": w.probability,
                    })

        text       = " ".join(all_text)
        confidence = float(np.exp(avg_logprob / seg_count)) if seg_count > 0 else 0.0

        print(f"[WHISPER] Transcribed: {text} ({len(all_words)} words)")
        return TranscribeResult(text=text, confidence=confidence, words=all_words)