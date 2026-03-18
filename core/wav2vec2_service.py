import numpy as np
import torch
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor


class Wav2Vec2Service:
    """
    Thay thế WhisperService bằng Wav2Vec2 CTC.

    Ưu điểm so với Whisper:
    - Kiến trúc CTC → decode theo thời gian thực, không cần chờ hết câu
    - Nhanh hơn ~2-3x trên CPU
    - Interface giữ nguyên: transcribe(pcm_bytes) -> str
      → không cần sửa TranscriptionWorker, VADProcessor, MainWindow

    Model mặc định: facebook/wav2vec2-large-960h-lv60-self
    - Train trên 960h LibriSpeech + self-training
    - WER ~1.9% clean / ~3.9% other (tốt nhất trong các model public)
    """

    def __init__(
        self,
        model_name: str = "facebook/wav2vec2-large-960h-lv60-self",
        device: str     = "cpu",
    ):
        print(f"[WAV2VEC2] Loading model: {model_name} on {device}...")
        self._device    = device
        self._processor = Wav2Vec2Processor.from_pretrained(model_name)
        self._model     = Wav2Vec2ForCTC.from_pretrained(model_name)
        self._model.eval()

        if device == "cuda":
            self._model = self._model.to("cuda")

        print("[WAV2VEC2] Model loaded.")

    def transcribe(self, pcm_bytes: bytes) -> str:
        """
        Nhận raw PCM s16le bytes (16kHz mono) → trả về text.
        Interface giống WhisperService.transcribe() để dễ thay thế.
        """
        print("[WAV2VEC2] Processing audio...")

        # Convert s16le bytes → float32 numpy array
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # Quá ngắn → bỏ qua
        if len(audio) < 1600:  # < 0.1s
            return ""

        # Tokenize
        inputs = self._processor(
            audio,
            sampling_rate=16000,
            return_tensors="pt",
            padding=True,
        )

        input_values    = inputs.input_values
        attention_mask  = inputs.get("attention_mask", None)

        if self._device == "cuda":
            input_values = input_values.to("cuda")
            if attention_mask is not None:
                attention_mask = attention_mask.to("cuda")

        # Inference
        with torch.no_grad():
            logits = self._model(
                input_values,
                attention_mask=attention_mask,
            ).logits

        # CTC decode
        predicted_ids  = torch.argmax(logits, dim=-1)
        transcription  = self._processor.batch_decode(predicted_ids)[0]

        # Wav2Vec2 output là UPPERCASE → convert về title case cho dễ đọc
        text = transcription.strip().lower().capitalize()

        print(f"[WAV2VEC2] Transcribed: {text}")
        return text