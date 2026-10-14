# Nhà Nấm · Trạm giám sát AI

Web giám sát cảm biến nhà nấm/nhà kính (ESP32 + Firebase) kèm **Máy chủ AI** chạy nền: phân tích dữ liệu bằng luật chuyên gia + model ML, tự động bật/tắt thiết bị 24/7, gửi cảnh báo và nhận lệnh qua **Zalo Bot**, tích hợp **LLM** viết nhận định/báo cáo.

![Tổng quan](docs/overview.png)

| Phân tích AI | Biểu đồ | Giao diện tối |
|---|---|---|
| ![Phân tích AI](docs/analysis.png) | ![Biểu đồ](docs/charts.png) | ![Giao diện tối](docs/overview-dark.png) |

```
ESP32 ──► Firebase RTDB ◄──► Web (index.html)        ← xem & điều khiển
                 ▲
                 └──► Máy chủ AI (server/app.py)      ← phân tích, AUTO 24/7, Zalo, LLM
                          ├─ SQLite lịch sử (server/data/)
                          ├─ Zalo Bot  ◄─► điện thoại
                          └─ LLM (Claude / OpenAI / Gemini / Groq...)
```

## Chạy nhanh

1. Sao chép `server/.env.example` thành `server/.env`, điền:
   - `ZALO_BOT_TOKEN` = token bot tạo tại https://bot.zapps.me
   - `ZALO_CHAT_IDS` = chat id người nhận (nhiều người: ngăn cách dấu phẩy)
   - `LLM_PROVIDER`, `LLM_API_KEY` (và `LLM_MODEL`, `LLM_BASE_URL` nếu cần) — để trống vẫn chạy, chỉ không có phần AI viết lời
2. Windows: nhấp đúp **`chay_server.bat`** (hoặc `pip install -r server/requirements.txt` rồi `python server/app.py`).
3. Mở http://localhost:5000. Điện thoại cùng WiFi: `http://<IP máy tính>:5000`.

> Không biết chat id? Nhắn `chatid` cho bot sau khi chạy server — bot trả lời ID; tab **Cảnh báo** trên web cũng liệt kê các chat đã nhắn.
>
> Muốn chạy thử an toàn (không ghi Firebase, không gửi Zalo): đặt `DRY_RUN=true`.

Web vẫn dùng được khi **không** chạy máy chủ AI (mở thẳng `index.html`): xem số liệu, điều khiển, biểu đồ trực tiếp, AUTO cơ bản trong trình duyệt.

## Chức năng

| Phần | Nội dung |
|---|---|
| **Tổng quan** | Điểm sức khỏe 0–100, nhận định AI, 6 thẻ cảm biến (đồng hồ + ngưỡng + xu hướng), trạng thái thiết bị, vấn đề & lịch sử bật/tắt |
| **Điều khiển** | Công tắc thiết bị, màn che, cài ngưỡng (có đơn vị & giải thích) |
| **Biểu đồ** | Trực tiếp / 1h / 6h / 24h / 7 ngày, đường ngưỡng, thấp–TB–cao |
| **Phân tích AI** | Danh sách vấn đề, dự báo 30 phút, trạng thái model ML, báo cáo AI, chat hỏi đáp |
| **Cảnh báo** | Lịch sử cảnh báo (lọc mức độ), trạng thái Zalo, gửi tin thử, tạm tắt thông báo |

### Phân tích (server/analyzer.py)
- **Luật chuyên gia**: vượt ngưỡng kéo dài > 2 phút (tránh báo nhầm do nhiễu), cảm biến lỗi/phi lý, nhiễu (giật số), báo 0 liên tục, đứng yên (ESP32 mất kết nối).
- **Thiết bị không hiệu quả**: bơm chạy 8 phút mà đất không ẩm lên, quạt 15 phút không hạ nhiệt, phun sương không tăng ẩm, đèn bật không sáng hơn, màn che nhận cả MỞ & ĐÓNG.
- **Model ML – IsolationForest**: học 24h dữ liệu (tự huấn luyện lại mỗi 30 phút), phát hiện tổ hợp chỉ số bất thường và chỉ ra cảm biến lệch nhiều nhất.
- **Dự báo**: hồi quy tuyến tính 20 phút gần nhất → giá trị sau 30 phút và thời điểm có thể chạm mức nguy hiểm.

