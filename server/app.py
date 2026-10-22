"""Máy chủ AI Nhà Nấm.

Chạy:  python server/app.py   rồi mở http://localhost:5000
"""
import hmac
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if sys.stdout is None:  # chạy ẩn bằng pythonw (không có cửa sổ)
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
for stream in (sys.stdout, sys.stderr):  # console Windows mặc định không in được tiếng Việt/emoji
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from flask import Flask, Response, abort, jsonify, request, send_from_directory  # noqa: E402

import config  # noqa: E402
import llm  # noqa: E402
from engine import Engine, now_text  # noqa: E402
from tunnel import Tunnel  # noqa: E402

_handlers = [logging.StreamHandler()]
if config.LOG_FILE:  # chạy ẩn (pythonw) không có cửa sổ -> ghi log ra file
    Path(config.LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    _handlers.append(logging.FileHandler(config.LOG_FILE, encoding="utf-8"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=_handlers,
)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__, static_folder=None)
engine = Engine()
tunnel = Tunnel(engine)

STATIC_EXT = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".webp"}
BLOCKED_DIRS = {"server", "Nhom5_Intrusion_Warning"}


def _same(a, b):
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


@app.before_request
def require_password():
    """Bật khi đặt APP_PASSWORD: cả web lẫn API đều cần đăng nhập (bắt buộc khi đưa ra Internet)."""
    if not config.APP_PASSWORD or request.method == "OPTIONS":
        return None
    auth = request.authorization
    if auth and _same(auth.username, config.APP_USER) and _same(auth.password, config.APP_PASSWORD):
        return None
    return Response(
        "Cần đăng nhập để vào Nhà Nấm", 401,
        {"WWW-Authenticate": 'Basic realm="Nha Nam", charset="UTF-8"'},
    )


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


# ------------------------------------------------------------------ web tĩnh
@app.route("/")
def index():
    return send_from_directory(config.PROJECT_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    p = Path(path)
    if p.parts[0] in BLOCKED_DIRS or p.suffix.lower() not in STATIC_EXT:
        abort(404)
    return send_from_directory(config.PROJECT_DIR, path)


# ----------------------------------------------------------------------- API
@app.route("/api/<path:_>", methods=["OPTIONS"])
def preflight(_):
    return "", 204


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "uptime_sec": round(time.time() - engine.started_at),
        "last_ok_ts": engine.last_ok_ts,
        "firebase_ok": engine.fail_count == 0 and engine.last_ok_ts is not None,
        "auto_control": config.AUTO_CONTROL_ENABLED,
        "dry_run": config.DRY_RUN,
        "poll_sec": config.POLL_INTERVAL_SEC,
        "zalo": engine.zalo.status(),
        "llm": llm.status(),
        "muted_until": engine.mute_until if engine.mute_until > time.time() else None,
        "tunnel": tunnel.status(),
    })


@app.get("/api/snapshot")
def snapshot():
    return jsonify(engine.snapshot())


@app.get("/api/history")
def history():
    range_key = request.args.get("range", "1h")
    return jsonify(engine.store.history(range_key))


@app.get("/api/alerts")
def alerts():
    limit = min(int(request.args.get("limit", 100)), 500)
    return jsonify({"alerts": engine.store.alerts(limit)})


@app.get("/api/events")
def events():
    return jsonify({"events": engine.store.device_events(int(request.args.get("limit", 50)))})


@app.post("/api/report")
def report():
    body = request.get_json(silent=True) or {}
    hours = int(body.get("hours", 24))
    text, by_ai = engine.build_report(hours)
    sent = False
    if body.get("send_zalo"):
        sent = engine.zalo.send(f"{text}\n🕒 {now_text()}", priority="high")
    return jsonify({"text": text, "by_ai": by_ai, "sent_zalo": sent})


@app.post("/api/ask")
def ask():
    question = ((request.get_json(silent=True) or {}).get("question") or "").strip()[:800]
    if not question:
        return jsonify({"error": "Câu hỏi trống"}), 400
    if not llm.status()["enabled"]:
        return jsonify({"answer": None, "error": "Chưa cấu hình LLM (LLM_API_KEY trong server/.env)"})
    answer = llm.ask(
        f"Người dùng hỏi trên web: \"{question}\". Trả lời rõ ràng, ngắn gọn dựa trên dữ liệu.",
        engine.context(), effort="low", max_tokens=4000,
    )
    return jsonify({"answer": answer, "error": None if answer else "LLM không phản hồi, thử lại sau"})


@app.post("/api/zalo/test")
def zalo_test():
    ok = engine.zalo.send(
        "👋 Xin chào! Đây là tin nhắn thử từ Máy chủ AI Nhà Nấm.\n"
        f"{engine.readings_line()}\nGõ /help để xem các lệnh.\n🕒 {now_text()}",
        priority="high",
    )
    return jsonify({"ok": ok, "error": engine.zalo.last_error, "status": engine.zalo.status()})


@app.get("/api/zalo/chats")
def zalo_chats():
    """Các chat đã nhắn cho bot — giúp lấy chat_id khi cài đặt."""
    return jsonify({"chats": list(engine.zalo.seen_chats.values())})


@app.post("/api/mute")
def mute():
    minutes = int((request.get_json(silent=True) or {}).get("minutes", 60))
    engine.mute_until = time.time() + minutes * 60 if minutes > 0 else 0
    return jsonify({"muted_until": engine.mute_until or None})


if __name__ == "__main__":
    engine.start()
    tunnel.start()
    print(f"\n🍄 Máy chủ AI Nhà Nấm: http://localhost:{config.PORT}\n")
    app.run(host=config.HOST, port=config.PORT, threaded=True, use_reloader=False)
