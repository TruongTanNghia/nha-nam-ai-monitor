"""Phân tích bằng Random Forest + thời tiết ngoài trời — CHỈ ĐỂ THAM KHẢO.

Thiết bị vẫn bật/tắt theo ngưỡng (firmware ESP32); kết quả ở đây chỉ hiện trên web và gửi Zalo để tham khảo.

Một mô hình RandomForestRegressor (nhiều đầu ra) học từ dữ liệu theo giờ. "Trạng thái" của 1 giờ là trung bình
15 phút CUỐI giờ đó (trung bình cả giờ bị kéo lệch khi cảm biến vừa đổi mạnh, vd độ ẩm đất 0% -> 100%):
  đầu vào  : trạng thái trong nhà giờ H (nhiệt độ, độ ẩm KK, độ ẩm đất, ánh sáng, pH, độ mặn, thiết bị đang bật),
             thời tiết ngoài trời giờ H và giờ H+1 (nhiệt độ, độ ẩm, mây, bức xạ, mưa, gió), giờ trong ngày
  đầu ra   : MỨC THAY ĐỔI trong 1 giờ của nhiệt độ, độ ẩm KK, độ ẩm đất, pH, độ mặn
Dự đoán = số đo hiện tại + các mức thay đổi (lặp 3 bước với thời tiết dự báo). Học mức thay đổi thay vì giá trị
tuyệt đối để mô hình ít dữ liệu không kéo số về "trung bình lịch sử" (vd đất 100% -> 48% sau 3 giờ).
Mỗi bước bị giới hạn trong mức thay đổi thực tế đã thấy; giờ có cảm biến lỗi/nhảy số bị loại khi học.
"""
import logging
import math
import time

import numpy as np

from sensors import SENSORS, fmt, is_valid, num, thresholds

try:
    from sklearn.ensemble import RandomForestRegressor
except ImportError:
    RandomForestRegressor = None

log = logging.getLogger("rf")

INDOOR = ["t", "h", "GT_AMDAT", "ANHSANG", "GT_PH", "GT_DOMAN"]
DEVICES = ["QUAT", "SUONG", "BOM", "DEN", "MO"]
OUT = ["temp", "hum", "cloud", "rad", "precip", "wind"]
OUT_NEXT = ["temp", "hum", "rad", "precip"]
TARGETS = ["t", "h", "GT_AMDAT", "GT_PH", "GT_DOMAN"]
# Mức thay đổi lớn nhất tin được trong 1 giờ trong nhà nấm (lớn hơn = cảm biến lỗi/vừa cắm lại)
MAX_STEP = {"t": 3.0, "h": 12.0, "GT_AMDAT": 12.0, "GT_PH": 0.4, "GT_DOMAN": 150.0}
MIN_STEP = {"t": 0.5, "h": 2.0, "GT_AMDAT": 2.0, "GT_PH": 0.05, "GT_DOMAN": 10.0}

FEATURES = (INDOOR + DEVICES + [f"o_{k}" for k in OUT] + [f"n_{k}" for k in OUT_NEXT] + ["hour_sin", "hour_cos"])
# Gộp các đặc trưng thành nhóm dễ hiểu khi giải thích "yếu tố ảnh hưởng"
GROUPS = {
    "Nhiệt độ ngoài trời": ["o_temp", "n_temp"],
    "Nắng (bức xạ mặt trời)": ["o_rad", "n_rad"],
    "Độ ẩm ngoài trời": ["o_hum", "n_hum"],
    "Mưa": ["o_precip", "n_precip"],
    "Mây": ["o_cloud"],
    "Gió": ["o_wind"],
    "Giờ trong ngày": ["hour_sin", "hour_cos"],
    "Thiết bị đang chạy": DEVICES,
    "Chỉ số trong nhà hiện tại": INDOOR,
}
MIN_SAMPLES = 24   # tối thiểu 24 giờ có cả dữ liệu cảm biến lẫn thời tiết
STATE_SEC = 900    # trạng thái = trung bình 15 phút gần nhất (khi huấn luyện: 15 phút cuối mỗi giờ)
RETRAIN_SEC = 3600


def _features(indoor, devices, w_now, w_next, hour_ts):
    hour = time.localtime(hour_ts).tm_hour
    row = [indoor.get(k) for k in INDOOR] + [devices.get(k, 0) or 0 for k in DEVICES]
    row += [w_now.get(k) for k in OUT] + [w_next.get(k) for k in OUT_NEXT]
    row += [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24)]
    return None if any(v is None for v in row) else row