### Tự động điều khiển (server/controller.py)
Khi web để **AUTO**, máy chủ AI điều khiển thay trình duyệt (web tự nhường khi thấy máy chủ chạy):
- Vùng trễ (hysteresis) → thiết bị không bật/tắt liên tục quanh ngưỡng.
- Bơm/phun sương chạy quá `PUMP_MAX_ON_MIN`/`MIST_MAX_ON_MIN` phút → **ngắt bảo vệ** + nghỉ bắt buộc + báo Zalo.
- Cảm biến lỗi → tắt thiết bị liên quan thay vì chạy theo số sai.

### Zalo
Tự gửi: cảnh báo mới (kèm “Nhận định AI” nếu có LLM), nhắc lại sau `ALERT_REPEAT_MIN` phút nếu chưa xử lý, báo khi đã ổn định, mỗi lần tự bật/tắt thiết bị, khi có người điều khiển từ web, mất kết nối Firebase, báo cáo buổi sáng lúc `DAILY_REPORT_HOUR`.

Lệnh (chỉ chat có trong `ZALO_CHAT_IDS`): `/trangthai` `/phantich` `/baocao` `/canhbao` `/auto` `/manual` `/bat quat` `/tat bom` `/tat het` `/mo man` `/dong man` `/im 60` `/batthongbao` `chatid` — hoặc hỏi tự do, AI trả lời dựa trên số liệu.

### LLM (server/llm.py)
| Nhà cung cấp | Cấu hình `.env` |
|---|---|
| Claude | `LLM_PROVIDER=anthropic`, `LLM_API_KEY=sk-ant-...` (mặc định `claude-opus-5-5`) |
| OpenAI | `LLM_PROVIDER=openai`, `LLM_API_KEY=sk-...` (mặc định `gpt-4o-mini`) |
| Gemini | `LLM_PROVIDER=openai`, `LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`, `LLM_MODEL=gemini-2.5-flash` |
| Groq / DeepSeek / OpenRouter / Ollama | `LLM_PROVIDER=openai` + `LLM_BASE_URL` + `LLM_MODEL` tương ứng |

Không có key → hệ thống dùng tin nhắn mẫu, mọi chức năng khác vẫn chạy.

## Cấu trúc
```
index.html, style.css, app.js   Web (Firebase realtime, điều khiển, tự động dự phòng)
js/                             Module web: cảm biến, biểu đồ, kết nối máy chủ AI
server/app.py                   Flask: phục vụ web + API
server/engine.py                Vòng lặp chính: đọc → lưu → điều khiển → phân tích → cảnh báo
server/analyzer.py              Luật chuyên gia, IsolationForest, dự báo
server/controller.py            Tự động điều khiển an toàn
server/commands.py, zalo_bot.py Zalo Bot: gửi tin & nhận lệnh
server/llm.py                   Kết nối LLM
```

## Lưu ý
- **Bảo mật Firebase**: database mặc định cho ghi tự do — xem [docs/BAO_MAT_FIREBASE.md](docs/BAO_MAT_FIREBASE.md) để khóa lại.
- Máy chủ AI ghi thêm nút nhỏ `AI_SERVER` (heartbeat, điểm sức khỏe) vào gốc Firebase.
- `server/.env` chứa token — không commit (đã có `.gitignore`).
- API của máy chủ AI không có đăng nhập: chỉ mở trong mạng nội bộ; muốn đưa ra Internet cần thêm xác thực.
