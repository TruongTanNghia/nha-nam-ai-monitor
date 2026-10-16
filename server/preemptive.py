"""Bật thiết bị sớm theo dự báo (chế độ AUTO).

ESP32 làm chủ chế độ AUTO nên máy chủ không bật thẳng thiết bị, mà ghi một "yêu cầu bật sớm" vào
Firebase: PRE = {BOM, DEN, QUAT, SUONG, MO, SEQ}. Firmware cộng yêu cầu này vào luật ngưỡng:
  QUAT = (t > SET_T) || PRE.QUAT ...  — các khóa an toàn (pH lệch, nước mặn) vẫn thắng.
SEQ tăng mỗi lần ghi; firmware bỏ qua yêu cầu nếu SEQ đứng yên quá 60 giây
(máy chủ tắt/mất mạng -> ESP32 tự quay về chạy theo ngưỡng).

Khi nào xin bật sớm: dự báo còn <= PREDICT_LEAD_MIN phút nữa chạm ngưỡng và xu hướng đủ tin cậy.
Khi nào thôi: chỉ số đã chạm ngưỡng (ESP32 tự giữ thiết bị), dự báo không còn, hoặc quá PREDICT_MAX_ON_MIN.
"""
import logging
import time

import config
from sensors import MODE_AUTO, num

log = logging.getLogger("predict")

# Cảm biến -> thiết bị firmware bật khi vượt ngưỡng (trời tối dưới ngưỡng: bật đèn + mở màn che)
DEVICE_FOR = {"t": ["QUAT"], "h": ["SUONG"], "GT_AMDAT": ["BOM"], "ANHSANG": ["DEN", "MO"]}
NAMES = {"QUAT": "quạt", "SUONG": "phun sương", "BOM": "máy bơm", "DEN": "đèn", "MO": "màn che (mở)"}
REFRESH_SEC = 15  # ghi lại SEQ định kỳ để firmware biết máy chủ còn sống


class Preemptive:
    def __init__(self):
        self.active = {}          # thiết bị -> {"since", "reason", "eta"}
        self.seq = int(time.time()) % 1_000_000_000
        self.last_write = 0
        self.last_state = None
        self.enabled = False
        self.reason_off = None

    def is_enabled(self, data, analysis):
        if not config.PREDICTIVE_ENABLED:
            return False, "Tắt trong cấu hình máy chủ (PREDICTIVE_ENABLED=false)"
        if num(data.get("AI_PREDICT"), 1) == 0:
            return False, "Đã tắt trên web"
        if num(data.get("MODE")) != MODE_AUTO:
            return False, "Chỉ hoạt động ở chế độ AUTO"
        if any(f["key"] in ("stale_all", "esp_offline") for f in analysis["findings"]):
            return False, "ESP32 mất kết nối"
        return True, None

    def step(self, data, analysis, now=None):
        """Trả (payload ghi vào Firebase PRE hoặc None, thiết bị vừa bật sớm, thiết bị vừa thôi)."""
        now = now or time.time()
        self.enabled, self.reason_off = self.is_enabled(data, analysis)
        forecasts = analysis.get("forecasts") or {}
        # Firmware vẫn khóa bơm/phun sương khi nước không đạt -> không xin bật sớm những thiết bị đó
        locks = (analysis.get("indices") or {}).get("water", {}).get("locks") or []
        locked = {"BOM"} if locks else set()
        if any("phun sương" in lock["devices"] for lock in locks):
            locked.add("SUONG")

        want = {}
        if self.enabled:
            for sensor, devs in DEVICE_FOR.items():
                f = forecasts.get(sensor)
                if not f:
                    continue
                for dev in devs:
                    if dev in locked:
                        continue
                    for e in f["events"]:
                        if e["kind"] != "trigger" or e["eta_min"] is None:
                            continue
                        start_now = e["eta_min"] <= config.PREDICT_LEAD_MIN and f["r2"] >= config.PREDICT_MIN_R2
                        # đang bật sớm: giữ tới khi chạm ngưỡng (event đổi sang "active") hoặc dự báo xa dần
                        keep = dev in self.active and e["eta_min"] <= config.PREDICT_LEAD_MIN * 2
                        if start_now or keep:
                            want[dev] = e
            for dev in list(want):
                since = self.active.get(dev, {}).get("since", now)
                if now - since > config.PREDICT_MAX_ON_MIN * 60:
                    want.pop(dev)  # bật sớm quá lâu mà chưa chạm ngưỡng -> thôi, để ESP32 theo ngưỡng

        started = [dev for dev in want if dev not in self.active]
        stopped = [dev for dev in self.active if dev not in want]
        for dev in stopped:
            self.active.pop(dev)
        for dev, e in want.items():
            if dev in self.active:
                self.active[dev].update(reason=e["text"], eta=e["eta_min"])
            else:
                self.active[dev] = {"since": now, "reason": e["text"], "eta": e["eta_min"]}

        state = {dev: int(dev in self.active) for dev in NAMES}
        changed = state != self.last_state
        refresh = any(state.values()) and now - self.last_write >= REFRESH_SEC
        if not (changed or refresh):
            return None, started, stopped
        self.seq = (self.seq + 1) % 1_000_000_000
        self.last_state, self.last_write = state, now
        return {**state, "SEQ": self.seq}, started, stopped

    def status(self, data):
        # Firmware mới gửi kèm PRE_ACK (SEQ đã nhận) -> biết bo mạch đã hỗ trợ bật sớm
        firmware_ok = data.get("PRE_ACK") is not None
        return {
            "enabled": self.enabled,
            "reason_off": self.reason_off,
            "lead_min": config.PREDICT_LEAD_MIN,
            "firmware_ok": firmware_ok,
            "active": {dev: {**info, "name": NAMES[dev]} for dev, info in self.active.items()},
        }
