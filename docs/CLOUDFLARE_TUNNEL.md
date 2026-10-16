# Đưa web Nhà Nấm ra Internet bằng Cloudflare Tunnel

Máy tính chạy **máy chủ AI** (`server/app.py`) làm server. Cloudflare Tunnel tạo một link `https://….trycloudflare.com` trỏ vào máy đó — không cần mở cổng modem, không cần IP tĩnh, miễn phí.

> Máy tính phải bật thì web/AI/Zalo mới chạy. ESP32 vẫn tự điều khiển nhà nấm khi máy tắt.

## Cách 1 — Cài 1 phát (khuyên dùng cho máy khách)

1. Chép cả thư mục dự án sang máy khách (tải ZIP từ GitHub hoặc `git clone`).
2. Nhấp đúp **`CAI_DAT_NHA_NAM.bat`**. File sẽ tự:
   - cài Python 3.12 (nếu chưa có), các thư viện, và `cloudflared` (qua `winget`);
   - tạo `server/.env`, **tự đặt mật khẩu web**, hỏi key Gemini / token Zalo / chat ID (Enter để bỏ qua);
   - đăng ký tác vụ **`NhaNamAI`** để máy chủ chạy ẩn mỗi khi đăng nhập Windows (tự khởi động lại nếu bị tắt);
   - khởi động máy chủ, mở đường hầm và in ra **link web + tài khoản + mật khẩu**, rồi mở trình duyệt.
3. Ghi lại mật khẩu. Đổi được trong `server/.env` (`APP_PASSWORD`), sau đó chạy lại file cài.

**Link đổi mỗi lần máy chủ khởi động lại.** Mỗi khi có link mới, máy chủ tự:
- gửi link về **Zalo** (nếu đã cấu hình bot + chat ID);
- ghi vào file **`LINK_WEB.txt`** trong thư mục dự án;
- hiện trên web, tab **Cảnh báo → Máy chủ AI** (có nút Chép).

Tắt: **`DUNG_NHA_NAM.bat`**. Bỏ luôn chế độ chạy cùng Windows: mở cmd tại thư mục dự án, chạy `DUNG_NHA_NAM.bat -GoTuChay`.

Chạy lại `CAI_DAT_NHA_NAM.bat` bất cứ lúc nào cũng an toàn: giữ nguyên cấu hình cũ, chỉ bổ sung cái còn thiếu.

Tùy chọn khi chạy từ cmd/PowerShell:

```bat
CAI_DAT_NHA_NAM.bat -NoPrompt -Autostart yes -GeminiKey "AQ..." -ZaloToken "123:abc" -ZaloChatId "xyz"
```

Nhật ký máy chủ: `server/data/server.log`.

## Cách 2 — Làm bằng tay

1. **Cài Python 3.10+** (python.org, tick *Add to PATH*) rồi cài thư viện:
   ```bat
   pip install -r server\requirements.txt
   ```
2. **Cài cloudflared**:
   ```bat
   winget install Cloudflare.cloudflared
   ```
3. **Cấu hình** — chép `server\.env.example` thành `server\.env`, sửa:
   ```ini
   APP_USER=admin
   APP_PASSWORD=mat-khau-cua-ban      # bắt buộc trước khi ra Internet
   TUNNEL_ENABLED=true                # máy chủ tự mở đường hầm + gửi link về Zalo
   LOG_FILE=server/data/server.log
   ```
   (thêm `LLM_*`, `ZALO_*` nếu dùng AI và Zalo)
4. **Chạy**: `python server\app.py` — link hiện trong cửa sổ, trong `LINK_WEB.txt` và gửi về Zalo.

   Hoặc không bật `TUNNEL_ENABLED`, tự mở đường hầm ở cửa sổ thứ hai:
   ```bat
   cloudflared tunnel --url http://localhost:5000
   ```
   (`chay_cloudflare.bat` làm sẵn 2 bước này.)
5. **Chạy cùng Windows** (tùy chọn): Task Scheduler → *Create Task* → Trigger *At log on* → Action chạy `pythonw.exe` với đối số `"<thư mục dự án>\server\app.py"`, *Start in* là thư mục dự án.

## Link cố định (không đổi sau mỗi lần khởi động)

Cần tài khoản Cloudflare (miễn phí) và một tên miền đã trỏ về Cloudflare:

```bat
cloudflared tunnel login
cloudflared tunnel create nhanam
cloudflared tunnel route dns nhanam nhanam.ten-mien-cua-ban.vn
cloudflared tunnel run --url http://localhost:5000 nhanam
```

Khi đó để `TUNNEL_ENABLED=false` (máy chủ không tự mở đường hầm tạm nữa) và cài named tunnel thành dịch vụ Windows: `cloudflared service install` (xem tài liệu Cloudflare Zero Trust).

## Bảo mật

- Luôn đặt `APP_PASSWORD` — máy chủ **từ chối mở đường hầm** khi chưa có mật khẩu.
- Link đang công khai nên nên khóa Firebase theo [BAO_MAT_FIREBASE.md](BAO_MAT_FIREBASE.md).
- Không gửi file `server/.env` cho người khác (chứa key Gemini, token Zalo, mật khẩu).
