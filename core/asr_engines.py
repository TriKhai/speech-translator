"""
core/asr_engines.py
Ba wrapper engine implement ASRBase.transcribe_full():

  WhisperASR   — wrap WhisperService (faster-whisper bên trong, không đổi gì)
  Wav2Vec2ASR  — HuggingFace transformers  (pip install transformers torch)
  VoskASR      — vosk offline CPU-only     (pip install vosk + model folder)
"""
from __future__ import annotations
from .asr_base import ASRBase, ASRResult


# ─────────────────────────────────────────────────────────────────────────────
class WhisperASR(ASRBase):
    """
    Wrap WhisperService — đã dùng faster-whisper bên trong,
    có word timestamps, VAD filter, int8 compute. Không đổi gì.
    """

    def __init__(self, model_path: str = "tiny.en", device: str = "cpu",
                 initial_prompt: str = ""):
        from .whisper_service import WhisperService
        self._svc = WhisperService(
            model_path=model_path,
            device=device,
            initial_prompt=initial_prompt,
        )

    def transcribe_full(self, audio: bytes) -> ASRResult:
        r = self._svc.transcribe_full(audio)
        # TranscribeResult và ASRResult cùng schema — forward thẳng
        return ASRResult(
            text       = r.text,
            confidence = r.confidence,
            words      = r.words,
        )


# ─────────────────────────────────────────────────────────────────────────────
class Wav2Vec2ASR(ASRBase):
    """
    Facebook Wav2Vec2 — ASR thay thế Whisper.
    Không có word timestamps nên words=[].
    Confidence ước tính từ mean max-softmax probability.

    Yêu cầu: pip install transformers torch
    """

    def __init__(self, model_id: str = "facebook/wav2vec2-base-960h",
                 device: str = "cpu"):
        from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC
        self._device    = device
        self._processor = Wav2Vec2Processor.from_pretrained(model_id)
        self._model     = (
            Wav2Vec2ForCTC.from_pretrained(model_id)
            .to(device)
            .eval()
        )

    def transcribe_full(self, audio: bytes) -> ASRResult:
        import torch
        import numpy as np

        arr = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        inputs = self._processor(
            arr, sampling_rate=16_000, return_tensors="pt", padding=True
        )
        with torch.no_grad():
            logits = self._model(inputs.input_values.to(self._device)).logits

        probs      = torch.softmax(logits, dim=-1)
        confidence = float(probs.max(dim=-1).values.mean().item())

        ids  = torch.argmax(logits, dim=-1)
        text = self._processor.batch_decode(ids)[0]

        return ASRResult(text=text, confidence=confidence, words=[])


# ─────────────────────────────────────────────────────────────────────────────
class VoskASR(ASRBase):
    """
    Vosk offline — nhẹ & nhanh, CPU only.
    Có word timestamps đầy đủ từ KaldiRecognizer.

    Yêu cầu: pip install vosk
    model_path: đường dẫn đến thư mục model đã giải nén.
    """

    def __init__(self, model_path: str):
        import json
        from vosk import Model, KaldiRecognizer
        self._json = json
        self._rec  = KaldiRecognizer(Model(model_path), 16_000)
        self._rec.SetWords(True)

    def transcribe_full(self, audio: bytes) -> ASRResult:
        self._rec.AcceptWaveform(audio)
        raw        = self._json.loads(self._rec.FinalResult())
        text       = raw.get("text", "")
        vosk_words = raw.get("result", [])

        words = [
            {
                "word":        w.get("word", ""),
                "start":       w.get("start", 0.0),
                "end":         w.get("end",   0.0),
                "probability": w.get("conf",  1.0),
            }
            for w in vosk_words
        ]
        confidence = (
            sum(w["probability"] for w in words) / len(words)
            if words else 1.0
        )
        return ASRResult(text=text, confidence=confidence, words=words)