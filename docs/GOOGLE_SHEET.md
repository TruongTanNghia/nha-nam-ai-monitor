# Lưu dữ liệu 24/7 vào Google Sheet

## Dữ liệu để AI học lấy từ đâu?

Có 2 nguồn, cùng đổ vào file `server/data/greenhouse.db` trên máy chạy máy chủ AI:

| Nguồn | Tần suất | Khi nào có |
|---|---|---|
| Máy chủ AI đọc Firebase | 5 giây/lần | chỉ khi máy tính đang chạy `CHAY_NHA_NAM.bat` |
| **ESP32 ghi Google Sheet** | **1 phút/lần** | **24/7, kể cả khi máy tính tắt** |

Máy chủ tự lấy Google Sheet về **lúc khởi động và 30 phút/lần**. Phút nào máy chủ chưa có số liệu thì lấy từ Sheet. Nhờ vậy biểu đồ và mô hình Random Forest có đủ dữ liệu ngày lẫn đêm. Trạng thái hiện ở web: tab **Phân tích AI → Mô hình Random Forest → Google Sheet**.

## Cài đặt (một lần)

### 1. Tạo link Apps Script mới (link cũ đã chết)

1. Mở Google Sheet **NHÀ NẤM**.
2. Chọn **Tiện ích mở rộng → Apps Script**. Xóa hết code cũ, dán toàn bộ nội dung file [`google_sheet/Code.gs`](../google_sheet/Code.gs) rồi bấm **Lưu**.
3. Chọn **Triển khai → Tùy chọn triển khai mới**, bấm bánh răng và chọn **Ứng dụng web**:
   - Thực thi dưới dạng: **Tôi**
   - Người có quyền truy cập: **Bất kỳ ai**
4. Bấm **Triển khai** và cấp quyền: chọn tài khoản → Nâng cao → Đi tới… (không an toàn) → Cho phép. Đây là script của chính mình nên an toàn.
5. Chép **URL ứng dụng web** (kết thúc bằng `/exec`).
6. Thử: dán vào trình duyệt `URL?sts=write&t=30&h=80&amdat=60&doman=200&ph=7&anhsang=300`. Phải hiện chữ **OK** và Sheet có thêm 1 dòng ở trên cùng. Thử xong xóa dòng đó đi.

> Sau này sửa code Apps Script thì vào **Triển khai → Quản lý các bản triển khai → sửa (bút chì) → Phiên bản: Mới**. Làm như vậy URL được giữ nguyên, không phải nạp lại ESP32.

### 2. Cho ESP32 ghi vào link mới

Trong `secrets.h` (cùng thư mục với file `.ino`):

```cpp
#define GOOGLE_SCRIPT_URL "https://script.google.com/macros/s/....../exec"
```

Nạp lại firmware `aduno.ino`. Mở Serial Monitor sẽ thấy `Google Sheet HTTP Code: 200` mỗi phút.

### 3. Cho máy chủ AI đọc Sheet

1. Trong Google Sheet bấm **Chia sẻ** và chọn **Bất kỳ ai có đường liên kết → Người xem**.
2. Trong `server/.env` thêm dòng sau. Mã Sheet là phần giữa `/d/` và `/edit` trong link.

   ```
   GOOGLE_SHEET_ID=ma_sheet_cua_ban
   ```

3. Chạy lại `CHAY_NHA_NAM.bat`. Trên web, mục **Google Sheet** hiện số dòng và thời điểm dòng mới nhất. Nếu dòng mới nhất cũ hơn 15 phút, web báo “ESP32 chưa ghi Sheet gần đây”: kiểm tra lại bước 2.

### 4. Bảng dự báo & thực tế (tab DỰ BÁO)

Máy chủ ghi lại **mọi lần Random Forest dự báo** (15 phút/lần, cho 1, 2, 3 giờ tới). Tới giờ được dự báo, máy chủ lấy **số đo thật** (trung bình ±7,5 phút) để so. Sau đó máy chủ ghi dòng đó lên tab **DỰ BÁO** của Google Sheet. Tab này tự tạo và nằm sau trang dữ liệu gốc.

Trong `server/.env` thêm **cùng link /exec** đã dán vào `secrets.h`:

```
GOOGLE_SCRIPT_URL=https://script.google.com/macros/s/....../exec
```

Mỗi dòng của tab DỰ BÁO gồm: lúc dự báo, dự báo cho lúc nào, sau mấy giờ. Với mỗi chỉ số (nhiệt độ, độ ẩm không khí, độ ẩm đất, ánh sáng, pH, độ mặn) có 3 cột: **giá trị lúc dự báo**, **RF dự báo**, **thực tế**.

## Tải file Excel (nộp BTC)

Web → tab **Phân tích AI** → thẻ **Mô hình Random Forest** → nút **7 / 30 / 365 ngày**. Hoặc mở `http://localhost:5000/api/export.xlsx?days=30`. File gồm các trang:

| Trang | Nội dung |
|---|---|
| Giới thiệu | nguồn dữ liệu, cách tính |
| Dữ liệu gốc | mỗi phút: 6 cảm biến + trạng thái thiết bị + số lần đo trong phút |
| Dự báo & thực tế | giống tab DỰ BÁO |
| Tổng hợp sai số | sai số trung bình của Random Forest và của cách "giữ nguyên giá trị", % tốt hơn, theo từng chỉ số × 1/2/3 giờ; kèm kết quả kiểm chứng walk-forward lúc huấn luyện |
| Thời tiết | Open-Meteo theo giờ |

Máy chủ giữ dữ liệu 365 ngày (`HISTORY_RETENTION_DAYS`). Google Sheet thì giữ mãi.

## Cột trong Sheet

`NGÀY, GIỜ, NHIỆT ĐỘ, ĐỘ ẨM KHÔNG KHÍ, ĐỘ ẨM ĐẤT, ĐỘ MẶN, PH, ÁNH SÁNG` (8 cột cũ, giữ nguyên) + `BƠM, ĐÈN, QUẠT, PHUN SƯƠNG, MÀN CHE MỞ, CHẾ ĐỘ AUTO` (mới, 1 = bật). Dòng mới nhất nằm trên cùng.
