"""Phân tích bằng Random Forest + thời tiết ngoài trời — CHỈ ĐỂ THAM KHẢO.

Thiết bị vẫn bật/tắt theo ngưỡng (firmware ESP32); kết quả ở đây chỉ hiện trên web và gửi Zalo để tham khảo.

Một mô hình RandomForestRegressor (nhiều đầu ra) học từ dữ liệu theo giờ. "Trạng thái" của 1 giờ là trung bình
15 phút CUỐI giờ đó (trung bình cả giờ bị kéo lệch khi cảm biến vừa đổi mạnh, vd độ ẩm đất 0% -> 100%):
  đầu vào  : trạng thái trong nhà giờ H (nhiệt độ, độ ẩm KK, độ ẩm đất, ánh sáng, thiết bị đang bật),
             thời tiết ngoài trời giờ H và giờ H+1 (nhiệt độ, độ ẩm, mây, bức xạ, mưa, gió), giờ trong ngày
  đầu ra   : nhiệt độ, độ ẩm KK, độ ẩm đất trong nhà 1 giờ sau
Dự kiến 3 giờ tới bằng cách lặp 3 bước với thời tiết dự báo.
"""
import logging
import math
import time

import numpy as np

from sensors import fmt, num, thresholds

try:
    from sklearn.ensemble import RandomForestRegressor
except ImportError:
    RandomForestRegressor = None

log = logging.getLogger("rf")

INDOOR = ["t", "h", "GT_AMDAT", "ANHSANG"]
DEVICES = ["QUAT", "SUONG", "BOM", "DEN", "MO"]
OUT = ["temp", "hum", "cloud", "rad", "precip", "wind"]
OUT_NEXT = ["temp", "hum", "rad", "precip"]
TARGETS = ["t", "h", "GT_AMDAT"]
TARGET_NAMES = {"t": "Nhiệt độ", "h": "Độ ẩm không khí", "GT_AMDAT": "Độ ẩm đất"}

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


