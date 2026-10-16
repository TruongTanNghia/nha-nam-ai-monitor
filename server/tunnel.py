"""Cloudflare Tunnel do máy chủ AI tự quản lý (TUNNEL_ENABLED=true).

Chạy `cloudflared tunnel --url http://localhost:PORT`, bắt link https://....trycloudflare.com,
lưu vào LINK_WEB.txt, hiện trên web và gửi về Zalo. Link miễn phí đổi mỗi lần khởi động lại,
nên mỗi khi có link mới đều báo lại. Đường hầm rớt thì tự mở lại.
"""
import atexit
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import config

log = logging.getLogger("tunnel")

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
LINK_FILE = config.PROJECT_DIR / "LINK_WEB.txt"


def find_cloudflared():
    if config.CLOUDFLARED_PATH and Path(config.CLOUDFLARED_PATH).exists():
        return config.CLOUDFLARED_PATH
    found = shutil.which("cloudflared")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            for sub in ("cloudflared/cloudflared.exe", "Microsoft/WinGet/Links/cloudflared.exe"):
                path = Path(base) / sub
                if path.exists():
                    return str(path)
    return None


class Tunnel:
    def __init__(self, engine):
        self.engine = engine
        self.url = None
        self.error = None
        self.started_at = None
        self.proc = None
        atexit.register(self.stop)

    def stop(self):
        """Tắt cloudflared khi máy chủ dừng (không để đường hầm mồ côi)."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def status(self):
        return {"enabled": config.TUNNEL_ENABLED, "url": self.url, "error": self.error}

    def start(self):
        if not config.TUNNEL_ENABLED:
            return
        if not config.APP_PASSWORD:
            self.error = "Chưa đặt APP_PASSWORD — không mở web ra Internet khi chưa có mật khẩu"
            log.error(self.error)
            return
        exe = find_cloudflared()
        if not exe:
            self.error = "Không tìm thấy cloudflared — cài bằng: winget install Cloudflare.cloudflared"
            log.error(self.error)
            return
        threading.Thread(target=self._run, args=(exe,), name="tunnel", daemon=True).start()

    def _run(self, exe):
        while self.engine.running:
            self.url = None
            log.info("Đang mở Cloudflare Tunnel...")
            try:
                proc = self.proc = subprocess.Popen(
                    [exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{config.PORT}"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                self.error = f"Không chạy được cloudflared: {exc}"
                log.error(self.error)
                return
            for line in proc.stdout:
                match = URL_RE.search(line)
                if match and match.group(0) != self.url:
                    self._on_new_url(match.group(0))
            proc.wait()
            self.error = f"Đường hầm bị ngắt (mã {proc.returncode}), mở lại sau 15 giây"
            log.warning(self.error)
            time.sleep(15)

    def _on_new_url(self, url):
        self.url, self.error, self.started_at = url, None, time.time()
        log.info("Link web công khai: %s", url)
        try:
            LINK_FILE.write_text(f"{url}\nTài khoản: {config.APP_USER} (mật khẩu: APP_PASSWORD trong server/.env)\n",
                                 encoding="utf-8")
        except OSError:
            pass
        self.engine.notify(
            f"🌐 Link web Nhà Nấm (mới):\n{url}\n"
            f"Đăng nhập tài khoản {config.APP_USER}, mật khẩu như đã cài đặt."
        )
