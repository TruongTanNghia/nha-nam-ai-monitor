// Kết nối Máy chủ AI (server/app.py): phân tích, cảnh báo, báo cáo, Zalo, hỏi đáp
import { SENSOR_BY_KEY, fmtWithUnit } from "./sensors.js";
import { $, $$, esc, fmtDateTime, icon, refreshIcons, setHTML, timeAgo, toast } from "./ui.js";

const STORAGE_KEY = "nhanam.backendUrl";
const SEV_TEXT = { critical: "Nghiêm trọng", warning: "Cảnh báo", info: "Ghi chú" };
const SEV_ICON = { critical: "octagon-alert", warning: "triangle-alert", info: "info" };
const CAT_TEXT = { threshold: "Ngưỡng", sensor: "Cảm biến", device: "Thiết bị", system: "Hệ thống", water: "Nước cấp" };
const DEVICE_NAME = { BOM: "Máy bơm", DEN: "Đèn", QUAT: "Quạt", SUONG: "Phun sương", MO: "Màn che (mở)", DONG: "Màn che (đóng)", MODE: "Chế độ" };
const SOURCE_TEXT = { auto: "Máy chủ AI", esp_auto: "ESP32 tự động", zalo: "Zalo", external: "Web", predict: "Bật sớm (bản cũ)", esp: "ESP32" };

export const backend = {
  url: null,
  online: false,
  health: null,
  snapshot: null,
};

function safeGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function safeSet(key, value) {
  try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch { /* bỏ qua */ }
}

function candidates() {
  const list = [];
  const saved = safeGet(STORAGE_KEY);
  if (saved) list.push(saved);
  if (location.protocol.startsWith("http")) list.push(location.origin);
  list.push("http://localhost:5000");
  return [...new Set(list.map((u) => u.replace(/\/$/, "")))];
}

