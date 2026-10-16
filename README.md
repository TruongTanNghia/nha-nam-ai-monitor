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

### Cài trên máy khách + đưa ra Internet (Cloudflare Tunnel)
Nhấp đúp **`CAI_DAT_NHA_NAM.bat`**: tự cài Python, thư viện, `cloudflared`, đặt mật khẩu web, hỏi key Gemini / token Zalo, cho máy chủ chạy ẩn cùng Windows và in ra link `https://….trycloudflare.com`. Link đổi mỗi lần khởi động lại — link mới tự gửi về Zalo, ghi vào `LINK_WEB.txt` và hiện ở tab Cảnh báo. Tắt bằng `DUNG_NHA_NAM.bat`.

Chi tiết, cách làm bằng tay và link cố định: [docs/CLOUDFLARE_TUNNEL.md](docs/CLOUDFLARE_TUNNEL.md).

> Không biết chat id? Nhắn `chatid` cho bot sau khi chạy server — bot trả lời ID; tab **Cảnh báo** trên web cũng liệt kê các chat đã nhắn.
>
> Muốn chạy thử an toàn (không ghi Firebase, không gửi Zalo): đặt `DRY_RUN=true`.

Web vẫn dùng được khi **không** chạy máy chủ AI (mở thẳng `index.html`): xem số liệu, điều khiển, biểu đồ trực tiếp.

## Firmware ESP32 (`aduno.ino`)
ESP32 đọc cảm biến (DHT11, BH1750, độ ẩm đất, độ mặn TDS, pH), hiển thị LCD, phát âm thanh DFPlayer, ghi Google Sheet và đồng bộ Firebase qua REST:

| Hướng | Nội dung | Chu kỳ |
|---|---|---|
| ESP → Firebase | `t h ANHSANG GT_AMDAT GT_DOMAN GT_PH`, nhịp tim `ESP_TS` (+ trạng thái thiết bị khi AUTO) | 0.7 s |
| Firebase → ESP | `SET_*`, `MODE`, lệnh thiết bị (dùng khi MANUAL) | 1 s |

**Chế độ AUTO do ESP32 tự điều khiển**: đèn bật khi ánh sáng < `SET_AS`; màn che MỞ khi ánh sáng ≤ `SET_AS`, ĐÓNG khi sáng hơn; quạt bật khi nhiệt độ > `SET_T`; phun sương bật khi độ ẩm < `SET_H`; bơm bật khi độ ẩm đất < `SET_AMDAT`. Bơm bị ngắt khi độ mặn > `SET_DOMAN`; bơm và phun sương bị ngắt khi pH ngoài `SET_PHLOW`–`SET_PHHIGH`.
**MANUAL**: web/Zalo ghi lệnh lên Firebase, ESP32 thực thi trong ~1 giây.

Nạp firmware: sao chép `secrets.example.h` thành `secrets.h` (cùng thư mục với `aduno.ino`), điền WiFi, link Google Apps Script và Firebase secret (nếu đã khóa rules). `secrets.h` không được đưa lên Git.

## Chức năng

| Phần | Nội dung |
|---|---|
| **Tổng quan** | Trạng thái kết nối ESP32 (đã kết nối / chậm / mất kết nối + từ bao giờ), **2 chỉ số riêng: Môi trường nhà nấm và Nước cấp**, nhận định AI, 6 thẻ cảm biến (đồng hồ + ngưỡng + xu hướng), trạng thái thiết bị, vấn đề & lịch sử bật/tắt |
| **Điều khiển** | Công tắc thiết bị, màn che, cài ngưỡng (có đơn vị & giải thích) |
| **Biểu đồ** | Trực tiếp / 1h / 6h / 24h / 7 ngày, đường ngưỡng, thấp–TB–cao |
| **Phân tích AI** | Danh sách vấn đề, dự báo 30 phút, trạng thái model ML, báo cáo AI, chat hỏi đáp |
| **Cảnh báo** | Lịch sử cảnh báo (lọc mức độ), trạng thái Zalo, gửi tin thử, tạm tắt thông báo |

### Hai chỉ số (server/indices.py)
| Chỉ số | Dữ liệu đầu vào | Ý nghĩa | Hành động |
|---|---|---|---|
| 🌿 **Môi trường nhà nấm** (0–100: Tốt/Khá/Cần chú ý/Nguy hiểm) | Nhiệt độ, độ ẩm không khí, ánh sáng, độ ẩm giá thể | Vi khí hậu có phù hợp với nấm không | Phun sương, quạt, màn che, đèn |
| 💧 **Chất lượng nước cấp** (Đạt/Cần theo dõi/Không đạt) | pH, độ mặn/TDS | Nước có an toàn để tưới, phun sương không | Khóa bơm; kiểm tra nguồn nước, lọc hoặc thay nước |

pH và độ mặn **không trung bình hóa** với nhiệt độ hay ánh sáng: môi trường tốt mà nước không đạt thì kết luận chung là “Nước không đạt”, kèm cảnh báo riêng *“Nước cấp không đạt — … đang bị khóa”* theo đúng firmware (pH ngoài khoảng → khóa bơm và phun sương; mặn vượt ngưỡng → khóa bơm).

