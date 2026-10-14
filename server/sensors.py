"""Mô tả cảm biến, thiết bị và ngưỡng — dùng chung cho toàn bộ server."""
import math

# key Firebase -> thông tin cảm biến
SENSORS = {
    "t": {"name": "Nhiệt độ", "unit": "°C", "phys": (-10, 80), "jump": 5, "digits": 1},
    "h": {"name": "Độ ẩm không khí", "unit": "%", "phys": (0, 100), "jump": 20, "digits": 0},
    "ANHSANG": {"name": "Ánh sáng", "unit": "lux", "phys": (0, 70000), "jump": None, "digits": 0},
    "GT_AMDAT": {"name": "Độ ẩm đất", "unit": "%", "phys": (0, 100), "jump": 25, "digits": 0},
    "GT_DOMAN": {"name": "Độ mặn", "unit": "ppm", "phys": (0, 5000), "jump": 300, "digits": 0},
    "GT_PH": {"name": "pH", "unit": "", "phys": (0, 14), "jump": 2, "digits": 2},
}
SENSOR_KEYS = list(SENSORS)

DEVICES = {
    "BOM": "Máy bơm",
    "DEN": "Đèn",
    "QUAT": "Quạt",
    "SUONG": "Phun sương",
    "MO": "Mở màn che",
    "DONG": "Đóng màn che",
}
DEVICE_KEYS = list(DEVICES)

SETTINGS = {
    "SET_AMDAT": "Ngưỡng độ ẩm đất",
    "SET_AS": "Ngưỡng ánh sáng",
    "SET_DOMAN": "Ngưỡng độ mặn",
    "SET_H": "Ngưỡng độ ẩm không khí",
    "SET_PHHIGH": "Ngưỡng pH cao",
    "SET_PHLOW": "Ngưỡng pH thấp",
    "SET_T": "Ngưỡng nhiệt độ",
}

MODE_AUTO = 1
MODE_MANUAL = 0


def num(value, default=None):
    """Ép kiểu số an toàn; trả default nếu không hợp lệ."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def lname(key):
    """Tên cảm biến viết thường để chèn giữa câu (giữ nguyên 'pH')."""
    name = SENSORS[key]["name"]
    return name if key == "GT_PH" else name.lower()


def fmt(key, value):
    """Định dạng giá trị cảm biến kèm đơn vị, vd '31.5°C'."""
    meta = SENSORS[key]
    if value is None:
        return "—"
    digits = meta["digits"]
    text = f"{value:.{digits}f}" if digits else f"{round(value):d}"
    unit = meta["unit"]
    if not unit:
        return text
    return f"{text}{unit}" if unit in ("°C", "%") else f"{text} {unit}"


def is_valid(key, value):
    if value is None:
        return False
    lo, hi = SENSORS[key]["phys"]
    return lo <= value <= hi


def thresholds(data):
    """Lấy các ngưỡng cài đặt; ngưỡng 0/thiếu coi như chưa cài (None)."""
    out = {}
    for key in SETTINGS:
        v = num(data.get(key))
        out[key] = v if v not in (None, 0) else None
    # pH thấp được phép bằng 0? Không hợp lý cho nhà nấm, giữ None.
    return out
