"""Tự động bật/tắt thiết bị theo ngưỡng từ máy chủ AI.

MẶC ĐỊNH TẮT (AUTO_CONTROL_ENABLED=false): firmware ESP32 đã tự chạy chế độ AUTO và ghi trạng thái
thiết bị lên Firebase — nếu server cũng điều khiển thì hai bên sẽ ghi đè nhau.
Chỉ bật khi firmware KHÔNG tự điều khiển. Luật giống firmware, bổ sung:
- Vùng trễ (hysteresis) để thiết bị không bật/tắt liên tục quanh ngưỡng.
- Thời gian tối thiểu giữa 2 lần đảo trạng thái.
- Giới hạn thời gian chạy liên tục của bơm/phun sương, sau đó nghỉ bắt buộc.
- Cảm biến lỗi -> tắt thiết bị liên quan thay vì chạy theo số liệu sai.

`explain_auto` dùng cả khi tắt: giải thích vì sao ESP32 vừa bật/tắt thiết bị.
"""
import logging
import time

import config
from sensors import MODE_AUTO, fmt, is_valid, lname, num, thresholds

log = logging.getLogger("controller")

WATER_LIMITS = {"BOM": config.PUMP_MAX_ON_MIN, "SUONG": config.MIST_MAX_ON_MIN}


def _compare(key, value, setting):
    sign = "<" if value < setting else ">" if value > setting else "="
    return f"{lname(key)} {fmt(key, value)} {sign} ngưỡng {fmt(key, setting)}"


def _light_reason(light, setting):
    return _compare("ANHSANG", light, setting)


def _interlock_reason(data, th):
    """Điều kiện ép tắt của firmware: {'BOM': lý do, 'SUONG': lý do}."""
    out = {}
    ph, sal = num(data.get("GT_PH")), num(data.get("GT_DOMAN"))
    lo, hi = th["SET_PHLOW"] or 0, th["SET_PHHIGH"] or 0
    if ph is not None and (ph < lo or ph > hi):
        out["BOM"] = out["SUONG"] = f"pH {fmt('GT_PH', ph)} ngoài khoảng {lo:g}–{hi:g}"
    elif sal is not None and th["SET_DOMAN"] is not None and sal > th["SET_DOMAN"]:
        out["BOM"] = _compare("GT_DOMAN", sal, th["SET_DOMAN"]) + " — ngắt bơm"
    return out


def explain_auto(device, value, data):
    """Giải thích vì sao firmware ESP32 (chế độ AUTO) vừa bật/tắt thiết bị."""
    th = thresholds(data)
    v = {k: num(data.get(k)) for k in ("t", "h", "ANHSANG", "GT_AMDAT")}
    if device in ("BOM", "SUONG") and not value:
        reason = _interlock_reason(data, th).get(device)
        if reason:
            return reason
    pairs = {
        "BOM": ("GT_AMDAT", "SET_AMDAT"), "SUONG": ("h", "SET_H"),
        "QUAT": ("t", "SET_T"), "DEN": ("ANHSANG", "SET_AS"),
        "MO": ("ANHSANG", "SET_AS"), "DONG": ("ANHSANG", "SET_AS"),
    }
    sensor, setting = pairs.get(device, (None, None))
    if sensor and v[sensor] is not None and th[setting] is not None:
        return _compare(sensor, v[sensor], th[setting])
    return ""


