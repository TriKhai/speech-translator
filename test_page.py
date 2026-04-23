import qtawesome as qta
from PyQt6.QtWidgets import QApplication, QLabel
import sys

app = QApplication(sys.argv)
icon = qta.icon("fa5s.microphone", color="#3b6ef5")
print("OK:", icon)