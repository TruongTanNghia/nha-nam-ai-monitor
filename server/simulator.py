"""Mô phỏng ESP32 cho máy không có mạch (chỉ khi DEMO_FORECAST=true trong server/.env).

Quy tắc (engine.py quyết định theo nhịp tim ESP_TS, cùng cách báo "mạch tắt"):
- Mạch đang gửi (esp_online True/None): dùng số THẬT của ESP32, mô phỏng tắt.
- Mạch không gửi (esp_online False): số cảm biến MÔ PHỎNG luôn dao động ±1–3 đơn vị (pH ±0,1–0,3) quanh số đo thật
  cuối cùng của mạch, đổi mỗi lần đọc (5 giây); thiết bị, chế độ, ngưỡng vẫn lấy từ Firebase.
- Số mô phỏng chỉ để HIỂN THỊ: không lưu vào cơ sở dữ liệu / CSV / Google Sheet, không gửi cảnh báo Zalo,
  không ghi lên Firebase (máy khác và mạch ESP32 dùng chung Firebase).
"""
import random

from sensors import SENSOR_KEYS, SENSORS, is_valid, num

BAND = {"GT_PH": (0.1, 0.3)}   # độ lệch so với số gốc: mặc định 1–3 đơn vị; pH 0,1–0,3 (±1–3 pH là vô lý)
DIGITS = {"GT_PH": 2}          # số thực: 1 chữ số lẻ, pH 2 chữ số
TARGET_TICKS = (4, 8)          # cứ 4–8 lần đọc (20–40 giây) chọn hướng lệch mới -> lên xuống như thật
FALLBACK = {"t": 30.0, "h": 80.0, "GT_AMDAT": 70.0, "ANHSANG": 500.0, "GT_PH": 6.5, "GT_DOMAN": 200.0}


class Simulator:
    def __init__(self, store=None):
        self.store = store
        self.values = None   # số mô phỏng hiện tại (None = đang dùng số thật)
        self.anchor = {}     # số gốc: số đo thật cuối cùng của mạch
        self.target = {}     # độ lệch đang hướng tới (|lệch| trong BAND)
        self.ticks = {}      # còn bao nhiêu lần đọc thì đổi hướng
        self.since = None
        self.rng = random.Random()

    @property
    def active(self):
        return self.values is not None

    def stop(self):
        """Mạch gửi số lại -> bỏ mô phỏng, lần sau bắt đầu lại từ số thật mới nhất."""
        self.values, self.since = None, None

    def _band(self, k):
        return BAND.get(k, (1.0, 3.0))

    def _new_target(self, k):
        """Độ lệch mới: |lệch| ngẫu nhiên trong BAND, hướng ngẫu nhiên; số gốc sát giới hạn vật lý (vd độ ẩm đất 100%,
        độ mặn 0) thì lệch vào phía trong để vẫn dao động."""
        lo_b, hi_b = self._band(k)
        lo, hi = SENSORS[k]["phys"]
        d = self.rng.uniform(lo_b, hi_b) * self.rng.choice((-1, 1))
        a = self.anchor[k]
        if not lo <= a + d <= hi:
            d = -d
        self.target[k] = d
        self.ticks[k] = self.rng.randint(*TARGET_TICKS)

    def _start(self, real, now):
        self.anchor = {}
        for k in SENSOR_KEYS:
            v = num(real.get(k))
            lo, hi = SENSORS[k]["phys"]
            self.anchor[k] = min(max(v, lo), hi) if is_valid(k, v) else FALLBACK[k]   # DHT báo 0 -> số mặc định
            self._new_target(k)
        self.values = {k: self.anchor[k] + self.target[k] for k in SENSOR_KEYS}
        self.since = now

    def step(self, real, now):
        """Gọi mỗi lần đọc (5 giây) khi mạch không gửi số. Trả bản ghi để hiển thị: cảm biến mô phỏng,
        còn lại (thiết bị, chế độ, ngưỡng) giữ nguyên của Firebase."""
        if self.values is None:
            self._start(real or {}, now)
        out = dict(real or {})
        for k in SENSOR_KEYS:
            self.ticks[k] -= 1
            if self.ticks[k] <= 0:
                self._new_target(k)
            lo_b, hi_b = self._band(k)
            digits = DIGITS.get(k, 1)
            a, old = self.anchor[k], self.values[k]
            # tiến dần về hướng lệch mới + rung nhẹ -> mỗi lần đọc đều đổi, không nhảy cóc
            v = old + 0.35 * (a + self.target[k] - old) + self.rng.gauss(0, hi_b * 0.06)
            lo, hi = SENSORS[k]["phys"]
            v = min(max(v, a - hi_b, lo), a + hi_b, hi)        # luôn trong số gốc ± 3 (pH ± 0,3)
            if round(v, digits) == round(old, digits):         # luôn nhảy số: đổi ít nhất 1 chữ số lẻ
                step = 10 ** -digits * self.rng.choice((-1, 1))
                v = v + step if a - hi_b <= v + step <= a + hi_b and lo <= v + step <= hi else v - step
            self.values[k] = v
            out[k] = round(v, digits)
        out["SIMULATED"] = True
        return out
