import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

# Load .env + kiểm tra HF_TOKEN TRƯỚC KHI import bất cứ gì khác
from ui.first_run_dialog import ensure_hf_token

from utils.widget_inspector import install_inspector

def main():
    # os.environ["QT_IM_MODULE"] = "fcitx"
    app = QApplication(sys.argv)

    #debug
    # inspector = install_inspector(app)

    app.setStyle("Fusion")

    # Hiện dialog nếu chưa có HF_TOKEN
    if not ensure_hf_token():
        sys.exit(0)   # User bấm Cancel → thoát

    # Import MainWindow SAU KHI có token (vì import sẽ load models)
    from ui.main_window import MainWindow
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()