"""Dự báo 1–3 giờ tới bằng Random Forest + thời tiết ngoài trời — CHỈ ĐỂ THAM KHẢO.

Thiết bị vẫn bật/tắt theo ngưỡng (firmware ESP32). Phương pháp chi tiết: docs/PHUONG_PHAP_DU_BAO.md

Tóm tắt:
- Dữ liệu: cứ 15 phút lấy 1 "trạng thái" = trung bình 15 phút của nhiệt độ, độ ẩm KK, độ ẩm đất, ánh sáng,
  pH, độ mặn, tỉ lệ bật thiết bị; ghép thời tiết Open-Meteo lúc đó và lúc cần dự báo.
- Mẫu = (trạng thái lúc τ, mốc h ∈ {1,2,3} giờ) -> mức thay đổi của 5 chỉ số tới lúc τ + h.
- Làm sạch: bỏ trạng thái có cảm biến ngoài khoảng vật lý, độ ẩm đất đúng 0% (cảm biến chưa cắm/đứt dây),
  và mẫu có thay đổi vượt mức vật lý hợp lý (nhảy số khi cắm lại cảm biến).
- Mô hình: MỘT RandomForestRegressor, mốc giờ h là một biến đầu vào, dự báo TRỰC TIẾP từng mốc
  (không lặp từng bước nên sai số không cộng dồn).
- Kiểm chứng: walk-forward theo thời gian — mỗi lần thử trên 1 đoạn thời gian sau, chỉ học các mẫu mà kết quả
  đã biết TRƯỚC khi đoạn thử bắt đầu (không rò rỉ dữ liệu). So với mốc chuẩn "giữ nguyên" (persistence).
  Chỉ số/mốc giờ nào mô hình KHÔNG tốt hơn mốc chuẩn thì hiển thị giá trị giữ nguyên và ghi rõ.
- Khoảng sai số: phân vị 80% của sai số tuyệt đối đo được khi kiểm chứng.
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
OUT = ["temp", "hum", "cloud", "rad", "precip", "wind"]   # thời tiết lúc hiện tại
OUT_FUT = ["temp", "hum", "rad", "precip"]                 # thời tiết lúc cần dự báo (dự báo Open-Meteo)
TARGETS = ["t", "h", "GT_AMDAT", "GT_PH", "GT_DOMAN"]
HORIZONS = (1, 2, 3)
OUTPUTS = [(hz, k) for hz in HORIZONS for k in TARGETS]
# Thay đổi lớn nhất hợp lý trong 1 giờ trong nhà nấm; vượt quá = lỗi cảm biến, bỏ khi học
MAX_JUMP = {"t": 6.0, "h": 25.0, "GT_AMDAT": 25.0, "GT_PH": 1.0, "GT_DOMAN": 300.0}

FEATURES = (INDOOR + DEVICES + [f"o_{k}" for k in OUT] + [f"f_{k}" for k in OUT_FUT]
            + ["hour_sin", "hour_cos", "horizon"])
GROUPS = {  # gộp đặc trưng thành nhóm dễ hiểu khi giải thích "yếu tố ảnh hưởng"
    "Nhiệt độ ngoài trời": ["o_temp", "f_temp"],
    "Nắng (bức xạ mặt trời)": ["o_rad", "f_rad"],
    "Độ ẩm ngoài trời": ["o_hum", "f_hum"],
    "Mưa": ["o_precip", "f_precip"],
    "Mây": ["o_cloud"],
    "Gió": ["o_wind"],
    "Giờ trong ngày": ["hour_sin", "hour_cos"],
    "Dự báo xa hay gần (1–3 giờ)": ["horizon"],
    "Thiết bị đang chạy": DEVICES,
    "Chỉ số trong nhà hiện tại": INDOOR,
}
STEP = 900          # 1 trạng thái = trung bình 15 phút
MIN_SAMPLES = 60    # ~7 giờ chạy liên tục (mỗi 15 phút cho tối đa 3 mẫu: +1, +2, +3 giờ)
MIN_TRAIN, MIN_TEST = 24, 6
FOLDS = 4
RETRAIN_SEC = 3600
RF_PARAMS = dict(n_estimators=200, max_depth=10, min_samples_leaf=3, random_state=42, n_jobs=1)
METHOD = ("Random Forest (200 cây), dự báo trực tiếp mức thay đổi sau 1–3 giờ; "
          "kiểm chứng walk-forward theo thời gian, so với mốc chuẩn 'giữ nguyên giá trị'")


def _features(state, devices, w_now, w_fut, t_mid, hz):
    hour = time.localtime(t_mid).tm_hour + time.localtime(t_mid).tm_min / 60
    row = [state.get(k) for k in INDOOR] + [devices.get(k, 0) or 0 for k in DEVICES]
    row += [(w_now or {}).get(k) for k in OUT] + [(w_fut or {}).get(k) for k in OUT_FUT]
    row += [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24), hz]
    return None if any(v is None for v in row) else row


def _clean(r):
    """Trạng thái dùng được: mọi chỉ số trong khoảng vật lý; độ ẩm đất đúng 0% = cảm biến chưa cắm/đứt dây."""
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
        buckets = self.store.bucket_readings(now - 30 * 86400, size=STEP)
        weather = self.store.weather_between(now - 31 * 86400, now)
        w_at = lambda ts: weather.get(int(ts // 3600 * 3600))
        X, Y, T, HZ, skipped = [], [], [], [], 0
        bad = {b for b, r in buckets.items() if not _clean(r)}
        for b in sorted(buckets):
            if b in bad:
                continue
            r, t_mid = buckets[b], b + STEP / 2
            for hz in HORIZONS:
                f = buckets.get(b + hz * 3600)
                if f is None:
                    continue
                if b + hz * 3600 in bad:
                    skipped += 1
                    continue
                dy = [f[k] - r[k] for k in TARGETS]
                if any(abs(d) > MAX_JUMP[k] * hz for k, d in zip(TARGETS, dy)):
                    skipped += 1  # nhảy số bất thường (cắm lại cảm biến...) -> không phải diễn biến thật
                    continue
                # khi học: thời tiết lúc τ + h là số đo thật (thay cho dự báo)
                x = _features(r, r, w_at(t_mid), w_at(t_mid + hz * 3600), t_mid, hz)
                if x is not None:
                    X.append(x)
                    Y.append(dy)
                    T.append(b)
                    HZ.append(hz)
        return (np.array(X, dtype=float), np.array(Y, dtype=float), np.array(T, dtype=float),
                np.array(HZ, dtype=int), skipped + len(bad))

    # ------------------------------------------------------------------ huấn luyện + kiểm chứng
    @staticmethod
    def _folds(T, HZ):
        """Walk-forward theo thời gian: chia khoảng thời gian thành FOLDS + 1 đoạn, thử lần lượt trên các đoạn sau;
        tập học chỉ gồm mẫu mà kết quả (lúc τ + h) đã có TRƯỚC khi đoạn thử bắt đầu."""
        t0, t1 = T.min(), T.max() + STEP
        edges = np.linspace(t0, t1, FOLDS + 2)
        out = []
        for a, b in zip(edges[1:-1], edges[2:]):
            te = np.where((T >= a) & (T < b))[0]
            tr = np.where(T + STEP + HZ * 3600 <= a)[0]
            if len(tr) >= MIN_TRAIN and len(te) >= MIN_TEST:
                out.append((tr, te))
        return out

    def train(self, now=None):
        now = now or time.time()
        self.last_train = now
        if RandomForestRegressor is None:
            self.info["note"] = "Chưa cài scikit-learn"
            return
        X, Y, T, HZ, skipped = self._dataset(now)
        self.info.update(samples=len(X), skipped=skipped,
                         period=[float(T.min()), float(T.max()) + STEP] if len(T) else None)
        folds = self._folds(T, HZ) if len(X) >= MIN_SAMPLES else []
        if len(folds) < 2:
            self.model, self.eval = None, {}
            need = max(0, MIN_SAMPLES - len(X))
            self.info.update(trained=False, validated=False, evaluation=[], importance=[], note=(
                f"Đang thu thập: {len(X)}/{MIN_SAMPLES} mẫu"
                + (f" (còn khoảng {max(1, round(need / 12))} giờ máy chủ chạy liên tục)" if need
                   else " — cần thêm dữ liệu theo thời gian để kiểm chứng")
                + (f"; đã loại {skipped} đoạn cảm biến lỗi/nhảy số" if skipped else "")
                + ". Chưa dự báo khi chưa kiểm chứng được."))
            return

        err_m, err_b, hz_te = [], [], []
        for tr, te in folds:
            m = RandomForestRegressor(**RF_PARAMS).fit(X[tr], Y[tr])
            err_m.append(np.abs(m.predict(X[te]) - Y[te]))
            err_b.append(np.abs(Y[te]))   # mốc chuẩn "giữ nguyên": thay đổi dự báo = 0
            hz_te.append(HZ[te])
        err_m, err_b, hz_te = np.vstack(err_m), np.vstack(err_b), np.concatenate(hz_te)
        self.eval, table = {}, []
        for hz in HORIZONS:
            sel = hz_te == hz
            for j, k in enumerate(TARGETS):
                if not sel.any():
                    continue
                em, eb = err_m[sel, j], err_b[sel, j]
                mae, mae_b = float(em.mean()), float(eb.mean())
                skill = (1 - mae / mae_b) * 100 if mae_b > 1e-9 else 0.0
                e = {"mae": mae, "mae_base": mae_b, "skill": skill, "use_rf": skill > 0,
                     "q80": float(np.quantile(em, 0.8)), "q80_base": float(np.quantile(eb, 0.8))}
                self.eval[(hz, k)] = e
                table.append({"hours": hz, "key": k, "mae": round(mae, 3), "mae_base": round(mae_b, 3),
                              "skill": round(skill), "use_rf": e["use_rf"], "n": int(sel.sum())})

        model = RandomForestRegressor(**RF_PARAMS).fit(X, Y)
        imp = dict(zip(FEATURES, model.feature_importances_))
        groups = sorted(((name, sum(imp[f] for f in feats)) for name, feats in GROUPS.items()), key=lambda g: -g[1])
        self.model = model
        better = sum(1 for e in self.eval.values() if e["use_rf"])
        self.info.update(
            trained=True, validated=True, trained_at=now, evaluation=table,
            test_samples=int(len(err_m)), folds=len(folds),
            importance=[{"name": n, "pct": round(v * 100)} for n, v in groups if v >= 0.03][:5],
            note=(f"Học từ {len(X)} mẫu, kiểm chứng walk-forward {len(folds)} lần trên {len(err_m)} mẫu chưa thấy; "
                  f"mô hình tốt hơn mốc 'giữ nguyên' ở {better}/{len(self.eval)} chỉ số × mốc giờ"
                  + (f"; đã loại {skipped} đoạn cảm biến lỗi" if skipped else "")),
        )
        log.info("Random Forest: %d mẫu, %d lần kiểm chứng, tốt hơn mốc chuẩn %d/%d", len(X), len(folds), better, len(self.eval))

    # ------------------------------------------------------------------ dự báo
    def analyze(self, recent_rows, data, now=None):
        """recent_rows: số liệu gần đây (cũ -> mới); data: bản ghi Firebase mới nhất."""
        now = now or time.time()
        if now - self.last_train >= RETRAIN_SEC or (self.model is None and now - self.last_train >= 600):
            self.train(now)
        th = thresholds(data)
        live = {k: num(data.get(k)) for k in INDOOR}
        rows = [r for r in recent_rows if r["ts"] >= now - STEP] or recent_rows[-1:]
        state = {k: _mean(rows, k) for k in INDOOR}       # đầu vào: trung bình 15 phút (giống lúc học)
        devices = {k: _mean(rows, k) or 0 for k in DEVICES}
        t_mid = now - STEP / 2
        predictions = []
        if self.model is not None:
            for hz in HORIZONS:
                x = _features(state, devices, self.weather.at(t_mid), self.weather.at(t_mid + hz * 3600), t_mid, hz)
                if x is None:
                    break
                dy = dict(zip(TARGETS, self.model.predict(np.array([x], dtype=float))[0]))
                row = {"hours": hz, "range": {}, "basis": {}}
                for k in TARGETS:
                    base = live[k] if is_valid(k, live[k]) else state[k]
                    e = self.eval.get((hz, k))
                    if base is None or e is None or (k == "GT_AMDAT" and (live[k] or 0) <= 0.5):
                        row[k] = None   # độ ẩm đất 0% = cảm biến lỗi -> không dự báo
                        continue
                    lo, hi = SENSORS[k]["phys"]
                    value = base + float(dy[k]) if e["use_rf"] else base
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
            if k == "GT_AMDAT" and v_now is not None and v_now <= 0.5:
                # đúng 0%: gần như chắc chắn cảm biến chưa cắm vào đất/đứt dây, nhưng firmware vẫn bơm theo số này
                out_now = True
                tips.append("Độ ẩm đất đang báo 0%" + (f" (dưới ngưỡng {fmt(k, th['SET_AMDAT'])}) — máy bơm đang bật "
                            "theo ngưỡng và có thể chạy liên tục" if th["SET_AMDAT"] else "")
                            + ". Giá trị đúng 0% thường do cảm biến chưa cắm vào đất hoặc đứt dây — kiểm tra cảm biến "
                            "(chưa dự báo độ ẩm đất khi cảm biến báo 0%).")
                continue
            if not out_of or v_now is None:
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
