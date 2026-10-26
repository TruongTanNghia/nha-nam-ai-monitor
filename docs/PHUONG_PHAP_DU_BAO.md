# Phương pháp dự báo vi khí hậu nhà nấm (Random Forest + thời tiết)

Mã nguồn: [server/rf_model.py](../server/rf_model.py). Kết quả kiểm chứng thực tế được hiển thị trực tiếp trên web (tab **Phân tích AI → Mô hình Random Forest**) và được tính lại mỗi giờ.

> Dự báo **chỉ để tham khảo**. Thiết bị (quạt, phun sương, bơm, đèn, màn che) luôn bật/tắt theo **ngưỡng** cài đặt trong firmware ESP32.

## 1. Bài toán
Dự báo sau **1, 2 và 3 giờ** cho 6 chỉ số trong nhà nấm: nhiệt độ, độ ẩm không khí, độ ẩm giá thể (đất), ánh sáng, pH và độ mặn của nước cấp. Mục đích là chuẩn bị trước, ví dụ kiểm tra nước cho béc phun khi độ ẩm sắp xuống dưới ngưỡng.

## 2. Dữ liệu
| Nguồn | Nội dung | Tần suất |
|---|---|---|
| Cảm biến ESP32 (qua Firebase) | nhiệt độ, độ ẩm KK, ánh sáng, độ ẩm đất, pH, độ mặn, trạng thái 5 thiết bị | 5 giây/lần, lưu SQLite |
| Open-Meteo (miễn phí, không cần key) | nhiệt độ, độ ẩm, mây, bức xạ mặt trời, lượng mưa, gió ngoài trời | theo giờ: 31 ngày qua + 2 ngày tới |

**Trạng thái:** cứ 15 phút lấy trung bình các lần đo trong 15 phút đó (lọc nhiễu của cảm biến, vẫn bám kịp khi chỉ số đổi nhanh).

**Mẫu dữ liệu:** một mẫu = (trạng thái lúc τ, xu hướng 1 giờ qua, mốc dự báo h ∈ {1, 2, 3} giờ) → mức thay đổi của 6 chỉ số từ τ tới τ + h. Mỗi trạng thái cho tối đa 3 mẫu; chỉ cần có số liệu lúc τ + h là dùng được, không bắt buộc đủ cả 3 giờ liên tục.

## 3. Làm sạch dữ liệu (quy tắc cố định, không chỉnh tay)
Một trạng thái 15 phút bị **loại** nếu:
1. Có ít hơn 10 lần đo, hoặc có chỉ số nằm ngoài khoảng vật lý của cảm biến (ví dụ độ ẩm ngoài 0–100%, pH ngoài 0–14).
2. Riêng độ ẩm đất đúng **0%** (cảm biến chưa cắm vào đất hoặc đứt dây): không loại cả mẫu, vì các cảm biến khác vẫn đúng. Lúc rút/cắm lại (0% ↔ 100%) bị loại theo quy tắc nhảy số bên dưới, và khi đất đang báo 0% thì không dự báo độ ẩm đất.

Một mẫu bị loại nếu thay đổi vượt mức vật lý hợp lý (mỗi giờ: nhiệt độ 6°C, độ ẩm 25%, độ ẩm đất 25%, pH 1, độ mặn 300 ppm; ánh sáng không lọc vì đổi rất nhanh theo mây, ngày/đêm, đèn). Thường đây là do rút/cắm lại cảm biến, không phải diễn biến thật.

Số đoạn bị loại được hiển thị trên web.

## 4. Đặc trưng đầu vào (28 biến)
- Trạng thái trong nhà lúc τ: 6 chỉ số.
- Xu hướng: mức thay đổi trong 1 giờ qua của nhiệt độ, độ ẩm KK, độ ẩm đất, ánh sáng (biến trễ – lag – chuẩn trong dự báo chuỗi thời gian; cho mô hình biết đang nóng lên hay nguội đi). Cần máy chủ chạy ít nhất 1 giờ mới dự báo.
- Tỉ lệ thời gian bật của 5 thiết bị trong 15 phút đó.
- Thời tiết lúc τ: 6 biến.
- Thời tiết lúc τ + h (nhiệt độ, độ ẩm, bức xạ, mưa): 4 biến. Khi học dùng số đo thật; khi dự báo dùng dự báo của Open-Meteo.
- Giờ trong ngày, mã hóa sin/cos (để 23h và 0h gần nhau).
- Mốc dự báo h (1, 2 hoặc 3 giờ).

## 5. Mô hình
- **Một** `RandomForestRegressor` (Breiman, 2001) nhiều đầu ra: 200 cây, độ sâu tối đa 10, tối thiểu 3 mẫu/lá.
- **Đầu ra (6):** *mức thay đổi* của 6 chỉ số sau h giờ so với hiện tại; h là biến đầu vào nên một mô hình dự báo cả 3 mốc. Dự báo = số đo hiện tại + mức thay đổi.
  - Học *mức thay đổi* thay vì giá trị tuyệt đối: khi dữ liệu ít, mô hình không bị kéo về giá trị trung bình lịch sử.
  - Dự báo **trực tiếp** từng mốc giờ, không lặp từng bước: sai số không cộng dồn.
