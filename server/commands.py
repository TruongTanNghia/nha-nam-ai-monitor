"""Nhận lệnh từ Zalo: xem trạng thái, điều khiển thiết bị, hỏi AI."""
import logging
import time
import unicodedata

import indices
import llm
from analyzer import SEV_RANK
from engine import SEV_ICON, now_text
from forecast import sentence
from sensors import DEVICES

log = logging.getLogger("commands")

DEVICE_ALIASES = {
    "bom": "BOM", "may bom": "BOM", "tuoi": "BOM",
    "den": "DEN", "quat": "QUAT",
    "suong": "SUONG", "phun suong": "SUONG", "phun": "SUONG",
}

HELP = """🤖 TRỢ LÝ NHÀ NẤM — danh sách lệnh
📊 /trangthai — số liệu hiện tại
🧪 /phantich — kết quả phân tích AI
📋 /baocao — báo cáo 24 giờ
🔔 /canhbao — các cảnh báo đang mở
⚙️ /auto | /manual — đổi chế độ
💡 /bat bom|den|quat|suong
🔌 /tat bom|den|quat|suong | /tat het
🪟 /mo man | /dong man — màn che
🔕 /im 60 — tạm tắt thông báo thường 60 phút (/batthongbao để bật lại)
💬 Hoặc nhắn câu hỏi bất kỳ để hỏi AI"""


def normalize(text):
    """Bỏ dấu, chữ thường, bỏ '/' đầu: '/Bật Quạt' -> 'bat quat'."""
    text = text.strip().lower().replace("đ", "d")
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return " ".join(text.lstrip("/!").split())


