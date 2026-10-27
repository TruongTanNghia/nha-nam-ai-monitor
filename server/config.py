"""Đọc cấu hình từ server/.env (hoặc biến môi trường)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv không bắt buộc
    load_dotenv = None

SERVER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SERVER_DIR.parent

if load_dotenv:
    load_dotenv(SERVER_DIR / ".env")


def _str(name, default=""):
    value = os.getenv(name, default).strip()
    # "KEY=   # chú thích" bị python-dotenv đọc thành giá trị "# chú thích" -> coi như để trống
    return default if value.startswith("#") else value


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _opt_float(name):
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return None


def _bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---------- Firebase ----------
FIREBASE_DB_URL = _str(
    "FIREBASE_DB_URL",
    "https://nha-kinh-84aa2-default-rtdb.asia-southeast1.firebasedatabase.app",
).rstrip("/")
FIREBASE_AUTH = _str("FIREBASE_AUTH")  # database secret / token nếu rules yêu cầu
POLL_INTERVAL_SEC = _int("POLL_INTERVAL_SEC", 5)

# ---------- Web server ----------
HOST = _str("HOST", "0.0.0.0")
PORT = _int("PORT", 5000)
# Đăng nhập web + API (bắt buộc khi đưa ra Internet qua Cloudflare Tunnel). Để trống = không cần đăng nhập
APP_USER = _str("APP_USER", "admin")
APP_PASSWORD = _str("APP_PASSWORD")
# Cloudflare Tunnel: máy chủ tự mở link công khai https://....trycloudflare.com và gửi link về Zalo
TUNNEL_ENABLED = _bool("TUNNEL_ENABLED", False)
CLOUDFLARED_PATH = _str("CLOUDFLARED_PATH")  # để trống = tự tìm
OPEN_BROWSER = _bool("OPEN_BROWSER", False)  # tự mở trình duyệt khi có link (CHAY_NHA_NAM.bat bật)
LOG_FILE = _str("LOG_FILE")  # ghi log ra file (khi chạy ẩn cùng Windows), vd server/data/server.log

# ---------- Zalo Bot ----------
ZALO_BOT_TOKEN = _str("ZALO_BOT_TOKEN")
ZALO_CHAT_IDS = [c.strip() for c in _str("ZALO_CHAT_IDS").split(",") if c.strip()]
ZALO_API_BASE = _str("ZALO_API_BASE", "https://bot-api.zaloplatforms.com").rstrip("/")
ZALO_COMMANDS_ENABLED = _bool("ZALO_COMMANDS_ENABLED", True)
# Zalo giới hạn số tin bot được gửi -> tự giới hạn để không bị khóa ("vượt quá hạn mức gửi tin nhắn")
ZALO_MAX_PER_HOUR = _int("ZALO_MAX_PER_HOUR", 30)
ZALO_MAX_PER_DAY = _int("ZALO_MAX_PER_DAY", 300)
ZALO_PAUSE_MIN = _int("ZALO_PAUSE_MIN", 120)  # Zalo báo vượt hạn mức -> ngừng gửi N phút

# ---------- LLM ----------
# LLM_PROVIDER: anthropic | openai (mọi API tương thích OpenAI: OpenAI, Gemini, Groq,
# DeepSeek, OpenRouter, Ollama...) | để trống = tự đoán theo API key
LLM_PROVIDER = _str("LLM_PROVIDER").lower()
LLM_API_KEY = _str("LLM_API_KEY")
LLM_MODEL = _str("LLM_MODEL")
LLM_BASE_URL = _str("LLM_BASE_URL").rstrip("/")
# Model dự phòng khi model chính quá tải (chỉ áp dụng API tương thích OpenAI), cách nhau dấu phẩy
LLM_FALLBACK_MODELS = [m.strip() for m in _str("LLM_FALLBACK_MODELS").split(",") if m.strip()]
LLM_TIMEOUT_SEC = _int("LLM_TIMEOUT_SEC", 30)

# ---------- Tự động điều khiển ----------
# Firmware ESP32 đã tự điều khiển ở chế độ AUTO -> server chỉ giám sát (bật lên sẽ ghi đè nhau)
AUTO_CONTROL_ENABLED = _bool("AUTO_CONTROL_ENABLED", False)
DRY_RUN = _bool("DRY_RUN", False)  # True: không ghi Firebase, không gửi Zalo (chỉ log)
MIN_TOGGLE_SEC = _int("MIN_TOGGLE_SEC", 30)
PUMP_MAX_ON_MIN = _float("PUMP_MAX_ON_MIN", 15)
MIST_MAX_ON_MIN = _float("MIST_MAX_ON_MIN", 20)
WATER_LOCKOUT_MIN = _float("WATER_LOCKOUT_MIN", 10)
HYST_SOIL = _float("HYST_SOIL", 3)
HYST_HUMIDITY = _float("HYST_HUMIDITY", 3)
HYST_TEMP = _float("HYST_TEMP", 1)
HYST_LIGHT_RATIO = _float("HYST_LIGHT_RATIO", 0.1)

# ---------- Cảnh báo ----------
NOTIFY_DEVICE_CHANGES = _bool("NOTIFY_DEVICE_CHANGES", True)
NOTIFY_MANUAL_CHANGES = _bool("NOTIFY_MANUAL_CHANGES", True)
# Báo MỌI lần bật/tắt thiết bị; các thay đổi trong N giây gom thành 1 tin (đỡ tốn hạn mức tin Zalo)
DEVICE_BATCH_SEC = _int("DEVICE_BATCH_SEC", 60)
ALERT_REPEAT_MIN = _float("ALERT_REPEAT_MIN", 30)
DAILY_REPORT_HOUR = _int("DAILY_REPORT_HOUR", 7)  # -1 để tắt
PERIODIC_REPORT_SEC = _int("PERIODIC_REPORT_SEC", 3600)  # gửi tình trạng định kỳ qua Zalo, 0 = tắt
if 0 < PERIODIC_REPORT_SEC < 600:  # 15 giây/lần làm bot hết hạn mức tin nhắn trong vài giờ
    PERIODIC_REPORT_SEC = 600
HISTORY_RETENTION_DAYS = _int("HISTORY_RETENTION_DAYS", 30)

# ---------- Thời tiết ngoài trời (Open-Meteo) cho phân tích Random Forest ----------
# Mặc định: TP. Vĩnh Long (nơi đặt nhà nấm). Đặt WEATHER_LAT/WEATHER_LON trong .env để dùng tọa độ khác
WEATHER_LAT = _opt_float("WEATHER_LAT")
WEATHER_LON = _opt_float("WEATHER_LON")
WEATHER_NAME = _str("WEATHER_NAME")
if WEATHER_LAT is None or WEATHER_LON is None:
    WEATHER_LAT, WEATHER_LON, WEATHER_NAME = 10.2537, 105.9722, WEATHER_NAME or "Vĩnh Long"

# ---------- Google Sheet ESP32 ghi 1 phút/lần (kể cả khi máy tính tắt) -> máy chủ lấy về lấp dữ liệu thiếu ----------
GOOGLE_SHEET_ID = _str("GOOGLE_SHEET_ID")  # phần giữa /d/ và /edit trong link Sheet; để trống = không dùng
GOOGLE_SHEET_GID = _str("GOOGLE_SHEET_GID", "0")

DB_PATH = SERVER_DIR / "data" / "greenhouse.db"
