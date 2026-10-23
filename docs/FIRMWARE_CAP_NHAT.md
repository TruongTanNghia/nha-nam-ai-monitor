# Cập nhật firmware ESP32 cho khớp web / máy chủ AI

> **Cách đơn giản nhất:** chép đè toàn bộ `aduno.ino` mới nhất vào file `.ino` đang dùng (giữ `secrets.h` cùng thư mục). Bản mới đã gồm mọi mục dưới đây **và** các sửa cho nguồn/WiFi chập chờn: mất WiFi vẫn chạy AUTO, rơ-le được cài ngay khi khởi động, vùng trễ + chống rơ-le đóng ngắt liên tục, bật thiết bị lệch nhau để tránh sụt áp, đọc pH/độ ẩm đất nhiều mẫu, báo lý do khởi động lại và sóng WiFi. Phần cứng: xem [PHAN_CUNG_ON_DINH.md](PHAN_CUNG_ON_DINH.md).

`aduno.ino` trong repo đã có sẵn mọi thay đổi dưới đây. Nếu bo đang chạy file khác (vd `Codenhanam2026_amthanh.ino`), sửa 3 chỗ sau rồi nạp lại.

## 1. Không ghi đè ngưỡng và chế độ (sửa lỗi "bấm Lưu cài đặt là mất")

Firmware cũ ghi `SET_*` và `MODE` lên Firebase mỗi 0.7 giây bằng giá trị trong bộ nhớ của nó. Nếu bấm Lưu trên web đúng lúc ESP32 chưa kịp đọc giá trị mới, ESP32 ghi đè giá trị cũ lên. Trong hàm gửi Firebase (`sendAllToFirebase`), **xóa** các dòng:

```cpp
doc["MODE"]       = MODE;
doc["SET_AMDAT"]  = SET_AMDAT;
doc["SET_AS"]     = SET_AS;
doc["SET_DOMAN"]  = SET_DOMAN;
doc["SET_H"]      = SET_H;
doc["SET_PHHIGH"] = SET_PHHIGH;
doc["SET_PHLOW"]  = SET_PHLOW;
doc["SET_T"]      = SET_T;
```

ESP32 vẫn **đọc** các giá trị này mỗi giây như cũ, chỉ không ghi nữa. (Web đã tự kiểm tra và ghi lại khi bị đè, nhưng sửa firmware mới hết hẳn.)

## 2. Chuyển AUTO → MANUAL không tự bật lại thiết bị

Đổi hàm gửi thành có tham số, và khi chuyển sang MANUAL thì gửi kèm trạng thái thiết bị đã tắt:

```cpp
bool sendToFirebase(bool includeDevices) {
  ...
  if (MODE == 1 || includeDevices) {   // thay cho if (MODE == 1)
    doc["BOM"] = BOM; ...
  }
  ...
}
bool sendAllToFirebase() { return sendToFirebase(false); }
```

Trong `controlDevices()`, đoạn `if (lastMODE == 1 && MODE == 0) { ... }` đổi `sendAllToFirebase();` thành `sendToFirebase(true);`.

## 3. Nhịp tim (web biết ESP32 còn kết nối)

Trong hàm gửi Firebase, ngay sau `doc["t"] = t;`:

```cpp
JsonObject espTs = doc.createNestedObject("ESP_TS");
espTs[".sv"] = "timestamp";
```

> **Đã bỏ “bật sớm theo dự báo” (PRE):** thiết bị chỉ bật/tắt theo ngưỡng. Nếu firmware trên bo còn đoạn đọc `doc["PRE"]`, biến `PRE_*`/`preSeq` và điều kiện `(pre && PRE_...)` trong `autoMode()`, hãy xóa (hoặc chép đè `aduno.ino` mới). Máy chủ AI cũng tự xóa khóa `PRE`, `AI_PREDICT`, `PRE_ACK` trên Firebase khi khởi động nên firmware cũ không còn bật sớm nữa.
