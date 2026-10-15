"""Dự báo hành động: từ xu hướng vài phút gần nhất, tính bao lâu nữa chạm ngưỡng
và thiết bị nào ESP32 sẽ bật/tắt (theo đúng luật AUTO của firmware).

Ví dụ: độ ẩm KK 67% -> 65% trong 2 phút (giảm 1%/phút), ngưỡng SET_H = 60%
=> "khoảng 5 phút nữa độ ẩm KK xuống dưới 60% -> ESP32 sẽ bật phun sương".
"""
import numpy as np

from sensors import SENSORS, SENSOR_KEYS, fmt, is_valid, lname

WINDOW_SEC = 600      # dùng tối đa 10 phút gần nhất để ước lượng tốc độ thay đổi
MIN_SPAN_SEC = 60     # cần ít nhất ~1 phút dữ liệu
MIN_POINTS = 6
HORIZON_MIN = 120     # chỉ báo những gì xảy ra trong 2 giờ tới

# Mức thay đổi nhỏ nhất (trong cả cửa sổ) được coi là xu hướng thật, không phải nhiễu đo
NOISE = {"t": 0.4, "h": 1.5, "ANHSANG": 30, "GT_AMDAT": 2, "GT_DOMAN": 20, "GT_PH": 0.15}

# (cảm biến, ngưỡng, phía kích hoạt, hành động khi vượt ngưỡng, hành động khi trở lại)
RULES = (
    ("t", "SET_T", "above", "bật quạt", "tắt quạt"),
    ("h", "SET_H", "below", "bật phun sương", "tắt phun sương"),
    ("GT_AMDAT", "SET_AMDAT", "below", "bật máy bơm", "tắt máy bơm"),
    ("ANHSANG", "SET_AS", "below", "bật đèn và mở màn che", "tắt đèn và đóng màn che"),
    ("GT_DOMAN", "SET_DOMAN", "above", "ngắt máy bơm vì nước mặn", "cho máy bơm chạy lại"),
    ("GT_PH", "SET_PHLOW", "below", "ngắt bơm và phun sương vì pH thấp", "cho bơm và phun sương chạy lại"),
    ("GT_PH", "SET_PHHIGH", "above", "ngắt bơm và phun sương vì pH cao", "cho bơm và phun sương chạy lại"),
)


def sentence(text):
    """Viết hoa chữ đầu câu, giữ nguyên 'pH'."""
    return text if not text or text.startswith("pH") else text[0].upper() + text[1:]


def eta_text(minutes):
    if minutes < 1:
        return "dưới 1 phút"
    if minutes < 60:
        return f"khoảng {round(minutes)} phút"
    return f"khoảng {minutes / 60:.1f} giờ".replace(".0 ", " ")


def rate_text(key, rate_per_min):
    unit = SENSORS[key]["unit"]
    digits = 2 if key == "GT_PH" else 1
    word = "tăng" if rate_per_min > 0 else "giảm"
    value = f"{abs(rate_per_min):.{digits}f}".rstrip("0").rstrip(".")
    sep = "" if unit in ("%", "°C", "") else " "
    return f"{word} {value}{sep}{unit}/phút"


def fit(rows, key, now):
    """Hồi quy tuyến tính trên cửa sổ gần nhất -> (giá trị hiện tại, tốc độ/phút, r2, độ dài phút) hoặc None."""
    pts = [(r["ts"], r.get(key)) for r in rows if r["ts"] >= now - WINDOW_SEC and is_valid(key, r.get(key))]
    if len(pts) < MIN_POINTS or pts[-1][0] - pts[0][0] < MIN_SPAN_SEC:
        return None
    t = np.array([(ts - now) / 60 for ts, _ in pts])
    y = np.array([v for _, v in pts], dtype=float)
    slope, intercept = np.polyfit(t, y, 1)
    resid = y - (slope * t + intercept)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float((resid ** 2).sum()) / ss_tot if ss_tot > 1e-9 else 0.0
    # Giá trị hiện tại = lần đo mới nhất (so ngưỡng giống hệt firmware); hồi quy chỉ dùng để lấy tốc độ
    return float(pts[-1][1]), float(slope), r2, (pts[-1][0] - pts[0][0]) / 60


