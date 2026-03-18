from PyQt6.QtCore import QObject, pyqtSignal

class AppSignals(QObject):
    english_received    = pyqtSignal(str, int)
    vietnamese_received = pyqtSignal(str, int)
    status_changed      = pyqtSignal(str)
    model_loaded        = pyqtSignal()
    audio_level         = pyqtSignal(float)
    diarization_done    = pyqtSignal(str, bool)