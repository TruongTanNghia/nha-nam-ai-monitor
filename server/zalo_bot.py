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
RETRY_WAITS = (2, 5, 10)     # gửi lỗi mạng/máy chủ Zalo -> thử lại sau 2s, 5s, 10s
PENDING_MAX = 40             # tin chưa gửi được (mất mạng) giữ lại tối đa
PENDING_MAX_AGE = 6 * 3600   # tin chờ quá 6 giờ thì bỏ


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
        self.session = requests.Session()       # gửi tin
        self.poll_session = requests.Session()  # getUpdates chạy ở luồng khác -> phiên riêng
        self.lock = threading.Lock()
        self.pending = deque()      # (chat_id, đoạn tin, lúc tạo) chưa gửi được vì mất mạng
        self.retry_thread = None
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
            "pending": len(self.pending),
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
            self._flush_pending()
            for chat_id in targets:
                for part in _chunks(text):
                    result = self._post(chat_id, part)
                    if result == "ok":
                        ok_any = True
                    elif result == "retry":  # mất mạng: giữ lại, gửi khi có mạng
                        self._queue(chat_id, part)
                    elif result == "quota":
                        return ok_any
                    time.sleep(0.3)
        return ok_any

    def _post(self, chat_id, part):
        """Gửi 1 đoạn, tự thử lại khi lỗi mạng/máy chủ. Trả ok | retry (lỗi tạm thời) | quota | fail."""
        for attempt in range(len(RETRY_WAITS) + 1):
            if attempt:
                time.sleep(RETRY_WAITS[attempt - 1])
            try:
                res = self.session.post(self._url("sendMessage"), json={"chat_id": chat_id, "text": part}, timeout=15)
                body = res.json() if res.content else {}
            except (requests.RequestException, ValueError) as exc:
                self.last_error = f"Lỗi mạng: {exc}"
                log.warning("Gửi Zalo lỗi (lần %d): %s", attempt + 1, exc)
                self.session = requests.Session()  # kết nối giữ sẵn có thể đã bị đóng -> mở phiên mới
                continue
            if res.ok and body.get("ok", True):
                self.sent_times.append(time.time())
                self.sent_count += 1
                self.last_error = None
                return "ok"
            self.last_error = f"HTTP {res.status_code}: {res.text[:200]}"
            desc = str(body.get("description", "")).lower()
            if res.status_code == 429 or "hạn mức" in desc or "limit" in desc or "quota" in desc:
                self.paused_until = time.time() + config.ZALO_PAUSE_MIN * 60
                log.error("Zalo báo vượt hạn mức — ngừng gửi %d phút: %s", config.ZALO_PAUSE_MIN, self.last_error)
                return "quota"
            if res.status_code < 500:  # sai token/chat_id...: thử lại cũng không được
                log.error("Gửi Zalo thất bại: %s", self.last_error)
                return "fail"
            log.warning("Máy chủ Zalo lỗi (lần %d): %s", attempt + 1, self.last_error)
        log.error("Gửi Zalo không được sau %d lần — giữ tin lại để gửi sau", len(RETRY_WAITS) + 1)
        return "retry"

    def _queue(self, chat_id, part):
        self.pending.append((chat_id, part, time.time()))
        while len(self.pending) > PENDING_MAX:
            self.pending.popleft()
            self.dropped += 1
        if self.retry_thread is None or not self.retry_thread.is_alive():
            self.retry_thread = threading.Thread(target=self._retry_loop, name="zalo-retry", daemon=True)
            self.retry_thread.start()

    def _flush_pending(self):
        """Gửi lại các tin còn tồn (gọi khi đang giữ self.lock). Trả True nếu đã gửi hết."""
        while self.pending:
            chat_id, part, created = self.pending[0]
            if time.time() - created > PENDING_MAX_AGE:
                self.pending.popleft()
                self.dropped += 1
                continue
            if time.time() < self.paused_until:
                return False
            late = int((time.time() - created) / 60)
            result = self._post(chat_id, part + (f"\n(⏳ gửi chậm {late} phút do mất mạng)" if late >= 2 else ""))
            if result == "retry":
                return False
            self.pending.popleft()
            if result == "quota":
                return False
            time.sleep(0.3)
        return True

    def _retry_loop(self):
        while self.pending:
            time.sleep(30)
            with self.lock:
                if self._flush_pending():
                    log.info("Đã gửi bù các tin Zalo bị chậm")

    def get_updates(self, timeout=30):
        """Long-polling lấy tin nhắn mới. Trả list các message dict."""
        if not self.token:
            return []
        try:
            res = self.poll_session.post(
                self._url("getUpdates"), json={"timeout": str(timeout)}, timeout=timeout + 10
            )
            if res.status_code == 408 or not res.content:
                return []  # hết thời gian chờ, không có tin mới
            body = res.json()
        except requests.Timeout:
            return []
        except (requests.RequestException, ValueError) as exc:
            log.warning("getUpdates lỗi: %s", exc)
            self.poll_session = requests.Session()
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
