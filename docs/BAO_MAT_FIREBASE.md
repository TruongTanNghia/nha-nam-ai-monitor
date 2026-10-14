# Khóa Firebase Realtime Database

Mặc định database cho **mọi người** đọc và ghi. Địa chỉ database có trong `app.js` (bắt buộc để web chạy), nên ai biết địa chỉ cũng bật/tắt được thiết bị. Làm các bước dưới để chỉ tài khoản của bạn mới ghi được.

## 1. Tạo tài khoản quản trị
Firebase Console → **Authentication** → *Sign-in method* → bật **Email/Password** → tab *Users* → **Add user**. Ghi lại **User UID**.

## 2. Đặt rules
Firebase Console → **Realtime Database** → **Rules**, dán nội dung [`firebase/database.rules.json`](../firebase/database.rules.json) rồi thay `THAY_BANG_UID_TAI_KHOAN_QUAN_TRI` bằng UID ở bước 1 → **Publish**.

- `.read: true`: vẫn cho xem số liệu, không cần đăng nhập.
- `.write`: chỉ tài khoản quản trị được ghi (điều khiển thiết bị, đổi ngưỡng).

## 3. Cho các thành phần đăng nhập
| Thành phần | Cách đăng nhập |
|---|---|
| **ESP32** | Thư viện Firebase ESP Client: điền `auth.user.email` / `auth.user.password` của tài khoản quản trị |
| **Máy chủ AI** | Project settings → *Service accounts* → *Database secrets* → copy secret vào `FIREBASE_AUTH=` trong `server/.env` |
| **Web** | Cần thêm form đăng nhập (Firebase Auth email/password) — chưa có trong bản này |

> ⚠️ Sau khi khóa, web hiện tại chỉ **xem** được; nút điều khiển và lưu ngưỡng sẽ báo lỗi cho tới khi web có đăng nhập. Lệnh điều khiển qua **Zalo** vẫn chạy vì đi qua máy chủ AI.
