"""Kéo dữ liệu ESP32 ghi lên Google Sheet về SQLite.

ESP32 ghi Sheet 1 phút/lần, chạy 24/7 kể cả khi máy tính tắt (google_sheet/Code.gs). Máy chủ chỉ tự lưu số liệu
khi đang chạy, nên định kỳ lấy Sheet về để lấp các phút còn thiếu -> biểu đồ và Random Forest có đủ ngày đêm.
Đọc qua Apps Script (GOOGLE_SCRIPT_URL, tự lấy trong secrets.h; Sheet không cần chia sẻ), hoặc qua link tải CSV
nếu đặt GOOGLE_SHEET_ID (Sheet chia sẻ "Bất kỳ ai có đường liên kết đều xem được").
"""
import csv
import datetime as dt
import io
import logging
import time

import requests

import config

log = logging.getLogger("sheet")

# Cột của Sheet (Code.gs): NGÀY, GIỜ, nhiệt độ, độ ẩm KK, độ ẩm đất, độ mặn, pH, ánh sáng, [bơm, đèn, quạt, sương, màn che, AUTO]
SENSOR_COLS = {"t": 2, "h": 3, "GT_AMDAT": 4, "GT_DOMAN": 5, "GT_PH": 6, "ANHSANG": 7}
DEVICE_COLS = {"BOM": 8, "DEN": 9, "QUAT": 10, "SUONG": 11, "MO": 12, "MODE": 13}
RECENT_ROWS = 3000   # dòng mới nhất ở trên cùng: lần sau chỉ lấy ~2 ngày gần nhất
FIRST_ROWS = 50000   # lần đầu (qua Apps Script): ~35 ngày


def _num(text):
    try:
        return float((text or "").strip().replace(",", "."))
    except ValueError:
        return None


def parse_csv(text):
    """CSV của Sheet -> list bản ghi {ts, t, h, ...}; bỏ dòng tiêu đề / dòng lỗi."""
    rows = []
    for line in csv.reader(io.StringIO(text)):
        if len(line) < 8:
            continue
        try:
            ts = dt.datetime.strptime(f"{line[0].strip()} {line[1].strip()}", "%d/%m/%Y %H:%M:%S").timestamp()
        except ValueError:
            continue
        row = {"ts": ts, **{k: _num(line[i]) for k, i in SENSOR_COLS.items()}}
        if any(row[k] is None for k in SENSOR_COLS):
            continue
        for k, i in DEVICE_COLS.items():
            row[k] = _num(line[i]) if len(line) > i else None
        if row["MO"] is not None:
            row["DONG"] = 1 - row["MO"]
        rows.append(row)
    return rows


class SheetSync:
    def __init__(self, store):
        self.store = store
        self.full_done = False
        self.status = {"enabled": bool(config.GOOGLE_SHEET_ID or config.GOOGLE_SCRIPT_URL), "rows": 0, "added": 0, "latest": None,
                       "synced_at": None, "error": None}

    def run(self):
        """Trả số bản ghi mới thêm vào CSDL (0 nếu chưa cài / lỗi mạng)."""
        if config.GOOGLE_SHEET_ID:
            url = (f"https://docs.google.com/spreadsheets/d/{config.GOOGLE_SHEET_ID}/export"
                   f"?format=csv&gid={config.GOOGLE_SHEET_GID}")
            if self.full_done:
                url += f"&range=A1:N{RECENT_ROWS}"
        elif config.GOOGLE_SCRIPT_URL:
            url = f"{config.GOOGLE_SCRIPT_URL}?sts=read&rows={RECENT_ROWS if self.full_done else FIRST_ROWS}"
        else:
            return 0
        try:
            try:
                res = requests.get(url, timeout=60)
            except requests.ConnectionError:  # Google thỉnh thoảng ngắt kết nối giữa chừng -> thử lại 1 lần
                time.sleep(3)
                res = requests.get(url, timeout=60)
            res.raise_for_status()
            if "text/csv" not in res.headers.get("Content-Type", ""):
                raise ValueError("Sheet chưa chia sẻ 'Bất kỳ ai có đường liên kết đều xem được'" if config.GOOGLE_SHEET_ID
                                 else "Apps Script chưa phải bản mới: dán lại google_sheet/Code.gs và triển khai")
            rows = parse_csv(res.content.decode("utf-8"))
        except (requests.RequestException, ValueError) as exc:
            self.status["error"] = str(exc)
            log.warning("Không lấy được Google Sheet: %s", exc)
            return 0
        added = self.store.add_sheet_readings(rows)
        self.full_done = True
        self.status.update(rows=len(rows), latest=max((r["ts"] for r in rows), default=None),
                           synced_at=time.time(), error=None, added=self.status["added"] + added)
        if added:
            log.info("Google Sheet: thêm %d phút dữ liệu còn thiếu (Sheet có %d dòng)", added, len(rows))
        return added
