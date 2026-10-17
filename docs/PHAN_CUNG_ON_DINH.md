# Làm phần cứng Nhà Nấm chạy ổn định

Dấu hiệu nguồn điện không ổn định: ESP32 tự khởi động lại, mất kết nối từng đợt, LCD nháy/hiện ký tự lạ, cảm biến đọc ra số vô lý (nhiệt độ 0°C, độ ẩm 0%), pH/độ ẩm đất nhảy liên tục, rơ-le đóng ngắt khi không có lệnh.

Web (tab **Cảnh báo → Nguồn & WiFi ESP32**) và Zalo cho biết ESP32 khởi động lại mấy lần, **vì sao** (sụt áp, mất điện, treo) và sóng WiFi mạnh hay yếu — cần nạp firmware `aduno.ino` mới.

## 1. Nguồn — nguyên nhân số 1

| Nên | Không nên |
|---|---|
| **Nguồn riêng cho ESP32**: adapter 5V ≥ 2A chất lượng tốt (hoặc mạch hạ áp riêng) | Lấy chung nguồn với bơm, quạt, phun sương, động cơ màn che |
| Rơ-le, bơm, quạt dùng **nguồn khác** (12V/24V/220V tùy thiết bị) | Cấp 5V cho cả ESP32 + 4–6 cuộn rơ-le từ cổng USB máy tính / sạc điện thoại yếu |
| Mô-đun rơ-le có **opto cách ly**: tháo jumper **JD-VCC**, cấp JD-VCC bằng nguồn rơ-le riêng, VCC lấy 3.3V/5V từ ESP32 | Để jumper JD-VCC nối VCC (cuộn rơ-le ăn dòng từ nguồn ESP32) |
| Tụ **470–1000 µF** + tụ **100 nF** sát chân 5V/GND của ESP32 | Dây nguồn dài, mảnh, mối nối lỏng / cắm breadboard |
| Dây nguồn ngắn, đủ to; GND nối chung **một điểm** | Nối GND kiểu "móc nối tiếp" qua nhiều mô-đun |

## 2. Tải cảm (bơm, quạt, phun sương, động cơ)

- Động cơ/bơm **DC**: gắn **diode ngược (1N4007/1N5819)** song song với tải.
- Tải **220V** (bơm, quạt): dùng mạch **RC snubber** hoặc **varistor (MOV)** trên tiếp điểm rơ-le; tốt hơn nữa dùng **SSR** cho tải bật/tắt nhiều.
- Firmware mới đã **bật lệch nhau 0.8 giây** giữa các thiết bị và mỗi rơ-le đổi trạng thái tối đa **1 lần / 15 giây** để giảm sụt áp do dòng khởi động — phần cứng vẫn nên có bảo vệ như trên.

## 3. Cảm biến

- **pH và TDS (độ mặn) trong cùng một nước sẽ nhiễu nhau** (đầu TDS phát dòng xoay chiều). Đặt 2 đầu dò cách xa nhau, hoặc dùng mô-đun cách ly cho pH.
- **DHT11**: điện trở kéo lên 10 kΩ (chân DATA lên 3.3V), dây ≤ 2 m; DHT11 kém chính xác — nên đổi **DHT22/SHT31** nếu được.
- Dây cảm biến analog (pH, độ ẩm đất, TDS) **đi riêng, xa dây 220V/dây động cơ**; dài thì dùng dây có chống nhiễu.
- Cảm biến độ ẩm đất báo **0%** liên tục: kiểm tra đã cắm sâu vào đất chưa, dây có đứt không, rồi hiệu chuẩn lại (công thức `map(rawSoil, 4096, 0, 0, 100) * 5`).

## 4. Chân ESP32

- **GPIO 2 (máy bơm)** là chân "strapping" lúc khởi động: một số mô-đun rơ-le kéo chân này làm ESP32 **không khởi động / không nạp được code**. Nếu gặp, chuyển rơ-le bơm sang **GPIO 25 hoặc 26** và sửa `#define BOM_PIN`.
- Cảm biến analog đang dùng GPIO 32/34/35 (ADC1) — đúng, vì ADC2 không dùng được khi bật WiFi.

## 5. WiFi

- Sóng yếu hơn **−80 dBm** là dễ rớt. Dời modem / gắn bộ kích sóng gần nhà nấm.
- ESP32 chỉ bắt **2.4 GHz**.
- Firmware mới **mất WiFi vẫn chạy**: vẫn đọc cảm biến, vẫn tự điều khiển AUTO theo ngưỡng đã lưu, tự kết nối lại mỗi 15 giây (bản cũ đứng toàn bộ khi mất WiFi).

## 6. Kiểm tra nhanh sau khi sửa

1. Nạp firmware mới, mở **Serial Monitor 115200**: dòng `Ly do khoi dong:` cho biết lần trước vì sao khởi động lại (`sut_ap` = sụt áp).
2. Cho bơm + quạt + phun sương cùng chạy (MANUAL): ESP32 **không được** khởi động lại, LCD không nháy.
3. Theo dõi web 1 ngày: **Khởi động lại 24h** nên là 0–1 lần.
