/** @OnlyCurrentDoc */
/**
 * Ghi dữ liệu Nhà Nấm vào Google Sheet "NHÀ NẤM" mỗi phút (dòng mới nhất ở trên cùng), kể cả khi máy tính tắt.
 *
 * CÀI ĐẶT (1 lần, khoảng 1 phút, KHÔNG phải nạp lại ESP32):
 *   Mở Google Sheet → Tiện ích mở rộng → Apps Script → xóa code cũ, dán toàn bộ file này → Lưu
 *   → ở thanh trên chọn hàm CAI_DAT → bấm Chạy → cấp quyền (Nâng cao → Đi tới… → Cho phép).
 *   Xong: Google tự đọc Firebase 1 phút/lần và ghi 1 dòng (mạch tắt thì không ghi số cũ).
 *
 * Tùy chọn (máy chủ AI lấp dữ liệu lúc máy tính tắt + ghi tab DỰ BÁO): Triển khai → Tùy chọn triển khai mới
 *   → Ứng dụng web (Thực thi: Tôi · Truy cập: Bất kỳ ai) → chép link /exec vào GOOGLE_SCRIPT_URL trong server/.env.
 *   <URL>?sts=read&rows=N      -> N dòng mới nhất dạng CSV
 *   POST JSON sts=forecast     -> bảng "dự báo & thực tế" vào tab DỰ BÁO
 *   <URL>?sts=write&t=..&h=..  -> cách cũ: ESP32 tự gửi (khi đó Google tự ngừng ghi để không trùng dòng)
 */
const SHEET_NAME = "";  // để trống = trang tính đầu tiên
const TZ = "Asia/Ho_Chi_Minh";
const FIREBASE_URL = "https://nha-kinh-84aa2-default-rtdb.asia-southeast1.firebasedatabase.app";
const FIREBASE_AUTH = "";  // database secret nếu đã khóa Firebase rules
const ESP_OFFLINE_MS = 3 * 60 * 1000;  // nhịp tim ESP_TS đứng yên quá 3 phút = mạch tắt -> không ghi
const HEADERS = ["NGÀY", "GIỜ", "NHIỆT ĐỘ\n(°C)", "ĐỘ ẨM KHÔNG KHÍ\n(%)", "ĐỘ ẨM ĐẤT\n(%)", "ĐỘ MẶN\n(PPM)", "PH",
  "ÁNH SÁNG\n(LUX)", "BƠM", "ĐÈN", "QUẠT", "PHUN SƯƠNG", "MÀN CHE MỞ", "CHẾ ĐỘ AUTO"];
const PARAMS = ["t", "h", "amdat", "doman", "ph", "anhsang", "bom", "den", "quat", "suong", "mo", "mode"];
const FIREBASE_KEYS = ["t", "h", "GT_AMDAT", "GT_DOMAN", "GT_PH", "ANHSANG", "BOM", "DEN", "QUAT", "SUONG", "MO", "MODE"];

/** CHẠY HÀM NÀY 1 LẦN: tạo lịch tự ghi 1 phút/lần (chạy lại cũng không bị trùng lịch). */
function CAI_DAT() {
  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === "ghiTuFirebase")
    .forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger("ghiTuFirebase").timeBased().everyMinutes(1).create();
  const kq = ghiTuFirebase();
  Logger.log("XONG. Google sẽ tự ghi dữ liệu vào Sheet mỗi phút, kể cả khi tắt máy tính. Lần này: " + kq);
}

/** Lịch 1 phút/lần: đọc số liệu ESP32 đang gửi lên Firebase rồi ghi 1 dòng. */
function ghiTuFirebase() {
  const espWrite = Number(PropertiesService.getScriptProperties().getProperty("espWrite") || 0);
  if (Date.now() - espWrite < 90 * 1000) return "ESP32 đang tự ghi qua link -> bỏ qua để không trùng dòng";
  let d;
  try {
    const res = UrlFetchApp.fetch(FIREBASE_URL + "/.json" + (FIREBASE_AUTH ? "?auth=" + FIREBASE_AUTH : ""),
      { muteHttpExceptions: true });
    if (res.getResponseCode() !== 200) return "Firebase lỗi " + res.getResponseCode();
    d = JSON.parse(res.getContentText()) || {};
  } catch (err) {
    return "Không đọc được Firebase: " + err;  // mạng chập chờn: bỏ qua phút này, không gửi email lỗi
  }
  if (d.ESP_TS && Date.now() - Number(d.ESP_TS) > ESP_OFFLINE_MS) return "mạch ESP32 đang tắt -> không ghi";
  ghiDong(FIREBASE_KEYS.map((k) => d[k]));
  return "đã ghi 1 dòng";
}

function dataSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return SHEET_NAME ? ss.getSheetByName(SHEET_NAME) : ss.getSheets()[0];
}

/** Chèn 1 dòng mới ở trên cùng (dưới tiêu đề). values theo thứ tự PARAMS. */
function ghiDong(values) {
  const sh = dataSheet();
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    if (sh.getRange(1, HEADERS.length).getValue() !== HEADERS[HEADERS.length - 1]) {
      sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);  // thêm tiêu đề cột thiết bị (giữ nguyên 8 cột cũ)
    }
    const now = new Date();
    const num = (v) => (v === undefined || v === null || v === "" || isNaN(Number(v)) ? "" : Number(v));
    const row = [Utilities.formatDate(now, TZ, "dd/MM/yyyy"), Utilities.formatDate(now, TZ, "HH:mm:ss")]
      .concat(values.map(num));
    sh.insertRowAfter(1);
    sh.getRange(2, 1, 1, 2).setNumberFormat("@");  // ngày/giờ dạng chữ: không bị Sheets đổi thành tháng/ngày
    sh.getRange(2, 1, 1, row.length).setValues([row]);
  } finally {
    lock.releaseLock();
  }
}

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.sts === "read") return readCsv(Number(p.rows) || 3000);
  if (p.sts !== "write") return ContentService.createTextOutput("Nha Nam: Apps Script dang chay");
  PropertiesService.getScriptProperties().setProperty("espWrite", String(Date.now()));
  ghiDong(PARAMS.map((k) => p[k]));
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
