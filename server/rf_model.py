"""Dự báo 1–3 giờ tới bằng Random Forest + thời tiết ngoài trời — CHỈ ĐỂ THAM KHẢO.

Thiết bị vẫn bật/tắt theo ngưỡng (firmware ESP32). Phương pháp chi tiết: docs/PHUONG_PHAP_DU_BAO.md

Tóm tắt:
- Dữ liệu: mỗi giờ H lấy "trạng thái" = trung bình 15 phút cuối giờ của nhiệt độ, độ ẩm KK, độ ẩm đất, ánh sáng,
  pH, độ mặn, tỉ lệ bật thiết bị; ghép thời tiết Open-Meteo giờ H và giờ H+1..H+3.
- Làm sạch: bỏ giờ có cảm biến ngoài khoảng vật lý, độ ẩm đất đúng 0% (cảm biến chưa cắm/đứt dây),
  và giờ có thay đổi vượt mức vật lý hợp lý (nhảy số khi cắm lại cảm biến).
- Mô hình: MỘT RandomForestRegressor nhiều đầu ra, dự báo TRỰC TIẾP mức thay đổi sau 1, 2, 3 giờ
  của 5 chỉ số (15 đầu ra) — không lặp từng bước nên sai số không cộng dồn.
- Kiểm chứng: walk-forward (TimeSeriesSplit, chừa khoảng trống 3 giờ giữa tập học và tập thử), so với
  mốc chuẩn "giữ nguyên" (persistence: giá trị sau h giờ = giá trị hiện tại). Chỉ số/mốc giờ nào mô hình
  KHÔNG tốt hơn mốc chuẩn thì hiển thị giá trị giữ nguyên và ghi rõ. Chưa đủ dữ liệu kiểm chứng thì chưa dự báo.
- Khoảng sai số: phân vị 80% của sai số tuyệt đối đo được khi kiểm chứng.
"""
import logging
import math
import time

import numpy as np

from sensors import SENSORS, fmt, is_valid, num, thresholds

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.model_selection import TimeSeriesSplit
except ImportError:
    RandomForestRegressor = TimeSeriesSplit = None

log = logging.getLogger("rf")

INDOOR = ["t", "h", "GT_AMDAT", "ANHSANG", "GT_PH", "GT_DOMAN"]
DEVICES = ["QUAT", "SUONG", "BOM", "DEN", "MO"]
OUT = ["temp", "hum", "cloud", "rad", "precip", "wind"]   # thời tiết giờ hiện tại
OUT_FUT = ["temp", "hum", "rad", "precip"]                 # thời tiết các giờ tới (dự báo Open-Meteo)
TARGETS = ["t", "h", "GT_AMDAT", "GT_PH", "GT_DOMAN"]
HORIZONS = (1, 2, 3)
OUTPUTS = [(hz, k) for hz in HORIZONS for k in TARGETS]
# Thay đổi lớn nhất hợp lý trong 1 giờ trong nhà nấm; vượt quá = lỗi cảm biến, bỏ khi học
MAX_JUMP = {"t": 6.0, "h": 25.0, "GT_AMDAT": 25.0, "GT_PH": 1.0, "GT_DOMAN": 300.0}

FEATURES = (INDOOR + DEVICES + [f"o_{k}" for k in OUT]
            + [f"f{hz}_{k}" for hz in HORIZONS for k in OUT_FUT] + ["hour_sin", "hour_cos"])
GROUPS = {  # gộp đặc trưng thành nhóm dễ hiểu khi giải thích "yếu tố ảnh hưởng"
    "Nhiệt độ ngoài trời": ["o_temp"] + [f"f{hz}_temp" for hz in HORIZONS],
    "Nắng (bức xạ mặt trời)": ["o_rad"] + [f"f{hz}_rad" for hz in HORIZONS],
    "Độ ẩm ngoài trời": ["o_hum"] + [f"f{hz}_hum" for hz in HORIZONS],
    "Mưa": ["o_precip"] + [f"f{hz}_precip" for hz in HORIZONS],
    "Mây": ["o_cloud"],
    "Gió": ["o_wind"],
    "Giờ trong ngày": ["hour_sin", "hour_cos"],
    "Thiết bị đang chạy": DEVICES,
    "Chỉ số trong nhà hiện tại": INDOOR,
}
MIN_SAMPLES = 36   # cần >= 36 mẫu (giờ) để vừa học vừa kiểm chứng walk-forward 3 lần
STATE_SEC = 900    # trạng thái = trung bình 15 phút gần nhất (khi học: 15 phút cuối mỗi giờ)
RETRAIN_SEC = 3600
RF_PARAMS = dict(n_estimators=200, max_depth=8, min_samples_leaf=3, random_state=42, n_jobs=1)
METHOD = ("Random Forest nhiều đầu ra (200 cây), dự báo trực tiếp mức thay đổi sau 1–3 giờ; "
          "kiểm chứng walk-forward theo thời gian, so với mốc chuẩn 'giữ nguyên giá trị'")


