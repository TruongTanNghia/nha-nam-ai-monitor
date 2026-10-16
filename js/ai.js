// Kết nối Máy chủ AI (server/app.py): phân tích, cảnh báo, báo cáo, Zalo, hỏi đáp
import { SENSOR_BY_KEY, fmtWithUnit } from "./sensors.js";
import { $, $$, esc, fmtDateTime, icon, refreshIcons, setHTML, timeAgo, toast } from "./ui.js";

const STORAGE_KEY = "nhanam.backendUrl";
const SEV_TEXT = { critical: "Nghiêm trọng", warning: "Cảnh báo", info: "Ghi chú" };
const SEV_ICON = { critical: "octagon-alert", warning: "triangle-alert", info: "info" };
const CAT_TEXT = { threshold: "Ngưỡng", sensor: "Cảm biến", device: "Thiết bị", ml: "Model ML", forecast: "Dự báo", system: "Hệ thống", water: "Nước cấp" };
const DEVICE_NAME = { BOM: "Máy bơm", DEN: "Đèn", QUAT: "Quạt", SUONG: "Phun sương", MO: "Màn che (mở)", DONG: "Màn che (đóng)", MODE: "Chế độ" };
const SOURCE_TEXT = { auto: "Máy chủ AI", esp_auto: "ESP32 tự động", zalo: "Zalo", external: "Web" };

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
      : emptyState("circle-check", "Không phát hiện bất thường", "Luật chuyên gia, model ML và dự báo đều cho kết quả tốt."));

  renderForecast(a);
  renderSoon();
  renderML(a);

  const llm = backend.health?.llm;
  $("#llmHint").textContent = !backend.online
    ? "Cần Máy chủ AI"
    : llm?.enabled ? `${llm.provider} · ${llm.model}` : "Chưa cấu hình LLM — thêm LLM_API_KEY trong server/.env";
}

const ARROW = { up: "trending-up", down: "trending-down", flat: "move-right" };

/** "khoảng 4 phút nữa ..." -> "Khoảng 4 phút nữa ..." */
const cap = (text) => (!text || text.startsWith("pH") ? text || "" : text[0].toUpperCase() + text.slice(1));

export function fmtEta(min) {
  if (min === null || min === undefined) return "";
  if (min < 1) return "<1′";
  return min < 60 ? `${Math.round(min)}′` : `${(min / 60).toFixed(1).replace(".0", "")}h`;
}

/** Thẻ "Dự báo thiết bị" ở trang tổng quan: các sự kiện sắp xảy ra, gần nhất trước */
export function renderSoon() {
  const el = $("#soonList");
  if (!backend.online) {
    return setHTML(el, emptyState("server-off", "Cần Máy chủ AI", "Chạy python server/app.py để xem bao lâu nữa thiết bị sẽ bật/tắt."));
  }
  if (backend.hardwareOffline) {
    return setHTML(el, emptyState("unplug", "Tạm dừng dự báo", "ESP32 đang mất kết nối nên số liệu đã cũ, dự báo sẽ chạy lại khi bo mạch gửi dữ liệu."));
  }
  const fc = backend.snapshot?.analysis?.forecasts || {};
  if (!Object.keys(fc).length) {
    return setHTML(el, emptyState("hourglass", "Đang đo xu hướng", "Cần khoảng 1 phút dữ liệu liên tục để dự báo."));
  }
  const events = Object.entries(fc).flatMap(([k, f]) => f.events.map((e) => ({ ...e, sensor: k })));
  events.sort((a, b) => (a.eta_min ?? 1e9) - (b.eta_min ?? 1e9));
  if (!events.length) {
    return setHTML(el, emptyState("circle-check", "Chưa có thiết bị nào sắp đổi trạng thái",
      "Các chỉ số đang đi ngang hoặc còn xa ngưỡng."));
  }
  const early = backend.snapshot?.predictive?.active || {};
  const DEVICE_FOR = { t: "QUAT", h: "SUONG", GT_AMDAT: "BOM", ANHSANG: "DEN" };  // ANHSANG còn mở màn che (MO)
  setHTML(el, events.slice(0, 5).map((e) => `
    <div class="soon ${e.kind}">
      <b class="soon-eta">${e.eta_min !== null ? fmtEta(e.eta_min) : icon("activity")}</b>
      <div>
        <div class="soon-text">${esc(cap(e.text))}
          ${e.kind === "trigger" && DEVICE_FOR[e.sensor] in early ? '<span class="tag pre">Đang bật sớm</span>' : ""}</div>
        <small><span class="swatch" style="background:var(${SENSOR_BY_KEY[e.sensor].color})"></span>
          ${esc(SENSOR_BY_KEY[e.sensor].name)} ${fmtWithUnit(e.sensor, fc[e.sensor].now)}
          ${fc[e.sensor].trend !== "flat" ? `· ${rateText(e.sensor, fc[e.sensor].rate_per_min)}` : ""}</small>
      </div>
    </div>`).join(""));
}

