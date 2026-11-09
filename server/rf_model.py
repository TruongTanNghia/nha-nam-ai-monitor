"""Dự báo 1–3 giờ tới bằng Random Forest + thời tiết ngoài trời — CHỈ ĐỂ THAM KHẢO.

Thiết bị vẫn bật/tắt theo ngưỡng (firmware ESP32). Phương pháp chi tiết: docs/PHUONG_PHAP_DU_BAO.md

Tóm tắt:
- Dữ liệu: cứ 15 phút lấy 1 "trạng thái" = trung bình 15 phút của nhiệt độ, độ ẩm KK, độ ẩm đất, ánh sáng,
  pH, độ mặn, tỉ lệ bật thiết bị; ghép thời tiết Open-Meteo lúc đó và lúc cần dự báo.
- Mẫu = (trạng thái lúc τ + xu hướng 1 giờ qua, mốc h ∈ {1,2,3} giờ) -> mức thay đổi của 6 chỉ số tới lúc τ + h.
  Học trên toàn bộ dữ liệu đã lưu; giờ đầu sau khi bật máy (chưa có xu hướng) vẫn dùng, xu hướng để trống.
- Làm sạch: bỏ trạng thái có cảm biến ngoài khoảng vật lý hoặc đọc lỗi (nhiệt độ/độ ẩm/pH đúng 0), đoạn số liệu
  ĐỨNG YÊN (mạch ESP32 tắt/mất mạng, Firebase giữ số cũ: cả 6 cảm biến không đổi suốt 15 phút), và mẫu có thay đổi
  vượt mức vật lý hợp lý (nhảy số khi cắm lại cảm biến).
- Mô hình: MỘT RandomForestRegressor, mốc giờ h là một biến đầu vào, dự báo TRỰC TIẾP từng mốc
  (không lặp từng bước nên sai số không cộng dồn).
- Dự báo = trung bình 15 phút hiện tại + a × (thay đổi Random Forest dự báo) + b × (thay đổi hồi quy tuyến tính Ridge
  dự báo), a + b ≤ 1, chọn riêng cho từng chỉ số × mốc giờ. a = b = 0 là "giữ nguyên" (persistence). Ridge ít tham
  số nên dùng được khi dữ liệu còn ít; Random Forest bắt được quan hệ phi tuyến khi dữ liệu nhiều.
- Kiểm chứng: walk-forward theo thời gian — mỗi lần thử trên 1 đoạn thời gian sau, chỉ học các mẫu mà kết quả
  đã biết TRƯỚC khi đoạn thử bắt đầu (không rò rỉ dữ liệu). Hệ số a, b của mỗi đoạn thử cũng chỉ được chọn từ các
  đoạn TRƯỚC nó, nên sai số báo cáo là sai số thật trên dữ liệu chưa thấy. So với mốc chuẩn "giữ nguyên".
  Ô nào a = b = 0 (mô hình chưa giúp được) thì dự báo = giữ nguyên, ghi rõ và không dùng để đưa gợi ý.
- Khoảng sai số: phân vị 80% của sai số tuyệt đối đo được khi kiểm chứng.
"""
import logging
import math
import random
import time

import numpy as np

import config
from sensors import SENSORS, fmt, is_valid, num, thresholds

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
except ImportError:
    RandomForestRegressor = None

log = logging.getLogger("rf")

INDOOR = ["t", "h", "GT_AMDAT", "ANHSANG", "GT_PH", "GT_DOMAN"]
DEVICES = ["QUAT", "SUONG", "BOM", "DEN", "MO"]
OUT = ["temp", "hum", "cloud", "rad", "precip", "wind"]   # thời tiết lúc hiện tại
OUT_FUT = ["temp", "hum", "rad", "precip"]                 # thời tiết lúc cần dự báo (dự báo Open-Meteo)
TREND = ["t", "h", "GT_AMDAT", "ANHSANG"]                  # xu hướng: thay đổi trong 1 giờ qua (đang nóng lên/nguội đi...)
TARGETS = ["t", "h", "GT_AMDAT", "ANHSANG", "GT_PH", "GT_DOMAN"]
LIVE_BASE = {"GT_AMDAT"}
DEMO_STEP = {"GT_PH": (0.1, 0.3)}   # DEMO: ± 1–3 đơn vị; pH ± 0,1–0,3 (± 1–3 pH là vô lý)   # dự báo tính từ số đo mới nhất thay cho trung bình 15 phút
HORIZONS = (1, 2, 3)
OUTPUTS = [(hz, k) for hz in HORIZONS for k in TARGETS]
# Thay đổi lớn nhất hợp lý trong 1 giờ trong nhà nấm; vượt quá = lỗi cảm biến, bỏ khi học
# ánh sáng đổi rất nhanh (mây, bật/tắt đèn, ngày/đêm) -> không lọc theo mức thay đổi
MAX_JUMP = {"t": 6.0, "h": 25.0, "GT_AMDAT": 25.0, "ANHSANG": 70000.0, "GT_PH": 1.0, "GT_DOMAN": 300.0}

