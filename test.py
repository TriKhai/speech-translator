"""
test_system_audio.py — Test capture system audio (loopback) qua ffmpeg

Cùng tech stack với AudioCapture của bạn:
  Linux   → ffmpeg + PulseAudio monitor source (nghe system audio)
  Windows → ffmpeg + dshow virtual cable HOẶC sounddevice WASAPI loopback

Chạy:
    python test_system_audio.py

Không cần cài thêm gì nếu đã có ffmpeg + numpy.
"""

import sys
import subprocess
import threading
import time
import os
import shutil
import numpy as np

PLATFORM = sys.platform


# ─────────────────────────────────────────────────────────────
# Utils
# ─────────────────────────────────────────────────────────────
def banner(msg: str):
    print("\n" + "═" * 60)
    print(f"  {msg}")
    print("═" * 60)


def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# ─────────────────────────────────────────────────────────────
# LINUX — tìm PulseAudio monitor source
# ─────────────────────────────────────────────────────────────
def find_pulse_monitor() -> str | None:
    """
    Tìm monitor source của sink đang RUNNING.
    Nếu không có RUNNING sink thì lấy cái đầu tiên có .monitor.
    """
    try:
        # Bước 1: tìm sink đang RUNNING
        sinks_out = subprocess.check_output(
            ["pactl", "list", "sinks", "short"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        running_sink_name = None
        for line in sinks_out.splitlines():
            parts = line.split()
            # Format: index  name  driver  format  state
            if len(parts) >= 5 and parts[-1] == "RUNNING":
                running_sink_name = parts[1]
                break

        # Bước 2: tìm monitor tương ứng
        sources_out = subprocess.check_output(
            ["pactl", "list", "sources", "short"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        first_monitor = None
        for line in sources_out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and ".monitor" in parts[1]:
                if first_monitor is None:
                    first_monitor = parts[1]
                # Monitor của sink RUNNING = tên sink + ".monitor"
                if running_sink_name and parts[1] == f"{running_sink_name}.monitor":
                    return parts[1]

        # Fallback: monitor đầu tiên tìm được
        return first_monitor

    except FileNotFoundError:
        pass

    return "default.monitor"


def list_pulse_sources():
    """In tất cả PulseAudio source, highlight cái đang RUNNING."""
    banner("PulseAudio Sources")
    try:
        sinks_out = subprocess.check_output(
            ["pactl", "list", "sinks", "short"],
            stderr=subprocess.DEVNULL, text=True,
        )
        running_sinks = set()
        for line in sinks_out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[-1] == "RUNNING":
                running_sinks.add(parts[1])

        out = subprocess.check_output(
            ["pactl", "list", "sources", "short"],
            stderr=subprocess.DEVNULL, text=True,
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                is_monitor = ".monitor" in parts[1]
                sink_name  = parts[1].replace(".monitor", "")
                is_running = sink_name in running_sinks
                if is_monitor and is_running:
                    marker = "★ [RUNNING] "
                elif is_monitor:
                    marker = "  [suspend] "
                else:
                    marker = "  [mic]     "
                print(f"  {marker} [{parts[0]:>2}] {parts[1]}")
        print("\n  ★ [RUNNING] = source cần dùng để capture system audio!")
    except FileNotFoundError:
        print("  ✗ pactl không tìm thấy — có thể đang dùng ALSA thuần")


# ─────────────────────────────────────────────────────────────
# WINDOWS — tìm loopback device
# ─────────────────────────────────────────────────────────────
def find_windows_loopback() -> dict | None:
    """
    Trên Windows, dùng sounddevice để tìm WASAPI loopback.
    Nếu không có sounddevice thì hướng dẫn dùng VB-Cable.
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            n = d["name"].lower()
            if any(k in n for k in ["stereo mix", "loopback", "what u hear",
                                     "cable output", "blackhole"]):
                if d["max_input_channels"] > 0:
                    return {"index": i, "name": d["name"], "lib": "sounddevice"}
        try:
            import pyaudiowpatch as paw
            p = paw.PyAudio()
            wasapi = p.get_host_api_info_by_type(paw.paWASAPI)
            spk_idx = wasapi["defaultOutputDevice"]
            spk = p.get_device_info_by_index(spk_idx)
            for i in range(p.get_device_count()):
                d = p.get_device_info_by_index(i)
                if d.get("isLoopbackDevice") and d["name"] == spk["name"]:
                    p.terminate()
                    return {"index": i, "name": d["name"], "lib": "pyaudiowpatch"}
            p.terminate()
        except ImportError:
            pass
    except ImportError:
        pass
    return None


# ─────────────────────────────────────────────────────────────
# Core capture test — dùng ffmpeg subprocess (giống AudioCapture)
# ─────────────────────────────────────────────────────────────
def capture_ffmpeg(source: str, fmt: str, duration: float = 5.0,
                   extra_args: list = None):
    """
    Capture system audio qua ffmpeg, in VU meter real-time.

    source     : tên device (pulse source name, dshow device name...)
    fmt        : ffmpeg -f format ("pulse", "dshow", "alsa", ...)
    extra_args : list args thêm vào trước -i (ví dụ ["-audio_buffer_size", "50"])
    """
    SAMPLE_RATE  = 16000
    CHANNELS     = 1
    CHUNK_FRAMES = 4096
    CHUNK_BYTES  = CHUNK_FRAMES * 2  # s16le = 2 bytes/sample

    cmd = ["ffmpeg"]
    if extra_args:
        cmd += extra_args
    cmd += [
        "-f", fmt,
        "-i", source,
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "-loglevel", "quiet",
        "-",
    ]

    print(f"\n  CMD: {' '.join(cmd)}\n")
    print(f"  Capturing {duration}s — hãy phát âm thanh (nhạc, Meet, YouTube...):\n")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("  ✗ ffmpeg không tìm thấy! Cài: sudo apt install ffmpeg")
        return False

    levels = []
    start  = time.time()

    while time.time() - start < duration:
        data = proc.stdout.read(CHUNK_BYTES)
        if not data:
            break
        arr   = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        level = float(np.abs(arr).mean())
        levels.append(level)
        bars  = int(level * 400)
        bar   = "█" * min(bars, 44)
        print(f"\r  Level: [{bar:<44}] {level:.5f}", end="", flush=True)

    proc.kill()
    proc.wait()
    print("\n")

    if not levels:
        stderr = proc.stderr.read().decode(errors="ignore")
        print(f"  ✗ Không capture được data.\n  ffmpeg stderr:\n{stderr[:400]}")
        return False

    avg  = np.mean(levels)
    peak = np.max(levels)
    print(f"  Chunks captured : {len(levels)}")
    print(f"  Avg level       : {avg:.5f}")
    print(f"  Peak level      : {peak:.5f}")

    if avg < 0.0002:
        print("\n  ⚠ Level quá thấp — kiểm tra:")
        print("     · Có âm thanh đang phát không?")
        print("     · Volume system có bị mute không?")
        print("     · Source name có đúng không?")
        return False
    else:
        print("\n  ✓ THÀNH CÔNG! System audio capture hoạt động.")
        print("  ✓ Sẵn sàng tích hợp vào AudioCapture của app.")
        return True


# ─────────────────────────────────────────────────────────────
# LINUX flow
# ─────────────────────────────────────────────────────────────
def run_linux():
    banner("Linux — PulseAudio / PipeWire Monitor")

    if not check_ffmpeg():
        print("  ✗ ffmpeg chưa cài: sudo apt install ffmpeg")
        return

    list_pulse_sources()

    monitor = find_pulse_monitor()
    print(f"\n  Tự động chọn: {monitor}")

    choice = input(
        f"  Nhấn Enter để dùng [{monitor}], hoặc nhập source name khác: "
    ).strip()
    source = choice if choice else monitor

    banner(f"Testing: {source}")
    success = capture_ffmpeg(source, fmt="pulse", duration=5.0)

    if not success:
        print("\n  Thử thêm:")
        print("  1. Chạy: pactl list sources short")
        print("  2. Chọn source có đuôi .monitor")
        print("  3. Chạy lại script và nhập tên source đó")


# ─────────────────────────────────────────────────────────────
# WINDOWS flow
# ─────────────────────────────────────────────────────────────
def run_windows():
    banner("Windows — WASAPI / Stereo Mix / VB-Cable")

    if not check_ffmpeg():
        print("  ✗ ffmpeg chưa cài. Tải tại: https://ffmpeg.org/download.html")
        return

    device = find_windows_loopback()

    if device:
        print(f"\n  ★ Tìm thấy: [{device['index']}] {device['name']}")
        print(f"  Lib: {device['lib']}")

        if device["lib"] == "sounddevice":
            _test_windows_sounddevice(device["index"], duration=5.0)
        else:
            _test_windows_pyaudiowpatch(device["index"], duration=5.0)
    else:
        print("\n  ✗ Không tìm thấy loopback device tự động.")
        print("\n  Các cách fix:")
        print("  1. Stereo Mix: Control Panel > Sound > Recording")
        print("     → Chuột phải > Show Disabled Devices > Enable Stereo Mix")
        print("  2. Cài VB-Audio Cable (free): https://vb-audio.com/Cable/")
        print("  3. pip install pyaudiowpatch  (WASAPI loopback không cần driver)")
        print("\n  Sau khi setup xong chạy lại script này.")


def _test_windows_sounddevice(device_idx: int, duration: float = 5.0):
    try:
        import sounddevice as sd
    except ImportError:
        print("  pip install sounddevice")
        return

    SAMPLE_RATE = 16000
    CHUNK       = 4096
    levels      = []

    print(f"\n  Capturing {duration}s qua sounddevice...\n")

    def cb(indata, frames, t, status):
        level = float(np.abs(indata).mean())
        levels.append(level)
        bars = int(level * 400)
        print(f"\r  Level: [{'█'*min(bars,44):<44}] {level:.5f}",
              end="", flush=True)

    with sd.InputStream(device=device_idx, channels=1,
                        samplerate=SAMPLE_RATE, blocksize=CHUNK,
                        dtype="float32", callback=cb):
        time.sleep(duration)

    print("\n")
    if levels:
        print(f"  Avg: {np.mean(levels):.5f}  Peak: {np.max(levels):.5f}")
        if np.mean(levels) > 0.0002:
            print("  ✓ THÀNH CÔNG!")
        else:
            print("  ⚠ Level thấp — kiểm tra âm thanh đang phát")


def _test_windows_pyaudiowpatch(device_idx: int, duration: float = 5.0):
    try:
        import pyaudiowpatch as paw
    except ImportError:
        print("  pip install pyaudiowpatch")
        return

    p = paw.PyAudio()
    d = p.get_device_info_by_index(device_idx)
    RATE  = int(d["defaultSampleRate"])
    CHUNK = 4096
    levels = []

    stream = p.open(
        format=paw.paInt16,
        channels=d["maxInputChannels"],
        rate=RATE,
        input=True,
        input_device_index=device_idx,
        frames_per_buffer=CHUNK,
    )
    print(f"\n  Capturing {duration}s qua pyaudiowpatch...\n")
    start = time.time()
    while time.time() - start < duration:
        data  = stream.read(CHUNK, exception_on_overflow=False)
        arr   = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        level = float(np.abs(arr).mean())
        levels.append(level)
        bars  = int(level * 400)
        print(f"\r  Level: [{'█'*min(bars,44):<44}] {level:.5f}",
              end="", flush=True)

    stream.stop_stream()
    stream.close()
    p.terminate()
    print("\n")
    if levels:
        print(f"  Avg: {np.mean(levels):.5f}  Peak: {np.max(levels):.5f}")
        if np.mean(levels) > 0.0002:
            print("  ✓ THÀNH CÔNG!")
        else:
            print("  ⚠ Level thấp")


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║        System Audio Capture — Test (ffmpeg-based)       ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"  Platform : {PLATFORM}")
    print(f"  ffmpeg   : {'✓' if check_ffmpeg() else '✗ chưa cài'}")

    try:
        if PLATFORM == "linux":
            run_linux()
        elif PLATFORM == "win32":
            run_windows()
        elif PLATFORM == "darwin":
            print("\n  macOS: cài BlackHole rồi chọn nó làm monitor source")
            print("  brew install blackhole-2ch")
            print("  Sau đó chạy: ffmpeg -f avfoundation -list_devices true -i ''")
        else:
            print(f"\n  Platform không hỗ trợ: {PLATFORM}")
    except KeyboardInterrupt:
        print("\n\n  Dừng test.")

    print()
    print("─" * 60)
    print("  Nếu ✓ → paste kết quả để tích hợp vào AudioCapture!")
    print("─" * 60)

# find /home/trikhai/Documents/pyqt/speech-translator/db -name "*.pyc" -delete
# find /home/trikhai/Documents/pyqt/speech-translator/db -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null