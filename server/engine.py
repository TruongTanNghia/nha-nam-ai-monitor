"""Vòng lặp chính: đọc Firebase -> lưu lịch sử -> phân tích -> cảnh báo Zalo.

Thiết bị bật/tắt theo NGƯỠNG (firmware ESP32). Random Forest + thời tiết ngoài trời chỉ để tham khảo.
"""
import datetime as dt
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import config
import indices
import llm
from analyzer import SEV_RANK, Analyzer
from controller import AutoController, explain_auto
from firebase_client import Firebase
from rf_model import RFAnalyzer, report_lines
from sensors import DEVICE_KEYS, DEVICES, MODE_AUTO, RESET_REASONS, SENSOR_KEYS, SENSORS, fmt, num, thresholds
from storage import Storage
from weather import Weather
from zalo_bot import ZaloBot

log = logging.getLogger("engine")

ICONS = {"t": "🌡", "h": "💧", "ANHSANG": "☀️", "GT_AMDAT": "🌱", "GT_DOMAN": "🧂", "GT_PH": "⚗️"}
SEV_ICON = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
SOURCE_TEXT = {
    "esp_auto": "🤖 ESP32 tự động",
    "external": "👤 bấm trên web",
    "auto": "🤖 máy chủ AI",
    "zalo": "📱 lệnh Zalo",
}
LINK_KEYS = ("stale_all", "esp_offline")  # mất kết nối ESP32: đã có tin riêng (_check_esp_link)
ESP_OFFLINE_SEC = 60       # nhịp tim ESP_TS đứng yên 60 giây -> coi là mạch tắt / mất kết nối
ESP_STALE_SEC = 300        # firmware cũ (không có ESP_TS): số liệu đứng yên 5 phút
WEATHER_EVERY = 900        # cập nhật thời tiết 15 phút/lần
RF_EVERY = 600             # chạy lại Random Forest 10 phút/lần


def now_text(ts=None):
    return dt.datetime.fromtimestamp(ts or time.time()).strftime("%H:%M %d/%m/%Y")