FEATURES = (INDOOR + [f"d_{k}" for k in TREND] + DEVICES + [f"o_{k}" for k in OUT] + [f"f_{k}" for k in OUT_FUT]
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
    "Xu hướng 1 giờ qua": [f"d_{k}" for k in TREND],
}
STEP = 900          # 1 trạng thái = trung bình 15 phút
MIN_SAMPLES = 60    # ~7 giờ chạy liên tục (mỗi 15 phút cho tối đa 3 mẫu: +1, +2, +3 giờ)
MAX_SAMPLES = 20000  # ~70 ngày chạy liên tục; nhiều hơn thì chỉ học phần gần nhất (học lại mỗi 15 phút trên máy yếu)
NEED_SAMPLES = 84   # thường cần chừng này mẫu mới đủ 2 lần kiểm chứng walk-forward (~9 giờ chạy liên tục)
MIN_TRAIN, MIN_TEST = 24, 6
FOLDS = 5
WEIGHTS = np.linspace(0, 1, 11)   # hệ số tin Random Forest được thử: 0; 0,1; ...; 1
RETRAIN_SEC = 900    # học lại mỗi 15 phút (mỗi 15 phút có thêm mẫu mới)
RETRY_SEC = 120      # chưa đủ dữ liệu: đếm lại mỗi 2 phút
RF_PARAMS = dict(n_estimators=200, max_depth=10, min_samples_leaf=5, max_features=0.5, random_state=42, n_jobs=1)
METHOD = ("Random Forest (200 cây) + hồi quy tuyến tính Ridge, dự báo trực tiếp mức thay đổi sau 1–3 giờ, kết hợp "
          "với 'giữ nguyên giá trị' theo hệ số chọn bằng kiểm chứng walk-forward theo thời gian")


def _features(state, prev, devices, w_now, w_fut, t_mid, hz):
    """prev: trạng thái 1 giờ trước (để tính xu hướng); chưa có (giờ đầu sau khi bật máy) -> xu hướng để trống (NaN),
    Random Forest (scikit-learn >= 1.4) tự xử lý ô trống nên vẫn học và dự báo được."""
    hour = time.localtime(t_mid).tm_hour + time.localtime(t_mid).tm_min / 60
    row = [state.get(k) for k in INDOOR]
    row += [math.nan if state.get(k) is None or prev.get(k) is None else state[k] - prev[k] for k in TREND]
    row += [devices.get(k, 0) or 0 for k in DEVICES]
    row += [(w_now or {}).get(k) for k in OUT] + [(w_fut or {}).get(k) for k in OUT_FUT]
    row += [math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24), hz]
    return None if any(v is None for v in row) else row


def _clean(r):
    """Trạng thái dùng được: mọi chỉ số trong khoảng vật lý. Độ ẩm đất 0% (cảm biến chưa cắm) KHÔNG loại cả mẫu —
    nhiệt độ, độ ẩm, ánh sáng... vẫn dùng được; lúc rút/cắm lại cảm biến (0% <-> 100%) bị lọc bởi MAX_JUMP,
    và khi đất đang báo 0% thì không dự báo đất."""
    if r is None or (r.get("n", 0) >= 10 and r.get("frozen")):
        return False  # số liệu đứng yên: mạch tắt / mất mạng
    return all(is_valid(k, r.get(k)) for k in INDOOR)


def _linear_fit(X, Y, HZ):
    """Hồi quy tuyến tính Ridge, mỗi mốc giờ 1 mô hình (mức thay đổi tăng theo h, không cộng như 1 biến).
    Ít tham số nên học được từ ít dữ liệu (vd "ngoài trời nóng lên 2°C thì trong nhà nóng lên ~1°C").
    Xu hướng chưa có (NaN) coi là 0 = chưa thấy đổi."""
    out = {}
    for hz in HORIZONS:
        sel = HZ == hz
        if sel.sum() >= 8:
            out[hz] = make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(np.nan_to_num(X[sel]), Y[sel])
    return out


