
import os
import json
import threading
from dataclasses import dataclass, field

import requests
from PyQt6.QtCore import QObject, pyqtSignal


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.1-8b-instant"

SYSTEM_PROMPT = """Bạn là trợ lý tóm tắt nội dung hội thoại.
Phân tích transcript và trả về JSON với cấu trúc sau (chỉ JSON thuần, không có text ngoài, không markdown):
{
  "summary": "Tóm tắt ngắn gọn 2-3 câu",
  "key_points": ["Điểm chính 1", "Điểm chính 2"],
  "action_items": ["Việc cần làm 1", "Việc cần làm 2"],
  "speakers": {
    "1": "Mô tả ngắn về Speaker 1 dựa trên nội dung",
    "2": "..."
  },
  "topics": ["Chủ đề 1", "Chủ đề 2"],
  "sentiment": "positive | neutral | negative"
}

Lưu ý quan trọng:
- Tóm tắt theo NỘI DUNG và CHỦ ĐỀ, không đề cập "Speaker 1", "Speaker 2" hay bất kỳ nhãn người nói nào
- Chỉ liệt kê các việc cụ thể được đề cập hoặc ngụ ý trong cuộc hội thoại
- Mỗi item bắt đầu bằng động từ hành động (Gửi, Kiểm tra, Liên hệ, v.v.)
- Nếu không có action item nào, trả về mảng rỗng []
"""


@dataclass
class SummaryResult:
    summary:      str
    key_points:   list[str]      = field(default_factory=list)
    action_items: list[str]      = field(default_factory=list)
    speakers:     dict[str, str] = field(default_factory=dict)
    topics:       list[str]      = field(default_factory=list)
    sentiment:    str            = "neutral"
    error:        str | None     = None


class SummaryService:

    MAX_CHARS = 12_000

    def summarize(
        self,
        segments: list[dict],
        lang:     str = "vi",
        style:    str = "bullet",
    ) -> SummaryResult:
        """Tóm tắt đồng bộ — gọi trong thread riêng, không block UI."""

        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            return SummaryResult(
                summary="Chưa có GROQ_API_KEY trong file .env.",
                error="no_api_key",
            )

        transcript_text = self._build_transcript(segments, lang)
        if not transcript_text.strip():
            return SummaryResult(
                summary="Không có nội dung để tóm tắt.",
                error="empty_transcript",
            )

        user_prompt = self._build_prompt(transcript_text, lang, style)

        try:
            response = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_prompt},
                    ],
                    "max_tokens":      1000,
                    "temperature":     0.3,
                    "response_format": {"type": "json_object"},
                },
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()

            raw = data["choices"][0]["message"]["content"]
            return self._parse_result(raw.strip())

        except requests.Timeout:
            return SummaryResult(
                summary="Timeout khi gọi Groq API. Thử lại sau.",
                error="timeout",
            )
        except requests.HTTPError as e:
            status = e.response.status_code if e.response else 0
            if status == 401:
                return SummaryResult(
                    summary="GROQ_API_KEY không hợp lệ. Kiểm tra lại .env.",
                    error="invalid_api_key",
                )
            if status == 429:
                raise
            print(f"[SUMMARY] HTTP error {status}: {e}")
            return SummaryResult(
                summary=f"Lỗi HTTP {status} từ Groq API.",
                error=f"http_{status}",
            )
        except KeyError:
            print(f"[SUMMARY] Unexpected response: {data}")
            return SummaryResult(
                summary="Groq trả về định dạng không mong đợi.",
                error="parse_error",
            )
        except Exception as e:
            print(f"[SUMMARY] Error: {e}")
            return SummaryResult(
                summary=f"Lỗi: {e}",
                error=str(e),
            )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _build_transcript(self, segments: list[dict], lang: str) -> str:
        lines = []
        for seg in segments:
            spk = seg.get("speaker_index", 1)
            if lang == "en":
                text = seg.get("text_en", "")
            elif lang == "both":
                en = seg.get("text_en", "")
                vi = seg.get("text_vi", "")
                text = f"{en} / {vi}"
            else:
                text = seg.get("text_vi", "") or seg.get("text_en", "")

            if text.strip():
                lines.append(f"Speaker {spk}: {text}")

        full = "\n".join(lines)
        if len(full) > self.MAX_CHARS:
            full = full[:self.MAX_CHARS] + "\n[... transcript bị cắt bớt ...]"
        return full

    def _build_prompt(self, transcript: str, lang: str, style: str) -> str:
        lang_hint = {
            "vi":   "Trả lời bằng tiếng Việt.",
            "en":   "Reply in English.",
            "both": "Trả lời song ngữ Anh-Việt.",
        }.get(lang, "Trả lời bằng tiếng Việt.")

        style_hint = (
            "Dùng bullet points ngắn gọn." if style == "bullet"
            else "Viết dạng đoạn văn mạch lạc."
        )

        return f"{lang_hint} {style_hint}\n\nTranscript:\n{transcript}"

    def _parse_result(self, raw: str) -> SummaryResult:
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                if part.startswith("json"):
                    raw = part[4:].strip()
                    break
                elif "{" in part:
                    raw = part.strip()
                    break

        try:
            data = json.loads(raw)
            return SummaryResult(
                summary      = data.get("summary", ""),
                key_points   = data.get("key_points", []),
                action_items = data.get("action_items", []),
                speakers     = {str(k): v for k, v in data.get("speakers", {}).items()},
                topics       = data.get("topics", []),
                sentiment    = data.get("sentiment", "neutral"),
            )
        except json.JSONDecodeError as e:
            print(f"[SUMMARY] JSON parse error: {e}\nRaw: {raw[:200]}")
            return SummaryResult(
                summary=raw[:500] if raw else "Không thể phân tích kết quả."
            )