class RFAnalyzer:
    def __init__(self, store, weather):
        self.store = store
        self.weather = weather
        self.model = None
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
        X, Y = [], []
        for h, r in by_hour.items():
            nxt = by_hour.get(h + 3600)
            w_now, w_next = weather.get(h), weather.get(h + 3600)
            if not nxt or not w_now or not w_next:
                continue
            x = _features(r, r, w_now, w_next, h)
            y = [nxt.get(k) for k in TARGETS]
            if x is not None and None not in y:
                X.append(x)
                Y.append(y)
        self.info["samples"] = len(X)
        if len(X) < MIN_SAMPLES:
            self.info["note"] = (f"Đang thu thập: có {len(X)}/{MIN_SAMPLES} giờ đủ dữ liệu cảm biến + thời tiết "
                                 "(cần máy chủ chạy ít nhất 1 ngày)")
            return
        X, Y = np.array(X, dtype=float), np.array(Y, dtype=float)

        # Đánh giá: học trên 80% giờ đầu, thử trên 20% giờ cuối (giống dự đoán tương lai thật)
        mae = {}
        if len(X) >= 40:
            cut = int(len(X) * 0.8)
            test = RandomForestRegressor(n_estimators=150, max_depth=10, min_samples_leaf=2, random_state=42)
            test.fit(X[:cut], Y[:cut])
            err = np.abs(test.predict(X[cut:]) - Y[cut:]).mean(axis=0)
            mae = {k: round(float(e), 2) for k, e in zip(TARGETS, err)}

        model = RandomForestRegressor(n_estimators=200, max_depth=10, min_samples_leaf=2, random_state=42)
        model.fit(X, Y)
        imp = dict(zip(FEATURES, model.feature_importances_))
        groups = sorted(((name, sum(imp[f] for f in feats)) for name, feats in GROUPS.items()), key=lambda g: -g[1])
        self.model = model
        self.info.update(
            trained=True, trained_at=now, mae=mae,
            importance=[{"name": n, "pct": round(v * 100)} for n, v in groups if v >= 0.03][:5],
            note=f"Học từ {len(X)} giờ dữ liệu, học lại mỗi giờ",
        )
        log.info("Đã huấn luyện Random Forest với %d giờ dữ liệu, sai số %s", len(X), mae)

    # ------------------------------------------------------------------ phân tích
    def analyze(self, recent_rows, data, now=None):
        """recent_rows: số liệu gần đây (cũ -> mới); data: bản ghi Firebase mới nhất."""
        now = now or time.time()
        if now - self.last_train >= RETRAIN_SEC or (self.model is None and now - self.last_train >= 600):
            self.train(now)
        th = thresholds(data)
        rows = [r for r in recent_rows if r["ts"] >= now - STATE_SEC] or recent_rows[-1:]
        indoor = {k: _mean(rows, k) for k in INDOOR}
        devices = {k: _mean(rows, k) or 0 for k in DEVICES}
        hour_ts = int(now // 3600 * 3600)
        predictions = []
        if self.model is not None:
            state = dict(indoor)
            for step in (1, 2, 3):
                w_now, w_next = self.weather.at(hour_ts + (step - 1) * 3600), self.weather.at(hour_ts + step * 3600)
                x = _features(state, devices, w_now or {}, w_next or {}, hour_ts + (step - 1) * 3600)
                if x is None:
                    break
                y = self.model.predict(np.array([x], dtype=float))[0]
                state.update({k: float(v) for k, v in zip(TARGETS, y)})
                predictions.append({"hours": step, **{k: round(state[k], 1) for k in TARGETS}})
        self.result = {
            **self.info,
            # "Bây giờ" = số đo mới nhất (giống thẻ cảm biến), không phải trung bình
            "now": {k: (round(num(data.get(k)), 1) if num(data.get(k)) is not None else None) for k in INDOOR},
            "predictions": predictions,
            "advice": self._advice(predictions, th, now),
            "ts": now,
        }
        return self.result

    def _advice(self, predictions, th, now):
        tips = []
        if predictions:
            worst = {k: (max if k == "t" else min)(p[k] for p in predictions) for k in TARGETS}
            when = {k: next(p["hours"] for p in predictions if p[k] == worst[k]) for k in TARGETS}
            if th["SET_T"] and worst["t"] > th["SET_T"]:
                tips.append(f"Nhiệt độ có thể lên khoảng {fmt('t', worst['t'])} trong {when['t']} giờ tới "
                            f"(trên ngưỡng {fmt('t', th['SET_T'])}) — quạt sẽ bật theo ngưỡng; có thể chuẩn bị che nắng.")
            if th["SET_H"] and worst["h"] < th["SET_H"]:
                tips.append(f"Độ ẩm không khí có thể xuống khoảng {fmt('h', worst['h'])} trong {when['h']} giờ tới "
                            f"(dưới ngưỡng {fmt('h', th['SET_H'])}) — phun sương sẽ bật theo ngưỡng; kiểm tra nước cho béc phun.")
            if th["SET_AMDAT"] and worst["GT_AMDAT"] < th["SET_AMDAT"]:
                tips.append(f"Độ ẩm đất có thể xuống khoảng {fmt('GT_AMDAT', worst['GT_AMDAT'])} trong "
                            f"{when['GT_AMDAT']} giờ tới — máy bơm sẽ bật theo ngưỡng; kiểm tra nước trong bồn.")
        nxt = [self.weather.at(now + k * 3600) for k in (1, 2, 3)]
        nxt = [w for w in nxt if w]
        rain = sum(w.get("precip") or 0 for w in nxt)
        if rain >= 1:
            tips.append(f"Ngoài trời sắp mưa (~{rain:.1f} mm trong 3 giờ tới) — độ ẩm có thể tăng, nhiệt độ có thể giảm.")
        hot = max((w.get("temp") or 0 for w in nxt), default=0)
        if hot >= 35:
            tips.append(f"Ngoài trời nắng nóng tới {hot:.0f}°C trong 3 giờ tới.")
        if not tips:
            tips.append("Chưa thấy chỉ số nào có khả năng vượt ngưỡng trong 3 giờ tới.")
        return tips


def report_lines(result):
    """Kết quả Random Forest dạng tin Zalo (chỉ để tham khảo)."""
    if not result:
        return ["🌲 Random Forest: đang khởi động…"]
    if not result.get("predictions"):
        return [f"🌲 Random Forest: {result.get('note') or 'chưa có dự đoán'}"]
    lines = ["🌲 Phân tích Random Forest (tham khảo — thiết bị vẫn bật/tắt theo ngưỡng):"]
    for p in result["predictions"]:
        lines.append(f"• +{p['hours']} giờ: 🌡 {fmt('t', p['t'])} | 💧 {fmt('h', p['h'])} | 🌱 {fmt('GT_AMDAT', p['GT_AMDAT'])}")
    lines += [f"👉 {tip}" for tip in result.get("advice") or []]
    if result.get("importance"):
        lines.append("Ảnh hưởng nhiều nhất: " + ", ".join(f"{i['name'].lower()} {i['pct']}%" for i in result["importance"][:3]))
    return lines


def _mean(rows, key):
    vals = [num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None
