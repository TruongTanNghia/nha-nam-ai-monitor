# Phương pháp dự báo vi khí hậu nhà nấm (Random Forest + thời tiết)

Mã nguồn: [server/rf_model.py](../server/rf_model.py). Kết quả kiểm chứng thực tế được hiển thị trực tiếp trên web (tab **Phân tích AI → Mô hình Random Forest**) và được tính lại mỗi giờ.

> Dự báo **chỉ để tham khảo**. Thiết bị (quạt, phun sương, bơm, đèn, màn che) luôn bật/tắt theo **ngưỡng** cài đặt trong firmware ESP32.

## 1. Bài toán
Dự báo sau **1, 2 và 3 giờ** cho 5 chỉ số trong nhà nấm: nhiệt độ, độ ẩm không khí, độ ẩm giá thể (đất), pH và độ mặn của nước cấp. Mục đích là chuẩn bị trước, ví dụ kiểm tra nước cho béc phun khi độ ẩm sắp xuống dưới ngưỡng.

## 2. Dữ liệu
| Nguồn | Nội dung | Tần suất |
|---|---|---|
| Cảm biến ESP32 (qua Firebase) | nhiệt độ, độ ẩm KK, ánh sáng, độ ẩm đất, pH, độ mặn, trạng thái 5 thiết bị | 5 giây/lần, lưu SQLite |
| Open-Meteo (miễn phí, không cần key) | nhiệt độ, độ ẩm, mây, bức xạ mặt trời, lượng mưa, gió ngoài trời | theo giờ: 31 ngày qua + 2 ngày tới |

**Mẫu dữ liệu:** mỗi giờ H là một mẫu. *Trạng thái* của giờ H là trung bình **15 phút cuối** giờ đó. Không lấy trung bình cả giờ vì khi một chỉ số vừa đổi mạnh, trung bình cả giờ sẽ không phản ánh đúng trạng thái lúc cuối giờ.

## 3. Làm sạch dữ liệu (quy tắc cố định, không chỉnh tay)
Một giờ bị **loại** nếu, ở giờ đó hoặc ở 3 giờ sau:
1. Có chỉ số nằm ngoài khoảng vật lý của cảm biến (ví dụ độ ẩm ngoài 0–100%, pH ngoài 0–14).
2. Độ ẩm đất đúng **0%**: cảm biến chưa cắm vào đất hoặc đứt dây. Firmware cũng coi đây là lỗi.
3. Thay đổi vượt mức vật lý hợp lý trong 1 giờ (nhiệt độ 6°C, độ ẩm 25%, độ ẩm đất 25%, pH 1, độ mặn 300 ppm). Thường đây là do rút/cắm lại cảm biến, không phải diễn biến thật.

Số giờ bị loại được hiển thị trên web.

## 4. Đặc trưng đầu vào (31 biến)
- Trạng thái trong nhà giờ H: 6 chỉ số.
- Tỉ lệ thời gian bật của 5 thiết bị trong 15 phút cuối giờ H.
- Thời tiết giờ H: 6 biến.
- Thời tiết giờ H+1, H+2, H+3 (nhiệt độ, độ ẩm, bức xạ, mưa): 12 biến. Khi học dùng số đo thật; khi dự báo dùng dự báo của Open-Meteo.
- Giờ trong ngày, mã hóa sin/cos (để 23h và 0h gần nhau).

## 5. Mô hình
- **Một** `RandomForestRegressor` (Breiman, 2001) nhiều đầu ra: 200 cây, độ sâu tối đa 8, tối thiểu 3 mẫu/lá.
- **Đầu ra (15):** *mức thay đổi* của 5 chỉ số sau 1, 2, 3 giờ, so với hiện tại. Dự báo = số đo hiện tại + mức thay đổi.
  - Học *mức thay đổi* thay vì giá trị tuyệt đối: khi dữ liệu ít, mô hình không bị kéo về giá trị trung bình lịch sử.
  - Dự báo **trực tiếp** từng mốc giờ, không lặp từng bước: sai số không cộng dồn.
- Học lại mỗi giờ trên toàn bộ dữ liệu 30 ngày gần nhất.

