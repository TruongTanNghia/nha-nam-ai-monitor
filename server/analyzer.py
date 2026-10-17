"""Phân tích dữ liệu cảm biến.

Ba lớp phân tích:
1. Luật chuyên gia: vượt ngưỡng kéo dài, cảm biến hỏng/nhiễu/đứng yên, thiết bị chạy không hiệu quả.
2. Model ML (IsolationForest): phát hiện tổ hợp giá trị bất thường so với lịch sử 24h.
3. Dự báo xu hướng (hồi quy tuyến tính 20 phút gần nhất): ước lượng 30 phút tới.
"""
import logging
import time

import numpy as np

import config
import indices

from forecast import eta_text, forecast_all, rate_text, sentence
from sensors import SENSORS, SENSOR_KEYS, fmt, is_valid, lname, num, thresholds

try:
    from sklearn.ensemble import IsolationForest
except ImportError:  # vẫn chạy được phần luật nếu thiếu scikit-learn
    IsolationForest = None

log = logging.getLogger("analyzer")

SEV_RANK = {"ok": 0, "info": 1, "warning": 2, "critical": 3, "fault": 3}

# Độ lệch chuẩn tối thiểu để chuẩn hoá (tránh chia ~0 khi dữ liệu đứng yên)
MIN_STD = {"t": 0.5, "h": 2, "ANHSANG": 50, "GT_AMDAT": 2, "GT_DOMAN": 20, "GT_PH": 0.1}

ADVICE = {
    "t_high": "Kiểm tra quạt, mở màn che thông gió, có thể phun sương làm mát.",
    "t_low": "Đóng màn che giữ nhiệt, kiểm tra nguồn sưởi nếu có.",
    "h_low": "Tăng phun sương, kiểm tra béc phun và nguồn nước.",
    "soil_low": "Kiểm tra máy bơm, đường ống và lượng nước trong bồn.",
    "salt_high": "Rửa mặn bằng nước sạch, giảm phân bón, kiểm tra nguồn nước tưới.",
    "ph": "Kiểm tra dung dịch tưới, điều chỉnh pH (vôi để tăng, axit nhẹ để giảm).",
}


def finding(key, severity, category, title, detail, sensor=None):
    return {
        "key": key, "severity": severity, "category": category,
        "title": title, "detail": detail, "sensor": sensor,
    }


def sensor_status(key, v, th):
    """Đánh giá 1 giá trị: trả (mức, mô tả). mức: ok|info|warning|critical|fault."""
    if v is None:
        return "fault", "Không có dữ liệu"
    if not is_valid(key, v):
        return "fault", f"Giá trị phi lý ({v})"

    if key == "t":
        s = th["SET_T"]
        if v >= 40 or (s and v >= s + 6):
            return "critical", "Nhiệt độ quá cao"
        if v < 10:
            return "critical", "Nhiệt độ quá thấp"
        if (s and v > s + 3) or v > 36:
            return "warning", "Nhiệt độ cao"
        if v < 15:
            return "warning", "Nhiệt độ thấp"
        if s and v > s:
            return "info", "Trên ngưỡng, quạt xử lý"
        return "ok", "Bình thường"

    if key == "h":
        s = th["SET_H"]
        if s and v < s - 25:
            return "critical", "Không khí quá khô"
        if s and v < s - 10:
            return "warning", "Không khí khô"
        if s and v < s:
            return "info", "Dưới ngưỡng, phun sương xử lý"
        return "ok", "Bình thường"

    if key == "GT_AMDAT":
        s = th["SET_AMDAT"]
        if v < 10 or (s and v < s - 25):
            return "critical", "Đất quá khô"
        if s and v < s - 10:
            return "warning", "Đất khô"
        if s and v < s:
            return "info", "Dưới ngưỡng, bơm xử lý"
        return "ok", "Bình thường"

    if key == "GT_DOMAN":
        s = th["SET_DOMAN"]
        if s and v > s * 1.3:
            return "critical", "Độ mặn rất cao"
        if s and v > s:
            return "warning", "Độ mặn vượt ngưỡng"
        return "ok", "Bình thường"

    if key == "GT_PH":
        lo, hi = th["SET_PHLOW"], th["SET_PHHIGH"]
        if v < 4 or v > 9.5 or (lo and v < lo - 1) or (hi and v > hi + 1):
            return "critical", "pH nguy hiểm"
        if lo and v < lo:
            return "warning", "pH thấp (chua)"
        if hi and v > hi:
            return "warning", "pH cao (kiềm)"
        return "ok", "Bình thường"

    if key == "ANHSANG":
        s = th["SET_AS"]
        if s and v < s:
            return "info", "Dưới ngưỡng, đèn hỗ trợ"
        return "ok", "Bình thường"

    return "ok", "Bình thường"


