"""Tự động bật/tắt thiết bị theo ngưỡng (chế độ AUTO) — chạy trên server 24/7.

So với logic cũ trong trình duyệt, bổ sung:
- Vùng trễ (hysteresis) để thiết bị không bật/tắt liên tục quanh ngưỡng.
- Thời gian tối thiểu giữa 2 lần đảo trạng thái.
- Giới hạn thời gian chạy liên tục của bơm/phun sương, sau đó nghỉ bắt buộc.
- Cảm biến lỗi -> tắt thiết bị liên quan thay vì chạy theo số liệu sai.
"""
import logging
import time

import config
from sensors import MODE_AUTO, fmt, is_valid, lname, num, thresholds

log = logging.getLogger("controller")

WATER_LIMITS = {"BOM": config.PUMP_MAX_ON_MIN, "SUONG": config.MIST_MAX_ON_MIN}


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

        # Màn che: mở khi độ mặn cao hoặc pH ngoài khoảng (giữ nguyên logic gốc của hệ thống)
        sal, ph = value("GT_DOMAN"), value("GT_PH")
        need_open = (
            (sal is not None and th["SET_DOMAN"] is not None and sal > th["SET_DOMAN"])
            or (ph is not None and th["SET_PHLOW"] is not None and ph < th["SET_PHLOW"])
            or (ph is not None and th["SET_PHHIGH"] is not None and ph > th["SET_PHHIGH"])
        )
        if sal is not None or ph is not None:
            desired["MO"], desired["DONG"] = (1, 0) if need_open else (0, 1)
            reasons["MO"] = reasons["DONG"] = (
                "độ mặn/pH ngoài ngưỡng" if need_open else "độ mặn và pH trong ngưỡng"
            )

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
