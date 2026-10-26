"""Thời tiết ngoài trời (Open-Meteo, miễn phí, không cần key).

Vị trí: WEATHER_LAT/WEATHER_LON trong server/.env, mặc định TP. Vĩnh Long (config.py).
Lấy thời tiết hiện tại + theo giờ 31 ngày qua và 2 ngày tới, lưu vào SQLite để mô hình Random Forest học.
"""
import logging
import time

import requests

import config

log = logging.getLogger("weather")

API = "https://api.open-meteo.com/v1/forecast"
FIELDS = "temperature_2m,relative_humidity_2m,cloud_cover,shortwave_radiation,precipitation,wind_speed_10m"
KEYS = ("temp", "hum", "cloud", "rad", "precip", "wind")


def _hourly_rows(block):
    times = block.get("time") or []
    cols = [block.get(f) for f in FIELDS.split(",")]
    rows = []
    for i, ts in enumerate(times):
        row = {"ts": int(ts)}
        for key, col in zip(KEYS, cols):
            row[key] = col[i] if col and i < len(col) else None
        rows.append(row)
    return rows


class Weather:
    def __init__(self, store):
        self.store = store
        self.location = None   # {"lat", "lon", "name"}
        self.current = None    # thời tiết hiện tại
        self.hourly = {}       # ts giờ -> thời tiết (quá khứ + dự báo)
        self.updated = None
        self.error = None

    @staticmethod
    def _locate():
        return {"lat": config.WEATHER_LAT, "lon": config.WEATHER_LON, "name": config.WEATHER_NAME or "Nhà nấm"}

    def refresh(self):
        """Gọi định kỳ (15 phút). Lỗi mạng thì giữ dữ liệu cũ."""
        try:
            self.location = self.location or self._locate()
            res = requests.get(API, timeout=20, params={
                "latitude": self.location["lat"], "longitude": self.location["lon"],
                "current": FIELDS, "hourly": FIELDS, "past_days": 31, "forecast_days": 2,
                "timeformat": "unixtime", "timezone": "auto",
            })
            res.raise_for_status()
            body = res.json()
        except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
            self.error = str(exc)
            log.warning("Không lấy được thời tiết: %s", exc)
            return False
        cur = body.get("current") or {}
        self.current = {"ts": cur.get("time"), **{k: cur.get(f) for k, f in zip(KEYS, FIELDS.split(","))}}
        rows = _hourly_rows(body.get("hourly") or {})
        now = time.time()
        self.hourly = {r["ts"]: r for r in rows}
        self.store.add_weather([r for r in rows if r["ts"] <= now])  # chỉ lưu giờ đã qua (số liệu thật)
        self.updated, self.error = now, None
        return True

    def at(self, ts):
        """Thời tiết của giờ chứa thời điểm ts (dự báo hoặc đã qua)."""
        return self.hourly.get(int(ts // 3600 * 3600))

    def text(self):
        """1-2 dòng thời tiết cho tin Zalo; None nếu chưa lấy được."""
        if not self.current or self.current.get("temp") is None:
            return None
        c = self.current
        place = (self.location or {}).get("name") or "ngoài trời"
        line = (f"🌤 Ngoài trời ({place}): {c['temp']:.0f}°C, ẩm {c['hum']:.0f}%, mây {c['cloud']:.0f}%"
                + (f", mưa {c['precip']:.1f} mm" if (c.get("precip") or 0) > 0 else ""))
        nxt = self.status()["next_hours"]
        if nxt:
            rain = sum(w.get("precip") or 0 for w in nxt)
            temps = [w["temp"] for w in nxt if w.get("temp") is not None]
            if temps:
                line += (f"\n   3 giờ tới: {min(temps):.0f}–{max(temps):.0f}°C, "
                         + (f"mưa ~{rain:.1f} mm" if rain >= 0.2 else "không mưa"))
        return line

    def status(self):
        now = time.time()
        upcoming = [self.hourly[h] for h in sorted(self.hourly) if now - 3600 < h <= now + 3 * 3600][:4]
        return {
            "location": self.location,
            "current": self.current,
            "next_hours": upcoming[1:] if upcoming else [],
            "updated": self.updated,
            "error": self.error,
        }
