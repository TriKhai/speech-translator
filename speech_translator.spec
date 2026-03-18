# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
import sounddevice

SD_PATH = Path(sounddevice.__file__).parent

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[
        (str(SD_PATH / '_sounddevice_data' / 'portaudio-binaries' / '*.dll'), '.'),
    ],
    datas=[
        ('models', 'models'),
    ],
    hiddenimports=[
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'sounddevice',
        'soundfile',
        'torch',
        'numpy',
        'faster_whisper',
        'pyannote.audio',
        'pyannote.core',
        'silero_vad',
        'deep_translator',
        'sqlite3',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'IPython',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SpeechTranslator',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='SpeechTranslator',
)
