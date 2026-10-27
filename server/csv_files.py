"""Tự lưu file CSV trên máy (mở bằng Excel / Google Sheet), không cần tải hay cài gì thêm.

Thư mục DU_LIEU_CSV/ trong thư mục dự án (đổi bằng CSV_DIR trong server/.env):
- du_lieu_goc_YYYY-MM.csv   : mỗi tháng 1 file, mỗi phút 1 dòng (6 cảm biến + thiết bị), cột giống trang "Dữ liệu gốc"
- du_bao_va_thuc_te.csv     : mọi lần Random Forest dự báo + số đo thật lúc tới giờ, giống tab DỰ BÁO
Ghi lại 5 phút/lần (file dự báo 15 phút/lần). File đang mở trong Excel thì Windows khóa -> bỏ qua, lần sau ghi tiếp.
"""
import csv
import datetime as dt
import logging
import os
import time
from pathlib import Path

import config
from export_xlsx import RAW_HEADERS, raw_row
from forecast_log import HEADERS as FORECAST_HEADERS
from forecast_log import ForecastLog

log = logging.getLogger("csv")

EVERY = 300
FORECAST_EVERY = 900  # so dự báo với thực tế tốn nhiều truy vấn hơn
FULL_DAYS = 40  # lúc khởi động / có thêm dữ liệu từ Google Sheet: ghi lại các tháng trong 40 ngày qua


class CsvFiles:
    def __init__(self, store, forecasts):
        self.store = store
        self.forecasts = forecasts
        self.dir = Path(config.CSV_DIR)
        self.last = 0
        self.last_forecast = 0
        self.full = True
        self.status = {"dir": str(self.dir), "files": 0, "rows": 0, "updated": None, "error": None}

    def mark_full(self):
        """Dữ liệu cũ vừa được thêm (Google Sheet) -> lần sau ghi lại cả các tháng trước."""
        self.full = True

    def update(self, now=None, force=False):
        now = now or time.time()
        if not force and not self.full and now - self.last < EVERY:
            return
        self.last = now
        days = FULL_DAYS if self.full else 2
        start = dt.datetime.fromtimestamp(now - days * 86400).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        months = {}
        for r in self.store.minute_readings(start.timestamp()):
            months.setdefault(dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m"), []).append(raw_row(r))
        files = [(f"du_lieu_goc_{m}.csv", RAW_HEADERS, rows) for m, rows in sorted(months.items())]
        if force or self.full or now - self.last_forecast >= FORECAST_EVERY:
            self.last_forecast = now
            since = now - config.HISTORY_RETENTION_DAYS * 86400
            groups = self.forecasts.compare(self.store.forecasts_between(since, now), now)
            files.append(("du_bao_va_thuc_te.csv", FORECAST_HEADERS, [ForecastLog.row(g) for g in groups]))

        locked = [name for name, headers, rows in files if not self._write(name, headers, rows)]
        self.full = bool(locked) and self.full
        self.status.update(files=len(list(self.dir.glob("*.csv"))), rows=sum(len(r) for _, _, r in files), updated=now,
                           error=(f"Đang mở trong Excel, chưa ghi được: {', '.join(locked)} (đóng file là tự ghi lại)"
                                  if locked else None))

    def _write(self, name, headers, rows):
        """Ghi ra file tạm rồi thay file cũ (không bao giờ để file ghi dở). False = file đang bị khóa."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / name
        tmp = path.with_name(name + ".tmp")
        try:
            # utf-8-sig: Excel đọc đúng tiếng Việt khi mở thẳng file
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow([h.replace("\n", " ") for h in headers])
                w.writerows(["" if v is None else v for v in r] for r in rows)
            os.replace(tmp, path)
            return True
        except PermissionError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return False
        except OSError as exc:
            log.warning("Ghi %s lỗi: %s", path, exc)
            return False
