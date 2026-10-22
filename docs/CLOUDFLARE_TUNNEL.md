# Đưa web Nhà Nấm ra Internet bằng Cloudflare Tunnel

Máy tính chạy máy chủ AI làm server. Cloudflare Tunnel tạo link `https://….trycloudflare.com` trỏ vào máy đó — không cần mở cổng modem, không cần IP tĩnh, miễn phí.

> Máy tính phải bật và cửa sổ phải mở thì web/AI/Zalo mới chạy. ESP32 vẫn tự điều khiển nhà nấm khi máy tắt.

## Cách 1 — 1 file duy nhất (khuyên dùng)

1. Chép cả thư mục dự án sang máy (tải ZIP từ GitHub hoặc `git clone`).
2. Nhấp đúp **`CHAY_NHA_NAM.bat`**:
   - gỡ chế độ tự chạy khi mở máy và tắt bản cũ còn chạy ngầm (nếu có);
   - lần đầu cài Python 3.12, thư viện, `cloudflared` (qua `winget`), tạo `server/.env`, hỏi key Gemini / token Zalo;
   - chạy web + máy chủ AI + đường hầm; ~30 giây sau in link, tự mở trình duyệt, gửi link về Zalo, ghi `LINK_WEB.txt`.
3. **Đóng cửa sổ = tắt hết.** Muốn chạy lại: bấm lại file.

Không cần đăng nhập (ai có link đều vào được — link ngẫu nhiên, đổi mỗi lần chạy). Muốn có mật khẩu: đặt `APP_PASSWORD=...` trong `server/.env`.

Nhật ký: `server/data/server.log`. Còn bản cũ chạy ngầm: `DUNG_NHA_NAM.bat`.

## Cách 2 — Làm bằng tay

1. Cài Python 3.10+ (python.org, tick *Add to PATH*), rồi `pip install -r server\requirements.txt`.
2. Cài cloudflared: `winget install Cloudflare.cloudflared`.
3. Chép `server\.env.example` thành `server\.env`, đặt `TUNNEL_ENABLED=true` (thêm `LLM_*`, `ZALO_*` nếu dùng).
4. Chạy `python server\app.py` — link hiện trong cửa sổ, `LINK_WEB.txt`, gửi về Zalo.

   Hoặc để `TUNNEL_ENABLED=false` và tự mở đường hầm ở cửa sổ thứ hai: `cloudflared tunnel --url http://localhost:5000`.

## Link cố định (không đổi sau mỗi lần chạy)

Cần tài khoản Cloudflare (miễn phí) và một tên miền đã trỏ về Cloudflare:

```bat
cloudflared tunnel login
cloudflared tunnel create nhanam
cloudflared tunnel route dns nhanam nhanam.ten-mien-cua-ban.vn
cloudflared tunnel run --url http://localhost:5000 nhanam
```

Khi đó để `TUNNEL_ENABLED=false` (máy chủ không tự mở đường hầm tạm nữa).

## Lưu ý

- Link vừa tạo cần 10–30 giây mới truy cập được; máy chủ chờ link hoạt động rồi mới báo và mở trình duyệt. Mở quá sớm trình duyệt có thể nhớ lỗi “không tìm thấy trang” vài phút — đợi chút rồi tải lại.
- Web không đăng nhập thì ai có link cũng điều khiển được thiết bị — chỉ gửi link cho người tin cậy; nên khóa Firebase theo [BAO_MAT_FIREBASE.md](BAO_MAT_FIREBASE.md).
- Không gửi file `server/.env` cho người khác (chứa key Gemini, token Zalo).
