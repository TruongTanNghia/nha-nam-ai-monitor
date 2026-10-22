"""Zalo Bot API: gửi tin nhắn và nhận tin nhắn (getUpdates).

Tài liệu: https://bot.zapps.me/docs — API giống Telegram Bot API.
"""
import logging
import threading
import time
from collections import deque

import requests

import config

log = logging.getLogger("zalo")

MAX_LEN = 2000  # giới hạn ký tự mỗi tin nhắn của Zalo Bot


def _chunks(text, size=MAX_LEN):
    """Cắt tin dài theo dòng để không vượt giới hạn."""
    parts, buf = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            parts.append(line[:size])
            line = line[size:]
        if len(buf) + len(line) > size:
            parts.append(buf)
            buf = ""
        buf += line
    if buf.strip():
        parts.append(buf)
    return parts


class ZaloBot:
    def __init__(self, token=config.ZALO_BOT_TOKEN, chat_ids=config.ZALO_CHAT_IDS):
        self.token = token
        self.chat_ids = list(chat_ids)
        self.session = requests.Session()
        self.lock = threading.Lock()
        self.seen_chats = {}  # chat_id -> {name, last_text, ts} để hỗ trợ lấy chat_id
        self.last_error = None
        self.sent_count = 0
        self.sent_times = deque()   # thời điểm các tin đã gửi trong 24 giờ
        self.dropped = 0            # số tin bỏ qua vì sắp hết hạn mức
        self.paused_until = 0       # Zalo báo vượt hạn mức -> ngừng gửi tới lúc này

    @property
    def configured(self):
        return bool(self.token and self.chat_ids)

    def _url(self, method):
        return f"{config.ZALO_API_BASE}/bot{self.token}/{method}"

    def status(self):
        return {
            "configured": self.configured,
            "has_token": bool(self.token),
            "chat_count": len(self.chat_ids),
            "commands": config.ZALO_COMMANDS_ENABLED,
            "sent_count": self.sent_count,
            "last_error": self.last_error,
            "sent_hour": self._count(3600),
            "sent_day": self._count(86400),
            "max_hour": config.ZALO_MAX_PER_HOUR,
            "max_day": config.ZALO_MAX_PER_DAY,
            "dropped": self.dropped,
            "paused_until": self.paused_until if self.paused_until > time.time() else None,
        }

    def _count(self, seconds):
        now = time.time()
        while self.sent_times and self.sent_times[0] < now - 86400:
            self.sent_times.popleft()
        return sum(1 for t in self.sent_times if t >= now - seconds)

    def _allowed(self, priority):
        """low: báo cáo định kỳ, thông báo bật/tắt; normal: cảnh báo; high: cảnh báo nghiêm trọng,
        trả lời lệnh, tin người dùng bấm gửi. Tin ít quan trọng dừng sớm để dành chỗ cho cảnh báo."""
        if time.time() < self.paused_until:
            return False
        hour, day = self._count(3600), self._count(86400)
        max_h, max_d = config.ZALO_MAX_PER_HOUR, config.ZALO_MAX_PER_DAY
        if priority == "high":
            return day < max_d
        if priority == "normal":
            return hour < max_h and day < max_d * 0.9
        return hour < max_h * 0.5 and day < max_d * 0.6

    def send(self, text, chat_ids=None, priority="normal"):
        """Gửi tin tới danh sách chat. Trả True nếu gửi thành công ít nhất 1 nơi."""
        targets = chat_ids or self.chat_ids
        if not self.token or not targets:
            log.info("[Zalo chưa cấu hình] %s", text.replace("\n", " | ")[:200])
            return False
        if not self._allowed(priority):
            self.dropped += 1
            log.warning("Bỏ qua tin Zalo (%s) để không vượt hạn mức: %s", priority, text.replace("\n", " | ")[:120])
            return False
        if config.DRY_RUN:
            log.info("[DRY_RUN] Zalo -> %s: %s", targets, text.replace("\n", " | ")[:300])
            return True

        ok_any = False
        with self.lock:  # giữ thứ tự tin nhắn
            for chat_id in targets:
                for part in _chunks(text):
                    try:
                        res = self.session.post(
                            self._url("sendMessage"),
                            json={"chat_id": chat_id, "text": part},
                            timeout=15,
                        )
                        body = res.json() if res.content else {}
                        self.sent_times.append(time.time())
                        if res.ok and body.get("ok", True):
                            ok_any = True
                            self.sent_count += 1
                            self.last_error = None
                        else:
                            self.last_error = f"HTTP {res.status_code}: {res.text[:200]}"
                            log.error("Gửi Zalo thất bại: %s", self.last_error)
                            desc = str(body.get("description", "")).lower()
                            if res.status_code == 429 or "hạn mức" in desc or "limit" in desc or "quota" in desc:
                                self.paused_until = time.time() + config.ZALO_PAUSE_MIN * 60
                                log.error("Zalo báo vượt hạn mức — ngừng gửi %d phút", config.ZALO_PAUSE_MIN)
                                return ok_any
                    except (requests.RequestException, ValueError) as exc:
                        self.last_error = str(exc)
                        log.error("Lỗi gửi Zalo: %s", exc)
                    time.sleep(0.3)
        return ok_any

    def get_updates(self, timeout=30):
        """Long-polling lấy tin nhắn mới. Trả list các message dict."""
        if not self.token:
            return []
        try:
            res = self.session.post(
                self._url("getUpdates"), json={"timeout": str(timeout)}, timeout=timeout + 10
            )
            if res.status_code == 408 or not res.content:
                return []  # hết thời gian chờ, không có tin mới
            body = res.json()
        except requests.Timeout:
            return []
        except (requests.RequestException, ValueError) as exc:
            log.warning("getUpdates lỗi: %s", exc)
            time.sleep(5)
            return []

        if not body.get("ok"):
            # 408 = chưa có tin nhắn mới. Zalo có thể trả ngay thay vì giữ kết nối -> nghỉ 1 giây
            if body.get("error_code") not in (408, None):
                log.warning("getUpdates: %s", body)
                time.sleep(5)
            else:
                time.sleep(1)
            return []

        result = body.get("result") or []
        updates = result if isinstance(result, list) else [result]
        messages = []
        for upd in updates:
            msg = (upd or {}).get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id") or "")
            text = (msg.get("text") or "").strip()
            if not chat_id or not text:
                continue
            sender = msg.get("from") or {}
            self.seen_chats[chat_id] = {
                "chat_id": chat_id,
                "name": sender.get("display_name") or sender.get("id") or "",
                "last_text": text[:100],
                "ts": time.time(),
                "allowed": chat_id in self.chat_ids,
            }
            messages.append({"chat_id": chat_id, "text": text, "from": sender})
        return messages