class AutoController:
    def __init__(self):
        self.last_toggle = {}   # device -> ts
        self.on_since = {}      # device -> ts bắt đầu bật
        self.lockout_until = {}  # device -> ts hết thời gian nghỉ bắt buộc

    def _hysteresis(self, current_on, value, on_when_below, threshold, band):
        """Trả trạng thái mong muốn (0/1) theo ngưỡng có vùng trễ."""
        if on_when_below:
            if value < threshold:
                return 1
            if value >= threshold + band:
                return 0
        else:
            if value > threshold:
                return 1
            if value <= threshold - band:
                return 0
        return current_on  # trong vùng trễ: giữ nguyên

    def step(self, data, now=None):
        """Tính lệnh điều khiển. Trả (updates: dict, changes: list[dict], alerts: list[dict])."""
        now = now or time.time()
        if num(data.get("MODE")) != MODE_AUTO or not config.AUTO_CONTROL_ENABLED:
            self.on_since.clear()
            return {}, [], []

        th = thresholds(data)
        current = {k: 1 if num(data.get(k)) == 1 else 0 for k in ("BOM", "DEN", "QUAT", "SUONG", "MO", "DONG")}
        for dev, on in current.items():
            if on:
                self.on_since.setdefault(dev, now)
            else:
                self.on_since.pop(dev, None)

        def value(key):
            v = num(data.get(key))
            return v if is_valid(key, v) else None

        desired, reasons, alerts = {}, {}, []

        rules = (
            ("BOM", "GT_AMDAT", "SET_AMDAT", True, config.HYST_SOIL),
            ("SUONG", "h", "SET_H", True, config.HYST_HUMIDITY),
            ("QUAT", "t", "SET_T", False, config.HYST_TEMP),
            ("DEN", "ANHSANG", "SET_AS", True, None),
        )
        for dev, sensor, setting, below, band in rules:
            v, s = value(sensor), th[setting]
            if s is None:
                continue
            if v is None:
                desired[dev] = 0
                reasons[dev] = f"cảm biến {lname(sensor)} lỗi — tắt để an toàn"
                continue
            if band is None:
                band = max(50, s * config.HYST_LIGHT_RATIO)
            desired[dev] = self._hysteresis(current[dev], v, below, s, band)
            sign = "<" if v < s else ">" if v > s else "="
            reasons[dev] = f"{lname(sensor)} {fmt(sensor, v)} {sign} ngưỡng {fmt(sensor, s)}"

        # Khóa an toàn giống firmware ESP32: mặn cao -> ngắt bơm; pH ngoài khoảng -> ngắt bơm + phun sương
        interlock = _interlock_reason(data, th)
        for dev in ("BOM", "SUONG"):
            reason = interlock.get(dev)
            if reason and desired.get(dev) == 1:
                desired[dev], reasons[dev] = 0, reason

        # Màn che theo ánh sáng (giống firmware): sáng <= ngưỡng -> MỞ, sáng hơn ngưỡng -> ĐÓNG
        light = value("ANHSANG")
        if light is not None and th["SET_AS"] is not None:
            is_open = light <= th["SET_AS"]
            desired["MO"], desired["DONG"] = (1, 0) if is_open else (0, 1)
            reasons["MO"] = reasons["DONG"] = _light_reason(light, th["SET_AS"])

        # Giới hạn chạy liên tục cho thiết bị dùng nước
        for dev, limit_min in WATER_LIMITS.items():
            if now < self.lockout_until.get(dev, 0):
                if desired.get(dev) == 1:
                    desired[dev] = 0
                    left = (self.lockout_until[dev] - now) / 60
                    reasons[dev] = f"đang nghỉ bắt buộc, còn {left:.0f} phút"
                continue
            started = self.on_since.get(dev)
            if current[dev] and started and now - started >= limit_min * 60 and desired.get(dev, 1) == 1:
                desired[dev] = 0
                self.lockout_until[dev] = now + config.WATER_LOCKOUT_MIN * 60
                name = "Máy bơm" if dev == "BOM" else "Phun sương"
                reasons[dev] = f"chạy quá {limit_min:g} phút liên tục — ngắt bảo vệ"
                alerts.append({
                    "key": f"safety_{dev}", "severity": "critical", "category": "device",
                    "title": f"{name} bị ngắt bảo vệ",
                    "detail": (
                        f"{name} chạy liên tục {limit_min:g} phút mà chưa đạt ngưỡng. Hệ thống đã TẮT và "
                        f"cho nghỉ {config.WATER_LOCKOUT_MIN:g} phút. Kiểm tra nguồn nước, đường ống, cảm biến."
                    ),
                    "sensor": None,
                })

        updates, changes = {}, []
        for dev, want in desired.items():
            if want == current[dev]:
                continue
            last = self.last_toggle.get(dev, 0)
            is_safety = "bảo vệ" in reasons.get(dev, "") or "lỗi" in reasons.get(dev, "")
            if now - last < config.MIN_TOGGLE_SEC and not is_safety:
                continue
            updates[dev] = want
            changes.append({"device": dev, "value": want, "reason": reasons.get(dev, "")})

        # Màn che luôn ghi theo cặp để không bao giờ MO=DONG=1
        if "MO" in updates or "DONG" in updates:
            updates["MO"], updates["DONG"] = desired["MO"], desired["DONG"]

        for dev in updates:
            self.last_toggle[dev] = now
        return updates, changes, alerts