def _values(rows, key):
    return [(r["ts"], r.get(key)) for r in rows if r.get(key) is not None]


def _window(rows, now, seconds):
    """Các bản ghi trong `seconds` giây gần nhất — chỉ trả về nếu dữ liệu phủ đủ khoảng."""
    win = [r for r in rows if r["ts"] >= now - seconds]
    if len(win) < 2 or win[0]["ts"] > now - seconds * 0.7:
        return None
    return win


def _on_since(rows, device, need_seen_off=True):
    """Thời điểm thiết bị bắt đầu bật liên tục tới hiện tại (None nếu đang tắt).

    need_seen_off: chỉ trả kết quả khi đã thấy lúc thiết bị còn tắt — nếu thiết bị bật từ trước
    khi server theo dõi thì không biết giá trị "trước khi bật" để so sánh hiệu quả.
    """
    if not rows or rows[-1].get(device) != 1:
        return None
    start = rows[-1]["ts"]
    for r in reversed(rows):
        if r.get(device) != 1:
            return start
        start = r["ts"]
    return None if need_seen_off else start


def _value_at(rows, key, ts):
    for r in rows:
        if r["ts"] >= ts and r.get(key) is not None:
            return r[key]
    return None


class Analyzer:
    def __init__(self):
        self.model = None
        self.mean = None
        self.std = None
        self.model_info = {
            "name": "IsolationForest",
            "trained": False,
            "samples": 0,
            "trained_at": None,
            "note": "Cần ít nhất 300 mẫu (~25 phút dữ liệu) để huấn luyện",
        }
        self.last_train = 0
        self.ml_streak = 0

    # ------------------------------------------------------------------ ML
    def maybe_train(self, rows, now, every_sec=1800):
        if IsolationForest is None:
            self.model_info["note"] = "Chưa cài scikit-learn"
            return
        if self.model is not None and now - self.last_train < every_sec:
            return
        if self.model is None and now - self.last_train < 120:
            return  # chưa đủ dữ liệu: thử lại sau 2 phút
        self.last_train = now

        X = np.array(
            [[r[k] for k in SENSOR_KEYS] for r in rows
             if all(is_valid(k, r.get(k)) for k in SENSOR_KEYS)],
            dtype=float,
        )
        if len(X) < 300:
            self.model_info["samples"] = len(X)
            return
        if len(X) > 4000:
            X = X[np.random.default_rng(0).choice(len(X), 4000, replace=False)]

        mean = X.mean(axis=0)
        std = np.maximum(X.std(axis=0), [MIN_STD[k] for k in SENSOR_KEYS])
        model = IsolationForest(n_estimators=150, contamination=0.01, random_state=42)
        model.fit((X - mean) / std)

        self.model, self.mean, self.std = model, mean, std
        self.model_info.update(
            trained=True, samples=int(len(X)), trained_at=now,
            note="Huấn luyện lại mỗi 30 phút trên dữ liệu 24 giờ gần nhất",
        )
        log.info("Đã huấn luyện IsolationForest với %d mẫu", len(X))

    def _ml_check(self, latest):
        if self.model is None or not all(is_valid(k, latest.get(k)) for k in SENSOR_KEYS):
            return None, None
        x = np.array([[latest[k] for k in SENSOR_KEYS]], dtype=float)
        z = (x - self.mean) / self.std
        score = float(self.model.decision_function(z)[0])  # <0: bất thường
        is_anomaly = self.model.predict(z)[0] == -1
        self.ml_streak = self.ml_streak + 1 if is_anomaly else 0

        contrib = sorted(
            ({"sensor": k, "z": round(float(z[0][i]), 2)} for i, k in enumerate(SENSOR_KEYS)),
            key=lambda c: -abs(c["z"]),
        )
        result = {"score": round(score, 3), "anomaly": bool(is_anomaly), "top": contrib[:3]}

        # Yêu cầu 3 lần liên tiếp và có ít nhất 1 cảm biến lệch >= 3σ để tránh báo nhầm
        if self.ml_streak >= 3 and abs(contrib[0]["z"]) >= 3:
            parts = []
            for c in contrib[:2]:
                k = c["sensor"]
                direction = "cao" if c["z"] > 0 else "thấp"
                parts.append(f"{SENSORS[k]['name']} {fmt(k, latest[k])} ({direction} bất thường, {abs(c['z']):.1f}σ)")
            return result, finding(
                "ml_anomaly", "warning", "ml",
                "AI phát hiện trạng thái bất thường",
                "Tổ hợp chỉ số khác hẳn 24h qua: " + "; ".join(parts) + ".",
                contrib[0]["sensor"],
            )
        return result, None

    # ------------------------------------------------------------ Forecast
    def _forecasts(self, rows, now, th):
        """Dự báo hành động (forecast.py) + cảnh báo sớm khi xu hướng sắp chạm mức nguy hiểm."""
        out = forecast_all(rows, now, th)
        findings = []
        for key, f in out.items():
            # Sắp chạm mức cảnh báo/nguy hiểm theo đánh giá sức khỏe (không chỉ ngưỡng thiết bị)
            f["eta_min"], f["eta_level"] = None, None
            if f["trend"] != "flat":
                now_level = SEV_RANK[sensor_status(key, f["now"], th)[0]]
                lo, hi = SENSORS[key]["phys"]
                for m in range(1, 61):
                    # kẹp trong giới hạn vật lý: kéo dài xu hướng quá xa không được thành "lỗi cảm biến"
                    value = min(max(f["now"] + f["rate_per_min"] * m, lo), hi)
                    lvl, _ = sensor_status(key, value, th)
                    if lvl != "fault" and SEV_RANK[lvl] >= 2 and SEV_RANK[lvl] > now_level:
                        f["eta_min"], f["eta_level"] = m, lvl
                        break
            if f["eta_min"] is not None:
                sev = "warning" if f["eta_level"] == "critical" and f["eta_min"] <= 20 else "info"
                findings.append(finding(
                    f"forecast_{key}", sev, "forecast",
                    f"Dự báo: {lname(key)} sắp tới mức {'nguy hiểm' if f['eta_level'] == 'critical' else 'cảnh báo'}",
                    f"{SENSORS[key]['name']} đang {rate_text(key, f['rate_per_min'])}, "
                    f"{eta_text(f['eta_min'])} nữa sẽ tới mức {'nguy hiểm' if f['eta_level'] == 'critical' else 'cảnh báo'}.",
                    key,
                ))
            # Thiết bị sắp được ESP32 bật/ngắt trong 15 phút tới
            for e in f["events"]:
                if e["kind"] == "trigger" and e["eta_min"] <= 15:
                    findings.append(finding(
                        f"soon_{key}_{e['setting']}", "info", "forecast",
                        f"Sắp {e['action']}", sentence(e["text"]) + ".", key,
                    ))
        return out, findings

    # ---------------------------------------------------------- Main entry
    def evaluate(self, data, rows, now=None, hw_last_change=None, hw_info=None):
        """data: bản ghi Firebase mới nhất; rows: lịch sử gần đây (cũ -> mới, đã gồm bản mới nhất)."""
        now = now or time.time()
        th = thresholds(data)
        latest = {k: num(data.get(k)) for k in SENSOR_KEYS}
        findings = []

        # 1) Trạng thái từng cảm biến + vượt ngưỡng kéo dài
        sensors = {}
        for key in SENSOR_KEYS:
            level, text = sensor_status(key, latest[key], th)
            sensors[key] = {"value": latest[key], "level": level, "text": text}
            name = SENSORS[key]["name"]

            if level == "fault":
                win = _window(rows, now, 60)
                if win and all(not is_valid(key, r.get(key)) for r in win):
                    findings.append(finding(
                        f"fault_{key}", "critical", "sensor",
                        f"Cảm biến {lname(key)} lỗi",
                        f"{text} liên tục 1 phút. Kiểm tra dây nối/nguồn cảm biến. "
                        "Thiết bị liên quan đã được giữ an toàn ở chế độ AUTO.",
                        key,
                    ))
                continue

            if SEV_RANK[level] >= 2:
                win = _window(rows, now, 120)
                if win:
                    levels = [sensor_status(key, num(r.get(key)), th)[0] for r in win]
                    worst_sustained = min(SEV_RANK[lv] for lv in levels)
                    if worst_sustained >= 2:
                        sev = "critical" if worst_sustained >= 3 else "warning"
                        advice_key = {
                            "t": "t_high" if latest[key] > (th["SET_T"] or 25) else "t_low",
                            "h": "h_low", "GT_AMDAT": "soil_low",
                            "GT_DOMAN": "salt_high", "GT_PH": "ph",
                        }.get(key)
                        detail = f"{name} đang ở {fmt(key, latest[key])}, kéo dài trên 2 phút."
                        if advice_key:
                            detail += " Gợi ý: " + ADVICE[advice_key]
                        findings.append(finding(f"threshold_{key}", sev, "threshold", text, detail, key))

        # 2) Sức khỏe cảm biến: nhiễu, đứng yên
        for key in SENSOR_KEYS:
            jump = SENSORS[key]["jump"]
            win = [r for r in rows if r["ts"] >= now - 600]
            if jump and len(win) > 5:
                vals = [num(r.get(key)) for r in win]
                spikes = sum(
                    1 for a, b in zip(vals, vals[1:])
                    if a is not None and b is not None and abs(b - a) > jump
                )
                if spikes >= 3:
                    findings.append(finding(
                        f"noise_{key}", "warning", "sensor",
                        f"Cảm biến {lname(key)} nhiễu",
                        f"Giá trị nhảy đột ngột {spikes} lần trong 10 phút. "
                        "Kiểm tra tiếp xúc dây, nguồn 3.3V/5V hoặc vị trí đặt cảm biến.",
                        key,
                    ))

        # Độ mặn = 0 là bình thường: firmware ép giá trị < 100 ppm về 0. Độ ẩm đất = 0 thì đáng ngờ.
        win5 = _window(rows, now, 300)
        if win5 and all(r.get("GT_AMDAT") == 0 for r in win5):
            findings.append(finding(
                "zero_GT_AMDAT", "warning", "sensor",
                "Cảm biến độ ẩm đất luôn báo 0",
                "Giá trị bằng 0 suốt 5 phút — cảm biến có thể chưa cắm, đứt dây, nằm ngoài đất hoặc chưa hiệu chuẩn. "
                "Ở chế độ AUTO, máy bơm sẽ bật liên tục theo số liệu này.",
                "GT_AMDAT",
            ))

        # Nguồn điện / WiFi của ESP32 (firmware gửi ESP_UP, ESP_RST, ESP_RSSI)
        hw = hw_info or {}
        if hw.get("restarts_1h", 0) >= 2:
            findings.append(finding(
                "power_unstable", "critical" if hw["restarts_1h"] >= 3 else "warning", "system",
                "Nguồn điện không ổn định",
                f"ESP32 khởi động lại {hw['restarts_1h']} lần trong 1 giờ (gần nhất: {hw.get('last_restart_reason')}). "
                "Kiểm tra bộ nguồn cấp cho ESP32, tách nguồn rơ-le/bơm/quạt khỏi nguồn ESP32, siết lại dây nguồn.",
            ))
        elif hw.get("last_restart_ts") and now - hw["last_restart_ts"] < 1800 and \
                hw.get("last_restart_reason") == "sụt áp nguồn (brownout)":
            findings.append(finding(
                "power_brownout", "warning", "system", "ESP32 vừa reset do sụt áp",
                "Điện áp cấp cho bo mạch bị tụt (thường khi bơm/quạt/phun sương khởi động). "
                "Nên dùng nguồn riêng cho ESP32 và thêm tụ lọc gần bo.",
            ))
        rssi = hw.get("rssi")
        if rssi is not None and rssi < -80:
            findings.append(finding(
                "wifi_weak", "warning", "system", "Sóng WiFi yếu",
                f"ESP32 bắt WiFi {rssi:.0f} dBm (yếu). Dễ mất kết nối — dời modem/bộ kích sóng gần nhà nấm hơn.",
            ))

        # Nhịp tim ESP32 (firmware ghi ESP_TS = giờ Firebase mỗi lần gửi): chính xác hơn việc đoán số liệu đứng yên
        esp_ts = num(data.get("ESP_TS"))
        if esp_ts and now - esp_ts / 1000 > 120:
            findings.append(finding(
                "esp_offline", "critical", "system",
                "ESP32 mất kết nối",
                f"Không nhận được dữ liệu từ bo mạch {int((now - esp_ts / 1000) // 60)} phút. "
                "Kiểm tra nguồn điện, WiFi của ESP32 rồi nhấn reset. Nhà nấm đang không được tự động điều khiển.",
            ))

        # ESP32 còn gửi không: dùng mốc "lần cuối số liệu thay đổi" (nhớ qua các lần khởi động lại máy chủ),
        # chưa có mốc thì xét các bản ghi từ lúc máy chủ chạy. Cùng ngưỡng 5 phút như trên web.
        win10 = _window(rows, now, 600)
        first = tuple(rows[0].get(k) for k in SENSOR_KEYS) if rows else None
        stale_min, known = None, bool(hw_last_change)
        if hw_last_change:
            stale_min = (now - hw_last_change) / 60
        elif len(rows) >= 2 and all(tuple(r.get(k) for k in SENSOR_KEYS) == first for r in rows):
            stale_min = (now - rows[0]["ts"]) / 60
        if stale_min is not None and stale_min >= 5:
            long_ago = f"{stale_min / 1440:.0f} ngày" if stale_min >= 2880 else (
                f"{stale_min / 60:.0f} giờ" if stale_min >= 90 else f"{stale_min:.0f} phút")
            findings.append(finding(
                "stale_all", "critical" if stale_min >= 30 else "warning", "system",
                "ESP32 có thể đã mất kết nối",
                f"Tất cả chỉ số đứng yên {'' if known else 'ít nhất '}{long_ago}. ESP32 có thể đã mất điện, "
                "mất WiFi hoặc bị treo — kiểm tra nguồn, WiFi rồi nhấn reset bo mạch. "
                "Trong lúc này nhà nấm không được tự động điều khiển.",
            ))
        elif win10 and len(win10) >= 20:  # ESP32 còn gửi: xét từng cảm biến analog có bị treo không
            win60 = _window(rows, now, 3600)
            for key in ("GT_AMDAT", "GT_DOMAN", "GT_PH"):
                values = {r.get(key) for r in win60} if win60 else set()
                if len(values) == 1 and values != {0}:  # 0 đã có luật riêng / là giá trị hợp lệ
                    findings.append(finding(
                        f"stuck_{key}", "info", "sensor",
                        f"Cảm biến {lname(key)} có thể bị treo",
                        "Giá trị không đổi suốt 1 giờ trong khi cảm biến khác vẫn thay đổi.",
                        key,
                    ))

        # 3) Hiệu quả thiết bị
        findings += self._device_checks(data, rows, now, th)

        # 4) Model ML
        ml_result, ml_finding = self._ml_check(latest)
        if ml_finding:
            findings.append(ml_finding)

        # 5) Dự báo
        forecasts, fc_findings = self._forecasts(rows, now, th)
        findings += fc_findings

        # Hai chỉ số riêng: môi trường nhà nấm và chất lượng nước cấp (không trung bình hóa với nhau)
        idx = indices.compute(sensors, findings, data, th)
        # Cảnh báo riêng cho nước cấp, chỉ khi lệch kéo dài >= 1 phút (pH dao động sát ngưỡng không gây báo liên tục)
        if idx["water"]["locks"]:
            win = _window(rows, now, 60)
            if win and all(indices.water_locks(r, th) for r in win):
                ph_lock = any("pH" in lock["reason"] for lock in idx["water"]["locks"])
                findings.append(finding(
                    "water_locked", "critical", "water", "Nước cấp không đạt",
                    idx["water"]["lock_text"], "GT_PH" if ph_lock else "GT_DOMAN",
                ))

        findings.sort(key=lambda f: -SEV_RANK[f["severity"]])
        return {
            "ts": now,
            "health": idx["overall"],
            "indices": idx,
            "summary": self._summary(findings, idx),
            "sensors": sensors,
            "findings": findings,
            "forecasts": forecasts,
            "ml": {**self.model_info, "latest": ml_result},
        }

    def _device_checks(self, data, rows, now, th):
        out = []

        def mins(start):
            return (now - start) / 60

        start = _on_since(rows, "BOM")
        if start and mins(start) >= 8:
            before, after = _value_at(rows, "GT_AMDAT", start), num(data.get("GT_AMDAT"))
            if before is not None and after is not None and after - before < 2:
                out.append(finding(
                    "device_BOM_ineffective", "warning", "device",
                    "Máy bơm chạy nhưng đất không ẩm lên",
                    f"Bơm đã chạy {mins(start):.0f} phút, độ ẩm đất {before:.0f}% → {after:.0f}%. "
                    "Có thể hết nước, nghẹt ống, bơm hỏng hoặc cảm biến đất đặt sai vị trí.",
                    "GT_AMDAT",
                ))

        start = _on_since(rows, "SUONG")
        if start and mins(start) >= 8:
            before, after = _value_at(rows, "h", start), num(data.get("h"))
            if before is not None and after is not None and after - before < 3:
                out.append(finding(
                    "device_SUONG_ineffective", "warning", "device",
                    "Phun sương chạy nhưng độ ẩm không tăng",
                    f"Phun sương đã chạy {mins(start):.0f} phút, độ ẩm {before:.0f}% → {after:.0f}%. "
                    "Kiểm tra béc phun, áp lực nước hoặc màn che đang mở quá rộng.",
                    "h",
                ))

        start = _on_since(rows, "QUAT")
        if start and mins(start) >= 15 and th["SET_T"]:
            before, after = _value_at(rows, "t", start), num(data.get("t"))
            if before is not None and after is not None and after > th["SET_T"] and after - before > -0.3:
                out.append(finding(
                    "device_QUAT_ineffective", "warning", "device",
                    "Quạt chạy lâu nhưng nhiệt độ không giảm",
                    f"Quạt đã chạy {mins(start):.0f} phút, nhiệt độ {before:.1f}°C → {after:.1f}°C. "
                    "Kiểm tra quạt có quay không, nên mở màn che hoặc phun sương hỗ trợ.",
                    "t",
                ))

        start = _on_since(rows, "DEN")
        if start and 2 <= mins(start) <= 30:
            before, after = _value_at(rows, "ANHSANG", start), num(data.get("ANHSANG"))
            if before is not None and after is not None and after < before * 1.05 + 5:
                out.append(finding(
                    "device_DEN_ineffective", "info", "device",
                    "Đèn bật nhưng cảm biến không thấy sáng hơn",
                    f"Ánh sáng {before:.0f} → {after:.0f} lux sau khi bật đèn. "
                    "Có thể đèn hỏng hoặc cảm biến không hướng về đèn.",
                    "ANHSANG",
                ))

        if num(data.get("MO")) == 1 and num(data.get("DONG")) == 1:
            out.append(finding(
                "device_door_conflict", "warning", "device",
                "Màn che nhận cả lệnh MỞ và ĐÓNG",
                "Hai tín hiệu MO và DONG cùng bằng 1 — động cơ có thể bị kẹt. Hãy chọn lại một trạng thái.",
            ))

        auto = num(data.get("MODE")) == 1
        for dev, limit, name in (
            ("BOM", config.PUMP_MAX_ON_MIN, "Máy bơm"),
            ("SUONG", config.MIST_MAX_ON_MIN, "Phun sương"),
        ):
            if auto and config.AUTO_CONTROL_ENABLED:
                continue  # máy chủ AI tự ngắt bảo vệ
            start = _on_since(rows, dev, need_seen_off=False)
            if start and mins(start) >= limit:
                out.append(finding(
                    f"device_{dev}_long_run", "warning", "device",
                    f"{name} bật liên tục {mins(start):.0f} phút",
                    "ESP32 đang AUTO nhưng chỉ số chưa đạt ngưỡng — kiểm tra nguồn nước, cảm biến và ngưỡng cài đặt."
                    if auto else "Đang ở chế độ thủ công — có thể quên tắt. Gửi lệnh /tat để tắt từ Zalo.",
                ))

        # Bật/tắt liên tục (ESP32 không có vùng trễ quanh ngưỡng)
        win = [r for r in rows if r["ts"] >= now - 600]
        for dev, name in (("BOM", "Máy bơm"), ("DEN", "Đèn"), ("QUAT", "Quạt"), ("SUONG", "Phun sương"), ("MO", "Màn che")):
            toggles = sum(1 for a, b in zip(win, win[1:]) if a.get(dev) != b.get(dev))
            if toggles >= 6:
                out.append(finding(
                    f"device_{dev}_chatter", "warning", "device",
                    f"{name} bật/tắt liên tục",
                    f"Đổi trạng thái {toggles} lần trong 10 phút — giá trị cảm biến đang dao động sát ngưỡng. "
                    "Nên chỉnh ngưỡng hoặc thêm vùng trễ trong firmware để bảo vệ thiết bị.",
                ))
        return out

    @staticmethod
    def _summary(findings, idx):
        important = [f for f in findings if SEV_RANK[f["severity"]] >= 2]
        lock = idx["water"]["lock_text"]
        if lock:
            others = [f["title"] for f in important if f.get("sensor") not in indices.WATER_SENSORS]
            return lock + (" Môi trường: " + "; ".join(others[:2]) + "." if others else "")
        if not important:
            minor = len(findings)
            return "Mọi chỉ số đang ổn định." + (f" Có {minor} ghi chú nhỏ để theo dõi." if minor else "")
        crit = sum(1 for f in important if f["severity"] == "critical")
        head = f"{len(important)} vấn đề cần chú ý" + (f" ({crit} nghiêm trọng)" if crit else "")
        return head + ": " + "; ".join(f["title"] for f in important[:3]) + "."
