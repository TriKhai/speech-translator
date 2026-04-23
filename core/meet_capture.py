"""
meet_capture.py — Google Meet Bot (Playwright bundled Chromium + PulseAudio)

Flow:
  1. Tạo PulseAudio virtual sink (meeting_sink)
  2. Playwright dùng bundled Chromium (version mới, không phải Chrome thật)
  3. Lần đầu: tự động login Google bằng MEET_BOT_EMAIL + MEET_BOT_PASSWORD từ .env
     Lần sau: dùng lại session đã lưu → không cần login lại
  4. Join Meet → vào waiting room → chờ host admit
  5. ffmpeg capture monitor source → PCM bytes → on_data callback

.env:
  MEET_BOT_EMAIL=your_bot@gmail.com
  MEET_BOT_PASSWORD=your_password

Dependencies:
  pip install playwright python-dotenv
  playwright install chromium
  sudo apt install ffmpeg pulseaudio-utils
"""

import os
import time
import subprocess
import threading
from enum import Enum, auto


class MeetStatus(Enum):
    IDLE       = auto()
    CONNECTING = auto()
    WAITING    = auto()
    IN_MEETING = auto()
    ENDED      = auto()
    ERROR      = auto()


class MeetCapture:

    SAMPLE_RATE  = 16_000
    CHANNELS     = 1
    CHUNK_FRAMES = 4096
    BOT_NAME     = "Meeting Recorder"
    SINK_NAME    = "meeting_sink"
    SESSION_DIR  = "/tmp/meet_bot_session"   # lưu session sau khi login

    def __init__(self, on_data: callable, on_status: callable = None):
        self._on_data   = on_data
        self._on_status = on_status or (lambda s, m: None)

        self._browser  = None
        self._page     = None
        self._pw       = None
        self._sink_mod = None
        self._ffmpeg   = None
        self._thread   = None
        self._running  = False
        self._status   = MeetStatus.IDLE

        # Load credentials từ .env
        self._email    = os.environ.get("MEET_BOT_EMAIL", "")
        self._password = os.environ.get("MEET_BOT_PASSWORD", "")
        if not self._email or not self._password:
            print("[MEET] WARNING: MEET_BOT_EMAIL hoặc MEET_BOT_PASSWORD chưa set trong .env")

    # ══════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════

    def join(self, meet_url: str):
        if self._status not in (MeetStatus.IDLE, MeetStatus.ERROR, MeetStatus.ENDED):
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._join_thread,
            args=(meet_url,),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._running = False
        self._cleanup()

    @property
    def status(self) -> MeetStatus:
        return self._status

    # ══════════════════════════════════════════════════════
    # Internal — main flow
    # ══════════════════════════════════════════════════════

    def _join_thread(self, meet_url: str):
        try:
            self._set_status(MeetStatus.CONNECTING, "Đang kết nối…")
            self._create_sink()
            self._launch_browser()

            # Login Google nếu chưa có session
            if not self._is_logged_in():
                self._set_status(MeetStatus.CONNECTING, "Đang đăng nhập Google…")
                self._google_login()
            else:
                print("[MEET] Session hợp lệ — bỏ qua login")

            self._open_meet(meet_url)
            self._click_join()

            self._set_status(MeetStatus.WAITING, "Chờ host cho phép vào…")
            admitted = self._wait_for_admit()

            if not admitted:
                self._set_status(MeetStatus.ERROR, "Không được admit hoặc timeout")
                self._cleanup()
                return

            self._set_status(MeetStatus.IN_MEETING, "Đang ghi âm cuộc họp…")
            self._start_ffmpeg_capture()
            self._monitor_meeting()

        except Exception as e:
            print(f"[MEET] Error: {e}")
            import traceback; traceback.print_exc()
            self._set_status(MeetStatus.ERROR, str(e))
            self._cleanup()

    # ══════════════════════════════════════════════════════
    # Internal — PulseAudio
    # ══════════════════════════════════════════════════════

    def _create_sink(self):
        self._remove_sink()
        try:
            result = subprocess.run(
                ["pactl", "load-module", "module-null-sink",
                 f"sink_name={self.SINK_NAME}",
                 "sink_properties=device.description=MeetingRecorder"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                self._sink_mod = result.stdout.strip()
                print(f"[MEET] PulseAudio sink: {self.SINK_NAME} (module {self._sink_mod})")
            else:
                raise RuntimeError(f"pactl failed: {result.stderr}")
        except FileNotFoundError:
            raise RuntimeError("pactl không tìm thấy — sudo apt install pulseaudio-utils")

    def _remove_sink(self):
        if self._sink_mod:
            subprocess.run(["pactl", "unload-module", self._sink_mod], capture_output=True)
            self._sink_mod = None
            print("[MEET] PulseAudio sink removed")

    # ══════════════════════════════════════════════════════
    # Internal — Browser
    # ══════════════════════════════════════════════════════

    def _launch_browser(self):
        """
        Dùng Playwright bundled Chromium (version mới).
        Lưu session vào SESSION_DIR để login 1 lần dùng mãi.
        """
        from playwright.sync_api import sync_playwright

        os.environ["PULSE_SINK"] = self.SINK_NAME
        self._pw = sync_playwright().start()

        # Tạo session dir nếu chưa có
        os.makedirs(self.SESSION_DIR, exist_ok=True)
        print(f"[MEET] Session dir: {self.SESSION_DIR}")
        print("[MEET] Using Playwright bundled Chromium")

        # executable_path=None → Playwright dùng bundled Chromium tự động
        context = self._pw.chromium.launch_persistent_context(
            user_data_dir   = self.SESSION_DIR,
            executable_path = None,   # Playwright bundled Chromium
            headless        = False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--use-fake-ui-for-media-stream",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-automation",
                "--exclude-switches=enable-automation",
                f"--alsa-output-device=pulse:{self.SINK_NAME}",
            ],
            env={
                **os.environ,
                "PULSE_SINK":   self.SINK_NAME,
                "PULSE_SOURCE": f"{self.SINK_NAME}.monitor",
            },
            permissions=["camera", "microphone"],
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        # Stealth patch
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined, configurable: true,
            });
            window.chrome = {
                runtime: {}, loadTimes: function() {}, csi: function() {}, app: {},
            };
            const _q = window.navigator.permissions.query;
            window.navigator.permissions.query = (p) => (
                p.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : _q(p)
            );
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['vi-VN','vi','en-US','en']
            });
        """)

        self._browser = context
        self._page    = context.new_page()

        try:
            from playwright_stealth import stealth_sync
            stealth_sync(self._page)
            print("[MEET] playwright-stealth applied")
        except ImportError:
            pass

        print("[MEET] Browser launched (Playwright bundled Chromium)")

    def _is_logged_in(self) -> bool:
        """Kiểm tra session còn hợp lệ không bằng cách mở accounts.google.com."""
        try:
            self._page.goto("https://accounts.google.com", timeout=15_000)
            time.sleep(2)
            url = self._page.url
            # Nếu đã login → redirect về myaccount hoặc hiện tên
            if "myaccount.google.com" in url or "accounts.google.com/v3/signin" not in url:
                email_check = self._page.evaluate("""() => {
                    const body = document.body ? document.body.innerText : '';
                    return body;
                }""")
                if self._email.split("@")[0].lower() in email_check.lower():
                    print(f"[MEET] Already logged in as {self._email}")
                    return True
            print("[MEET] Not logged in — cần login")
            return False
        except Exception as e:
            print(f"[MEET] Login check error: {e}")
            return False

    def _google_login(self):
        """Tự động login Google với email/password từ .env."""
        print(f"[MEET] Logging in as {self._email}…")
        page = self._page

        # Mở trang login
        page.goto("https://accounts.google.com/signin", timeout=15_000)
        time.sleep(2)

        # ── Bước 1: Nhập email ──────────────────────────
        try:
            page.wait_for_selector("input[type='email']", timeout=10_000)
            page.fill("input[type='email']", self._email)
            time.sleep(0.5)
            page.click("button:has-text('Next'), #identifierNext")
            print("[MEET] Email entered")
            time.sleep(2)
        except Exception as e:
            raise RuntimeError(f"Không nhập được email: {e}")

        # ── Bước 2: Nhập password ───────────────────────
        try:
            page.wait_for_selector("input[type='password']", timeout=10_000)
            page.fill("input[type='password']", self._password)
            time.sleep(0.5)
            page.click("button:has-text('Next'), #passwordNext")
            print("[MEET] Password entered")
            time.sleep(3)
        except Exception as e:
            raise RuntimeError(f"Không nhập được password: {e}")

        # ── Bước 3: Xử lý 2FA / "Stay signed in" popup ─
        # "Stay signed in?" → bấm Yes
        for txt in ["Yes", "Có", "I agree", "Đồng ý", "Continue", "Tiếp tục"]:
            try:
                page.click(f"button:has-text('{txt}')", timeout=3_000)
                print(f"[MEET] Clicked: {txt}")
                time.sleep(1)
                break
            except Exception:
                pass

        # Kiểm tra có bị chặn bởi 2FA không
        time.sleep(2)
        url = page.url
        if "signin" in url or "challenge" in url:
            # Có thể đang yêu cầu 2FA — chờ user xử lý thủ công tối đa 60s
            print("[MEET] WARNING: Có thể cần xác minh 2FA — vui lòng xác nhận trên điện thoại (60s)")
            deadline = time.time() + 60
            while time.time() < deadline:
                if "myaccount" in page.url or "meet.google.com" in page.url:
                    break
                if "signin" not in page.url and "challenge" not in page.url:
                    break
                time.sleep(2)

        print(f"[MEET] Login done — URL: {page.url}")

    def _open_meet(self, url: str):
        """Mở Meet URL, đợi trang load."""
        print(f"[MEET] Opening: {url}")
        try:
            self._page.goto(url, wait_until="load", timeout=30_000)
        except Exception:
            pass

        time.sleep(5)
        print(f"[MEET] URL: {self._page.url}")
        print(f"[MEET] Title: {self._page.title()}")

        result = self._page.evaluate("""() => {
            return {
                inputs:  document.querySelectorAll('input').length,
                buttons: document.querySelectorAll('button').length,
                body:    document.body ? document.body.innerText.substring(0, 200) : '',
            };
        }""")
        print(f"[MEET] DOM: {result['inputs']} inputs, {result['buttons']} buttons")
        print(f"[MEET] Body: {result['body'][:150]}")

    def _click_join(self):
        """Tắt mic/camera + bấm Join."""
        page = self._page
        time.sleep(1)

        # Tắt mic/camera
        for aria in ["Turn off microphone", "Turn off camera", "Tắt micrô", "Tắt máy ảnh"]:
            try:
                page.locator(f"[aria-label='{aria}']").click(timeout=2_000)
                print(f"[MEET] Disabled: {aria}")
            except Exception:
                pass

        time.sleep(0.5)

        # Bấm join
        join_clicked = page.evaluate("""() => {
            const texts = [
                'Ask to join', 'Join now', 'Yêu cầu tham gia',
                'Tham gia ngay', 'Tham gia',
            ];
            for (const btn of document.querySelectorAll('button')) {
                const t = (btn.innerText || btn.textContent || '').trim();
                if (texts.some(x => t.includes(x))) {
                    btn.click();
                    return t;
                }
            }
            return null;
        }""")

        if join_clicked:
            print(f"[MEET] Clicked join: '{join_clicked}'")
        else:
            print("[MEET] WARNING: Không tìm thấy nút join!")
            try:
                self._page.screenshot(path="/tmp/meet_debug.png", full_page=True)
                print("[MEET] Screenshot: /tmp/meet_debug.png")
            except Exception:
                pass

    def _wait_for_admit(self, timeout_sec: int = 300) -> bool:
        """Poll mỗi 2s chờ host admit."""
        deadline = time.time() + timeout_sec

        while time.time() < deadline and self._running:
            try:
                result = self._page.evaluate("""() => {
                    const body = document.body ? document.body.innerText : '';
                    const inMeeting = !!(
                        document.querySelector("[aria-label='Leave call']")        ||
                        document.querySelector("[aria-label='Rời cuộc gọi']")      ||
                        body.includes('Turn on microphone')                         ||
                        body.includes('Bật micrô')                                  ||
                        body.includes('Bạn không thể bật tiếng của người khác')    ||
                        body.includes('Chế độ cài đặt âm thanh')                   ||
                        body.includes('You cannot unmute')                          ||
                        body.includes('mic_off')
                    );
                    const denied = (
                        body.includes("You can't join this call")  ||
                        body.includes("Bạn không thể tham gia")    ||
                        body.includes("aren't allowed")
                    );
                    return { inMeeting, denied, body: body.substring(0, 100) };
                }""")

                print(f"[MEET] Waiting… {result['body'][:80]}")

                if result["inMeeting"]:
                    print("[MEET] Admitted!")
                    return True
                if result["denied"]:
                    print("[MEET] Denied")
                    return False

            except Exception as e:
                print(f"[MEET] Poll error: {e}")

            time.sleep(2)

        print("[MEET] Timeout")
        return False

    def _monitor_meeting(self):
        """Chờ meeting kết thúc."""
        while self._running:
            try:
                ended = self._page.evaluate("""() => {
                    const body = document.body ? document.body.innerText : '';
                    return (
                        body.includes("You've left the meeting")   ||
                        body.includes("The call has ended")         ||
                        body.includes("Bạn đã rời khỏi cuộc họp")  ||
                        body.includes("Cuộc gọi đã kết thúc")
                    );
                }""")
                if ended:
                    print("[MEET] Meeting ended")
                    self._set_status(MeetStatus.ENDED, "Cuộc họp đã kết thúc")
                    self._cleanup()
                    return
            except Exception:
                break
            time.sleep(3)

    # ══════════════════════════════════════════════════════
    # Internal — ffmpeg capture
    # ══════════════════════════════════════════════════════

    def _route_chromium_audio(self):
        """
        Move tất cả audio stream của Chromium sang meeting_sink.
        Cần thiết vì PULSE_SINK env không đủ để route audio của subprocess.
        """
        try:
            # Lấy danh sách sink inputs hiện tại
            result = subprocess.run(
                ["pactl", "list", "sink-inputs"],
                capture_output=True, text=True,
            )
            # Lấy sink index của meeting_sink
            sink_result = subprocess.run(
                ["pactl", "list", "sinks", "short"],
                capture_output=True, text=True,
            )
            sink_idx = None
            for line in sink_result.stdout.splitlines():
                if self.SINK_NAME in line:
                    sink_idx = line.split()[0]
                    break

            if not sink_idx:
                print("[MEET] WARNING: Không tìm được sink index")
                return

            # Move tất cả sink inputs (audio streams) sang meeting_sink
            moved = 0
            for line in result.stdout.splitlines():
                if line.strip().startswith("Sink Input #"):
                    input_idx = line.strip().split("#")[1]
                    subprocess.run(
                        ["pactl", "move-sink-input", input_idx, sink_idx],
                        capture_output=True,
                    )
                    moved += 1

            print(f"[MEET] Routed {moved} audio stream(s) → {self.SINK_NAME} (idx={sink_idx})")
        except Exception as e:
            print(f"[MEET] Audio route error: {e}")

    def _start_ffmpeg_capture(self):
        # Route Chromium audio sang sink trước khi capture
        self._route_chromium_audio()

        monitor_source = f"{self.SINK_NAME}.monitor"
        chunk_size     = self.CHUNK_FRAMES * 2

        self._ffmpeg = subprocess.Popen(
            [
                "ffmpeg",
                "-f", "pulse",
                "-i", monitor_source,
                "-af", "aresample=resampler=soxr",   # chất lượng resample tốt hơn
                "-ac", "1",          # stereo → mono
                "-ar", "16000",      # 48000 → 16000Hz
                "-f", "s16le",
                "-loglevel", "quiet", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        print(f"[MEET] ffmpeg capturing: {monitor_source}")

        def _read_loop():
            while self._running and self._ffmpeg:
                data = self._ffmpeg.stdout.read(chunk_size)
                if not data:
                    break
                self._on_data(data)

        threading.Thread(target=_read_loop, daemon=True).start()

    # ══════════════════════════════════════════════════════
    # Internal — cleanup
    # ══════════════════════════════════════════════════════

    def _cleanup(self):
        self._running = False

        if self._ffmpeg:
            try:
                self._ffmpeg.kill()
                self._ffmpeg.wait(timeout=3)
            except Exception:
                pass
            self._ffmpeg = None

        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

        self._remove_sink()
        print("[MEET] Cleanup done")

    def _set_status(self, status: MeetStatus, msg: str = ""):
        self._status = status
        print(f"[MEET] Status: {status.name} — {msg}")
        self._on_status(status, msg)