## 6. Kiểm chứng
- **Walk-forward (TimeSeriesSplit, tối đa 5 lần):** mỗi lần học trên các giờ trước, thử trên các giờ **sau đó** mà mô hình chưa thấy. Không xáo trộn dữ liệu theo thời gian, vì xáo trộn sẽ cho kết quả đẹp giả.
- **Khoảng trống 3 giờ** giữa tập học và tập thử: mục tiêu 3 giờ của mẫu học không chồng lên tập thử, tránh rò rỉ dữ liệu.
- **Mốc chuẩn "giữ nguyên" (persistence):** dự báo giá trị sau h giờ = giá trị hiện tại. Đây là mốc chuẩn tiêu chuẩn trong dự báo chuỗi thời gian (Hyndman & Athanasopoulos). Mô hình chỉ có ý nghĩa khi tốt hơn mốc này.
- **Chỉ số đánh giá:**
  - MAE: sai số tuyệt đối trung bình, cùng đơn vị với chỉ số.
  - Skill (%) = (1 − MAE<sub>mô hình</sub> / MAE<sub>giữ nguyên</sub>) × 100. Dương nghĩa là mô hình tốt hơn.
- **Quy tắc hiển thị:** với mỗi cặp (chỉ số, mốc giờ), nếu skill ≤ 0 thì web và Zalo hiển thị giá trị *giữ nguyên*, đánh dấu “=”. Hệ thống không đưa ra con số mà nó không chứng minh được là tốt hơn cách đơn giản nhất.
- **Chưa kiểm chứng thì chưa dự báo:** cần tối thiểu 36 mẫu giờ. Trước đó web ghi “Đang thu thập dữ liệu để kiểm chứng”.

## 7. Khoảng sai số
Mỗi ô dự báo kèm **±**: phân vị 80% của sai số tuyệt đối đo được khi kiểm chứng. Nghĩa là khoảng 80% lần dự báo trước đây có sai số không vượt quá mức này.

## 8. Đọc bảng trên web
- **Bảng dự báo** (trang Tổng quan): cột “Bây giờ” là số đo mới nhất; các cột +1/+2/+3 giờ là dự báo kèm ±. Ô có “=” là giữ nguyên. Ô màu cam là ngoài ngưỡng.
- **Bảng kiểm chứng** (tab Phân tích AI): MAE của mô hình cho từng chỉ số × mốc giờ, kèm % tốt hơn/kém hơn mốc “giữ nguyên”.
- **Yếu tố ảnh hưởng:** độ quan trọng của đặc trưng (giảm độ hỗn tạp trung bình, MDI), gộp theo nhóm.

## 9. Hạn chế
- Dữ liệu còn ít (vài ngày đến vài tuần). Kết quả kiểm chứng sẽ ổn định hơn khi thu thập lâu hơn.
- Thiết bị bật/tắt theo ngưỡng làm thay đổi chính các chỉ số đang được dự báo (vòng phản hồi). Mô hình học quan hệ này từ biến “thiết bị đang chạy” nhưng không mô phỏng vật lý.
- Khi học dùng thời tiết thực đo, khi dự báo dùng thời tiết dự báo, nên sai số thực tế có thể lớn hơn một chút so với kiểm chứng.
- Vị trí thời tiết mặc định đoán theo IP mạng. Nên đặt `WEATHER_LAT`/`WEATHER_LON` đúng tọa độ nhà nấm.
- pH và độ mặn thường thay đổi chậm và ngẫu nhiên, nên mốc “giữ nguyên” thường khó bị vượt. Đây là kết quả đúng, không phải lỗi.

## Tài liệu tham khảo
- L. Breiman (2001). *Random Forests*. Machine Learning 45(1), 5–32.
- R. J. Hyndman, G. Athanasopoulos (2021). *Forecasting: Principles and Practice*, 3rd ed., OTexts. Các mục 5.2 (phương pháp đơn giản làm mốc chuẩn) và 5.10 (kiểm chứng chéo theo chuỗi thời gian).
- scikit-learn: `RandomForestRegressor`, `TimeSeriesSplit` (tham số `gap`).
- Open-Meteo Weather Forecast API: https://open-meteo.com
