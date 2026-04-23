"""
test_system_audio.py
Test capture system audio (loa) trên Linux dùng PipeWire monitor.

Chạy:
    python test_system_audio.py

Trong khi test: mở YouTube hoặc bất kỳ app nào có âm thanh.
Kết quả: in ra transcript những gì nghe được từ loa.
"""

import subprocess
import numpy as np
import threading
import sys

# ── Config ────────────────────────────────────────────────
# Thử lần lượt từ dưới lên nếu cái này không có tiếng
MONITOR_DEVICE = "alsa_output.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__hw_sofhdadsp__sink.monitor"

SAMPLE_RATE  = 16000
CHUNK_FRAMES = 4096


def capture_system_audio(on_data: callable):
    """Capture system audio dùng ffmpeg + PulseAudio monitor."""
    cmd = [
        "ffmpeg",
        "-f", "pulse",                  # PulseAudio/PipeWire input
        "-i", MONITOR_DEVICE,           # monitor device
        "-ac", "1",                     # mono
        "-ar", str(SAMPLE_RATE),        # 16kHz
        "-f", "s16le",                  # PCM s16le
        "-loglevel", "quiet",
        "pipe:1",
    ]

    print(f"[TEST] Starting capture from: {MONITOR_DEVICE[:50]}...")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    chunk_size = CHUNK_FRAMES * 2  # 2 bytes per sample (s16le)
    print("[TEST] Capturing... (phát âm thanh từ loa để test)")

    try:
        while True:
            data = process.stdout.read(chunk_size)
            if not data:
                break
            on_data(data)
    except KeyboardInterrupt:
        pass
    finally:
        process.kill()
        print("[TEST] Stopped.")


def check_audio_level(data: bytes):
    """In ra level âm thanh để kiểm tra có nhận được tiếng không."""
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    rms   = float(np.sqrt(np.mean(audio ** 2)))

    # Visualize bằng bar đơn giản
    bar_len = int(rms * 500)
    bar     = "█" * min(bar_len, 40)
    level   = f"{rms:.4f}"

    if rms > 0.001:
        print(f"\r[LEVEL] {level:8s} |{bar:<40}|", end="", flush=True)
    else:
        print(f"\r[LEVEL] {level:8s} | (silence)                        |", end="", flush=True)


# ── Main ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("TEST SYSTEM AUDIO CAPTURE")
    print("=" * 50)
    print("Mở YouTube hoặc bất kỳ app nào phát âm thanh.")
    print("Nhấn Ctrl+C để dừng.")
    print()

    try:
        capture_system_audio(on_data=check_audio_level)
    except KeyboardInterrupt:
        print("\n[TEST] Done!")