def clock(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def duration_text(sec):
    m = int(sec // 60)
    if m < 1:
        return f"{int(sec)} giây"
    if m < 90:
        return f"{m} phút"
    return f"{m // 60} giờ {m % 60} phút" if m < 2880 else f"{m // 1440} ngày"


def device_text(dev, value):
    if dev in ("MO", "DONG"):
        return "MỞ màn che" if (dev == "MO") == bool(value) else "ĐÓNG màn che"
    return f"{'BẬT' if value else 'TẮT'} {DEVICES[dev].lower()}"


class Engine:
    def __init__(self):
        self.fb = Firebase()
        self.store = Storage()
        self.analyzer = Analyzer()
        self.ctrl = AutoController()
        self.weather = Weather(self.store)
        self.rf = RFAnalyzer(self.store, self.weather)
        self.zalo = ZaloBot()
        self.outbox = ThreadPoolExecutor(max_workers=1, thread_name_prefix="outbox")
        self.lock = threading.RLock()

        self.recent = deque(maxlen=int(7200 / config.POLL_INTERVAL_SEC) + 20)  # ~2 giờ
        self.latest_data = None
        self.latest_values = None
        self.analysis = None
        self.prev_values = None
        self.our_writes = {}  # device -> (value, ts, source)
        self.active = {}      # key cảnh báo đang mở
        self.event_sent = {}  # key sự kiện -> lần gửi gần nhất
        self.fail_count = 0
        self.offline_alerted = False
        self.last_ok_ts = None
        self.dev_batch = []           # các lần bật/tắt chờ gom thành 1 tin Zalo
        self.dev_msg_times = deque()  # lúc gửi các tin bật/tắt (giãn tin khi thiết bị đảo liên tục)
        self.last_prune = 0
        self.last_daily = None
        self.mute_until = 0
        self.last_periodic = 0
        self.periodic_busy = False
        self.started_at = time.time()
        self.running = False
        self.hw_last_change = None    # lần cuối số liệu cảm biến thay đổi -> ESP32 còn gửi
        self.esp_up_prev = None       # ESP_UP lần đọc trước, để phát hiện ESP32 khởi động lại
        self.esp_ts_prev = None       # ESP_TS lần đọc trước
        self.esp_ts_seen = None       # lúc (đồng hồ máy chủ) thấy ESP_TS đổi gần nhất
        self.esp_online = None        # None = chưa biết
        self.esp_offline_since = None
        self.esp_restart_reason = None  # lý do khởi động lại vừa phát hiện, báo kèm tin "hoạt động lại"
        self.last_weather = 0
        self.last_rf = 0

    # ------------------------------------------------------------------ setup
    def start(self):
        self.store.resolve_all_active()
        since = time.time() - 7200
        for row in self.store.readings_since(since):
            self.recent.append(row)
        history = self.store.readings_since(time.time() - 3 * 86400)
        # Lần cuối số liệu cảm biến còn thay đổi (để báo ESP32 mất kết nối từ khi nào, kể cả sau khi khởi động lại)
        for a, b in zip(history, history[1:]):
            if any(a.get(k) != b.get(k) for k in SENSOR_KEYS):
                self.hw_last_change = b["ts"]
        # Bỏ yêu cầu "bật sớm theo dự báo" của bản cũ: thiết bị chỉ chạy theo ngưỡng
        self.fb.update({"PRE": None, "AI_PREDICT": None, "PRE_ACK": None})
        self.running = True
        threading.Thread(target=self._loop, name="engine", daemon=True).start()
        threading.Thread(target=self._background, name="weather-rf", daemon=True).start()
        if config.ZALO_COMMANDS_ENABLED and self.zalo.token:
            from commands import CommandHandler
            handler = CommandHandler(self)
            threading.Thread(target=handler.loop, name="zalo-commands", daemon=True).start()
        log.info(
            "Engine chạy: poll %ss | AUTO server: %s | Zalo: %s | LLM: %s | DRY_RUN: %s",
            config.POLL_INTERVAL_SEC, config.AUTO_CONTROL_ENABLED, self.zalo.configured,
            llm.status(), config.DRY_RUN,
        )

    def _loop(self):
        while self.running:
            started = time.time()
            try:
                self.tick()
            except Exception:  # không để 1 lỗi làm dừng cả hệ thống
                log.exception("Lỗi trong vòng lặp")
            time.sleep(max(0.5, config.POLL_INTERVAL_SEC - (time.time() - started)))

    def _background(self):
        """Thời tiết (15 phút/lần) + Random Forest (10 phút/lần): chạy riêng để không làm chậm vòng đọc cảm biến."""
        while self.running:
            now = time.time()
            try:
                if now - self.last_weather >= (WEATHER_EVERY if self.weather.updated else 120):
                    self.last_weather = now
                    if self.weather.refresh() and self.rf.result is None:
                        self.last_rf = 0  # có thời tiết lần đầu -> phân tích ngay
                if now - self.last_rf >= RF_EVERY and self.latest_data:
                    self.last_rf = now
                    with self.lock:
                        rows, data = list(self.recent), dict(self.latest_data)
                    self.rf.analyze(rows, data, now)
            except Exception:
                log.exception("Lỗi phân tích thời tiết / Random Forest")
            time.sleep(20)

    # ------------------------------------------------------------------- tick
    def tick(self):
        data = self.fb.get("/")
        now = time.time()
        if data is None:
            self.fail_count += 1
            if self.fail_count >= 6 and not self.offline_alerted:
                self.offline_alerted = True
                self.notify("📡 Mất kết nối tới Firebase hơn 30 giây. Kiểm tra Internet của máy chủ AI.")
            return
        if self.offline_alerted:
            self.notify("✅ Đã kết nối lại Firebase.")
        self.fail_count, self.offline_alerted = 0, False
        self.last_ok_ts = now

        values = {k: num(data.get(k)) for k in SENSOR_KEYS}
        values.update({k: 1 if num(data.get(k)) == 1 else 0 for k in DEVICE_KEYS})
        values["MODE"] = MODE_AUTO if num(data.get("MODE")) == MODE_AUTO else 0
        self.store.add_reading(now, values)
        prev = self.latest_values
        if prev is not None and any(prev.get(k) != values.get(k) for k in SENSOR_KEYS):
            self.hw_last_change = now
        self._track_esp_restart(data, now)
        self._check_esp_link(data, now)

        with self.lock:
            self.latest_data, self.latest_values = data, values
            self.recent.append({"ts": now, **values})
            self._detect_external_changes(values, now)
            self.prev_values = values

        # Máy chủ tự điều khiển (mặc định tắt — ESP32 điều khiển theo ngưỡng)
        updates, changes, safety_events = self.ctrl.step(data, now)
        if updates and self.fb.update(updates):
            for ch in changes:
                self.our_writes[ch["device"]] = (ch["value"], now, "auto")
                self.store.add_device_event(now, ch["device"], ch["value"], "auto", ch["reason"])
                self._queue_device(now, ch["device"], ch["value"], "auto", ch["reason"])

        # Phân tích + cảnh báo
        with self.lock:
            rows = list(self.recent)
        analysis = self.analyzer.evaluate(data, rows, now, hw_last_change=self.hw_last_change,
                                          hw_info=self.hardware_info(data, now))
        with self.lock:
            self.analysis = analysis
        self._process_findings(analysis["findings"], safety_events, now)
        self._flush_devices(now)

        self._housekeeping(now)

    @staticmethod
    def _merge_door(changes):
        """MO và DONG đổi cùng lúc -> chỉ báo 1 dòng."""
        out, door_ts = [], set()
        for ch in changes:
            if ch["device"] in ("MO", "DONG"):
                if ch.get("ts") in door_ts:
                    continue
                door_ts.add(ch.get("ts"))
                if ch["device"] == "DONG":
                    ch = {**ch, "device": "MO", "value": 1 - ch["value"]}
            out.append(ch)
        return out

    def _detect_external_changes(self, values, now):
        """Thiết bị/chế độ đổi mà không do server ghi.

        AUTO: firmware ESP32 tự bật/tắt theo ngưỡng -> báo kèm lý do.
        MANUAL: có người bấm trên web.
        """
        prev = self.prev_values
        if prev is None:
            return
        if values["MODE"] != prev["MODE"]:
            self.store.add_device_event(now, "MODE", values["MODE"], "external", "")
            self._queue_device(now, "MODE", values["MODE"], "external", "")

        auto = values["MODE"] == MODE_AUTO
        source = "esp_auto" if auto else "external"
        for dev in DEVICE_KEYS:
            if values[dev] == prev[dev]:
                continue
            ours = self.our_writes.get(dev)
            if ours and ours[0] == values[dev] and now - ours[1] < 60:
                self.our_writes.pop(dev, None)
                continue  # do server/Zalo vừa ghi, đã ghi nhận
            reason = explain_auto(dev, values[dev], self.latest_data) if auto else ""
            self.store.add_device_event(now, dev, values[dev], source, reason)
            self._queue_device(now, dev, values[dev], source, reason)

    # ------------------------------------------------- báo bật/tắt thiết bị
    def _queue_device(self, ts, device, value, source, reason):
        if source == "zalo":
            return  # lệnh Zalo đã được trả lời ngay trong khung chat
        notify = config.NOTIFY_DEVICE_CHANGES if source in ("esp_auto", "auto") else config.NOTIFY_MANUAL_CHANGES
        if notify:
            self.dev_batch.append({"ts": ts, "device": device, "value": value, "source": source, "reason": reason})

    def _flush_devices(self, now):
        """Báo MỌI lần bật/tắt: gom các thay đổi trong DEVICE_BATCH_SEC giây thành 1 tin.
        Thiết bị đảo liên tục (đã gửi >= 10 tin/giờ) thì giãn thành 5 phút/tin để không hết hạn mức Zalo."""
        if not self.dev_batch:
            return
        while self.dev_msg_times and self.dev_msg_times[0] < now - 3600:
            self.dev_msg_times.popleft()
        wait = config.DEVICE_BATCH_SEC * (5 if len(self.dev_msg_times) >= 10 else 1)
        if now - self.dev_batch[0]["ts"] < wait:
            return
        batch, self.dev_batch = self.dev_batch, []
        self.dev_msg_times.append(now)
        lines = []
        for ch in self._merge_door(batch):
            if ch["device"] == "MODE":
                text = f"⚙️ Chuyển chế độ {'AUTO 🤖' if ch['value'] == MODE_AUTO else 'MANUAL ✋'}"
            else:
                text = device_text(ch["device"], ch["value"])
            src = SOURCE_TEXT.get(ch["source"], ch["source"])
            lines.append(f"• {clock(ch['ts'])} {text} — {src}" + (f": {ch['reason']}" if ch["reason"] else ""))
        shown = lines[:15]
        if len(lines) > 15:
            shown.append(f"… và {len(lines) - 15} lần bật/tắt khác")
        title = "🔧 THIẾT BỊ BẬT/TẮT" + (f" ({len(lines)} lần)" if len(lines) > 1 else "")
        self.notify(title + "\n" + "\n".join(shown), important=True)

    # ------------------------------------------------- mạch ESP32 tắt / mở lại
    def _check_esp_link(self, data, now):
        """Báo Zalo NGAY khi mạch ESP32 tắt/mất kết nối và khi hoạt động lại (ưu tiên cao)."""
        esp_ts = num(data.get("ESP_TS"))
        if esp_ts:  # firmware có nhịp tim (2 giây/lần)
            # Quyết định theo lúc ESP_TS thay đổi (đồng hồ máy chủ) -> không bị lệch giờ giữa máy và Firebase.
            # Lần đọc đầu chưa biết: coi như vừa thấy, sau 60 giây không đổi mới là mất kết nối.
            if esp_ts != self.esp_ts_prev:
                self.esp_ts_prev, self.esp_ts_seen = esp_ts, now
            online = now - self.esp_ts_seen <= ESP_OFFLINE_SEC
            last_seen = esp_ts / 1000  # giờ Firebase của lần gửi cuối, chỉ để hiển thị
        elif self.hw_last_change:  # firmware cũ: dựa vào số liệu còn thay đổi
            last_seen = self.hw_last_change
            online = now - last_seen <= ESP_STALE_SEC
        else:
            return
        was = self.esp_online
        self.esp_online = online
        if was is None:  # lần đầu sau khi máy chủ khởi động
            if not online:
                self.esp_offline_since = last_seen
                self.store.add_device_event(now, "ESP_LINK", 0, "esp", "mất kết nối")
                self.notify(f"🔴 MẠCH ESP32 ĐANG TẮT / MẤT KẾT NỐI\nLần cuối nhận dữ liệu: {now_text(last_seen)}.\n"
                            "Kiểm tra nguồn điện và WiFi của bo mạch.", priority="high")
            return
        if was and not online:
            self.esp_offline_since = last_seen
            self.store.add_device_event(now, "ESP_LINK", 0, "esp", "mất kết nối")
            log.warning("ESP32 mất kết nối (lần cuối %s)", now_text(last_seen))
            long_ago = now - last_seen > 600  # máy chủ vừa bật lên mà ESP32 đã tắt từ trước
            self.notify(f"🔴 MẠCH ESP32 {'ĐANG' if long_ago else 'VỪA'} TẮT / MẤT KẾT NỐI\n"
                        f"Lần cuối nhận dữ liệu: {now_text(last_seen) if long_ago else clock(last_seen)}.\n"
                        "Có thể mất điện, mất WiFi hoặc bo mạch bị treo. Trong lúc này nhà nấm không được tự động "
                        "điều khiển.", priority="high")
        elif online and not was:
            # cùng một đồng hồ (Firebase hoặc máy chủ) cho cả 2 mốc
            off = last_seen - self.esp_offline_since if self.esp_offline_since else None
            self.store.add_device_event(now, "ESP_LINK", 1, "esp", "hoạt động lại")
            log.info("ESP32 hoạt động lại")
            reason = self.esp_restart_reason
            self.esp_restart_reason = None
            self.notify("🟢 MẠCH ESP32 ĐÃ HOẠT ĐỘNG LẠI"
                        + (f"\nĐã mất kết nối khoảng {duration_text(off)}." if off else "")
                        + (f"\nLý do khởi động: {reason}." if reason else "")
                        + "\n" + self.readings_line(data), priority="high")
            self.esp_offline_since = None

    def _track_esp_restart(self, data, now):
        """ESP_UP (giây chạy liên tục) giảm -> ESP32 vừa khởi động lại; ghi lại kèm lý do và báo Zalo."""
        up = num(data.get("ESP_UP"))
        if up is None:
            return
        prev, self.esp_up_prev = self.esp_up_prev, up
        if prev is None or up >= prev:
            return
        reason = RESET_REASONS.get(data.get("ESP_RST"), "không rõ")
        self.store.add_device_event(now, "ESP_RESTART", 1, "esp", reason)
        log.warning("ESP32 khởi động lại: %s", reason)
        if self.esp_online is False:
            self.esp_restart_reason = reason  # báo chung trong tin "đã hoạt động lại"
            return
        # Mất điện chớp nhoáng (chưa kịp bị coi là mất kết nối) -> vẫn báo
        self.notify(f"🔄 MẠCH ESP32 VỪA KHỞI ĐỘNG LẠI — lý do: {reason}.\n"
                    "Nếu lặp lại nhiều lần: kiểm tra nguồn điện cấp cho bo mạch và rơ-le.", priority="high")

    # ----------------------------------------------------------------- alerts
    def _process_findings(self, findings, events, now):
        to_send, resolved = [], []
        seen = set()
        with self.lock:
            for f in findings:
                seen.add(f["key"])
                a = self.active.get(f["key"])
                if a is None:
                    a = {"id": self.store.add_alert(f, sent=False), "finding": f,
                         "first": now, "last_sent": 0, "sent_sev": None, "missing": 0}
                    self.active[f["key"]] = a
                else:
                    a["missing"] = 0
                    if (f["severity"], f["title"]) != (a["finding"]["severity"], a["finding"]["title"]):
                        self.store.update_alert(a["id"], f)
                    a["finding"] = f

                if SEV_RANK[f["severity"]] < 2 or f["key"] in LINK_KEYS:
                    continue  # info: chỉ hiện trên web; mất kết nối ESP32 đã báo riêng
                escalated = a["sent_sev"] is not None and SEV_RANK[f["severity"]] > SEV_RANK[a["sent_sev"]]
                repeat_due = a["sent_sev"] is not None and now - a["last_sent"] >= config.ALERT_REPEAT_MIN * 60
                if a["sent_sev"] is None or escalated or repeat_due:
                    a["last_sent"], a["sent_sev"] = now, f["severity"]
                    to_send.append({**f, "repeat": repeat_due and not escalated, "_id": a["id"]})

            for key, a in list(self.active.items()):
                if key in seen:
                    continue
                a["missing"] += 1
                if a["missing"] >= 3:  # biến mất 3 lần liên tiếp mới coi là đã ổn
                    self.store.resolve_alert(a["id"])
                    del self.active[key]
                    if a["sent_sev"]:
                        resolved.append(a["finding"])

        for e in events:  # sự kiện tức thời (vd: ngắt bảo vệ bơm)
            # cùng loại sự kiện lặp lại liên tục (cảm biến hỏng) -> chỉ báo lại sau 2 x ALERT_REPEAT_MIN
            send = now - self.event_sent.get(e["key"], 0) >= config.ALERT_REPEAT_MIN * 120
            alert_id = self.store.add_alert(e, sent=False)
            self.store.resolve_alert(alert_id)
            if send:
                self.event_sent[e["key"]] = now
                to_send.append({**e, "_id": alert_id})

        if to_send or resolved:
            self.outbox.submit(self._send_alert_message, to_send, resolved)

    def _send_alert_message(self, new, resolved):
        try:
            crit = any(f["severity"] == "critical" for f in new)
            muted = time.time() < self.mute_until
            if muted and not crit:
                log.info("Đang tắt thông báo — bỏ qua %d cảnh báo", len(new))
                return
            lines = []
            if new:
                lines.append("🚨 CẢNH BÁO NGHIÊM TRỌNG" if crit else "⚠️ CẢNH BÁO NHÀ NẤM")
                for f in new:
                    tag = " (vẫn chưa xử lý)" if f.get("repeat") else ""
                    lines.append(f"{SEV_ICON[f['severity']]} {f['title']}{tag}\n   {f['detail']}")
            if resolved:
                lines.append("✅ Đã trở lại bình thường:")
                lines += [f"• {f['title']}" for f in resolved]
            lines.append(self.readings_line())
            text = "\n".join(lines)

            if new and llm.status()["enabled"]:
                advice = llm.ask(
                    "Có cảnh báo mới (xem 'canh_bao_moi'). Viết phần 'Nhận định AI' cho tin Zalo: "
                    "tối đa 4 dòng, mỗi dòng bắt đầu bằng '•', nêu nguyên nhân khả dĩ và việc nên làm ngay. "
                    "Không nhắc lại toàn bộ số liệu, không chào hỏi.",
                    {**self.context(), "canh_bao_moi": [
                        {k: v for k, v in f.items() if not k.startswith("_")} for f in new
                    ]},
                    effort="low", max_tokens=2000,
                )
                if advice:
                    text += "\n\n🧠 Nhận định AI:\n" + advice
            if self.zalo.send(text + f"\n🕒 {now_text()}", priority="high" if crit else "normal"):
                for f in new:
                    if f.get("_id"):
                        self.store.mark_alert_sent(f["_id"])
        except Exception:
            log.exception("Lỗi gửi cảnh báo")

    def notify(self, text, important=True, priority=None):
        """Gửi tin thông thường (không chặn vòng lặp). important=False -> ưu tiên thấp, dừng sớm khi gần hết hạn mức."""
        if priority != "high" and time.time() < self.mute_until:
            return
        self.outbox.submit(self.zalo.send, f"{text}\n🕒 {now_text()}", None,
                           priority or ("normal" if important else "low"))

    # --------------------------------------------------------------- helpers
    def readings_line(self, data=None):
        if data is not None:
            v = {k: num(data.get(k)) for k in SENSOR_KEYS}
            v["MODE"] = num(data.get("MODE"))
        else:
            v = self.latest_values or {}
        parts = [f"{ICONS[k]} {fmt(k, v.get(k))}" for k in SENSOR_KEYS]
        mode = "AUTO" if v.get("MODE") == MODE_AUTO else "MANUAL"
        return "📊 " + " | ".join(parts) + f"\n⚙️ Chế độ: {mode}"

    def rf_report(self):
        """Thời tiết + Random Forest dạng tin Zalo (tham khảo)."""
        lines = []
        weather = self.weather.text()
        if weather:
            lines.append(weather)
        if self.esp_online is False:
            lines.append("🌲 Random Forest: tạm dừng vì ESP32 mất kết nối (số liệu cũ).")
        else:
            lines += report_lines(self.rf.result)
        return lines

    def context(self, hours=1):
        """Dữ liệu gọn gửi cho LLM."""
        with self.lock:
            data = dict(self.latest_data or {})
            analysis = self.analysis or {}
        th = thresholds(data)
        rf = self.rf.result or {}
        return {
            "thoi_gian": now_text(),
            "che_do": "AUTO" if num(data.get("MODE")) == MODE_AUTO else "MANUAL",
            "cam_bien": {
                SENSORS[k]["name"]: {
                    "gia_tri": fmt(k, num(data.get(k))),
                    "danh_gia": (analysis.get("sensors", {}).get(k) or {}).get("text"),
                } for k in SENSOR_KEYS
            },
            "nguong_cai_dat": {k: v for k, v in th.items() if v is not None},
            "thiet_bi": {DEVICES[k]: ("BẬT" if num(data.get(k)) == 1 else "TẮT") for k in DEVICE_KEYS},
            "chi_so_tong": analysis.get("health"),
            # Hai chỉ số tách riêng, không trung bình hóa: môi trường (t, h, ánh sáng, ẩm giá thể) và nước cấp (pH, mặn)
            "chi_so_moi_truong": (analysis.get("indices") or {}).get("environment"),
            "chi_so_nuoc_cap": (analysis.get("indices") or {}).get("water"),
            "van_de_hien_tai": [
                {"muc": f["severity"], "tieu_de": f["title"], "chi_tiet": f["detail"]}
                for f in analysis.get("findings", [])
            ],
            "thoi_tiet_ngoai_troi": self.weather.text(),
            "random_forest_tham_khao": {
                "du_doan_3_gio_toi": rf.get("predictions"),
                "goi_y": rf.get("advice"),
                "ghi_chu": "Chỉ tham khảo; thiết bị bật/tắt theo ngưỡng cài đặt.",
            } if rf.get("predictions") else None,
            f"thong_ke_{hours}h": self.stats(hours),
        }

    def stats(self, hours=24):
        rows = self.store.readings_since(time.time() - hours * 3600)
        out = {}
        for k in SENSOR_KEYS:
            vals = [r[k] for r in rows if r.get(k) is not None]
            if vals:
                out[SENSORS[k]["name"]] = {
                    "min": round(min(vals), 2), "max": round(max(vals), 2),
                    "tb": round(sum(vals) / len(vals), 2), "don_vi": SENSORS[k]["unit"],
                }
        if rows:
            out["ty_le_thoi_gian_bat_thiet_bi_%"] = {
                DEVICES[d]: round(100 * sum(1 for r in rows if r.get(d) == 1) / len(rows))
                for d in ("BOM", "DEN", "QUAT", "SUONG")
            }
        out["so_mau"] = len(rows)
        return out

    def build_report(self, hours=24):
        """Báo cáo tổng hợp: LLM viết nếu có, không thì dùng mẫu."""
        ctx = self.context(hours=hours)
        alerts = [a for a in self.store.alerts(200) if a["ts"] >= time.time() - hours * 3600]
        ctx["canh_bao_trong_ky"] = [
            {"luc": now_text(a["ts"]), "muc": a["severity"], "tieu_de": a["title"]} for a in alerts[:30]
        ]
        text = llm.ask(
            f"Viết BÁO CÁO TÌNH HÌNH NHÀ NẤM {hours} GIỜ QUA để gửi Zalo (tối đa ~1500 ký tự): "
            "1) đánh giá tổng quan + điểm sức khỏe, 2) diễn biến các chỉ số đáng chú ý (min/max/TB), "
            "3) thiết bị hoạt động thế nào, 4) các cảnh báo đã xảy ra, 5) 2-4 khuyến nghị cụ thể.",
            ctx, effort="medium", max_tokens=8000,
        )
        if text:
            return text, True
        return self._template_report(ctx, hours, alerts), False

    def _template_report(self, ctx, hours, alerts):
        with self.lock:
            idx = (self.analysis or {}).get("indices")
        lines = [f"📋 BÁO CÁO NHÀ NẤM {hours}H QUA", *indices.summary_lines(idx)]
        st = ctx.get(f"thong_ke_{hours}h", {})
        for name, s in st.items():
            if isinstance(s, dict) and "min" in s:
                unit = s["don_vi"] if s["don_vi"] in ("", "°C", "%") else " " + s["don_vi"]
                lines.append(f"• {name}: TB {s['tb']:g}{unit} (thấp {s['min']:g} – cao {s['max']:g})")
        on = st.get("ty_le_thoi_gian_bat_thiet_bi_%")
        if on:
            lines.append("⚙️ Thời gian bật: " + ", ".join(f"{k} {v}%" for k, v in on.items()))
        lines.append(f"🔔 Cảnh báo trong kỳ: {len(alerts)}")
        for a in alerts[:5]:
            lines.append(f"   {SEV_ICON.get(a['severity'], '•')} {a['title']} ({now_text(a['ts'])})")
        issues = ctx.get("van_de_hien_tai") or []
        lines.append("Hiện tại: " + ("; ".join(i["tieu_de"] for i in issues[:3]) if issues else "ổn định ✅"))
        return "\n".join(lines)

    def hardware_info(self, data, now):
        """Thông tin nguồn/WiFi của ESP32 (cần firmware gửi ESP_UP, ESP_RST, ESP_RSSI)."""
        restarts = [e for e in self.store.device_events(200)
                    if e["device"] == "ESP_RESTART" and e["ts"] >= now - 86400]
        return {
            "uptime_sec": num(data.get("ESP_UP")),
            "reset_reason": RESET_REASONS.get(data.get("ESP_RST")) if data.get("ESP_RST") else None,
            "rssi": num(data.get("ESP_RSSI")),
            "restarts_1h": sum(1 for e in restarts if e["ts"] >= now - 3600),
            "restarts_24h": len(restarts),
            "last_restart_ts": restarts[0]["ts"] if restarts else None,
            "last_restart_reason": restarts[0]["reason"] if restarts else None,
        }

    def _housekeeping(self, now):
        if now - self.last_prune > 3600:
            self.last_prune = now
            self.store.prune()
        hour = config.DAILY_REPORT_HOUR
        today = dt.date.today()
        if 0 <= hour <= 23 and dt.datetime.now().hour == hour and self.last_daily != today:
            if now - self.started_at > 600:  # tránh gửi ngay khi vừa khởi động
                self.last_daily = today
                self.outbox.submit(self._send_daily_report)

        every = config.PERIODIC_REPORT_SEC
        # Tin định kỳ đầu tiên sau khi khởi động 3 phút (đã có thời tiết + kết quả Random Forest)
        if every > 0 and now - self.last_periodic >= every and now - self.started_at >= 180 and not self.periodic_busy:
            if now >= self.mute_until:
                self.last_periodic, self.periodic_busy = now, True
                self.outbox.submit(self._send_periodic_report)

    def _send_periodic_report(self):
        try:
            with self.lock:
                analysis, data = self.analysis, self.latest_data or {}
            if not analysis:
                return
            devices = " | ".join(
                f"{name} {'🟢' if num(data.get(key)) == 1 else '⚪'}"
                for key, name in (("BOM", "Bơm"), ("DEN", "Đèn"), ("QUAT", "Quạt"), ("SUONG", "Sương"))
            )
            door = "Mở" if num(data.get("MO")) == 1 else "Đóng" if num(data.get("DONG")) == 1 else "—"
            lines = [
                "🍄 NHÀ NẤM — BÁO CÁO ĐỊNH KỲ",
                *indices.summary_lines(analysis.get("indices")),
                self.readings_line(),
                f"🔧 {devices} | Màn che: {door}",
            ]
            important = [f for f in analysis["findings"] if SEV_RANK[f["severity"]] >= 2]
            if important:
                lines.append("⚠️ " + "; ".join(f["title"] for f in important[:3]))
            lines += ["", *self.rf_report()]
            self.zalo.send("\n".join(lines) + f"\n🕒 {now_text()}", priority="low")
        except Exception:
            log.exception("Lỗi gửi báo cáo định kỳ")
        finally:
            self.periodic_busy = False

    def _send_daily_report(self):
        try:
            text, _ = self.build_report(24)
            self.zalo.send(f"☀️ Báo cáo buổi sáng\n{text}\n🕒 {now_text()}")
        except Exception:
            log.exception("Lỗi gửi báo cáo ngày")

    # ------------------------------------------------------------- control API
    def _verify_write(self, updates, tries=3):
        """Firmware cũ ghi lại MODE/SET_* mỗi 0.7 giây nên có thể đè mất giá trị vừa ghi:
        chờ 2.5 giây đọc lại, bị đè thì ghi lại."""
        if config.DRY_RUN:
            return True
        for _ in range(tries):
            time.sleep(2.5)
            current = self.fb.get("/")
            if current is None:
                continue
            lost = {k: v for k, v in updates.items() if num(current.get(k)) != num(v)}
            if not lost:
                return True
            log.warning("ESP32 ghi đè %s, ghi lại", list(lost))
            self.fb.update(lost)
        return False

    def set_devices(self, updates, source="zalo"):
        """Ghi lệnh điều khiển (từ Zalo). Trả (ok, thông báo lỗi)."""
        with self.lock:
            data = self.latest_data or {}
        if "MODE" not in updates and num(data.get("MODE")) == MODE_AUTO:
            return False, "Đang ở chế độ AUTO. Gửi /manual để chuyển sang thủ công trước."
        if not self.fb.update(updates):
            return False, "Không ghi được Firebase, thử lại sau."
        if "MODE" in updates and not self._verify_write(updates):
            return False, "ESP32 liên tục ghi đè chế độ cũ — cần nạp firmware mới (không ghi SET_* và MODE)."
        now = time.time()
        with self.lock:  # cập nhật ngay, không chờ lần đọc Firebase kế tiếp
            self.latest_data = {**(self.latest_data or {}), **updates}
        for dev, val in updates.items():
            if dev in DEVICE_KEYS:
                self.our_writes[dev] = (val, now, source)
                self.store.add_device_event(now, dev, val, source, "")
        if "MODE" in updates:
            with self.lock:
                if self.prev_values is not None:
                    self.prev_values = {**self.prev_values, "MODE": updates["MODE"]}
            self.store.add_device_event(now, "MODE", updates["MODE"], source, "")
        return True, ""

    def snapshot(self):
        with self.lock:
            return {
                "data": self.latest_data,
                "analysis": self.analysis,
                "last_ok_ts": self.last_ok_ts,
                "muted_until": self.mute_until if self.mute_until > time.time() else None,
                "rf": self.rf.result or self.rf.info,
                "weather": self.weather.status(),
                "esp_online": self.esp_online,
                "hardware": {
                    "last_change_ts": self.hw_last_change,
                    "esp_ts": (self.latest_data or {}).get("ESP_TS"),
                    **self.hardware_info(self.latest_data or {}, time.time()),
                },
            }
