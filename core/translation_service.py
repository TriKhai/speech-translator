import time
from deep_translator import GoogleTranslator


class TranslationService:

    MAX_RETRIES = 3   # số lần thử tối đa
    BACKOFF_BASE = 1  # giây, tăng theo lũy thừa 2: 1s, 2s, 4s

    def __init__(self):
        print("[TRANSLATE] TranslationService ready.")

    def translate(self, text: str, src: str = "en", dest: str = "vi") -> str:
        if not text.strip():
            return text

        last_error = None

        # FIX #7: Retry với exponential backoff thay vì thất bại ngay lần đầu
        for attempt in range(self.MAX_RETRIES):
            try:
                result = GoogleTranslator(source=src, target=dest).translate(text)

                # Kiểm tra kết quả hợp lệ (GoogleTranslator đôi khi trả None)
                if result and result.strip():
                    print(f"[TRANSLATE] OK (lần {attempt + 1}): "
                          f"{text[:30]}... → {result[:30]}...")
                    return result
                else:
                    raise ValueError(f"Kết quả dịch rỗng (attempt {attempt + 1})")

            except Exception as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    wait = self.BACKOFF_BASE * (2 ** attempt)   # 1s, 2s, 4s
                    print(f"[TRANSLATE] Lỗi lần {attempt + 1}/{self.MAX_RETRIES}: "
                          f"{e} — thử lại sau {wait}s")
                    time.sleep(wait)
                else:
                    print(f"[TRANSLATE] Thất bại sau {self.MAX_RETRIES} lần: {e}")

        # Hết retry → fallback về text gốc, KHÔNG crash
        print(f"[TRANSLATE] Fallback về text gốc: {text[:40]}...")
        return text