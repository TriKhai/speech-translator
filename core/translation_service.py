from deep_translator import GoogleTranslator


class TranslationService:

    def __init__(self):
        print("[TRANSLATE] TranslationService ready.")

    def translate(self, text: str, src: str = "en", dest: str = "vi") -> str:
        if not text.strip():
            return text
        try:
            result = GoogleTranslator(source=src, target=dest).translate(text)
            print(f"[TRANSLATE] {text} → {result}")
            return result
        except Exception as e:
            print(f"[TRANSLATE] Error: {e} — trả về text gốc")
            return text