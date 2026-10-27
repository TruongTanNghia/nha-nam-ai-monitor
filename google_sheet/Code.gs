/**
 * Nhận dữ liệu ESP32 gửi lên mỗi phút và ghi vào Google Sheet "NHÀ NẤM" (dòng mới nhất ở trên cùng).
 *
 * Cài đặt (xem docs/GOOGLE_SHEET.md):
 *   Mở Google Sheet → Tiện ích mở rộng → Apps Script → dán toàn bộ file này → Lưu
 *   → Triển khai → Tùy chọn triển khai mới → Loại: Ứng dụng web
 *     Thực thi dưới dạng: Tôi · Người có quyền truy cập: Bất kỳ ai → Triển khai
 *   → chép URL kết thúc bằng /exec vào GOOGLE_SCRIPT_URL trong secrets.h rồi nạp lại ESP32.
 *
 * ESP32 gọi:  <URL>?sts=write&t=..&h=..&amdat=..&doman=..&ph=..&anhsang=..&bom=..&den=..&quat=..&suong=..&mo=..&mode=..
 */
const SHEET_NAME = "";  // để trống = trang tính đầu tiên
const TZ = "Asia/Ho_Chi_Minh";
const HEADERS = ["NGÀY", "GIỜ", "NHIỆT ĐỘ\n(°C)", "ĐỘ ẨM KHÔNG KHÍ\n(%)", "ĐỘ ẨM ĐẤT\n(%)", "ĐỘ MẶN\n(PPM)", "PH",
  "ÁNH SÁNG\n(LUX)", "BƠM", "ĐÈN", "QUẠT", "PHUN SƯƠNG", "MÀN CHE MỞ", "CHẾ ĐỘ AUTO"];
const PARAMS = ["t", "h", "amdat", "doman", "ph", "anhsang", "bom", "den", "quat", "suong", "mo", "mode"];

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.sts !== "write") return ContentService.createTextOutput("Nha Nam: Apps Script dang chay");
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = SHEET_NAME ? ss.getSheetByName(SHEET_NAME) : ss.getSheets()[0];
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    if (sh.getRange(1, HEADERS.length).getValue() !== HEADERS[HEADERS.length - 1]) {
      sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);  // thêm tiêu đề cột thiết bị (giữ nguyên 8 cột cũ)
    }
    const now = new Date();
    const num = (k) => (p[k] === undefined || p[k] === "" ? "" : Number(p[k]));
    const row = [Utilities.formatDate(now, TZ, "dd/MM/yyyy"), Utilities.formatDate(now, TZ, "HH:mm:ss")]
      .concat(PARAMS.map(num));
    sh.insertRowAfter(1);
    const target = sh.getRange(2, 1, 1, row.length);
    sh.getRange(2, 1, 1, 2).setNumberFormat("@");  // ngày/giờ dạng chữ: không bị Sheets đổi thành tháng/ngày
    target.setValues([row]);
  } finally {
    lock.releaseLock();
  }
  return ContentService.createTextOutput("OK");
}
