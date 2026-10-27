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
 * Máy chủ AI (tự lấy link trong secrets.h):
 *   <URL>?sts=read&rows=N      -> N dòng mới nhất dạng CSV (lấp dữ liệu lúc máy tính tắt; Sheet không cần chia sẻ)
 *   POST JSON sts=forecast     -> bảng "dự báo & thực tế" vào tab DỰ BÁO
 */
const SHEET_NAME = "";  // để trống = trang tính đầu tiên
const TZ = "Asia/Ho_Chi_Minh";
const HEADERS = ["NGÀY", "GIỜ", "NHIỆT ĐỘ\n(°C)", "ĐỘ ẨM KHÔNG KHÍ\n(%)", "ĐỘ ẨM ĐẤT\n(%)", "ĐỘ MẶN\n(PPM)", "PH",
  "ÁNH SÁNG\n(LUX)", "BƠM", "ĐÈN", "QUẠT", "PHUN SƯƠNG", "MÀN CHE MỞ", "CHẾ ĐỘ AUTO"];
const PARAMS = ["t", "h", "amdat", "doman", "ph", "anhsang", "bom", "den", "quat", "suong", "mo", "mode"];

function dataSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return SHEET_NAME ? ss.getSheetByName(SHEET_NAME) : ss.getSheets()[0];
}

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.sts === "read") return readCsv(Number(p.rows) || 3000);
  if (p.sts !== "write") return ContentService.createTextOutput("Nha Nam: Apps Script dang chay");
  const sh = dataSheet();
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

/** `rows` dòng mới nhất (cùng tiêu đề) dạng CSV, giống "Tải xuống → CSV" của Google Sheet. */
function readCsv(rows) {
  const sh = dataSheet();
  const n = Math.min(sh.getLastRow(), rows + 1);
  if (n < 1) return ContentService.createTextOutput("").setMimeType(ContentService.MimeType.CSV);
  const cell = (v) => (/[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v);
  const text = sh.getRange(1, 1, n, HEADERS.length).getDisplayValues().map((r) => r.map(cell).join(",")).join("\n");
  return ContentService.createTextOutput(text).setMimeType(ContentService.MimeType.CSV);
}

/** Máy chủ AI ghi các lần dự báo đã có kết quả thực tế vào tab DỰ BÁO (tự tạo, nằm sau trang dữ liệu gốc). */
function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return ContentService.createTextOutput("LOI: noi dung khong phai JSON");
  }
  if (body.sts !== "forecast" || !body.rows || !body.rows.length) return ContentService.createTextOutput("OK 0");
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = ss.getSheetByName(body.tab) || ss.insertSheet(body.tab, ss.getNumSheets());
    if (sh.getLastRow() === 0) {
      sh.getRange(1, 1, 1, body.headers.length).setValues([body.headers]).setFontWeight("bold").setWrap(true);
      sh.setFrozenRows(1);
    }
    const rows = body.rows.map((r) => r.map((v) => (v === null || v === undefined ? "" : v)));
    const start = sh.getLastRow() + 1;
    sh.getRange(start, 1, rows.length, 3).setNumberFormat("@");  // ngày/giờ dạng chữ
    sh.getRange(start, 1, rows.length, rows[0].length).setValues(rows);
  } finally {
    lock.releaseLock();
  }
  return ContentService.createTextOutput("OK " + body.rows.length);
}
