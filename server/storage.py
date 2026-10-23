"""Lưu lịch sử cảm biến, sự kiện thiết bị và cảnh báo vào SQLite."""
import sqlite3
import threading
import time

import config
from sensors import DEVICE_KEYS, SENSOR_KEYS

READING_COLS = SENSOR_KEYS + DEVICE_KEYS + ["MODE"]

RANGES = {  # giây
    "15m": 15 * 60,
    "1h": 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
}


class Storage:
    def __init__(self, path=config.DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init()

    def _init(self):
        cols = ", ".join(f'"{c}" REAL' for c in READING_COLS)
        with self.lock, self.conn:
            self.conn.executescript(
                f"""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS readings (ts REAL PRIMARY KEY, {cols});
                CREATE TABLE IF NOT EXISTS device_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, device TEXT, value INTEGER, source TEXT, reason TEXT
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL, key TEXT, severity TEXT, category TEXT,
                    title TEXT, detail TEXT, status TEXT DEFAULT 'active',
                    resolved_ts REAL, sent_zalo INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
                CREATE INDEX IF NOT EXISTS idx_events_ts ON device_events(ts);
                CREATE TABLE IF NOT EXISTS weather_hourly (
                    ts INTEGER PRIMARY KEY, temp REAL, hum REAL, cloud REAL, rad REAL, precip REAL, wind REAL
                );
                """
            )

    # ---------- readings ----------
    def add_reading(self, ts, values):
        cols = ", ".join(f'"{c}"' for c in READING_COLS)
        marks = ", ".join("?" for _ in READING_COLS)
        row = [ts] + [values.get(c) for c in READING_COLS]
        with self.lock, self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO readings (ts, {cols}) VALUES (?, {marks})", row
            )

    def readings_since(self, since_ts):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM readings WHERE ts >= ? ORDER BY ts", (since_ts,)
            ).fetchall()
        return [dict(r) for r in rows]

    def history(self, range_key="1h", max_points=240):
        """Lịch sử đã gộp theo khoảng (trung bình) để vẽ biểu đồ."""
        span = RANGES.get(range_key, 3600)
        now = time.time()
        bucket = max(config.POLL_INTERVAL_SEC, span / max_points)
        sensor_avgs = ", ".join(f'AVG("{k}") AS "{k}"' for k in SENSOR_KEYS)
        device_avgs = ", ".join(f'AVG("{k}") AS "{k}"' for k in DEVICE_KEYS)
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT CAST(ts / ? AS INTEGER) * ? AS bucket, {sensor_avgs}, {device_avgs}
                    FROM readings WHERE ts >= ? GROUP BY bucket ORDER BY bucket""",
                (bucket, bucket, now - span),
            ).fetchall()
        return {"range": range_key, "bucket_sec": bucket, "points": [dict(r) for r in rows]}

    def hourly_readings(self, since_ts, min_samples=30, tail_sec=3600):
        """Trung bình từng giờ (giờ tròn, epoch) của cảm biến + tỉ lệ thời gian bật thiết bị.
        tail_sec < 3600: chỉ lấy `tail_sec` giây cuối mỗi giờ (trạng thái lúc cuối giờ, không bị kéo bởi đầu giờ)."""
        sensors = ", ".join(f'AVG("{k}") AS "{k}"' for k in SENSOR_KEYS)
        devices = ", ".join(f'AVG("{k}") AS "{k}"' for k in DEVICE_KEYS)
        with self.lock:
            rows = self.conn.execute(
                f"""SELECT CAST(ts / 3600 AS INTEGER) * 3600 AS hour, COUNT(*) AS n, {sensors}, {devices}
                    FROM readings WHERE ts >= ? AND ts - CAST(ts / 3600 AS INTEGER) * 3600 >= ?
                    GROUP BY hour HAVING n >= ? ORDER BY hour""",
                (since_ts, 3600 - tail_sec, min_samples),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- thời tiết ngoài trời (theo giờ) ----------
    def add_weather(self, rows):
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO weather_hourly (ts, temp, hum, cloud, rad, precip, wind) VALUES (?,?,?,?,?,?,?)",
                [(r["ts"], r["temp"], r["hum"], r["cloud"], r["rad"], r["precip"], r["wind"]) for r in rows],
            )

    def weather_between(self, start_ts, end_ts):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM weather_hourly WHERE ts >= ? AND ts <= ? ORDER BY ts", (start_ts, end_ts)
            ).fetchall()
        return {r["ts"]: dict(r) for r in rows}

    def prune(self):
        cutoff = time.time() - config.HISTORY_RETENTION_DAYS * 86400
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM device_events WHERE ts < ?", (cutoff,))

    # ---------- device events ----------
    def add_device_event(self, ts, device, value, source, reason=""):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO device_events (ts, device, value, source, reason) VALUES (?,?,?,?,?)",
                (ts, device, value, source, reason),
            )

    def device_events(self, limit=50):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM device_events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- alerts ----------
    def add_alert(self, finding, sent):
        with self.lock, self.conn:
            cur = self.conn.execute(
                """INSERT INTO alerts (ts, key, severity, category, title, detail, sent_zalo)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    time.time(), finding["key"], finding["severity"], finding["category"],
                    finding["title"], finding["detail"], int(sent),
                ),
            )
            return cur.lastrowid

    def update_alert(self, alert_id, finding):
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE alerts SET severity=?, title=?, detail=? WHERE id=?",
                (finding["severity"], finding["title"], finding["detail"], alert_id),
            )

    def mark_alert_sent(self, alert_id):
        with self.lock, self.conn:
            self.conn.execute("UPDATE alerts SET sent_zalo=1 WHERE id=?", (alert_id,))

    def resolve_alert(self, alert_id):
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE alerts SET status='resolved', resolved_ts=? WHERE id=?",
                (time.time(), alert_id),
            )

    def resolve_all_active(self):
        """Gọi khi khởi động: cảnh báo cũ của phiên trước coi như đã đóng."""
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE alerts SET status='resolved', resolved_ts=? WHERE status='active'",
                (time.time(),),
            )

    def alerts(self, limit=100):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
