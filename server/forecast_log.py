"""Nhật ký dự báo: lưu mỗi lần Random Forest dự báo (15 phút/lần), tới giờ thì so với số đo THẬT.

- Đây là kiểm chứng trên dữ liệu thật sau khi dự báo (ngoài kiểm chứng walk-forward lúc học): mỗi dòng có
  giá trị lúc dự báo, số mô hình dự báo, số đo thực tế -> tính sai số, so với cách "giữ nguyên giá trị".
- Thực tế = trung bình số đo ±7,5 phút quanh thời điểm được dự báo (giống cách mô hình học: trung bình 15 phút).
- Ghi các dòng đã có kết quả lên tab "DỰ BÁO" của Google Sheet (GOOGLE_SCRIPT_URL trong server/.env).
"""
import datetime as dt
import logging
import time

import requests

import config
from rf_model import HORIZONS, TARGETS
from sensors import SENSORS

log = logging.getLogger("forecast")

WINDOW = 450          # ±7,5 phút quanh thời điểm được dự báo
SUMMARY_EVERY = 900   # tính lại bảng tổng hợp 15 phút/lần
SHEET_TAB = "DỰ BÁO"


def label(k):
    s = SENSORS[k]
    return f"{s['name']} ({s['unit']})" if s["unit"] else s["name"]


HEADERS = (["NGÀY DỰ BÁO", "GIỜ DỰ BÁO", "DỰ BÁO CHO LÚC", "SAU (GIỜ)"]
           + [f"{label(k)} {col}" for k in TARGETS for col in ("lúc dự báo", "RF dự báo", "thực tế")])


def _round(k, v):
    return None if v is None else round(v, 2 if k == "GT_PH" else 1)


def _actual_ok(k, v):
    # độ ẩm đất ~0% = cảm biến lỗi/chưa cắm -> không dùng để chấm điểm
    return v is not None and not (k == "GT_AMDAT" and v <= 0.5)


class ForecastLog:
    def __init__(self, store):
        self.store = store
        self.summary = {"rows": [], "checked": 0, "pending": 0, "updated": None}
        self.sheet = {"enabled": bool(config.GOOGLE_SCRIPT_URL), "sent": 0, "sent_at": None, "error": None}
        self.last_summary = 0

    def record(self, result):
        """Gọi sau mỗi lần dự báo; bảng giữ lần đầu tiên của mỗi khung 15 phút."""
        preds = (result or {}).get("predictions") or []
        now_vals = (result or {}).get("now") or {}
        rows = [(result["ts"], p["hours"], k, now_vals.get(k), p[k], p["basis"].get(k))
                for p in preds for k in TARGETS if p.get(k) is not None]
        if rows:
            self.store.add_forecasts(rows)

    def compare(self, records, now=None):
        """Bản ghi dự báo -> mỗi (lúc dự báo, mốc giờ) 1 dòng có giá trị lúc dự báo, dự báo, thực tế."""
        now = now or time.time()
        groups = {}
        for r in records:
            g = groups.setdefault((r["made_bucket"], r["hours"]), {
                "made": r["made_ts"], "target": r["target_ts"], "hours": r["hours"], "vals": {}, "actual": None})
            g["vals"][r["key"]] = r
        for g in groups.values():
            if g["target"] + WINDOW <= now:
                g["actual"] = self.store.mean_between(g["target"] - WINDOW, g["target"] + WINDOW)
        return [groups[key] for key in sorted(groups)]

    @staticmethod
    def row(g):
        """1 dòng cho Google Sheet / Excel (cột như HEADERS)."""
        made, target = dt.datetime.fromtimestamp(g["made"]), dt.datetime.fromtimestamp(g["target"])
        out = [made.strftime("%d/%m/%Y"), made.strftime("%H:%M"), target.strftime("%H:%M %d/%m/%Y"), g["hours"]]
        for k in TARGETS:
            v = g["vals"].get(k) or {}
            act = (g["actual"] or {}).get(k)
            out += [_round(k, v.get("now_value")), _round(k, v.get("predicted")),
                    _round(k, act) if _actual_ok(k, act) and v else None]
        return out

    @staticmethod
    def score(groups):
        """Sai số trung bình RF so với "giữ nguyên" trên các dự báo đã có kết quả."""
        err = {(h, k): ([], []) for h in HORIZONS for k in TARGETS}
        for g in groups:
            for k, v in g["vals"].items():
                act = (g["actual"] or {}).get(k)
                if not _actual_ok(k, act) or v["now_value"] is None or (g["hours"], k) not in err:
                    continue
                err[(g["hours"], k)][0].append(abs(v["predicted"] - act))
                err[(g["hours"], k)][1].append(abs(v["now_value"] - act))
        rows = []
        for (h, k), (em, eb) in err.items():
            if not em:
                continue
            mae, mae_b = sum(em) / len(em), sum(eb) / len(eb)
            skill = max(-100.0, min(100.0, (1 - mae / mae_b) * 100)) if mae_b > 1e-9 else (0.0 if mae < 1e-9 else -100.0)
            rows.append({"hours": h, "key": k, "n": len(em), "mae": round(mae, 3), "mae_base": round(mae_b, 3),
                         "skill": round(skill)})
        return rows

    def refresh_summary(self, now=None):
        now = now or time.time()
        if now - self.last_summary < SUMMARY_EVERY:
            return
        self.last_summary = now
        groups = self.compare(self.store.forecasts_between(now - config.HISTORY_RETENTION_DAYS * 86400, now), now)
        done = [g for g in groups if g["actual"]]
        self.summary = {"rows": self.score(done), "checked": len(done),
                        "pending": sum(1 for g in groups if g["target"] + WINDOW > now), "updated": now}

    def push_sheet(self, now=None):
        """Ghi các dòng đã có kết quả lên tab DỰ BÁO của Google Sheet. Trả số dòng đã ghi."""
        if not config.GOOGLE_SCRIPT_URL:
            return 0
        now = now or time.time()
        # đợi thêm 1 giờ sau giờ được dự báo: kịp lấy số đo từ Google Sheet nếu lúc đó máy tính tắt
        groups = self.compare(self.store.forecasts_unsent(now - 3600), now)
        if not groups:
            return 0
        try:
            res = requests.post(config.GOOGLE_SCRIPT_URL, timeout=60, json={
                "sts": "forecast", "tab": SHEET_TAB, "headers": HEADERS, "rows": [self.row(g) for g in groups]})
            res.raise_for_status()
            if not res.text.startswith("OK"):
                raise ValueError(f"Apps Script trả về: {res.text[:120]} (dán lại google_sheet/Code.gs mới và triển khai phiên bản mới)")
        except (requests.RequestException, ValueError) as exc:
            self.sheet["error"] = str(exc)
            log.warning("Không ghi được bảng dự báo lên Google Sheet: %s", exc)
            return 0
        self.store.mark_forecasts_sent([(int(g["made"] // 900) * 900, g["hours"]) for g in groups])
        self.sheet.update(sent=self.sheet["sent"] + len(groups), sent_at=now, error=None)
        log.info("Google Sheet: ghi %d dòng dự báo & thực tế", len(groups))
        return len(groups)

    def status(self):
        return {**self.summary, "sheet": self.sheet}
