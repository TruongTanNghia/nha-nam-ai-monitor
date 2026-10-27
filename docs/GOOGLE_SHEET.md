# Lưu dữ liệu 24/7 vào Google Sheet

## Cài đặt: 1 lần, khoảng 1 phút, KHÔNG phải nạp lại ESP32

1. Mở Google Sheet **NHÀ NẤM** → **Tiện ích mở rộng → Apps Script**.
2. Xóa hết code cũ, dán toàn bộ file [`google_sheet/Code.gs`](../google_sheet/Code.gs) → bấm **Lưu** (biểu tượng đĩa mềm).
3. Ở thanh trên cùng, ô chọn hàm: chọn **`CAI_DAT`** → bấm **Chạy**.
4. Google hỏi quyền: chọn tài khoản → **Nâng cao → Đi tới… (không an toàn) → Cho phép**. Đây là script của chính mình nên an toàn.

Xong. Nhật ký thực thi hiện chữ **XONG**. Từ giờ **Google tự đọc Firebase 1 phút/lần và ghi 1 dòng** lên trên cùng Sheet, kể cả khi máy tính tắt. Lúc mạch ESP32 tắt (nhịp tim `ESP_TS` đứng yên quá 3 phút) thì không ghi, để không có số cũ lặp lại. Chạy lại `CAI_DAT` bao nhiêu lần cũng không bị ghi trùng.

> Không cần link `/exec`, không cần sửa `secrets.h`, không cần nạp lại ESP32. Nếu ESP32 vẫn tự gửi lên link Apps Script (cách cũ) thì Google tự ngừng ghi, để không trùng dòng.

## Tùy chọn: máy chủ AI dùng chung Sheet

Không bắt buộc. File Excel tải trên web (bên dưới) đã có đủ dữ liệu gốc và bảng dự báo & thực tế. Nếu muốn thêm 2 việc sau:
- máy chủ lấy dữ liệu Sheet về, lấp những lúc máy tính tắt (lúc khởi động, rồi 30 phút/lần) để Random Forest học đủ ngày đêm;
- máy chủ ghi bảng dự báo & thực tế vào tab **DỰ BÁO** trên Sheet;

thì làm như sau:
1. Apps Script → **Triển khai → Tùy chọn triển khai mới**, bánh răng chọn **Ứng dụng web**. Đặt Thực thi dưới dạng: **Tôi**, Người có quyền truy cập: **Bất kỳ ai** → **Triển khai** → chép link kết thúc bằng `/exec`.
2. Thêm dòng `GOOGLE_SCRIPT_URL=<link /exec>` vào `server/.env`, rồi chạy lại `CHAY_NHA_NAM.bat`. Máy chủ cũng tự tìm link này trong `secrets.h` (thư mục dự án, thư mục bên cạnh, hoặc `D:\KHOA HOC KY THUAT\CLB TIN HOC\TIN HOC TRE 2026\nhanam\`).

Cửa sổ đen lúc chạy có dòng `Google Sheet: link Apps Script trong …`. Sheet **không cần chia sẻ** công khai. Trạng thái xem ở web: tab **Phân tích AI → Mô hình Random Forest → Google Sheet**.

> Sau này sửa code Apps Script: **Triển khai → Quản lý các bản triển khai → bút chì → Phiên bản: Mới**. Làm vậy link được giữ nguyên.
> Cách cũ vẫn dùng được: chia sẻ Sheet “Bất kỳ ai có đường liên kết → Người xem” và đặt `GOOGLE_SHEET_ID` (phần giữa `/d/` và `/edit` của link Sheet).

### Bảng dự báo & thực tế (tab DỰ BÁO)

Máy chủ ghi lại **mọi lần Random Forest dự báo** (15 phút/lần, cho 1, 2, 3 giờ tới). Tới giờ được dự báo, máy chủ lấy **số đo thật** (trung bình ±7,5 phút) để so. Sau đó máy chủ ghi dòng đó lên tab **DỰ BÁO**, tab này nằm sau trang dữ liệu gốc. Dòng đầu tiên có sau khoảng 2–4 giờ máy chủ chạy liên tục.

Mỗi dòng của tab DỰ BÁO gồm: lúc dự báo, dự báo cho lúc nào, sau mấy giờ. Với mỗi chỉ số (nhiệt độ, độ ẩm không khí, độ ẩm đất, ánh sáng, pH, độ mặn) có 3 cột: **giá trị lúc dự báo**, **RF dự báo**, **thực tế**.

## File CSV tự lưu trên máy (không cần làm gì)

Máy chủ chạy là tự ghi vào thư mục **`DU_LIEU_CSV`** (nằm trong thư mục dự án, cạnh `CHAY_NHA_NAM.bat`), 5 phút/lần:

| File | Nội dung |
|---|---|
| `du_lieu_goc_2026-10.csv` | mỗi tháng 1 file, mỗi phút 1 dòng: 6 cảm biến + thiết bị + chế độ AUTO + số lần đo (giống trang “Dữ liệu gốc” của file Excel) |
| `du_bao_va_thuc_te.csv` | mọi lần Random Forest dự báo + số đo thật lúc tới giờ (giống tab DỰ BÁO) |

Mở thẳng bằng Excel hoặc kéo vào Google Drive. Nếu đang mở file trong Excel thì máy chủ chờ, đóng Excel là tự ghi tiếp. Muốn lưu chỗ khác thì đặt `CSV_DIR=D:\...` trong `server/.env`.

> Excel hiện mọi thứ dồn vào 1 cột (máy để định dạng số kiểu Việt Nam): vào **Dữ liệu → Từ Văn bản/CSV** → chọn file → Dấu phân cách: **Dấu phẩy**.

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
