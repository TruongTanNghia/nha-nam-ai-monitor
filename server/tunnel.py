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
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests

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
            log.warning("Web mở ra Internet KHÔNG cần đăng nhập (APP_PASSWORD trống) — ai có link đều vào được")
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
                    errors="replace",
                    # Chạy trong cửa sổ lệnh: cloudflared dùng chung cửa sổ -> đóng cửa sổ là tắt cả đường hầm.
                    # Chạy ẩn (pythonw, không có cửa sổ): không bật thêm cửa sổ đen cho cloudflared.
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.__stdout__ is None else 0,
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
        log.info("Cloudflare cấp link: %s — đang chờ link hoạt động...", url)
        threading.Thread(target=self._announce, args=(url,), name="tunnel-announce", daemon=True).start()

    def _announce(self, url):
        """Link mới cần 10-30 giây để truy cập được; mở/gửi sớm quá dễ báo 'không vào được trang'."""
        time.sleep(15)
        ready = False
        for _ in range(12):
            if self.url != url:  # đã có link khác
                return
            try:
                ready = requests.get(url + "/api/health", timeout=10).status_code < 500
            except requests.RequestException:
                ready = False
            if ready:
                break
            time.sleep(5)
        login = (f"\nĐăng nhập: tài khoản {config.APP_USER}, mật khẩu trong server/.env"
                 if config.APP_PASSWORD else "")
        try:
            LINK_FILE.write_text(f"{url}\n{login.strip()}\n", encoding="utf-8")
        except OSError:
            pass
        bar = "=" * 64
        print(f"\n{bar}\n  WEB NHÀ NẤM ĐÃ LÊN MẠNG — mở trên điện thoại / máy khác:\n\n  {url}\n"
              f"{'  (link chưa phản hồi, thử lại sau 1 phút)' if not ready else ''}\n{bar}\n", flush=True)
        if config.OPEN_BROWSER:
            webbrowser.open(url)
        self.engine.notify(f"🌐 Link web Nhà Nấm (mới):\n{url}{login}", priority="high")
