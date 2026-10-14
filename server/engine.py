"""Vòng lặp chính: đọc Firebase -> lưu lịch sử -> tự điều khiển -> phân tích -> cảnh báo Zalo."""
import datetime as dt
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import config
import llm
from analyzer import SEV_RANK, Analyzer
from controller import AutoController, explain_auto
from firebase_client import Firebase
from sensors import DEVICE_KEYS, DEVICES, MODE_AUTO, SENSOR_KEYS, SENSORS, fmt, num, thresholds
from storage import Storage
from zalo_bot import ZaloBot

log = logging.getLogger("engine")

ICONS = {"t": "🌡", "h": "💧", "ANHSANG": "☀️", "GT_AMDAT": "🌱", "GT_DOMAN": "🧂", "GT_PH": "⚗️"}
SEV_ICON = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}


def now_text(ts=None):
    return dt.datetime.fromtimestamp(ts or time.time()).strftime("%H:%M %d/%m/%Y")


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
        self.device_notified = {}  # thiết bị -> lần báo Zalo gần nhất
        self.last_prune = 0
        self.last_daily = None
        self.mute_until = 0
        self.last_periodic = 0
        self.periodic_busy = False
        self.periodic_ai_text = None
        self.periodic_ai_ts = 0
        self.periodic_ai_keys = frozenset()
        self.started_at = time.time()
        self.running = False

    # ------------------------------------------------------------------ setup
    def start(self):
        self.store.resolve_all_active()
        since = time.time() - 7200
        for row in self.store.readings_since(since):
            self.recent.append(row)
        self.analyzer.maybe_train(self.store.readings_since(time.time() - 86400), time.time())
        self.running = True
        threading.Thread(target=self._loop, name="engine", daemon=True).start()
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

        with self.lock:
            self.latest_data, self.latest_values = data, values
            self.recent.append({"ts": now, **values})
            self._detect_external_changes(values, now)
            self.prev_values = values

        # Tự động điều khiển
        updates, changes, safety_events = self.ctrl.step(data, now)
        if updates and self.fb.update(updates):
            lines = []
            for ch in changes:
                self.our_writes[ch["device"]] = (ch["value"], now, "auto")
                self.store.add_device_event(now, ch["device"], ch["value"], "auto", ch["reason"])
            for ch in self._merge_door(changes):
                lines.append(f"• {device_text(ch['device'], ch['value'])} — {ch['reason']}")
            if lines and config.NOTIFY_DEVICE_CHANGES:
                self.notify("🤖 Máy chủ AI tự động điều khiển:\n" + "\n".join(lines), important=False)

        # Phân tích + cảnh báo
        with self.lock:
            rows = list(self.recent)
        analysis = self.analyzer.evaluate(data, rows, now)
        with self.lock:
            self.analysis = analysis
        self._process_findings(analysis["findings"], safety_events, now)

        self._housekeeping(now)

    @staticmethod
    def _merge_door(changes):
        """MO và DONG đổi cùng lúc -> chỉ báo 1 dòng."""
        out, door_done = [], False
        for ch in changes:
            if ch["device"] in ("MO", "DONG"):
                if door_done:
                    continue
                door_done = True
                if ch["device"] == "DONG":
                    ch = {**ch, "device": "MO", "value": 1 - ch["value"]}
            out.append(ch)
        return out

    def _detect_external_changes(self, values, now):
        """Thiết bị/chế độ đổi mà không do server ghi.

        AUTO: firmware ESP32 tự bật/tắt theo ngưỡng -> báo kèm lý do.
        MANUAL: có người điều khiển từ web.
        """
        prev = self.prev_values
        if prev is None:
            return
        parts = []
        if values["MODE"] != prev["MODE"]:
            mode = "AUTO 🤖" if values["MODE"] == MODE_AUTO else "MANUAL ✋"
            self.store.add_device_event(now, "MODE", values["MODE"], "external", "")
            if config.NOTIFY_MANUAL_CHANGES:
                parts.append(f"⚙️ Chế độ chuyển sang {mode}")

        auto = values["MODE"] == MODE_AUTO
        source = "esp_auto" if auto else "external"
        changed = []
        for dev in DEVICE_KEYS:
            if values[dev] == prev[dev]:
                continue
            ours = self.our_writes.get(dev)
            if ours and ours[0] == values[dev] and now - ours[1] < 60:
                self.our_writes.pop(dev, None)
                continue  # do server/Zalo vừa ghi, đã ghi nhận
            reason = explain_auto(dev, values[dev], self.latest_data) if auto else ""
            self.store.add_device_event(now, dev, values[dev], source, reason)
            changed.append({"device": dev, "value": values[dev], "reason": reason})

        # ESP32 không có vùng trễ -> thiết bị có thể đảo liên tục: mỗi thiết bị chỉ báo 1 lần / N phút
        lines = []
        for ch in self._merge_door(changed):
            key = "MO" if ch["device"] in ("MO", "DONG") else ch["device"]
            if now - self.device_notified.get(key, 0) < config.DEVICE_NOTIFY_MIN * 60:
                continue
            self.device_notified[key] = now
            lines.append(f"• {device_text(ch['device'], ch['value'])}" + (f" — {ch['reason']}" if ch["reason"] else ""))
        if lines and (config.NOTIFY_DEVICE_CHANGES if auto else config.NOTIFY_MANUAL_CHANGES):
            title = "🤖 ESP32 tự động điều khiển:" if auto else "👤 Điều khiển thủ công từ web:"
            parts.append(title + "\n" + "\n".join(lines))
        if parts:
            self.notify("\n".join(parts), important=False)

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

                if SEV_RANK[f["severity"]] < 2:
                    continue  # info: chỉ hiện trên web
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
            if self.zalo.send(text + f"\n🕒 {now_text()}"):
                for f in new:
                    if f.get("_id"):
                        self.store.mark_alert_sent(f["_id"])
        except Exception:
            log.exception("Lỗi gửi cảnh báo")

    def notify(self, text, important=True):
        """Gửi tin thông thường (không chặn vòng lặp)."""
        if not important and time.time() < self.mute_until:
            return
        self.outbox.submit(self.zalo.send, f"{text}\n🕒 {now_text()}")

    # --------------------------------------------------------------- helpers
    def readings_line(self):
        v = self.latest_values or {}
        parts = [f"{ICONS[k]} {fmt(k, v.get(k))}" for k in SENSOR_KEYS]
        mode = "AUTO" if v.get("MODE") == MODE_AUTO else "MANUAL"
        return "📊 " + " | ".join(parts) + f"\n⚙️ Chế độ: {mode}"

    def context(self, hours=1):
        """Dữ liệu gọn gửi cho LLM."""
        with self.lock:
            data = dict(self.latest_data or {})
            analysis = self.analysis or {}
        th = thresholds(data)
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
            "diem_suc_khoe": analysis.get("health"),
            "van_de_hien_tai": [
                {"muc": f["severity"], "tieu_de": f["title"], "chi_tiet": f["detail"]}
                for f in analysis.get("findings", [])
            ],
            "du_bao_30_phut": analysis.get("forecasts"),
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
        health = ctx.get("diem_suc_khoe") or {}
        lines = [f"📋 BÁO CÁO NHÀ NẤM {hours}H QUA", f"💚 Sức khỏe: {health.get('score', '—')}/100 ({health.get('label', '—')})"]
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

    def _housekeeping(self, now):
        since_train = now - self.analyzer.last_train
        if since_train >= (120 if self.analyzer.model is None else 1800):
            self.analyzer.maybe_train(self.store.readings_since(now - 86400), now)
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
        if every > 0 and now - self.last_periodic >= every and not self.periodic_busy:
            if now >= self.mute_until:
                self.last_periodic, self.periodic_busy = now, True
                self.outbox.submit(self._send_periodic_report)

    def _periodic_ai(self, analysis):
        """Nhận định AI cho tin định kỳ: viết lại mỗi PERIODIC_AI_MIN phút hoặc khi danh sách vấn đề đổi."""
        if not llm.status()["enabled"]:
            return None
        keys = frozenset(f["key"] for f in analysis["findings"])
        stale = time.time() - self.periodic_ai_ts >= config.PERIODIC_AI_MIN * 60
        if self.periodic_ai_text is None or stale or keys != self.periodic_ai_keys:
            text = llm.ask(
                "Viết 'Nhận định AI' cho tin Zalo định kỳ về nhà nấm: tối đa 4 dòng, mỗi dòng bắt đầu bằng '•'. "
                "Nêu tình trạng chung, vấn đề đáng lo nhất (nếu có) và 1-2 việc nên làm. Không chào hỏi, "
                "không liệt kê lại toàn bộ số liệu.",
                self.context(), effort="low", max_tokens=1500,
            )
            if text:
                self.periodic_ai_text, self.periodic_ai_ts, self.periodic_ai_keys = text, time.time(), keys
        return self.periodic_ai_text

    def _send_periodic_report(self):
        try:
            with self.lock:
                analysis, data = self.analysis, self.latest_data or {}
            if not analysis:
                return
            health = analysis["health"]
            devices = " | ".join(
                f"{name} {'🟢' if num(data.get(key)) == 1 else '⚪'}"
                for key, name in (("BOM", "Bơm"), ("DEN", "Đèn"), ("QUAT", "Quạt"), ("SUONG", "Sương"))
            )
            door = "Mở" if num(data.get("MO")) == 1 else "Đóng" if num(data.get("DONG")) == 1 else "—"
            lines = [
                f"🍄 NHÀ NẤM — {health['score']}/100 ({health['label']})",
                self.readings_line(),
                f"🔧 {devices} | Màn che: {door}",
            ]
            important = [f for f in analysis["findings"] if SEV_RANK[f["severity"]] >= 2]
            if important:
                lines.append("⚠️ " + "; ".join(f["title"] for f in important[:3]))
            ai = self._periodic_ai(analysis)
            if ai:
                age = int((time.time() - self.periodic_ai_ts) / 60)
                lines.append(f"\n🧠 Nhận định AI ({'vừa cập nhật' if age < 1 else f'{age} phút trước'}):\n{ai}")
            self.zalo.send("\n".join(lines) + f"\n🕒 {now_text()}")
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
    def set_devices(self, updates, source="zalo"):
        """Ghi lệnh điều khiển (từ Zalo). Trả (ok, thông báo lỗi)."""
        with self.lock:
            data = self.latest_data or {}
        if "MODE" not in updates and num(data.get("MODE")) == MODE_AUTO:
            return False, "Đang ở chế độ AUTO. Gửi /manual để chuyển sang thủ công trước."
        if not self.fb.update(updates):
            return False, "Không ghi được Firebase, thử lại sau."
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
            }