def _features(state, devices, weather_at, hour_ts):
    """weather_at(ts) -> thời tiết của giờ chứa ts (dict) hoặc None."""
    hour = time.localtime(hour_ts).tm_hour
    w0 = weather_at(hour_ts) or {}
    row = [state.get(k) for k in INDOOR] + [devices.get(k, 0) or 0 for k in DEVICES]
    row += [w0.get(k) for k in OUT]
    for hz in HORIZONS:
        w = weather_at(hour_ts + hz * 3600) or {}
        row += [w.get(k) for k in OUT_FUT]
    row += [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24)]
    return None if any(v is None for v in row) else row


def _clean(r):
    """Giờ dùng được: mọi chỉ số trong khoảng vật lý; độ ẩm đất đúng 0% = cảm biến chưa cắm/đứt dây."""
    return r is not None and all(is_valid(k, r.get(k)) for k in INDOOR) and r["GT_AMDAT"] > 0.5


class RFAnalyzer:
    def __init__(self, store, weather):
        self.store = store
        self.weather = weather
        self.model = None
        self.eval = {}      # (giờ, chỉ số) -> {"mae", "mae_base", "skill", "q80", "q80_base", "use_rf"}
        self.info = {"name": "Random Forest", "method": METHOD, "trained": False, "validated": False,
                     "samples": 0, "skipped": 0, "period": None, "trained_at": None,
                     "evaluation": [], "importance": [], "note": "Đang chờ dữ liệu cảm biến + thời tiết"}
        self.result = None
        self.last_train = 0

    # ------------------------------------------------------------------ dữ liệu
    def _dataset(self, now):
        hours = self.store.hourly_readings(now - 30 * 86400, min_samples=10, tail_sec=STATE_SEC)
        by_hour = {r["hour"]: r for r in hours}
        weather = self.store.weather_between(now - 31 * 86400, now)
        X, Y, T, skipped = [], [], [], 0
        for h in sorted(by_hour):
            r = by_hour[h]
            future = [by_hour.get(h + hz * 3600) for hz in HORIZONS]
            if any(f is None for f in future):
                continue
            if not _clean(r) or not all(_clean(f) for f in future):
                skipped += 1
                continue
            dy = [f[k] - r[k] for f in future for k in TARGETS]       # thứ tự giống OUTPUTS
            if any(abs(d) > MAX_JUMP[k] * hz for (hz, k), d in zip(OUTPUTS, dy)):
                skipped += 1
                continue
            x = _features(r, r, weather.get, h)  # thời tiết các giờ sau: số đo thật (thay cho dự báo)
            if x is not None:
                X.append(x)
                Y.append(dy)
                T.append(h)
        return np.array(X, dtype=float), np.array(Y, dtype=float), T, skipped

    # ------------------------------------------------------------------ huấn luyện + kiểm chứng
    def train(self, now=None):
        now = now or time.time()
        self.last_train = now
        if RandomForestRegressor is None:
            self.info["note"] = "Chưa cài scikit-learn"
            return
        X, Y, T, skipped = self._dataset(now)
        self.info.update(samples=len(X), skipped=skipped,
                         period=[T[0], T[-1] + 3600] if T else None)
        if len(X) < MIN_SAMPLES:
            self.model, self.eval = None, {}
            self.info.update(trained=False, validated=False, evaluation=[], importance=[], note=(
                f"Đang thu thập: {len(X)}/{MIN_SAMPLES} mẫu (mỗi mẫu = 1 giờ có đủ cảm biến 3 giờ sau + thời tiết)"
                + (f"; đã loại {skipped} giờ cảm biến lỗi/nhảy số" if skipped else "")
                + ". Chưa dự báo khi chưa kiểm chứng được."))
            return

        # Walk-forward: học trên quá khứ, thử trên các giờ ngay sau đó; chừa 3 giờ để tránh rò rỉ dữ liệu
        splits = min(5, len(X) // 12)
        err_m, err_b = [], []
        for tr, te in TimeSeriesSplit(n_splits=splits, gap=max(HORIZONS)).split(X):
            m = RandomForestRegressor(**RF_PARAMS).fit(X[tr], Y[tr])
            err_m.append(np.abs(m.predict(X[te]) - Y[te]))
            err_b.append(np.abs(Y[te]))   # mốc chuẩn "giữ nguyên": thay đổi dự báo = 0
        err_m, err_b = np.vstack(err_m), np.vstack(err_b)
        self.eval, table = {}, []
        for j, (hz, k) in enumerate(OUTPUTS):
            mae, mae_b = float(err_m[:, j].mean()), float(err_b[:, j].mean())
            skill = (1 - mae / mae_b) * 100 if mae_b > 1e-9 else 0.0
            e = {"mae": mae, "mae_base": mae_b, "skill": skill, "use_rf": skill > 0,
                 "q80": float(np.quantile(err_m[:, j], 0.8)), "q80_base": float(np.quantile(err_b[:, j], 0.8))}
            self.eval[(hz, k)] = e
            table.append({"hours": hz, "key": k, "mae": round(mae, 3), "mae_base": round(mae_b, 3),
                          "skill": round(skill), "use_rf": e["use_rf"]})

        model = RandomForestRegressor(**RF_PARAMS).fit(X, Y)
        imp = dict(zip(FEATURES, model.feature_importances_))
        groups = sorted(((name, sum(imp[f] for f in feats)) for name, feats in GROUPS.items()), key=lambda g: -g[1])
        self.model = model
        better = sum(1 for e in self.eval.values() if e["use_rf"])
        self.info.update(
            trained=True, validated=True, trained_at=now, evaluation=table,
            test_samples=int(len(err_m)), folds=splits,
            importance=[{"name": n, "pct": round(v * 100)} for n, v in groups if v >= 0.03][:5],
            note=(f"Học từ {len(X)} mẫu giờ, kiểm chứng walk-forward {splits} lần trên {len(err_m)} giờ chưa thấy; "
                  f"mô hình tốt hơn mốc 'giữ nguyên' ở {better}/{len(OUTPUTS)} chỉ số × mốc giờ"
                  + (f"; đã loại {skipped} giờ cảm biến lỗi" if skipped else "")),
        )
        log.info("Random Forest: %d mẫu, %d lần kiểm chứng, tốt hơn mốc chuẩn %d/%d", len(X), splits, better, len(OUTPUTS))

    # ------------------------------------------------------------------ dự báo
    def analyze(self, recent_rows, data, now=None):
        """recent_rows: số liệu gần đây (cũ -> mới); data: bản ghi Firebase mới nhất."""
        now = now or time.time()
        if now - self.last_train >= RETRAIN_SEC or (self.model is None and now - self.last_train >= 600):
            self.train(now)
        th = thresholds(data)
        live = {k: num(data.get(k)) for k in INDOOR}
        rows = [r for r in recent_rows if r["ts"] >= now - STATE_SEC] or recent_rows[-1:]
        state = {k: _mean(rows, k) for k in INDOOR}       # đầu vào: trung bình 15 phút (giống lúc học)
        devices = {k: _mean(rows, k) or 0 for k in DEVICES}
        predictions = []
        x = _features(state, devices, self.weather.at, int(now // 3600 * 3600)) if self.model is not None else None
        if x is not None:
            dy = dict(zip(OUTPUTS, self.model.predict(np.array([x], dtype=float))[0]))
            for hz in HORIZONS:
                row = {"hours": hz, "range": {}, "basis": {}}
                for k in TARGETS:
                    base = live[k] if is_valid(k, live[k]) else state[k]
                    if base is None or (k == "GT_AMDAT" and (live[k] or 0) <= 0.5):
                        row[k] = None   # độ ẩm đất 0% = cảm biến lỗi -> không dự báo
                        continue
                    e = self.eval[(hz, k)]
                    lo, hi = SENSORS[k]["phys"]
                    value = base + float(dy[(hz, k)]) if e["use_rf"] else base
                    err = e["q80"] if e["use_rf"] else e["q80_base"]
                    digits = 2 if k == "GT_PH" else 1
                    row[k] = round(min(max(value, lo), hi), digits)
                    row["range"][k] = [round(max(value - err, lo), digits), round(min(value + err, hi), digits)]
                    row["basis"][k] = "rf" if e["use_rf"] else "persist"
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
        """So số đo hiện tại + dự báo với ngưỡng: đang ngoài ngưỡng (luôn báo, kể cả khi mô hình đang học),
        bao lâu nữa trở lại, hoặc sắp vượt ngưỡng. Chỉ dùng ô dự báo của mô hình (không dùng ô 'giữ nguyên')."""
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
            series = [(p["hours"], p[k]) for p in predictions
                      if p.get(k) is not None and p["basis"].get(k) == "rf"]
            name = SENSORS[k]["name"]
            if out_of(v_now):  # ĐANG ngoài ngưỡng: luôn báo, không cần mô hình
                out_now = True
                text = f"{name} đang {fmt(k, v_now)} ({limit_text}) — {now_action}"
                if series:
                    back = next(((hrs, v) for hrs, v in series if not out_of(v)), None)
                    text += (f"; dự báo khoảng {back[0]} giờ nữa trở lại trong ngưỡng (~{fmt(k, back[1])})" if back
                             else "; dự báo vẫn chưa về ngưỡng trong 3 giờ tới — kiểm tra thiết bị")
                tips.append(text + ".")
            else:
                first = next(((hrs, v) for hrs, v in series if out_of(v)), None)
                if first:
                    tips.append(f"{name} có thể tới khoảng {fmt(k, first[1])} sau {first[0]} giờ ({limit_text}) — {soon_action}.")
        nxt = [self.weather.at(now + k * 3600) for k in HORIZONS]
        nxt = [w for w in nxt if w]
        rain = sum(w.get("precip") or 0 for w in nxt)
        if rain >= 1:
            tips.append(f"Ngoài trời sắp mưa (~{rain:.1f} mm trong 3 giờ tới) — độ ẩm có thể tăng, nhiệt độ có thể giảm.")
        hot = max((w.get("temp") or 0 for w in nxt), default=0)
        if hot >= 35:
            tips.append(f"Ngoài trời nắng nóng tới {hot:.0f}°C trong 3 giờ tới.")
        if predictions and (live.get("GT_AMDAT") or 0) <= 0.5:
            tips.append("Độ ẩm đất đang báo 0% — cảm biến có thể chưa cắm/đứt dây nên chưa dự báo độ ẩm đất.")
        if not predictions and not out_now:
            tips.append("Các chỉ số đang trong ngưỡng. Chưa dự báo 3 giờ tới vì mô hình chưa đủ dữ liệu để kiểm chứng.")
        elif not tips:
            tips.append("Chưa thấy chỉ số nào có khả năng vượt ngưỡng trong 3 giờ tới.")
        return tips


def report_lines(result):
    """Kết quả dự báo dạng tin Zalo (chỉ để tham khảo)."""
    if not result:
        return ["🌲 Random Forest: đang khởi động…"]
    if not result.get("predictions"):
        return ([f"🌲 Random Forest: {result.get('note') or 'chưa có dự báo'}"]
                + [f"👉 {tip}" for tip in result.get("advice") or []])
    lines = ["🌲 Dự báo Random Forest (tham khảo — thiết bị vẫn bật/tắt theo ngưỡng; '=' là giữ nguyên giá trị):"]
    icons = {"t": "🌡", "h": "💧", "GT_AMDAT": "🌱", "GT_PH": "⚗️ pH", "GT_DOMAN": "🧂"}
    for p in result["predictions"]:
        cells = [f"{icons[k]} {fmt(k, p.get(k))}{'=' if p.get('basis', {}).get(k) == 'persist' else ''}" for k in TARGETS]
        lines.append(f"• +{p['hours']} giờ: " + " | ".join(cells))
    lines += [f"👉 {tip}" for tip in result.get("advice") or []]
    best = [e for e in result.get("evaluation") or [] if e["hours"] == 1 and e["use_rf"]]
    if best:
        lines.append("📏 Kiểm chứng 1 giờ tới: " + ", ".join(
            f"{SENSORS[e['key']]['name'].lower()} sai số TB {fmt(e['key'], e['mae'])} (tốt hơn 'giữ nguyên' {e['skill']}%)"
            for e in best))
    return lines


def _mean(rows, key):
    vals = [num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None