def _linear_predict(models, X, HZ):
    P = np.zeros((len(X), len(TARGETS)))
    for hz, m in models.items():
        sel = HZ == hz
        if sel.any():
            P[sel] = m.predict(np.nan_to_num(X[sel]))
    return P


def _best_mix(rf, lin, y):
    """Hệ số (a, b), a + b ≤ 1, cho sai số tuyệt đối nhỏ nhất của a × Random Forest + b × tuyến tính so với y
    (phần còn lại 1 − a − b là "giữ nguyên"). Hòa thì chọn tổng nhỏ hơn (gần "giữ nguyên" hơn)."""
    if not len(y):
        return 0.0, 0.0
    best = (np.abs(y).mean(), 0.0, 0.0, 0.0)
    for a in WEIGHTS:
        for b in WEIGHTS:
            if a + b <= 1 + 1e-9:
                err = np.abs(a * rf + b * lin - y).mean()
                if err < best[0] - 1e-12:
                    best = (err, a + b, float(a), float(b))
    return best[2], best[3]


class RFAnalyzer:
    def __init__(self, store, weather):
        self.store = store
        self.weather = weather
        self.model = None
        self.linear = {}    # mốc giờ -> hồi quy tuyến tính (Ridge)
        self.eval = {}      # (giờ, chỉ số) -> {"mae", "mae_base", "skill", "q80", "q80_base", "use_rf"}
        self.info = {"name": "Random Forest", "method": METHOD, "trained": False, "validated": False,
                     "samples": 0, "skipped": 0, "period": None, "trained_at": None,
                     "evaluation": [], "importance": [], "note": "Đang chờ dữ liệu cảm biến + thời tiết"}
        self.result = None
        self.last_train = 0
        self.streak_hours = 0.0
        self._demo_ranges = (0, {})

    # ------------------------------------------------------------------ dữ liệu
    def _dataset(self, now):
        # học trên TOÀN BỘ dữ liệu đã lưu (HISTORY_RETENTION_DAYS); thời tiết các giờ cũ lấy từ kho Open-Meteo
        since = now - config.HISTORY_RETENTION_DAYS * 86400
        buckets = self.store.bucket_readings(since, size=STEP)
        weather = self.store.weather_between(since - 86400, now)
        w_at = lambda ts: weather.get(int(ts // 3600 * 3600))
        X, Y, T, HZ, skipped = [], [], [], [], 0
        bad = {b for b, r in buckets.items() if not _clean(r)}
        # số giờ mạch + máy chủ đang chạy liền tới giờ (để ước tính bao lâu nữa có dự báo)
        b, streak = int(now // STEP * STEP) - STEP, 0
        if b not in buckets:
            b -= STEP  # khoảng 15 phút vừa xong có thể chưa đủ số đo
        while b in buckets and b not in bad:
            streak, b = streak + 1, b - STEP
        self.streak_hours = streak * STEP / 3600
        for b in sorted(buckets):
            if b in bad:
                continue
            r, t_mid = buckets[b], b + STEP / 2
            # giờ đầu sau khi bật máy / mạch chạy lại: chưa có trạng thái 1 giờ trước -> xu hướng để trống
            prev = {} if b - 3600 in bad else (buckets.get(b - 3600) or {})
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
                x = _features(r, prev, r, w_at(t_mid), w_at(t_mid + hz * 3600), t_mid, hz)
                if x is not None:
                    X.append(x)
                    Y.append(dy)
                    T.append(b)
                    HZ.append(hz)
        if len(X) > MAX_SAMPLES:  # máy yếu: chỉ học các mẫu gần nhất (dữ liệu xếp theo thời gian)
            X, Y, T, HZ = X[-MAX_SAMPLES:], Y[-MAX_SAMPLES:], T[-MAX_SAMPLES:], HZ[-MAX_SAMPLES:]
        return (np.array(X, dtype=float), np.array(Y, dtype=float), np.array(T, dtype=float),
                np.array(HZ, dtype=int), skipped + len(bad))

    # ------------------------------------------------------------------ huấn luyện + kiểm chứng
    @staticmethod
    def _folds(T, HZ):
        """Walk-forward theo thời gian: chia dữ liệu (xếp theo thời gian) thành FOLDS + 1 đoạn có số mẫu gần bằng nhau,
        thử lần lượt trên các đoạn sau; tập học chỉ gồm mẫu mà kết quả (lúc τ + h) đã có TRƯỚC khi đoạn thử bắt đầu.
        Chia theo số mẫu (không theo khoảng thời gian) để dữ liệu rải rác nhiều ngày vẫn đủ mẫu mỗi đoạn."""
        edges = np.quantile(T, np.linspace(0, 1, FOLDS + 2))
        edges[-1] = T.max() + STEP
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
            # chạy liền S giờ cho khoảng 12·S − 24 mẫu (mỗi mẫu cần số đo thật 1–3 giờ sau)
            hours = max(1, math.ceil(max(0, NEED_SAMPLES - len(X)) / 12 + max(0.0, 2 - self.streak_hours)))
            self.info.update(trained=False, validated=False, evaluation=[], importance=[], need=NEED_SAMPLES, note=(
                f"Đang học: {len(X)}/{NEED_SAMPLES} mẫu. Dự báo đầu tiên sau khoảng {hours} giờ nữa nếu mạch ESP32 "
                f"và máy tính chạy liên tục (đang chạy liền {self.streak_hours:.1f} giờ; máy chủ mới cần khoảng 9 giờ liền). "
                "AI học trên toàn bộ dữ liệu đã lưu, chỉ bỏ các đoạn mạch tắt (số đứng yên)"
                + (f"; đã loại {skipped} đoạn cảm biến lỗi/số liệu đứng yên" if skipped else "")
                + ". Chưa dự báo khi chưa kiểm chứng được."))
            return

        # Dự báo của từng đoạn thử bằng 2 mô hình, mỗi mô hình chỉ học dữ liệu trước đoạn đó
        runs = [(te, RandomForestRegressor(**RF_PARAMS).fit(X[tr], Y[tr]).predict(X[te]),
                 _linear_predict(_linear_fit(X[tr], Y[tr], HZ[tr]), X[te], HZ[te])) for tr, te in folds]
        self.eval, table, n_test = {}, [], 0
        for hz in HORIZONS:
            for j, k in enumerate(TARGETS):
                parts = [(P[HZ[te] == hz, j], L[HZ[te] == hz, j], Y[te][HZ[te] == hz, j]) for te, P, L in runs]
                cat = lambda ps, c: np.concatenate([p[c] for p in ps])
                # hệ số của đoạn i chọn từ các đoạn trước i -> đoạn đầu chỉ dùng để chọn, không chấm điểm
                em, eb = [], []
                for i in range(1, len(parts)):
                    a, b = _best_mix(cat(parts[:i], 0), cat(parts[:i], 1), cat(parts[:i], 2))
                    p, lin, y = parts[i]
                    em.append(np.abs(a * p + b * lin - y))
                    eb.append(np.abs(y))   # mốc chuẩn "giữ nguyên": thay đổi dự báo = 0
                em, eb = np.concatenate(em), np.concatenate(eb)
                if not len(em):
                    continue
                a, b = _best_mix(cat(parts, 0), cat(parts, 1), cat(parts, 2))
                mae, mae_b = float(em.mean()), float(eb.mean())
                if mae_b > 1e-9:
                    skill = max(-100.0, min(100.0, (1 - mae / mae_b) * 100))
                else:  # số liệu không đổi -> giữ nguyên là đúng tuyệt đối
                    skill = 0.0 if mae < 1e-9 else -100.0
                use_rf = a + b > 0 and skill > 0
                if not use_rf:  # ô này dự báo = giữ nguyên -> sai số của dự báo chính là sai số giữ nguyên
                    mae, skill, a, b = mae_b, 0.0, 0.0, 0.0
                e = {"mae": mae, "mae_base": mae_b, "skill": skill, "use_rf": use_rf, "w": a, "w_lin": b,
                     "q80": float(np.quantile(em if use_rf else eb, 0.8))}
                self.eval[(hz, k)] = e
                n_test = max(n_test, len(em))
                table.append({"hours": hz, "key": k, "mae": round(mae, 3), "mae_base": round(mae_b, 3),
                              "skill": round(skill), "use_rf": use_rf, "w": round(a, 1), "w_lin": round(b, 1),
                              "n": int(len(em))})
        if not self.eval:
            self.model = None
            self.info.update(trained=False, validated=False, evaluation=[], importance=[],
                             note=f"Đang thu thập: {len(X)} mẫu — cần thêm dữ liệu theo thời gian để kiểm chứng.")
            return

        model = RandomForestRegressor(**RF_PARAMS).fit(X, Y)
        imp = dict(zip(FEATURES, model.feature_importances_))
        groups = sorted(((name, sum(imp[f] for f in feats)) for name, feats in GROUPS.items()), key=lambda g: -g[1])
        self.model, self.linear = model, _linear_fit(X, Y, HZ)
        better = sum(1 for e in self.eval.values() if e["use_rf"])
        self.info.update(
            trained=True, validated=True, trained_at=now, evaluation=table,
            test_samples=n_test, folds=len(folds) - 1,
            importance=[{"name": n, "pct": round(v * 100)} for n, v in groups if v >= 0.03][:5],
            note=(f"Học từ {len(X)} mẫu, kiểm chứng walk-forward {len(folds) - 1} lần trên {n_test} mẫu chưa thấy; "
                  f"mô hình (Random Forest + tuyến tính) dự báo tốt hơn 'giữ nguyên' ở {better}/{len(self.eval)} chỉ số × mốc giờ, "
                  "các ô còn lại dùng 'giữ nguyên'"
                  + (f"; đã loại {skipped} đoạn cảm biến lỗi" if skipped else "")),
        )
        log.info("Random Forest: %d mẫu, %d lần kiểm chứng, tốt hơn mốc chuẩn %d/%d", len(X), len(folds) - 1, better, len(self.eval))

    # ------------------------------------------------------------------ dự báo
    def analyze(self, recent_rows, data, now=None):
        """recent_rows: số liệu gần đây (cũ -> mới); data: bản ghi Firebase mới nhất."""
        now = now or time.time()
        if now - self.last_train >= (RETRAIN_SEC if self.model is not None else RETRY_SEC):
            try:
                self.train(now)
            except Exception as exc:  # vd ổ đĩa đầy: giữ mô hình đang có, vẫn dự báo, lần sau học lại
                self.last_train = now
                self.info["note"] = f"Học lại lỗi ({exc}) — đang dùng mô hình lần trước"
                log.exception("Random Forest học lại lỗi")
        th = thresholds(data)
        live = {k: num(data.get(k)) for k in INDOOR}
        rows = [r for r in recent_rows if r["ts"] >= now - STEP] or recent_rows[-1:]
        state = {k: _mean(rows, k) for k in INDOOR}       # đầu vào: trung bình 15 phút (giống lúc học)
        old = [r for r in recent_rows if now - 3600 - STEP <= r["ts"] < now - 3600]
        # 15 phút của 1 giờ trước -> xu hướng; lúc đó số liệu đứng yên (mạch tắt) thì chưa có xu hướng -> chưa dự báo
        prev = {} if _frozen(old) else {k: _mean(old, k) for k in INDOOR}
        devices = {k: _mean(rows, k) or 0 for k in DEVICES}
        t_mid = now - STEP / 2
        predictions = []
        if self.model is not None and not _frozen(rows):
            for hz in HORIZONS:
                x = _features(state, prev, devices, self.weather.at(t_mid), self.weather.at(t_mid + hz * 3600), t_mid, hz)
                if x is None:
                    break  # thiếu thời tiết hoặc cảm biến chưa có số đo
                xa = np.array([x], dtype=float)
                dy = dict(zip(TARGETS, self.model.predict(xa)[0]))
                dl = dict(zip(TARGETS, _linear_predict(self.linear, xa, np.array([hz]))[0]))
                row = {"hours": hz, "range": {}, "basis": {}}
                for k in TARGETS:
                    # gốc = trung bình 15 phút (giống lúc học): cảm biến nhảy số (độ mặn, ánh sáng) đỡ lệch hơn 1 lần đo.
                    # Độ ẩm đất ít nhiễu nhưng đổi bậc khi bơm bật/tắt -> số đo mới nhất bám kịp hơn trung bình
                    first, second = (live, state) if k in LIVE_BASE else (state, live)
                    base = first[k] if is_valid(k, first[k]) else (second[k] if is_valid(k, second[k]) else None)
                    e = self.eval.get((hz, k))
                    if base is None or e is None or (k == "GT_AMDAT" and (live[k] or 0) <= 0.5):
                        row[k] = None   # độ ẩm đất 0% = cảm biến lỗi -> không dự báo
                        continue
                    lo, hi = SENSORS[k]["phys"]
                    value = base + e["w"] * float(dy[k]) + e["w_lin"] * float(dl[k])
                    err = e["q80"]
                    digits = 2 if k == "GT_PH" else 1
                    row[k] = round(min(max(value, lo), hi), digits)
                    row["range"][k] = [round(max(value - err, lo), digits), round(min(value + err, hi), digits)]
                    # "persist": kiểm chứng thấy Random Forest chưa giúp được -> dự báo = giữ nguyên, không dùng để gợi ý
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

    def demo(self, data, now=None):
        """DEMO (DEMO_FORECAST=true, máy không có cảm biến): số GIẢ LẬP = số đo hiện tại ± ngẫu nhiên 1–3 đơn vị,
        kẹp trong khoảng đã thu thập. Không phải dự báo của mô hình; không ghi vào nhật ký dự báo / CSV / Sheet."""
        now = now or time.time()
        if now - self._demo_ranges[0] > 600:  # khoảng đã thu thập: tính lại 10 phút/lần (gọi 5 giây/lần khi mô phỏng)
            self._demo_ranges = (now, self.store.sensor_ranges(now - config.HISTORY_RETENTION_DAYS * 86400))
        ranges = self._demo_ranges[1]
        live = {k: num(data.get(k)) for k in INDOOR}
        rng = random.Random(int(now // STEP))   # giữ nguyên trong 15 phút, không nhảy số mỗi lần tải trang
        predictions = []
        for hz in HORIZONS:
            row = {"hours": hz, "range": {}, "basis": {}}
            for k in TARGETS:
                v = live[k]
                if v is None or (k != "GT_AMDAT" and not is_valid(k, v)):
                    row[k] = None
                    continue
                lo, hi = ranges.get(k, SENSORS[k]["phys"])
                d = rng.uniform(*DEMO_STEP.get(k, (1.0, 3.0))) * rng.choice((-1, 1))
                row[k] = round(min(max(v + d, min(lo, v)), max(hi, v)), 2 if k == "GT_PH" else 1)
                row["basis"][k] = "demo"
            predictions.append(row)
        return {
            **self.info, "demo": True, "validated": True, "trained_at": now, "ts": now,
            "note": "DEMO: số giả lập (số đo hiện tại ± 1–3 đơn vị), không phải dự báo của mô hình",
            "now": {k: (round(live[k], 2 if k == "GT_PH" else 1) if live[k] is not None else None) for k in INDOOR},
            "predictions": predictions,
            "advice": self._advice(predictions, live, thresholds(data), now),
        }

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
            ("ANHSANG", (lambda v: v < th["SET_AS"]) if th["SET_AS"] else None,
             f"dưới ngưỡng {fmt('ANHSANG', th['SET_AS'])}", "đèn sẽ bật và màn che mở theo ngưỡng",
             "đèn đang bật và màn che đang mở theo ngưỡng"),
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
    lines = ["🌲 Dự báo Random Forest (tham khảo — thiết bị vẫn bật/tắt theo ngưỡng):"]
    icons = {"t": "🌡", "h": "💧", "GT_AMDAT": "🌱", "ANHSANG": "☀️", "GT_PH": "⚗️ pH", "GT_DOMAN": "🧂"}
    for p in result["predictions"]:
        cells = [f"{icons[k]} {fmt(k, p.get(k))}" for k in TARGETS]
        lines.append(f"• +{p['hours']} giờ: " + " | ".join(cells))
    lines += [f"👉 {tip}" for tip in result.get("advice") or []]
    best = [e for e in result.get("evaluation") or [] if e["hours"] == 1 and e["use_rf"]]
    if best:
        lines.append("📏 Kiểm chứng 1 giờ tới: " + ", ".join(
            f"{SENSORS[e['key']]['name'].lower()} sai số TB {fmt(e['key'], e['mae'])} (tốt hơn 'giữ nguyên' {e['skill']}%)"
            for e in best))
    return lines


def _frozen(rows):
    """Cả 6 cảm biến không đổi suốt khoảng (>= 10 lần đọc) = số liệu đứng yên do mạch tắt / mất mạng."""
    return len(rows) >= 10 and len({tuple(r.get(k) for k in INDOOR) for r in rows}) == 1


def _mean(rows, key):
    vals = [num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None and (key not in SENSORS or is_valid(key, v))]
    return sum(vals) / len(vals) if vals else None
