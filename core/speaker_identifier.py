import numpy as np
import torch
import torch.nn.functional as F
from pyannote.audio import Model, Inference


class SpeakerIdentifier:
    """
    Nhận diện speaker dùng pyannote/embedding.
    - Extract voice embedding từ mỗi chunk audio
    - Cosine similarity để so sánh với speaker đã biết
    - Speaker mới nếu similarity < threshold

    Logic hiển thị:
    - Cùng speaker liền nhau → nối tiếp text
    - Khác speaker → xuống hàng, hiện "Speaker N:"
    """

    SIMILARITY_THRESHOLD = 0.30

    def __init__(self, device: str = "cpu"):
        print("[SPEAKER] Loading pyannote/embedding model...")
        model = Model.from_pretrained(
            "pyannote/embedding",
            use_auth_token=True,
        )
        self._inference = Inference(
            model,
            window="whole",
            device=torch.device(device),
        )
        print("[SPEAKER] Model loaded.")

        # {speaker_id: embedding ndarray}
        self._speakers: dict[int, np.ndarray] = {}
        self._next_id = 1
        self._last_id = None  # speaker vừa nói

    def identify(self, pcm_bytes: bytes) -> int:
        """
        Nhận PCM s16le 16kHz bytes → trả về speaker_id (1, 2, 3, ...)
        """
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        waveform = torch.tensor(audio).unsqueeze(0)  # [1, T]
        embedding = self._inference({"waveform": waveform, "sample_rate": 16000})
        embedding = embedding / (np.linalg.norm(embedding) + 1e-8)

        best_id    = None
        best_score = -1.0

        for spk_id, spk_emb in self._speakers.items():
            score = float(np.dot(embedding, spk_emb))
            if score > best_score:
                best_score = score
                best_id    = spk_id

        if best_score >= self.SIMILARITY_THRESHOLD:
            # Cùng speaker → update embedding moving average
            alpha = 0.3
            updated = alpha * embedding + (1 - alpha) * self._speakers[best_id]
            self._speakers[best_id] = updated / (np.linalg.norm(updated) + 1e-8)
            print(f"[SPEAKER] Speaker {best_id} (score={best_score:.3f})")
            self._last_id = best_id
            return best_id
        else:
            # Speaker mới
            new_id = self._next_id
            self._next_id += 1
            self._speakers[new_id] = embedding
            print(f"[SPEAKER] New → Speaker {new_id} (score={best_score:.3f})")
            self._last_id = new_id
            return new_id

    def reset(self):
        self._speakers.clear()
        self._next_id = 1
        self._last_id = None