### Phân tích (server/analyzer.py)
- **Luật chuyên gia**: vượt ngưỡng kéo dài > 2 phút (tránh báo nhầm do nhiễu), cảm biến lỗi/phi lý, nhiễu (giật số), báo 0 liên tục, đứng yên (ESP32 mất kết nối).
- **Thiết bị**: bơm chạy 8 phút mà đất không ẩm lên, quạt 15 phút không hạ nhiệt, phun sương không tăng ẩm, đèn bật không sáng hơn, màn che nhận cả MỞ & ĐÓNG, bật/tắt liên tục (≥ 6 lần/10 phút), bơm/phun sương chạy quá lâu.
- **Model ML – IsolationForest**: học 24h dữ liệu (tự huấn luyện lại mỗi 30 phút), phát hiện tổ hợp chỉ số bất thường và chỉ ra cảm biến lệch nhiều nhất.
- **Dự báo thiết bị** (`server/forecast.py`): từ tốc độ thay đổi trong vài phút gần nhất (cần ~1 phút dữ liệu), tính bao lâu nữa chạm ngưỡng và ESP32 sẽ làm gì — vd *“khoảng 4 phút nữa độ ẩm không khí xuống dưới 60% → ESP32 sẽ bật phun sương”*, hoặc khi nào chỉ số trở lại để tắt thiết bị. Hiện trên trang Tổng quan, tab Phân tích, tin Zalo định kỳ và lệnh `/phantich`. Cảm biến chưa cài ngưỡng (giá trị 0) thì chưa dự báo được thiết bị.

### Bật thiết bị sớm theo dự báo (server/preemptive.py)
Ở chế độ AUTO, khi dự báo còn ≤ `PREDICT_LEAD_MIN` phút (mặc định 3) nữa chỉ số chạm ngưỡng, máy chủ ghi yêu cầu `PRE` lên Firebase để ESP32 **bật sớm** quạt, phun sương, máy bơm, đèn và **mở màn che sớm** khi trời sắp tối. Khóa an toàn của firmware (pH lệch, nước mặn) vẫn thắng; máy chủ tắt/mất mạng thì yêu cầu tự hết hạn sau 60 giây. Bật/tắt bằng công tắc ở tab **Điều khiển**. Cần firmware có hỗ trợ `PRE` — xem [docs/FIRMWARE_CAP_NHAT.md](docs/FIRMWARE_CAP_NHAT.md).

### Máy chủ AI và chế độ AUTO
Mặc định máy chủ AI **chỉ giám sát**: khi ESP32 tự bật/tắt thiết bị, máy chủ báo Zalo kèm lý do (vd “BẬT máy bơm — độ ẩm đất 32% < ngưỡng 40%”), tối đa 1 lần / `DEVICE_NOTIFY_MIN` phút mỗi thiết bị, và cảnh báo khi thiết bị bật/tắt liên tục hoặc bơm chạy quá lâu.

Nếu dùng firmware **không** tự điều khiển, đặt `AUTO_CONTROL_ENABLED=true` để máy chủ điều khiển thay (cùng luật với firmware, thêm vùng trễ, ngắt bảo vệ bơm/phun sương sau `PUMP_MAX_ON_MIN`/`MIST_MAX_ON_MIN` phút, tắt thiết bị khi cảm biến lỗi). Không bật cùng lúc với firmware tự điều khiển — hai bên sẽ ghi đè nhau.

### Zalo
Tự gửi: **tình trạng định kỳ mỗi `PERIODIC_REPORT_SEC` giây** (mặc định 15 s) kèm nhận định AI (AI viết lại mỗi `PERIODIC_AI_MIN` phút hoặc khi có vấn đề mới, để không vượt hạn mức LLM), cảnh báo mới (kèm “Nhận định AI” nếu có LLM), nhắc lại sau `ALERT_REPEAT_MIN` phút nếu chưa xử lý, báo khi đã ổn định, mỗi lần tự bật/tắt thiết bị, khi có người điều khiển từ web, mất kết nối Firebase, báo cáo buổi sáng lúc `DAILY_REPORT_HOUR`.

Lệnh (chỉ chat có trong `ZALO_CHAT_IDS`): `/trangthai` `/phantich` `/baocao` `/canhbao` `/auto` `/manual` `/bat quat` `/tat bom` `/tat het` `/mo man` `/dong man` `/im 60` `/batthongbao` `chatid` — hoặc hỏi tự do, AI trả lời dựa trên số liệu.

### LLM (server/llm.py)
| Nhà cung cấp | Cấu hình `.env` |
|---|---|
| Claude | `LLM_PROVIDER=anthropic`, `LLM_API_KEY=sk-ant-...` (mặc định `claude-opus-5-5`) |
| OpenAI | `LLM_PROVIDER=openai`, `LLM_API_KEY=sk-...` (mặc định `gpt-4o-mini`) |
| Gemini | `LLM_PROVIDER=openai`, `LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`, `LLM_MODEL=gemini-3.8-flash` |
| Groq / DeepSeek / OpenRouter / Ollama | `LLM_PROVIDER=openai` + `LLM_BASE_URL` + `LLM_MODEL` tương ứng |

Không có key → hệ thống dùng tin nhắn mẫu, mọi chức năng khác vẫn chạy.

## Cấu trúc
```
aduno.ino                       Firmware ESP32
index.html, style.css, app.js   Web (Firebase realtime, điều khiển)
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
- `server/.env` chứa token — không commit (đã có `.gitignore`).
- API của máy chủ AI không có đăng nhập: chỉ mở trong mạng nội bộ; muốn đưa ra Internet cần thêm xác thực.