async function api(path, options = {}) {
  if (!backend.url) throw new Error("offline");
  const res = await fetch(backend.url + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    signal: AbortSignal.timeout(options.timeout || 8000),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Tìm máy chủ AI đang chạy. Trả true nếu kết nối được. */
export async function connect() {
  const urls = backend.url ? [backend.url, ...candidates()] : candidates();
  for (const url of [...new Set(urls)]) {
    try {
      const res = await fetch(url + "/api/health", { signal: AbortSignal.timeout(2500) });
      if (!res.ok) continue;
      const health = await res.json();
      if (!health.ok) continue;
      backend.url = url;
      backend.online = true;
      backend.health = health;
      return true;
    } catch { /* thử địa chỉ tiếp theo */ }
  }
  backend.online = false;
  backend.health = null;
  return false;
}

export async function refreshHealth() {
  try {
    backend.health = await api("/api/health");
    backend.online = true;
  } catch {
    backend.online = false;
    await connect();
  }
  return backend.online;
}

export async function refreshSnapshot() {
  if (!backend.online) return null;
  try {
    backend.snapshot = await api("/api/snapshot");
    return backend.snapshot;
  } catch {
    backend.online = false;
    return null;
  }
}

export async function fetchHistory(range) {
  return api(`/api/history?range=${encodeURIComponent(range)}`, { timeout: 15000 });
}

/* ================= Phân tích ================= */

export function findingHTML(f, compact = false) {
  const sensor = f.sensor && SENSOR_BY_KEY[f.sensor] ? SENSOR_BY_KEY[f.sensor].name : null;
  return `
    <article class="finding sev-${f.severity}">
      <div class="finding-icon">${icon(SEV_ICON[f.severity] || "info")}</div>
      <div class="finding-body">
        <div class="finding-title">${esc(f.title)}</div>
        ${compact ? "" : `<p>${esc(f.detail)}</p>`}
        <div class="finding-meta">
          <span class="sev-tag">${SEV_TEXT[f.severity] || f.severity}</span>
          <span>${esc(CAT_TEXT[f.category] || f.category)}</span>
          ${sensor ? `<span>${esc(sensor)}</span>` : ""}
        </div>
      </div>
    </article>`;
}

function emptyState(iconName, title, text) {
  return `<div class="empty">${icon(iconName)}<b>${esc(title)}</b><span>${esc(text)}</span></div>`;
}

export function offlineHTML() {
  return emptyState(
    "server-off",
    "Máy chủ AI chưa chạy",
    "Mở terminal tại thư mục dự án, chạy: python server/app.py, rồi tải lại trang.",
  );
}

export function renderAnalysis() {
  const a = backend.snapshot?.analysis;
  const banner = $("#serverBanner");
  if (!backend.online) {
    banner.hidden = false;
    setHTML(banner, `${icon("server-off")}<div><b>Chưa kết nối Máy chủ AI.</b> Phân tích nâng cao, cảnh báo Zalo và tự động 24/7 cần chạy <code>python server/app.py</code>. Đang hiển thị đánh giá cơ bản trên trình duyệt.</div>`);
  } else {
    banner.hidden = true;
  }

  setHTML($("#overviewFindings"), !backend.online
    ? offlineHTML()
    : a?.findings?.length
      ? a.findings.slice(0, 4).map((f) => findingHTML(f, true)).join("")
      : emptyState("circle-check", "Không có vấn đề", "Mọi chỉ số và thiết bị hoạt động bình thường."));

  setHTML($("#aiFindings"), !backend.online
    ? offlineHTML()
    : a?.findings?.length
      ? a.findings.map((f) => findingHTML(f)).join("")
      : emptyState("circle-check", "Không phát hiện bất thường", "Mọi chỉ số nằm trong ngưỡng, cảm biến và thiết bị hoạt động bình thường."));

  renderRF();
  renderWeather();
  renderRFModel();
  renderHardwareInfo();

  const llm = backend.health?.llm;
  $("#llmHint").textContent = !backend.online
    ? "Cần Máy chủ AI"
    : llm?.enabled ? `${llm.provider} · ${llm.model}` : "Chưa cấu hình LLM — thêm LLM_API_KEY trong server/.env";
}

/* ================= Random Forest + thời tiết (chỉ tham khảo) ================= */
/** [chỉ số, tên, hàm "ngoài ngưỡng"(giá trị, ngưỡng đang cài)] — ngưỡng 0 = chưa cài */
const over = (key) => (v, th) => Number(th[key]) > 0 && v > Number(th[key]);
const under = (key) => (v, th) => Number(th[key]) > 0 && v < Number(th[key]);
const RF_TARGETS = [
  ["t", "Nhiệt độ", over("SET_T")],
  ["h", "Độ ẩm KK", under("SET_H")],
  ["GT_AMDAT", "Độ ẩm đất", under("SET_AMDAT")],
  ["ANHSANG", "Ánh sáng", under("SET_AS")],
  ["GT_PH", "pH", (v, th) => under("SET_PHLOW")(v, th) || over("SET_PHHIGH")(v, th)],
  ["GT_DOMAN", "Độ mặn", over("SET_DOMAN")],
];
const fmtHour = (ts) => new Date(ts * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });

function weatherNowHTML(w) {
  const c = w?.current;
  if (!c || c.temp === null || c.temp === undefined) return "";
  const rain = c.precip > 0 ? ` · mưa ${c.precip} mm` : "";
  return `
    <div class="wx-now">
      ${icon(c.precip > 0 ? "cloud-rain" : c.cloud >= 70 ? "cloud" : "cloud-sun")}
      <b>${Math.round(c.temp)}°C</b>
      <div><span>Ẩm ${Math.round(c.hum)}% · mây ${Math.round(c.cloud)}% · gió ${Math.round(c.wind)} km/h${rain}</span>
        <small>Ngoài trời · ${esc(w.location?.name || "")}</small></div>
    </div>`;
}

/** Thẻ tổng quan: thời tiết ngoài trời + Random Forest dự đoán 1-3 giờ tới + gợi ý */
function renderRF() {
  const el = $("#rfOverview");
  if (!backend.online) return setHTML(el, emptyState("server-off", "Cần Máy chủ AI", "Chạy CHAY_NHA_NAM.bat để phân tích bằng Random Forest và thời tiết ngoài trời."));
  const rf = backend.snapshot?.rf;
  const w = backend.snapshot?.weather;
  const th = backend.snapshot?.data || {};
  let body;
  if (backend.hardwareOffline) {
    body = emptyState("unplug", "Tạm dừng phân tích", "ESP32 đang mất kết nối nên số liệu trong nhà đã cũ.");
  } else if (!rf?.predictions?.length) {
    const pct = Math.min(100, Math.round(((rf?.samples || 0) / 60) * 100));
    body = `
      <div class="rf-learning">
        ${icon("loader")}<div><b>Đang thu thập dữ liệu để kiểm chứng</b><small>${esc(rf?.note || "Đang chờ dữ liệu")}</small>
        <div class="meter"><span style="width:${pct}%"></span></div></div>
      </div>`;
  } else {
    const head = `<div></div><div class="rf-col">Bây giờ</div>` + rf.predictions.map((p) => `<div class="rf-col">+${p.hours} giờ</div>`).join("");
    const rows = RF_TARGETS.map(([k, name, outOf]) => {
      const bad = (v) => v !== null && v !== undefined && outOf(v, th);
      const cell = (v, p) => {
        if (v === null || v === undefined) return `<div class="rf-val">—</div>`;
        // Mỗi ô chỉ 1 con số; khoảng sai số để ở tooltip, độ tin cậy xem bảng kiểm chứng (tab Phân tích AI)
        const r = p?.range?.[k];
        const tip = r ? `Khoảng ${fmtWithUnit(k, r[0])} – ${fmtWithUnit(k, r[1])} (theo sai số kiểm chứng)` : "";
        return `<div class="rf-val ${bad(v) ? "warn" : ""}" title="${esc(tip)}">${fmtWithUnit(k, v)}</div>`;
      };
      return `<div class="rf-name"><span class="swatch" style="background:var(${SENSOR_BY_KEY[k].color})"></span>${name}</div>`
        + cell(th[k] !== undefined && th[k] !== null ? Number(th[k]) : rf.now?.[k]) + rf.predictions.map((p) => cell(p[k], p)).join("");  // "Bây giờ" = số đo mới nhất
    }).join("");
    body = `<div class="rf-table" style="--cols:${rf.predictions.length + 1}">${head}${rows}</div>
      <p class="rf-legend">Dự báo lúc ${rf.ts ? fmtHour(rf.ts) : "—"}, mô hình học lúc ${rf.trained_at ? fmtHour(rf.trained_at) : "—"} (học lại mỗi 15 phút) · Độ chính xác: xem bảng kiểm chứng ở tab Phân tích AI</p>`;
  }
  const advice = rf?.advice?.length && !backend.hardwareOffline
    ? `<ul class="rf-advice">${rf.advice.map((t) => `<li>${icon("lightbulb")}<span>${esc(t)}</span></li>`).join("")}</ul>` : "";
  setHTML(el, weatherNowHTML(w) + body + advice);
}

function renderWeather() {
  const el = $("#weatherCard");
  if (!backend.online) return setHTML(el, offlineHTML());
  const w = backend.snapshot?.weather;
  if (!w?.current) {
    return setHTML(el, emptyState("cloud-off", "Chưa lấy được thời tiết", w?.error ? `Lỗi: ${w.error}` : "Đang tải dữ liệu từ Open-Meteo…"));
  }
  $("#weatherHint").textContent = w.updated ? `Open-Meteo · ${timeAgo(w.updated * 1000)}` : "Open-Meteo";
  const next = (w.next_hours || []).map((h) => `
    <div class="wx-hour">
      <time>${fmtHour(h.ts)}</time>${icon(h.precip > 0.1 ? "cloud-rain" : h.cloud >= 70 ? "cloud" : "sun")}
      <b>${Math.round(h.temp)}°C</b><small>ẩm ${Math.round(h.hum)}%${h.precip > 0 ? ` · ${h.precip} mm` : ""}</small>
    </div>`).join("");
  setHTML(el, weatherNowHTML(w) + (next ? `<div class="wx-hours">${next}</div>` : "")
    + `<p class="note">Vị trí: ${esc(w.location?.name || "?")} (${w.location?.lat?.toFixed(2)}, ${w.location?.lon?.toFixed(2)}) — đặt WEATHER_LAT/WEATHER_LON trong server/.env nếu chưa đúng.</p>`);
}

/** Số gọn theo độ chính xác của cảm biến (pH 2 chữ số, còn lại 1) */
const fmtNum = (k, v) => (k === "GT_PH" ? v.toFixed(2) : v < 10 ? v.toFixed(1) : Math.round(v).toString());

function renderRFModel() {
  const el = $("#rfModel");
  if (!backend.online) return setHTML(el, offlineHTML());
  const rf = backend.snapshot?.rf;
  if (!rf) return setHTML(el, "");
  const UNIT = { t: "°C", h: "%", GT_AMDAT: "%", ANHSANG: " lux", GT_PH: "", GT_DOMAN: " ppm" };
  const period = rf.period ? `${fmtDateTime(rf.period[0] * 1000)} → ${fmtDateTime(rf.period[1] * 1000)}` : "—";
  const ev = rf.evaluation || [];
  const evalTable = ev.length ? `
    <div class="meter-label"><span>Kiểm chứng trên dữ liệu chưa thấy (sai số trung bình)</span></div>
    <div class="rf-eval">
      <div></div>${[1, 2, 3].map((h) => `<div class="rf-col">+${h} giờ</div>`).join("")}
      ${RF_TARGETS.map(([k, name]) => `<div class="rf-name">${name}</div>` + [1, 2, 3].map((h) => {
        const e = ev.find((x) => x.key === k && x.hours === h);
        if (!e) return `<div class="rf-val">—</div>`;
        return `<div class="rf-val ${e.use_rf ? "good" : "persist"}" title="Random Forest: ${fmtNum(k, e.mae)}${UNIT[k]} · giữ nguyên: ${fmtNum(k, e.mae_base)}${UNIT[k]}">`
          + `±${fmtNum(k, e.mae)}${UNIT[k]}<small>${e.skill > 0 ? "+" : ""}${e.skill}%</small></div>`;
      }).join("")).join("")}
    </div>
    <p class="note">% = mô hình tốt hơn (+) hay kém hơn (−) cách dự báo "giữ nguyên giá trị hiện tại". Ô chưa tốt hơn (màu xám) thì dự báo vẫn hiển thị nhưng không dùng để đưa gợi ý.</p>` : "";
  setHTML(el, `
    <div class="ml-state ${rf.validated ? "normal" : "learning"}">${icon(rf.validated ? "shield-check" : "loader")} ${rf.validated ? "Đã huấn luyện và kiểm chứng" : "Đang thu thập dữ liệu để kiểm chứng"}</div>
    <dl class="kv">
      <div><dt>Phương pháp</dt><dd>${esc(rf.method || "Random Forest")}</dd></div>
      <div><dt>Dữ liệu</dt><dd>${rf.samples ?? 0} mẫu (15 phút/mẫu)${rf.skipped ? `, đã loại ${rf.skipped} đoạn cảm biến lỗi` : ""} · ${period}</dd></div>
      ${rf.validated ? `<div><dt>Kiểm chứng</dt><dd>Walk-forward ${rf.folds} lần, ${rf.test_samples} mẫu thử chưa thấy</dd></div>` : ""}
      <div><dt>Lần huấn luyện</dt><dd>${rf.trained_at ? timeAgo(rf.trained_at * 1000) : "—"}</dd></div>
    </dl>
    ${evalTable}
    ${rf.importance?.length ? `<div class="meter-label"><span>Yếu tố ảnh hưởng nhiều nhất</span></div>
      <div class="rf-imp">${rf.importance.map((i) => `
        <div><span>${esc(i.name)}</span><div class="meter"><span style="width:${i.pct}%"></span></div><b>${i.pct}%</b></div>`).join("")}</div>` : ""}
    <p class="note">${esc(rf.note || "")}. Kết quả chỉ để tham khảo, thiết bị vẫn bật/tắt theo ngưỡng cài đặt. Phương pháp: docs/PHUONG_PHAP_DU_BAO.md</p>`);
}

function fmtDuration(sec) {
  if (sec === null || sec === undefined) return "—";
  if (sec < 120) return `${Math.round(sec)} giây`;
  if (sec < 7200) return `${Math.round(sec / 60)} phút`;
  if (sec < 172800) return `${Math.round(sec / 3600)} giờ`;
  return `${Math.round(sec / 86400)} ngày`;
}

/** Thẻ "Nguồn & WiFi ESP32": phát hiện nguồn không ổn định qua số lần khởi động lại + lý do */
function renderHardwareInfo() {
  const hw = backend.snapshot?.hardware;
  const box = $("#hwInfo");
  if (!backend.online || !hw) return setHTML(box, `<div><dt>Trạng thái</dt><dd>Cần Máy chủ AI</dd></div>`);
  if (hw.uptime_sec === null || hw.uptime_sec === undefined) {
    setHTML(box, `<div><dt>Firmware</dt><dd>Chưa gửi thông tin nguồn</dd></div>`);
    $("#hwNote").textContent = "Nạp firmware aduno.ino mới để xem ESP32 có hay khởi động lại vì sụt áp không, và sóng WiFi mạnh/yếu.";
    return;
  }
  const rssi = hw.rssi;
  const rssiText = rssi === null ? "—" : `${rssi} dBm · ${rssi >= -60 ? "mạnh" : rssi >= -72 ? "khá" : rssi >= -80 ? "yếu" : "rất yếu"}`;
  setHTML(box, `
    <div><dt>Chạy liên tục</dt><dd>${fmtDuration(hw.uptime_sec)}</dd></div>
    <div><dt>Khởi động lại 24h</dt><dd>${hw.restarts_24h} lần${hw.restarts_1h ? ` (${hw.restarts_1h} trong 1 giờ)` : ""}</dd></div>
    <div><dt>Lý do lần khởi động gần nhất</dt><dd>${esc(hw.reset_reason || "—")}</dd></div>
    <div><dt>Sóng WiFi</dt><dd>${esc(rssiText)}</dd></div>`);
  $("#hwNote").textContent = hw.restarts_1h >= 2 || hw.reset_reason === "sụt áp nguồn (brownout)"
    ? "Nguồn điện chưa ổn định — xem docs/PHAN_CUNG_ON_DINH.md để tách nguồn rơ-le và thêm tụ lọc."
    : "";
}

export function renderEvents(events) {
  const el = $("#eventList");
  if (!backend.online) return setHTML(el, offlineHTML());
  if (!events?.length) return setHTML(el, emptyState("history", "Chưa có hoạt động", "Các lần bật/tắt thiết bị sẽ hiện ở đây."));
  setHTML(el, events.slice(0, 8).map((e) => {
    const on = Number(e.value) === 1;
    const what = e.device === "ESP_RESTART" ? "ESP32 khởi động lại"
      : e.device === "ESP_LINK" ? (on ? "ESP32 hoạt động lại" : "ESP32 tắt / mất kết nối")
      : e.device === "MODE" ? `Chế độ ${on ? "AUTO" : "MANUAL"}` : `${on ? "Bật" : "Tắt"} ${DEVICE_NAME[e.device] || e.device}`;
    return `
      <div class="event">
        <span class="event-dot ${on ? "on" : ""}"></span>
        <div><b>${esc(what)}</b><small>${esc(SOURCE_TEXT[e.source] || e.source)}${e.reason ? " · " + esc(e.reason) : ""}</small></div>
        <time>${timeAgo(e.ts * 1000)}</time>
      </div>`;
  }).join(""));
}

/* ================= Cảnh báo & Zalo ================= */
let alertFilter = "all";
let lastAlerts = [];

export function renderAlerts(alerts) {
  if (alerts) lastAlerts = alerts;
  const el = $("#alertTable");
  if (!backend.online) return setHTML(el, offlineHTML());
  const list = lastAlerts.filter((a) => alertFilter === "all" || a.severity === alertFilter);
  const active = lastAlerts.filter((a) => a.status === "active" && a.severity !== "info").length;
  const badge = $("#navAlertBadge");
  badge.hidden = !active;
  badge.textContent = active;
  if (!list.length) return setHTML(el, emptyState("bell-off", "Chưa có cảnh báo", "Hệ thống sẽ ghi lại mọi cảnh báo ở đây."));
  setHTML(el, list.map((a) => `
    <div class="alert-row sev-${a.severity}">
      <div class="finding-icon">${icon(SEV_ICON[a.severity] || "info")}</div>
      <div class="alert-main">
        <div class="alert-title">${esc(a.title)}
          ${a.status === "active" ? `<span class="tag live">Đang mở</span>` : `<span class="tag">Đã xử lý</span>`}
          ${a.sent_zalo ? `<span class="tag zalo">${icon("send")} Zalo</span>` : ""}
        </div>
        <p>${esc(a.detail)}</p>
      </div>
      <time>${fmtDateTime(a.ts * 1000)}</time>
    </div>`).join(""));
}

export async function refreshAlerts() {
  if (!backend.online) return renderAlerts([]);
  try {
    const [{ alerts }, { events }] = await Promise.all([api("/api/alerts?limit=150"), api("/api/events?limit=20")]);
    renderAlerts(alerts);
    renderEvents(events);
  } catch { /* giữ dữ liệu cũ */ }
}

function renderTunnel() {
  const t = backend.health?.tunnel;
  const el = $("#tunnelInfo");
  if (!t?.enabled) return setHTML(el, "");
  setHTML(el, t.url
    ? `<div class="tunnel-box ok">${icon("globe")}<div><b>Link công khai (Cloudflare)</b>
         <a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.url)}</a>
         <small>Link đổi mỗi lần khởi động lại máy chủ — link mới luôn được gửi về Zalo.</small></div>
         <button class="btn" type="button" data-copy="${esc(t.url)}">${icon("copy")} Chép</button></div>`
    : `<div class="tunnel-box warn">${icon("globe")}<div><b>Đang mở đường hầm…</b>
         <small>${esc(t.error || "Chờ Cloudflare cấp link")}</small></div></div>`);
}

export function renderZalo() {
  renderTunnel();
  const z = backend.health?.zalo;
  const tag = $("#zaloTag");
  if (!backend.online || !z) {
    tag.textContent = "Ngoại tuyến";
    tag.className = "tag";
    setHTML($("#zaloInfo"), `<p class="note">Cần Máy chủ AI để gửi Zalo.</p>`);
    return;
  }
  tag.textContent = z.configured ? "Đã kết nối" : "Chưa cấu hình";
  tag.className = `tag ${z.configured ? "ok" : "warn"}`;
  const muted = backend.health.muted_until;
  setHTML($("#zaloInfo"), `
    <div><dt>Bot token</dt><dd>${z.has_token ? "Đã có" : "Chưa có"}</dd></div>
    <div><dt>Người nhận</dt><dd>${z.chat_count} chat</dd></div>
    <div><dt>Nhận lệnh</dt><dd>${z.commands ? "Bật" : "Tắt"}</dd></div>
    <div><dt>Hạn mức 24 giờ</dt><dd>${z.sent_day ?? 0}/${z.max_day ?? "—"} tin</dd></div>
    <div><dt>Trong 1 giờ</dt><dd>${z.sent_hour ?? 0}/${z.max_hour ?? "—"} tin${z.dropped ? ` · bỏ qua ${z.dropped}` : ""}</dd></div>
    ${z.paused_until ? `<div class="err"><dt>Zalo báo vượt hạn mức — tạm ngừng gửi đến</dt><dd>${fmtDateTime(z.paused_until * 1000)}</dd></div>` : ""}
    ${muted ? `<div><dt>Tạm tắt đến</dt><dd>${fmtDateTime(muted * 1000)}</dd></div>` : ""}
    ${z.last_error ? `<div class="err"><dt>Lỗi gần nhất</dt><dd>${esc(z.last_error)}</dd></div>` : ""}`);
  $("#muteSelect").value = muted ? $("#muteSelect").value : "0";
}

async function refreshChats() {
  if (!backend.online) return;
  try {
    const { chats } = await api("/api/zalo/chats");
    setHTML($("#zaloChats"), chats.length
      ? `<div class="chat-ids"><b>Chat đã nhắn cho bot</b>${chats.map((c) => `
          <div><code>${esc(c.chat_id)}</code> ${esc(c.name)} ${c.allowed ? `<span class="tag ok">có quyền</span>` : `<span class="tag warn">chưa có quyền</span>`}</div>`).join("")}</div>`
      : "");
  } catch { /* bỏ qua */ }
}

/* ================= Gắn sự kiện ================= */
export function bindAIControls() {
  $$("[data-sev]").forEach((btn) => btn.addEventListener("click", () => {
    $$("[data-sev]").forEach((b) => b.classList.toggle("active", b === btn));
    alertFilter = btn.dataset.sev;
    renderAlerts();
  }));

  const reportBox = $("#reportBox");
  async function report(sendZalo) {
    if (!backend.online) return toast("Máy chủ AI chưa chạy", "err");
    const btns = [$("#btnReport"), $("#btnReportZalo")];
    btns.forEach((b) => (b.disabled = true));
    reportBox.classList.add("loading");
    reportBox.textContent = "AI đang tổng hợp dữ liệu…";
    try {
      const hours = Number($("#reportHours").value);
      const r = await api("/api/report", { method: "POST", body: JSON.stringify({ hours, send_zalo: sendZalo }), timeout: 90000 });
      reportBox.textContent = r.text;
      if (!r.by_ai) reportBox.insertAdjacentHTML("beforeend", `\n\n<span class="note">(Báo cáo mẫu — thêm LLM_API_KEY để AI viết báo cáo chi tiết)</span>`);
      if (sendZalo) toast(r.sent_zalo ? "Đã gửi báo cáo về Zalo" : "Gửi Zalo thất bại — kiểm tra cấu hình", r.sent_zalo ? "ok" : "err");
    } catch (err) {
      reportBox.textContent = "Không tạo được báo cáo: " + err.message;
    } finally {
      reportBox.classList.remove("loading");
      btns.forEach((b) => (b.disabled = false));
    }
  }
  $("#btnReport").addEventListener("click", () => report(false));
  $("#btnReportZalo").addEventListener("click", () => report(true));

  $("#chatForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("#chatInput");
    const q = input.value.trim();
    if (!q) return;
    const log = $("#chatLog");
    const add = (cls, text) => {
      const div = document.createElement("div");
      div.className = `msg ${cls}`;
      div.textContent = text;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
      return div;
    };
    add("me", q);
    input.value = "";
    if (!backend.online) return add("bot err", "Máy chủ AI chưa chạy nên em chưa trả lời được.");
    const pending = add("bot typing", "Đang suy nghĩ…");
    try {
      const r = await api("/api/ask", { method: "POST", body: JSON.stringify({ question: q }), timeout: 90000 });
      pending.className = r.answer ? "msg bot" : "msg bot err";
      pending.textContent = r.answer || r.error;
    } catch (err) {
      pending.className = "msg bot err";
      pending.textContent = "Lỗi: " + err.message;
    }
  });

  $("#btnZaloTest").addEventListener("click", async () => {
    if (!backend.online) return toast("Máy chủ AI chưa chạy", "err");
    const r = await api("/api/zalo/test", { method: "POST", timeout: 20000 }).catch((e) => ({ ok: false, error: e.message }));
    $("#zaloResult").textContent = r.ok ? "✅ Đã gửi tin thử, kiểm tra Zalo nhé!" : `❌ Gửi thất bại: ${r.error || "chưa cấu hình ZALO_BOT_TOKEN / ZALO_CHAT_IDS"}`;
    refreshHealth().then(renderZalo);
    refreshChats();
  });

  $("#muteSelect").addEventListener("change", async (ev) => {
    if (!backend.online) return toast("Máy chủ AI chưa chạy", "err");
    const minutes = Number(ev.target.value);
    await api("/api/mute", { method: "POST", body: JSON.stringify({ minutes }) }).catch(() => null);
    toast(minutes ? `Tạm tắt thông báo thường ${minutes} phút` : "Đã bật lại thông báo");
    refreshHealth().then(renderZalo);
  });

  const input = $("#backendInput");
  input.value = safeGet(STORAGE_KEY) || "";
  $("#btnBackendSave").addEventListener("click", async () => {
    const url = input.value.trim().replace(/\/$/, "");
    safeSet(STORAGE_KEY, url);
    backend.url = url || null;
    const ok = await connect();
    toast(ok ? `Đã kết nối ${backend.url}` : "Không kết nối được máy chủ", ok ? "ok" : "err");
    document.dispatchEvent(new CustomEvent("backend-changed"));
  });

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    try { await navigator.clipboard.writeText(btn.dataset.copy); toast("Đã chép link"); } catch { toast("Không chép được", "err"); }
  });

  refreshChats();
  setInterval(refreshChats, 60000);
  refreshIcons();
}