def forecast_sensor(rows, key, now, th):
    res = fit(rows, key, now)
    if res is None:
        return None
    current, slope, r2, span_min = res
    significant = abs(slope) * span_min >= NOISE[key] and r2 >= 0.3
    trend = "flat" if not significant else ("up" if slope > 0 else "down")
    lo, hi = SENSORS[key]["phys"]
    out = {
        "now": round(current, 2),
        "in30": round(min(max(current + slope * 30 if significant else current, lo), hi), 2),
        "rate_per_min": round(slope, 3) if significant else 0.0,
        "trend": trend,
        "r2": round(r2, 2),
        "window_min": round(span_min, 1),
        "events": [],
        "missing_thresholds": [],
    }
    name = lname(key)
    for sensor, setting, side, on_action, off_action in RULES:
        if sensor != key:
            continue
        s = th.get(setting)
        if s is None:
            out["missing_thresholds"].append(setting)
            continue
        past = current < s if side == "below" else current > s
        toward = (slope < 0) if side == "below" else (slope > 0)
        word = "xuống dưới" if side == "below" else "vượt"
        back = "lên lại" if side == "below" else "về lại dưới"
        event = None
        if not past and significant and toward:
            eta = (current - s) / -slope if side == "below" else (s - current) / slope
            event = {
                "kind": "trigger", "setting": setting, "threshold": s, "eta_min": round(eta, 1),
                "action": on_action,
                "text": f"{eta_text(eta)} nữa {name} {word} {fmt(key, s)} → ESP32 sẽ {on_action}",
            }
        elif past and significant and not toward:
            eta = (s - current) / slope if side == "below" else (current - s) / -slope
            event = {
                "kind": "recover", "setting": setting, "threshold": s, "eta_min": round(eta, 1),
                "action": off_action,
                "text": f"{eta_text(eta)} nữa {name} {back} {fmt(key, s)} → ESP32 sẽ {off_action}",
            }
        elif past:
            event = {
                "kind": "active", "setting": setting, "threshold": s, "eta_min": None,
                "action": on_action,
                "text": f"{name} đang {'dưới' if side == 'below' else 'trên'} ngưỡng {fmt(key, s)} "
                        f"→ ESP32 đang {on_action}"
                        + (f", nhưng vẫn {rate_text(key, slope)}" if significant else ""),
            }
        if event and (event["eta_min"] is None or event["eta_min"] <= HORIZON_MIN):
            out["events"].append(event)

    timed = [e for e in out["events"] if e["eta_min"] is not None]
    if timed:
        out["headline"] = min(timed, key=lambda e: e["eta_min"])["text"]
    elif out["events"]:
        out["headline"] = out["events"][0]["text"]
    elif significant:
        out["headline"] = f"{SENSORS[key]['name']} đang {rate_text(key, slope)}, chưa chạm ngưỡng nào trong 2 giờ tới"
    else:
        out["headline"] = f"{SENSORS[key]['name']} ổn định quanh {fmt(key, current)}"
    return out


def forecast_all(rows, now, th):
    return {k: f for k in SENSOR_KEYS if (f := forecast_sensor(rows, k, now, th))}


def upcoming(forecasts, limit=None):
    """Các sự kiện sắp xảy ra, gần nhất trước — dùng cho tin Zalo và trang tổng quan."""
    events = [
        {**e, "sensor": k}
        for k, f in forecasts.items() for e in f["events"] if e["eta_min"] is not None
    ]
    events.sort(key=lambda e: e["eta_min"])
    return events[:limit] if limit else events
