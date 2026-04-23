"""
core/asr_base.py
Interface chung cho tất cả ASR engine.

TranscriptionWorker gọi: engine.transcribe_full(audio) -> ASRResult
ASRResult khớp với TranscribeResult của WhisperService:
  .text        str
  .confidence  float
  .words       list[{"word", "start", "end", "probability"}]
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ASRResult:
    text:       str
    confidence: float      = 1.0
    words:      list[dict] = field(default_factory=list)


class ASRBase(ABC):

    @abstractmethod
    def transcribe_full(self, audio: bytes) -> ASRResult:
        """
        Nhận raw PCM-16 mono 16 kHz bytes.
        Trả về ASRResult. Blocking — gọi trong background thread.
        """
        ...

    def unload(self):
        """Giải phóng model khỏi RAM/VRAM."""
        import gc
        for attr in ("_model", "_svc", "_processor", "_rec"):
            if hasattr(self, attr):
                try:
                    delattr(self, attr)
                except Exception:
                    pass
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    @property
    def name(self) -> str:
        return self.__class__.__name__