class CommandHandler:
    def __init__(self, engine):
        self.engine = engine
        self.zalo = engine.zalo

    def loop(self):
        log.info("Đang lắng nghe lệnh Zalo...")
        while self.engine.running:
            for msg in self.zalo.get_updates(timeout=30):
                try:
                    self.handle(msg["chat_id"], msg["text"])
                except Exception:
                    log.exception("Lỗi xử lý lệnh Zalo")
                    self.zalo.send("⚠️ Có lỗi khi xử lý lệnh, thử lại sau.", [msg["chat_id"]])

    def reply(self, chat_id, text):
        self.zalo.send(text, [chat_id], priority="high")

    def handle(self, chat_id, text):
        cmd = normalize(text)
        log.info("Zalo %s: %s", chat_id, text)

        if cmd in ("chatid", "id"):
            return self.reply(chat_id, f"🆔 Chat ID của bạn: {chat_id}")
        if chat_id not in self.zalo.chat_ids:
            return self.reply(
                chat_id,
                f"⛔ Bạn chưa có quyền điều khiển nhà nấm.\nChat ID: {chat_id}\n"
                "Nhờ quản trị thêm ID này vào ZALO_CHAT_IDS trong server/.env",
            )

        e = self.engine
        if cmd in ("help", "menu", "start", "huong dan", "lenh", "?"):
            return self.reply(chat_id, HELP)
        if cmd in ("trangthai", "trang thai", "status", "tt"):
            return self.reply(chat_id, self.status_text())
        if cmd in ("phantich", "phan tich", "analysis", "pt"):
            return self.reply(chat_id, self.analysis_text())
        if cmd in ("baocao", "bao cao", "report", "bc"):
            if llm.status()["enabled"]:
                self.reply(chat_id, "⏳ Đang tổng hợp báo cáo...")
            report, _ = e.build_report(24)
            return self.reply(chat_id, report)
        if cmd in ("canhbao", "canh bao", "alerts", "cb"):
            return self.reply(chat_id, self.alerts_text())
        if cmd in ("auto", "tudong", "tu dong"):
            return self.control(chat_id, {"MODE": 1}, "⚙️ Đã chuyển sang AUTO 🤖")
        if cmd in ("manual", "thucong", "thu cong", "tay"):
            return self.control(chat_id, {"MODE": 0}, "⚙️ Đã chuyển sang MANUAL ✋")
        if cmd in ("tat het", "tat tat ca"):
            return self.control(
                chat_id, {"BOM": 0, "DEN": 0, "QUAT": 0, "SUONG": 0}, "🔌 Đã TẮT tất cả thiết bị"
            )
        if cmd in ("mo man", "mo man che", "mo"):
            return self.control(chat_id, {"MO": 1, "DONG": 0}, "🪟 Đã MỞ màn che")
        if cmd in ("dong man", "dong man che", "dong"):
            return self.control(chat_id, {"MO": 0, "DONG": 1}, "🪟 Đã ĐÓNG màn che")
        for verb, value in (("bat ", 1), ("tat ", 0)):
            if cmd.startswith(verb):
                dev = DEVICE_ALIASES.get(cmd[len(verb):].strip())
                if dev:
                    word = "BẬT" if value else "TẮT"
                    return self.control(chat_id, {dev: value}, f"✅ Đã {word} {DEVICES[dev].lower()}")
        if cmd.startswith("im"):
            parts = cmd.split()
            minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
            e.mute_until = time.time() + minutes * 60
            return self.reply(chat_id, f"🔕 Tạm tắt thông báo thường {minutes} phút. Cảnh báo NGHIÊM TRỌNG vẫn gửi.")
        if cmd in ("batthongbao", "bat thong bao", "unmute"):
            e.mute_until = 0
            return self.reply(chat_id, "🔔 Đã bật lại thông báo.")

        # Câu hỏi tự do -> AI
        if llm.status()["enabled"]:
            answer = llm.ask(
                f"Người dùng hỏi qua Zalo: \"{text.strip()[:500]}\". Trả lời ngắn gọn (dưới 800 ký tự) "
                "dựa trên dữ liệu nhà nấm.",
                e.context(),
                effort="low", max_tokens=3000,
            )
            if answer:
                return self.reply(chat_id, answer)
            return self.reply(chat_id, "⚠️ AI đang bận, thử lại sau hoặc gõ /help.")
        return self.reply(chat_id, "🤔 Mình chưa hiểu lệnh này.\n\n" + HELP)

    def control(self, chat_id, updates, ok_text):
        ok, err = self.engine.set_devices(updates, source="zalo")
        self.reply(chat_id, ok_text if ok else f"⚠️ {err}")

    # ---------------------------------------------------------------- texts
    def status_text(self):
        e = self.engine
        snap = e.snapshot()
        if not snap["data"]:
            return "⏳ Chưa có dữ liệu từ cảm biến."
        a = snap["analysis"] or {}
        data = snap["data"]
        devs = []
        for key, name in (("BOM", "Bơm"), ("DEN", "Đèn"), ("QUAT", "Quạt"), ("SUONG", "Sương")):
            devs.append(f"{name} {'🟢' if data.get(key) == 1 else '⚪'}")
        door = "Mở" if data.get("MO") == 1 else "Đóng" if data.get("DONG") == 1 else "—"
        idx_lines = "\n".join(indices.summary_lines(a.get("indices")))
        return (
            f"🍄 TRẠNG THÁI NHÀ NẤM\n{idx_lines}\n{e.readings_line()}\n"
            f"🔧 {' | '.join(devs)} | Màn che: {door}\n"
            f"📝 {a.get('summary', '')}\n🕒 {now_text(snap['last_ok_ts'])}"
        )

    def analysis_text(self):
        a = self.engine.snapshot()["analysis"]
        if not a:
            return "⏳ Chưa có dữ liệu phân tích."
        lines = ["🧪 PHÂN TÍCH", *indices.summary_lines(a.get("indices"))]
        if a["findings"]:
            for f in a["findings"][:8]:
                lines.append(f"{SEV_ICON[f['severity']]} {f['title']}: {f['detail']}")
        else:
            lines.append("✅ Không phát hiện vấn đề.")
        fc = a.get("forecasts") or {}
        if fc:
            lines.append("🔮 Dự báo:")
            for f in fc.values():
                lines.append(f"• {sentence(f['headline'])}")
        ml = a.get("ml") or {}
        if ml.get("trained"):
            state = "BẤT THƯỜNG" if (ml.get("latest") or {}).get("anomaly") else "bình thường"
            lines.append(f"🤖 Model {ml['name']} ({ml['samples']} mẫu): {state}")
        return "\n".join(lines)

    def alerts_text(self):
        active = [a for a in self.engine.store.alerts(50) if a["status"] == "active"]
        active = [a for a in active if SEV_RANK.get(a["severity"], 0) >= 2]
        if not active:
            return "✅ Không có cảnh báo nào đang mở."
        lines = ["🔔 CẢNH BÁO ĐANG MỞ"]
        for a in active[:10]:
            lines.append(f"{SEV_ICON[a['severity']]} {a['title']} (từ {now_text(a['ts'])})")
        return "\n".join(lines)