- Học lại mỗi 15 phút trên toàn bộ dữ liệu 30 ngày gần nhất; dự báo tính lại mỗi 2 phút.

## 6. Kiểm chứng
- **Walk-forward theo thời gian (tối đa 4 lần):** chia khoảng thời gian có dữ liệu thành các đoạn liên tiếp. Mỗi lần thử trên một đoạn **sau**, mô hình chưa thấy đoạn đó. Không xáo trộn dữ liệu theo thời gian, vì xáo trộn sẽ cho kết quả đẹp giả.
- **Không rò rỉ dữ liệu:** tập học của mỗi lần chỉ gồm các mẫu mà *kết quả* (lúc τ + h) đã có **trước** khi đoạn thử bắt đầu, giống hệt tình huống dự báo thật.
- **Mốc chuẩn "giữ nguyên" (persistence):** dự báo giá trị sau h giờ = giá trị hiện tại. Đây là mốc chuẩn tiêu chuẩn trong dự báo chuỗi thời gian (Hyndman & Athanasopoulos). Mô hình chỉ có ý nghĩa khi tốt hơn mốc này.
- **Chỉ số đánh giá:**
  - MAE: sai số tuyệt đối trung bình, cùng đơn vị với chỉ số.
  - Skill (%) = (1 − MAE<sub>mô hình</sub> / MAE<sub>giữ nguyên</sub>) × 100. Dương nghĩa là mô hình tốt hơn.
- **Quy tắc hiển thị:** với mỗi cặp (chỉ số, mốc giờ), nếu skill ≤ 0 thì dự báo vẫn hiển thị (một con số) nhưng **không dùng để đưa gợi ý**; bảng kiểm chứng ở tab Phân tích AI cho biết ô nào đã được chứng minh tốt hơn cách đơn giản nhất.
- **Chưa kiểm chứng thì chưa dự báo:** cần tối thiểu 60 mẫu (khoảng 7–10 giờ máy chủ chạy liên tục) và ít nhất 2 lần kiểm chứng. Trước đó web ghi “Đang thu thập dữ liệu để kiểm chứng” kèm số giờ còn thiếu. Trong lúc đó web vẫn báo các chỉ số **đang** ngoài ngưỡng.

## 7. Khoảng sai số
Mỗi ô dự báo kèm **±**: phân vị 80% của sai số tuyệt đối đo được khi kiểm chứng. Nghĩa là khoảng 80% lần dự báo trước đây có sai số không vượt quá mức này.

## 8. Đọc bảng trên web
- **Bảng dự báo** (trang Tổng quan): cột “Bây giờ” là số đo mới nhất; các cột +1/+2/+3 giờ là dự báo (rê chuột vào ô để xem khoảng sai số 80%). Ô màu cam là ngoài ngưỡng.
- **Bảng kiểm chứng** (tab Phân tích AI): MAE của mô hình cho từng chỉ số × mốc giờ, kèm % tốt hơn/kém hơn mốc “giữ nguyên”.
- **Yếu tố ảnh hưởng:** độ quan trọng của đặc trưng (giảm độ hỗn tạp trung bình, MDI), gộp theo nhóm.

## 9. Hạn chế
- Dữ liệu còn ít (vài ngày đến vài tuần). Kết quả kiểm chứng sẽ ổn định hơn khi thu thập lâu hơn.
- Thiết bị bật/tắt theo ngưỡng làm thay đổi chính các chỉ số đang được dự báo (vòng phản hồi). Mô hình học quan hệ này từ biến “thiết bị đang chạy” nhưng không mô phỏng vật lý.
- Khi học dùng thời tiết thực đo, khi dự báo dùng thời tiết dự báo, nên sai số thực tế có thể lớn hơn một chút so với kiểm chứng.
- Thời tiết lấy theo tọa độ trung tâm TP. Vĩnh Long; có thể đặt `WEATHER_LAT`/`WEATHER_LON` đúng tọa độ nhà nấm.
- pH và độ mặn thường thay đổi chậm và ngẫu nhiên, nên mốc “giữ nguyên” thường khó bị vượt. Đây là kết quả đúng, không phải lỗi.

## Tài liệu tham khảo
- L. Breiman (2001). *Random Forests*. Machine Learning 45(1), 5–32.
- R. J. Hyndman, G. Athanasopoulos (2021). *Forecasting: Principles and Practice*, 3rd ed., OTexts. Các mục 5.2 (phương pháp đơn giản làm mốc chuẩn) và 5.10 (kiểm chứng chéo theo chuỗi thời gian).
- scikit-learn: `RandomForestRegressor`.
- Open-Meteo Weather Forecast API: https://open-meteo.com
