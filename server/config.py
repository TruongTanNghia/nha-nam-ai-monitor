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
    return os.getenv(name, default).strip()


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

# ---------- Zalo Bot ----------
ZALO_BOT_TOKEN = _str("ZALO_BOT_TOKEN")
ZALO_CHAT_IDS = [c.strip() for c in _str("ZALO_CHAT_IDS").split(",") if c.strip()]
ZALO_API_BASE = _str("ZALO_API_BASE", "https://bot-api.zaloplatforms.com").rstrip("/")
ZALO_COMMANDS_ENABLED = _bool("ZALO_COMMANDS_ENABLED", True)

# ---------- LLM ----------
# LLM_PROVIDER: anthropic | openai (mọi API tương thích OpenAI: OpenAI, Gemini, Groq,
# DeepSeek, OpenRouter, Ollama...) | để trống = tự đoán theo API key
LLM_PROVIDER = _str("LLM_PROVIDER").lower()
LLM_API_KEY = _str("LLM_API_KEY")
LLM_MODEL = _str("LLM_MODEL")
LLM_BASE_URL = _str("LLM_BASE_URL").rstrip("/")
LLM_TIMEOUT_SEC = _int("LLM_TIMEOUT_SEC", 60)

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
DEVICE_NOTIFY_MIN = _float("DEVICE_NOTIFY_MIN", 5)  # mỗi thiết bị báo Zalo tối đa 1 lần / N phút
ALERT_REPEAT_MIN = _float("ALERT_REPEAT_MIN", 30)
DAILY_REPORT_HOUR = _int("DAILY_REPORT_HOUR", 7)  # -1 để tắt
HISTORY_RETENTION_DAYS = _int("HISTORY_RETENTION_DAYS", 30)

DB_PATH = SERVER_DIR / "data" / "greenhouse.db"