# ── Qt Worker ────────────────────────────────────────────────────────────────

class SummaryWorker(QObject):
    """
    Chạy summarize() trong thread riêng, emit signal khi xong.

    Usage:
        worker = SummaryWorker(segments, lang="vi")
        worker.done.connect(lambda r: show_summary(r))
        worker.error.connect(lambda msg: show_error(msg))
        worker.start()
    """
    done    = pyqtSignal(object)   # SummaryResult
    error   = pyqtSignal(str)
    started = pyqtSignal()

    def __init__(
        self,
        segments: list[dict],
        lang:     str = "vi",
        style:    str = "bullet",
        parent=None,
    ):
        super().__init__(parent)
        self._segments = segments
        self._lang     = lang
        self._style    = style
        self._svc      = SummaryService()

    def start(self):
        self.started.emit()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import time
        for attempt in range(3):
            try:
                result = self._svc.summarize(self._segments, self._lang, self._style)
                self.done.emit(result)
                return
            except Exception as e:
                msg = str(e)
                if "429" in msg and attempt < 2:
                    wait = (attempt + 1) * 5
                    print(f"[SUMMARY] Rate limited, retry in {wait}s… (attempt {attempt+1}/3)")
                    time.sleep(wait)
                else:
                    self.error.emit(msg)
                    return


# ── Mindmap ──────────────────────────────────────────────────────────────────

MINDMAP_SYSTEM_PROMPT = """Bạn là trợ lý tạo mindmap từ nội dung cuộc họp/hội thoại.
Phân tích transcript và trả về JSON cấu trúc cây mindmap (chỉ JSON thuần, không markdown):
{
  "central": "Chủ đề trung tâm ngắn gọn (tối đa 5 từ)",
  "branches": [
    {
      "label": "Nhánh chính 1",
      "color": "#60A5FA",
      "children": [
        { "label": "Ý con 1.1", "children": [] }
      ]
    }
  ]
}

Quy tắc điều chỉnh theo độ dài nội dung:
- Nội dung ngắn (dưới 500 từ): 2-3 nhánh chính, mỗi nhánh 1-2 con
- Nội dung trung bình (500-1500 từ): 3-4 nhánh chính, mỗi nhánh 2-3 con
- Nội dung dài (trên 1500 từ): tối đa 5 nhánh chính, mỗi nhánh tối đa 4 con, mỗi con tối đa 2 cháu
- Luôn ưu tiên các chủ đề QUAN TRỌNG NHẤT, bỏ qua chi tiết nhỏ
- Label ngắn gọn, tối đa 8 từ mỗi node
- Dùng màu đa dạng cho branches: "#60A5FA", "#34D399", "#F59E0B", "#A78BFA", "#F87171"
- Trả về tiếng Việt"""


@dataclass
class MindmapResult:
    central:  str
    branches: list[dict]
    error:    str | None = None


class MindmapService:

    MAX_CHARS = 10_000

    def generate(self, segments: list[dict], lang: str = "vi") -> MindmapResult:
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            return MindmapResult(central="", branches=[], error="no_api_key")

        # Build transcript text
        lines = []
        for seg in segments:
            spk  = seg.get("speaker_index", 1)
            text = seg.get("text_vi", "") or seg.get("text_en", "")
            if text.strip():
                lines.append(f"Speaker {spk}: {text}")
        transcript = "\n".join(lines)
        if not transcript.strip():
            return MindmapResult(central="", branches=[], error="empty_transcript")
        if len(transcript) > self.MAX_CHARS:
            transcript = transcript[:self.MAX_CHARS] + "\n[...]"

        prompt = f"Tạo mindmap từ transcript sau:\n\n{transcript}"

        try:
            response = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": MINDMAP_SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                    "max_tokens":      1500,
                    "temperature":     0.4,
                    "response_format": {"type": "json_object"},
                },
                timeout=25,
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            data = json.loads(raw)
            return MindmapResult(
                central  = data.get("central", "Mindmap"),
                branches = data.get("branches", []),
            )
        except requests.Timeout:
            return MindmapResult(central="", branches=[], error="timeout")
        except Exception as e:
            print(f"[MINDMAP] Error: {e}")
            return MindmapResult(central="", branches=[], error=str(e))


class MindmapWorker(QObject):
    done  = pyqtSignal(object)   # MindmapResult
    error = pyqtSignal(str)

    def __init__(self, segments: list[dict], parent=None):
        super().__init__(parent)
        self._segments = segments
        self._svc      = MindmapService()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            result = self._svc.generate(self._segments)
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))