export function rateText(key, rate) {
  const unit = SENSOR_BY_KEY[key].unit;
  const digits = key === "GT_PH" ? 2 : 1;
  const value = Math.abs(rate).toFixed(digits).replace(/\.?0+$/, "");
  const sep = ["%", "°C", ""].includes(unit) ? "" : " ";
  return `${rate > 0 ? "tăng" : "giảm"} ${value}${sep}${unit}/phút`;
}

function renderForecast(a) {
  const el = $("#forecastTable");
  if (!backend.online) return setHTML(el, offlineHTML());
  const fc = a?.forecasts || {};
  const keys = Object.keys(fc);
  if (!keys.length) {
    return setHTML(el, emptyState("hourglass", "Đang đo xu hướng", "Cần khoảng 1 phút dữ liệu liên tục để dự báo."));
  }
  const rows = keys.map((k) => {
    const f = fc[k];
    const timed = f.events.filter((e) => e.eta_min !== null).sort((x, y) => x.eta_min - y.eta_min)[0];
    const missing = f.missing_thresholds.length ? " · chưa cài ngưỡng nên chưa dự báo được thiết bị" : "";
    return `
      <div class="fc-row">
        <div class="fc-name"><span class="swatch" style="background:var(${SENSOR_BY_KEY[k].color})"></span>${esc(SENSOR_BY_KEY[k].name)}</div>
        <div class="fc-vals">
          <b>${fmtWithUnit(k, f.now)}</b>
          ${icon(ARROW[f.trend], `trend ${f.trend}`)}
          <span>${f.trend === "flat" ? "đi ngang" : rateText(k, f.rate_per_min)}</span>
        </div>
        <span class="eta ${timed?.kind === "trigger" ? "warning" : ""}">
          ${timed ? `<b>${fmtEta(timed.eta_min)}</b> ` : ""}${esc(cap(f.headline))}${esc(missing)}
        </span>
      </div>`;
  });
  setHTML(el, rows.join(""));
}

function renderML(a) {
  const el = $("#mlCard");
  if (!backend.online) return setHTML(el, offlineHTML());
  const ml = a?.ml;
  if (!ml) return setHTML(el, "");
  const latest = ml.latest;
  const scorePct = latest ? Math.round(Math.min(1, Math.max(0, 0.5 - latest.score)) * 100) : 0;
  const state = !ml.trained ? "learning" : latest?.anomaly ? "anomaly" : "normal";
  const stateText = { learning: "Đang học dữ liệu", anomaly: "Phát hiện bất thường", normal: "Hoạt động bình thường" }[state];
  setHTML(el, `
    <div class="ml-state ${state}">${icon(state === "anomaly" ? "triangle-alert" : state === "learning" ? "loader" : "shield-check")} ${stateText}</div>
    <dl class="kv">
      <div><dt>Thuật toán</dt><dd>${esc(ml.name)}</dd></div>
      <div><dt>Dữ liệu huấn luyện</dt><dd>${ml.samples ?? 0} mẫu</dd></div>
      <div><dt>Lần huấn luyện</dt><dd>${ml.trained_at ? timeAgo(ml.trained_at * 1000) : "—"}</dd></div>
    </dl>
    ${latest ? `
      <div class="meter-label"><span>Mức bất thường hiện tại</span><b>${scorePct}%</b></div>
      <div class="meter"><span style="width:${scorePct}%"></span></div>
      <div class="ml-top">Ảnh hưởng nhiều nhất: ${latest.top.map((t) =>
        `<span>${esc(SENSOR_BY_KEY[t.sensor].name)} <b>${t.z > 0 ? "+" : ""}${t.z}σ</b></span>`).join("")}</div>` : ""}
    <p class="note">${esc(ml.note || "")}</p>`);
}

export function renderEvents(events) {
  const el = $("#eventList");
  if (!backend.online) return setHTML(el, offlineHTML());
  if (!events?.length) return setHTML(el, emptyState("history", "Chưa có hoạt động", "Các lần bật/tắt thiết bị sẽ hiện ở đây."));
  setHTML(el, events.slice(0, 8).map((e) => {
    const on = Number(e.value) === 1;
    const what = e.device === "MODE" ? `Chế độ ${on ? "AUTO" : "MANUAL"}` : `${on ? "Bật" : "Tắt"} ${DEVICE_NAME[e.device] || e.device}`;
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
    <div><dt>Đã gửi</dt><dd>${z.sent_count} tin</dd></div>
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