def _ok(r):
    """Giờ dùng được để học: đủ chỉ số hợp lệ; độ ẩm đất đúng 0% = cảm biến chưa cắm/đứt dây."""
    return all(is_valid(k, r.get(k)) for k in INDOOR) and r["GT_AMDAT"] > 0.5


class RFAnalyzer:
    def __init__(self, store, weather):
        self.store = store
        self.weather = weather
        self.model = None
        self.step_limit = dict(MAX_STEP)
        self.info = {"name": "Random Forest", "trained": False, "samples": 0, "trained_at": None,
                     "mae": {}, "importance": [], "note": "Đang chờ dữ liệu cảm biến + thời tiết"}
        self.result = None
        self.last_train = 0

    # ------------------------------------------------------------------ huấn luyện
    def train(self, now=None):
        now = now or time.time()
        self.last_train = now
        if RandomForestRegressor is None:
            self.info["note"] = "Chưa cài scikit-learn"
            return
        hours = self.store.hourly_readings(now - 30 * 86400, min_samples=10, tail_sec=STATE_SEC)
        by_hour = {r["hour"]: r for r in hours}
        weather = self.store.weather_between(now - 31 * 86400, now)
        X, Y, skipped = [], [], 0
        for h, r in by_hour.items():
            nxt = by_hour.get(h + 3600)
            w_now, w_next = weather.get(h), weather.get(h + 3600)
            if not nxt or not w_now or not w_next:
                continue
            if not _ok(r) or not _ok(nxt):
                skipped += 1
                continue
            dy = [nxt[k] - r[k] for k in TARGETS]
            if any(abs(d) > 2 * MAX_STEP[k] for k, d in zip(TARGETS, dy)):
                skipped += 1  # nhảy số bất thường (cắm lại cảm biến, nhúng nước...) -> không phải diễn biến thật
                continue
            x = _features(r, r, w_now, w_next, h)
            if x is not None:
                X.append(x)
                Y.append(dy)
        self.info["samples"] = len(X)
        if len(X) < MIN_SAMPLES:
            self.info["note"] = (f"Đang thu thập: có {len(X)}/{MIN_SAMPLES} giờ đủ dữ liệu cảm biến + thời tiết "
                                 "(cần máy chủ chạy ít nhất 1 ngày)"
                                 + (f"; bỏ {skipped} giờ cảm biến lỗi/nhảy số" if skipped else ""))
            return
        X, Y = np.array(X, dtype=float), np.array(Y, dtype=float)

        # Giới hạn mỗi bước: 1.5 lần mức thay đổi lớn (95%) đã thấy, nằm trong [MIN_STEP, MAX_STEP]
        q95 = np.percentile(np.abs(Y), 95, axis=0)
        self.step_limit = {k: float(min(MAX_STEP[k], max(MIN_STEP[k], 1.5 * q))) for k, q in zip(TARGETS, q95)}

        # Đánh giá: học trên 80% giờ đầu, thử trên 20% giờ cuối (giống dự đoán tương lai thật)
        mae = {}
        if len(X) >= 40:
            cut = int(len(X) * 0.8)
            test = RandomForestRegressor(n_estimators=150, max_depth=8, min_samples_leaf=3, random_state=42)
            test.fit(X[:cut], Y[:cut])
            err = np.abs(test.predict(X[cut:]) - Y[cut:]).mean(axis=0)
            mae = {k: round(float(e), 2) for k, e in zip(TARGETS, err)}

        model = RandomForestRegressor(n_estimators=200, max_depth=8, min_samples_leaf=3, random_state=42)
        model.fit(X, Y)
        imp = dict(zip(FEATURES, model.feature_importances_))
        groups = sorted(((name, sum(imp[f] for f in feats)) for name, feats in GROUPS.items()), key=lambda g: -g[1])
        self.model = model
        self.info.update(
            trained=True, trained_at=now, mae=mae,
            importance=[{"name": n, "pct": round(v * 100)} for n, v in groups if v >= 0.03][:5],
            note=f"Học từ {len(X)} giờ dữ liệu, học lại mỗi giờ"
                 + (f" (bỏ {skipped} giờ cảm biến lỗi/nhảy số)" if skipped else ""),
        )
        log.info("Đã huấn luyện Random Forest với %d giờ dữ liệu (bỏ %d), sai số %s", len(X), skipped, mae)

    # ------------------------------------------------------------------ phân tích
    def analyze(self, recent_rows, data, now=None):
        """recent_rows: số liệu gần đây (cũ -> mới); data: bản ghi Firebase mới nhất."""
        now = now or time.time()
        if now - self.last_train >= RETRAIN_SEC or (self.model is None and now - self.last_train >= 600):
            self.train(now)
        th = thresholds(data)
        live = {k: num(data.get(k)) for k in INDOOR}
        rows = [r for r in recent_rows if r["ts"] >= now - STATE_SEC] or recent_rows[-1:]
        feat = {k: _mean(rows, k) for k in INDOOR}       # đầu vào: trung bình 15 phút (giống lúc học)
        devices = {k: _mean(rows, k) or 0 for k in DEVICES}
        hour_ts = int(now // 3600 * 3600)
        predictions = []
        if self.model is not None:
            value = {k: live[k] if is_valid(k, live[k]) else feat[k] for k in TARGETS}  # dự đoán bám số đo hiện tại
            for step in (1, 2, 3):
                w_now, w_next = self.weather.at(hour_ts + (step - 1) * 3600), self.weather.at(hour_ts + step * 3600)
                x = _features(feat, devices, w_now or {}, w_next or {}, hour_ts + (step - 1) * 3600)
                if x is None:
                    break
                dy = self.model.predict(np.array([x], dtype=float))[0]
                row = {"hours": step}
                for k, d in zip(TARGETS, dy):
                    lim = self.step_limit[k]
                    d = min(max(float(d), -lim), lim)
                    lo, hi = SENSORS[k]["phys"]
                    if value[k] is not None:
                        value[k] = min(max(value[k] + d, lo), hi)
                    if feat[k] is not None:
                        feat[k] = min(max(feat[k] + d, lo), hi)
                    # độ ẩm đất đúng 0% = cảm biến lỗi -> không dự đoán
                    bad = value[k] is None or (k == "GT_AMDAT" and (live[k] or 0) <= 0.5)
                    row[k] = None if bad else round(value[k], 2 if k == "GT_PH" else 1)
                predictions.append(row)
        self.result = {
            **self.info,
            # "Bây giờ" = số đo mới nhất (giống thẻ cảm biến), không phải trung bình
            "now": {k: (round(live[k], 2 if k == "GT_PH" else 1) if live[k] is not None else None) for k in INDOOR},
            "predictions": predictions,
            "advice": self._advice(predictions, live, th, now),
            "ts": now,
        }
        return self.result

    def _advice(self, predictions, live, th, now):
        """So số đo hiện tại + dự đoán với ngưỡng: đang ngoài ngưỡng (luôn báo, kể cả khi mô hình đang học),
        bao lâu nữa trở lại, hoặc sắp vượt ngưỡng."""
        lo_ph, hi_ph = th["SET_PHLOW"], th["SET_PHHIGH"]
        rules = [  # (chỉ số, hàm "ngoài ngưỡng", mô tả ngưỡng, thiết bị lúc sắp vượt, thiết bị lúc đang vượt)
            ("t", (lambda v: v > th["SET_T"]) if th["SET_T"] else None,
             f"trên ngưỡng {fmt('t', th['SET_T'])}", "quạt sẽ bật theo ngưỡng", "quạt đang bật theo ngưỡng"),
            ("h", (lambda v: v < th["SET_H"]) if th["SET_H"] else None,
             f"dưới ngưỡng {fmt('h', th['SET_H'])}", "phun sương sẽ bật theo ngưỡng", "phun sương đang bật theo ngưỡng"),
            ("GT_AMDAT", (lambda v: v < th["SET_AMDAT"]) if th["SET_AMDAT"] else None,
             f"dưới ngưỡng {fmt('GT_AMDAT', th['SET_AMDAT'])}", "máy bơm sẽ bật theo ngưỡng", "máy bơm đang bật theo ngưỡng"),
            ("GT_PH", (lambda v: (lo_ph and v < lo_ph) or (hi_ph and v > hi_ph)) if (lo_ph or hi_ph) else None,
             f"ngoài khoảng {lo_ph or '—'}–{hi_ph or '—'}", "máy bơm và phun sương sẽ bị khóa; kiểm tra nước cấp",
             "máy bơm và phun sương đang bị khóa; kiểm tra nước cấp"),
            ("GT_DOMAN", (lambda v: v > th["SET_DOMAN"]) if th["SET_DOMAN"] else None,
             f"trên ngưỡng {fmt('GT_DOMAN', th['SET_DOMAN'])}", "máy bơm sẽ bị khóa; kiểm tra nguồn nước",
             "máy bơm đang bị khóa; kiểm tra nguồn nước"),
        ]
        tips, out_now = [], False
        for k, out_of, limit_text, soon_action, now_action in rules:
            v_now = live.get(k)
            if not out_of or v_now is None or (k == "GT_AMDAT" and v_now <= 0.5):
                continue
            series = [(p["hours"], p[k]) for p in predictions if p.get(k) is not None]
            name = SENSORS[k]["name"]
            if out_of(v_now):  # ĐANG ngoài ngưỡng: luôn báo, không cần mô hình
                out_now = True
                text = f"{name} đang {fmt(k, v_now)} ({limit_text}) — {now_action}"
                if series:
                    back = next(((hrs, v) for hrs, v in series if not out_of(v)), None)
                    text += (f"; dự kiến khoảng {back[0]} giờ nữa trở lại trong ngưỡng (~{fmt(k, back[1])})" if back
                             else "; có thể vẫn chưa về ngưỡng trong 3 giờ tới — kiểm tra thiết bị")
                tips.append(text + ".")
            else:
                first = next(((hrs, v) for hrs, v in series if out_of(v)), None)
                if first:
                    tips.append(f"{name} có thể tới khoảng {fmt(k, first[1])} sau {first[0]} giờ ({limit_text}) — {soon_action}.")
        nxt = [self.weather.at(now + k * 3600) for k in (1, 2, 3)]
        nxt = [w for w in nxt if w]
        rain = sum(w.get("precip") or 0 for w in nxt)
        if rain >= 1:
            tips.append(f"Ngoài trời sắp mưa (~{rain:.1f} mm trong 3 giờ tới) — độ ẩm có thể tăng, nhiệt độ có thể giảm.")
        hot = max((w.get("temp") or 0 for w in nxt), default=0)
        if hot >= 35:
            tips.append(f"Ngoài trời nắng nóng tới {hot:.0f}°C trong 3 giờ tới.")
        if predictions and (live.get("GT_AMDAT") or 0) <= 0.5:
            tips.append("Độ ẩm đất đang báo 0% — cảm biến có thể chưa cắm/đứt dây nên chưa dự đoán độ ẩm đất.")
        if not predictions and not out_now:
            tips.append("Các chỉ số đang trong ngưỡng. Chưa dự đoán được 3 giờ tới vì mô hình còn đang học.")
        elif not tips:
            tips.append("Chưa thấy chỉ số nào có khả năng vượt ngưỡng trong 3 giờ tới.")
        return tips


def report_lines(result):
    """Kết quả Random Forest dạng tin Zalo (chỉ để tham khảo)."""
    if not result:
        return ["🌲 Random Forest: đang khởi động…"]
    if not result.get("predictions"):
        return ([f"🌲 Random Forest: {result.get('note') or 'chưa có dự đoán'}"]
                + [f"👉 {tip}" for tip in result.get("advice") or []])
    lines = ["🌲 Phân tích Random Forest (tham khảo — thiết bị vẫn bật/tắt theo ngưỡng):"]
    for p in result["predictions"]:
        lines.append(f"• +{p['hours']} giờ: 🌡 {fmt('t', p['t'])} | 💧 {fmt('h', p['h'])} | 🌱 {fmt('GT_AMDAT', p['GT_AMDAT'])}"
                     f" | ⚗️ pH {fmt('GT_PH', p.get('GT_PH'))} | 🧂 {fmt('GT_DOMAN', p.get('GT_DOMAN'))}")
    lines += [f"👉 {tip}" for tip in result.get("advice") or []]
    if result.get("importance"):
        lines.append("Ảnh hưởng nhiều nhất: " + ", ".join(f"{i['name'].lower()} {i['pct']}%" for i in result["importance"][:3]))
    return lines


def _mean(rows, key):
    vals = [num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None
