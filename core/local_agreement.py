"""
local_agreement.py  —  KHÔNG thay đổi logic nội bộ

Bug #2 được fix ở transcription_worker.py (mỗi speaker có 1 instance riêng).
File này giữ nguyên để không phá vỡ import.

LocalAgreement Policy — chống text nháy.
= LocalAgreement Policy trong WhisperLiveKit

Ý tưởng:
- Chỉ emit phần text ổn định xuất hiện ở cả chunk hiện tại lẫn chunk trước
- Tương tự "longest common prefix" giữa 2 lần transcribe liên tiếp

Ví dụ:
    chunk t-1: "hello how are"
    chunk t:   "hello how are you"
    → emit:    "hello how are"  (phần chung ổn định)
    → giữ lại: "you"            (chờ confirm ở chunk tiếp theo)
"""


class LocalAgreement:

    def __init__(self):
        self._prev_words: list[str] = []
        self._emitted_count: int    = 0

    def process(self, text: str) -> str | None:
        """
        Nhận text mới → trả về phần ổn định cần emit.
        Trả về None nếu chưa có gì ổn định.
        """
        if not text.strip():
            return None

        curr_words = text.strip().split()

        # Tìm common prefix giữa prev và curr
        common_len = 0
        for i, (p, c) in enumerate(zip(self._prev_words, curr_words)):
            if p.lower() == c.lower():
                common_len = i + 1
            else:
                break

        # Phần ổn định = common prefix chưa được emit
        stable_words = curr_words[:common_len]
        new_stable   = stable_words[self._emitted_count:]

        self._prev_words    = curr_words
        self._emitted_count = common_len

        if new_stable:
            return " ".join(new_stable)
        return None

    def flush(self) -> str | None:
        """
        Khi kết thúc câu (VAD phát hiện silence dài)
        → emit toàn bộ phần còn lại chưa emit.
        """
        remaining = self._prev_words[self._emitted_count:]
        self.reset()
        if remaining:
            return " ".join(remaining)
        return None

    def reset(self):
        self._prev_words    = []
        self._emitted_count = 0