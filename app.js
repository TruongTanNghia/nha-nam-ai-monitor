import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getDatabase,
  ref,
  update,
  onValue,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-database.js";

import {
  DEVICES, LEVEL_ICON, LEVEL_TEXT, SENSORS, SETTINGS, SEV_RANK,
  fmtValue, num, sensorStatus, thresholdText, thresholds,
} from "./js/sensors.js";
import { $, $$, esc, fmtClock, icon, refreshIcons, setHTML, timeAgo, toast } from "./js/ui.js";
import { buildChartCards, gaugeSVG, renderCharts, resetCharts, sparkline, updateGauge } from "./js/charts.js";
import {
  backend, bindAIControls, connect, fetchHistory, refreshAlerts, refreshHealth,
  fmtEta, rateText, refreshSnapshot, renderAnalysis, renderEvents, renderZalo,
} from "./js/ai.js";

const firebaseConfig = {
  apiKey: "AIzaSyA-nkaKxm-nwWaRcwKgqHdPX5I30fJwVr0",
  authDomain: "nha-kinh-84aa2.firebaseapp.com",
  databaseURL: "https://nha-kinh-84aa2-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId: "nha-kinh-84aa2",
  storageBucket: "nha-kinh-84aa2.firebasestorage.app",
  messagingSenderId: "282119761626",
  appId: "1:282119761626:web:8cdeddc726456cebcbbfe3"
};

const app = initializeApp(firebaseConfig);
const db = getDatabase(app);

const MODE_AUTO = 1;
const MODE_MANUAL = 0;
const LIVE_MAX_POINTS = 360; // ~30 phút với chu kỳ 5 giây

const state = {
  data: null,
  lastDataAt: null,
  live: [],            // [{ts, t, h, ...}] dữ liệu trực tiếp trong trình duyệt
  range: "live",
  busy: false,
  editingSettings: false,
};

/* =========================
   TABS
========================= */
const titleMap = {
  tongquan: "Tổng quan hệ thống",
  dieukhien: "Điều khiển thiết bị",
  bieudo: "Biểu đồ cảm biến",
  phantich: "Phân tích AI",
  canhbao: "Cảnh báo & Zalo",
};

function openTab(tabId) {
  $$(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabId));
  $$(".tab-content").forEach((c) => c.classList.toggle("active", c.id === tabId));
  $("#pageTitle").textContent = titleMap[tabId];
  if (tabId === "bieudo") renderChartTab();
  if (tabId === "canhbao") refreshAlerts();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
$$(".tab-btn").forEach((btn) => btn.addEventListener("click", () => openTab(btn.dataset.tab)));
$$("[data-goto]").forEach((btn) => btn.addEventListener("click", () => openTab(btn.dataset.goto)));

/* =========================
   THEME
========================= */
function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  const dark = theme ? theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  $("#themeBtn").innerHTML = `${icon(dark ? "sun" : "moon-star")}<span>${dark ? "Giao diện sáng" : "Giao diện tối"}</span>`;
  refreshIcons($("#themeBtn"));
  resetCharts();
  renderChartTab();
}
$("#themeBtn").addEventListener("click", () => {
  const isDark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = isDark ? "light" : "dark";
  try { localStorage.setItem("nhanam.theme", next); } catch { /* bỏ qua */ }
  applyTheme(next);
});

/* =========================
   MODE AUTO / MANUAL
========================= */
function renderMode(data) {
  const auto = Number(data?.MODE) === MODE_AUTO;
  $("#btnAuto").classList.toggle("active", auto);
  $("#btnManual").classList.toggle("active", !auto);
  const note = $("#modeNote");
  note.innerHTML = auto
    ? `${icon("cpu")} ESP32 tự điều khiển theo ngưỡng`
    : `${icon("hand")} Điều khiển thủ công từ web / Zalo`;
  refreshIcons(note);
}

async function setMode(mode) {
  try {
    await update(ref(db, "/"), { MODE: Number(mode) });
    toast(mode === MODE_AUTO ? "Đã chuyển sang AUTO" : "Đã chuyển sang MANUAL");
  } catch (error) {
    console.error("Lỗi cập nhật MODE:", error);
    toast("Không đổi được chế độ", "err");
  }
}
$("#btnManual").addEventListener("click", () => setMode(MODE_MANUAL));
$("#btnAuto").addEventListener("click", () => setMode(MODE_AUTO));

/* =========================
   THẺ CẢM BIẾN (TỔNG QUAN)
========================= */
function buildSensorTiles() {
  $("#sensorGrid").innerHTML = SENSORS.map((s, i) => `
    <article class="sensor card reveal" id="tile-${s.key}" style="--c:var(${s.color}); --d:${i * 60}ms">
      <header>
        <span class="sensor-icon">${icon(s.icon)}</span>
        <h3>${esc(s.name)}</h3>
        <span class="status-pill" data-level="ok">${icon("circle-check")}<span>—</span></span>
      </header>
      <div class="sensor-body">
        <div class="reading"><b class="value">—</b><span class="unit">${esc(s.unit)}</span></div>
        ${gaugeSVG()}
      </div>
      <footer>
        <span class="threshold">—</span>
        <span class="spark-wrap"></span>
      </footer>
    </article>`).join("");
  refreshIcons($("#sensorGrid"));
}

function gaugeRange(s, value, th) {
  let max = s.max;
  const marks = s.key === "GT_PH" ? [th.SET_PHLOW, th.SET_PHHIGH] : [th[s.set]];
  for (const v of [value, ...marks]) if (v !== null && v !== undefined && v > max * 0.95) max = v * 1.25;
  return { min: s.min, max, marks };
}

function renderSensorTiles(data) {
  const th = thresholds(data);
  const analysis = backend.online ? backend.snapshot?.analysis : null;
  for (const s of SENSORS) {
    const tile = document.getElementById(`tile-${s.key}`);
    if (!tile) continue;
    const value = num(data?.[s.key]);
    const st = sensorStatus(s.key, value, th);
    tile.dataset.level = st.level;
    tile.querySelector(".value").textContent = fmtValue(s.key, value);

    const pill = tile.querySelector(".status-pill");
    if (pill.dataset.level !== st.level || pill.lastChild.textContent !== st.text) {
      pill.dataset.level = st.level;
      pill.innerHTML = `${icon(LEVEL_ICON[st.level])}<span>${esc(st.text)}</span>`;
      pill.title = LEVEL_TEXT[st.level];
      refreshIcons(pill);
    }

    const { min, max, marks } = gaugeRange(s, value, th);
    updateGauge(tile.querySelector(".gauge-svg"), value, min, max, marks);

    // Dòng cuối thẻ: ngưỡng + dự báo (vd "Tối thiểu 60% · 4′ nữa bật phun sương")
    let trend = "";
    const fc = analysis?.forecasts?.[s.key];
    const next = fc?.events.filter((e) => e.eta_min !== null).sort((x, y) => x.eta_min - y.eta_min)[0];
    if (next) trend = ` · ${fmtEta(next.eta_min)} nữa ${next.action}`;
    else if (fc && fc.trend !== "flat") trend = ` · ${fc.trend === "up" ? "↗" : "↘"} ${rateText(s.key, fc.rate_per_min)}`;
    tile.querySelector(".threshold").textContent = thresholdText(s.key, th) + trend;

    const series = state.live.slice(-40).map((p) => p[s.key]);
    tile.querySelector(".spark-wrap").innerHTML = sparkline(series);
  }
}

/* =========================
   HERO: ĐIỂM SỨC KHỎE + CHIPS
========================= */
function localHealth(data) {
  const th = thresholds(data);
  let score = 100;
  const issues = [];
  for (const s of SENSORS) {
    const st = sensorStatus(s.key, num(data?.[s.key]), th);
    if (SEV_RANK[st.level] >= 2) {
      score -= st.level === "warning" ? 12 : 25;
      issues.push(`${s.name}: ${st.text.toLowerCase()}`);
    }
  }
  score = Math.max(0, score);
  const label = score >= 85 ? "Tốt" : score >= 65 ? "Khá" : score >= 40 ? "Cần chú ý" : "Nguy hiểm";
  const summary = issues.length
    ? `${issues.length} chỉ số cần chú ý: ${issues.slice(0, 3).join("; ")}.`
    : "Các chỉ số đang trong ngưỡng an toàn.";
  return { score, label, summary };
}

function renderHero(data) {
  const a = backend.online ? backend.snapshot?.analysis : null;
  const h = a ? { ...a.health, summary: a.summary } : localHealth(data);
  const arc = $("#healthArc");
  const C = 2 * Math.PI * 52;
  arc.style.strokeDasharray = `${(h.score / 100) * C} ${C}`;
  const level = h.score >= 85 ? "ok" : h.score >= 65 ? "info" : h.score >= 40 ? "warning" : "critical";
  $(".health").dataset.level = level;
  $("#healthScore").textContent = h.score;
  $("#healthLabel").textContent = h.label;
  $("#heroSummary").textContent = h.summary + (a ? "" : " (đánh giá cơ bản trên trình duyệt)");

  const llm = backend.health?.llm;
  const zalo = backend.health?.zalo;
  const chips = [
    [state.lastDataAt && Date.now() - state.lastDataAt < 60000 ? "ok" : "warn", "radio", state.lastDataAt ? `Cảm biến · ${timeAgo(state.lastDataAt)}` : "Chưa có dữ liệu"],
    [backend.online ? "ok" : "off", "server", backend.online ? "Máy chủ AI" : "Máy chủ AI tắt"],
    [zalo?.configured ? "ok" : "off", "message-circle", zalo?.configured ? "Zalo đã kết nối" : "Zalo chưa cấu hình"],
    [llm?.enabled ? "ok" : "off", "sparkles", llm?.enabled ? `LLM · ${llm.model}` : "LLM chưa cấu hình"],
  ];
  setHTML($("#statusChips"), chips.map(([cls, ic, text]) => `<span class="chip ${cls}">${icon(ic)}${esc(text)}</span>`).join(""));
}

function renderDeviceStrip(data) {
  const items = DEVICES.map((d) => [d.name, d.icon, Number(data?.[d.key]) === 1]);
  const door = Number(data?.MO) === 1 ? "Mở" : Number(data?.DONG) === 1 ? "Đóng" : "—";
  setHTML($("#deviceStrip"), items.map(([name, ic, on]) => `
      <div class="dev ${on ? "on" : ""}">${icon(ic)}<span>${esc(name)}</span><b>${on ? "BẬT" : "TẮT"}</b></div>`).join("")
    + `<div class="dev ${door === "Mở" ? "on" : ""}">${icon("blinds")}<span>Màn che</span><b>${door.toUpperCase()}</b></div>`);
}

/* =========================
   ĐIỀU KHIỂN THIẾT BỊ
========================= */
function renderDeviceControls(data) {
  const auto = Number(data?.MODE) === MODE_AUTO;
  const disabled = auto || state.busy ? "disabled" : "";
  $("#controlHint").textContent = auto ? "Đang AUTO — chuyển MANUAL để điều khiển tay" : "Chế độ thủ công";

  const devicesHtml = DEVICES.map((d) => {
    const on = Number(data?.[d.key]) === 1;
    return `
      <div class="device-item ${on ? "on" : ""}">
        <span class="device-icon">${icon(d.icon)}</span>
        <div class="device-text">
          <div class="device-name">${esc(d.name)}</div>
          <small>${auto ? esc(d.rule) : on ? "Đang chạy" : "Đang tắt"}</small>
        </div>
        <button class="switch ${on ? "on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(d.name)}"
                data-device="${d.key}" ${disabled}><span></span></button>
      </div>`;
  }).join("");

  const mo = Number(data?.MO) === 1, dong = Number(data?.DONG) === 1;
  const doorHtml = `
    <div class="device-item ${mo ? "on" : ""}">
      <span class="device-icon">${icon("blinds")}</span>
      <div class="device-text">
        <div class="device-name">Màn che</div>
        <small>${auto ? "Mở khi ánh sáng ≤ ngưỡng, đóng khi sáng hơn" : mo ? "Đang mở" : dong ? "Đang đóng" : "Chưa chọn"}</small>
      </div>
      <div class="segmented">
        <button class="${mo ? "active" : ""}" data-door="MO" ${disabled}>Mở</button>
        <button class="${dong ? "active" : ""}" data-door="DONG" ${disabled}>Đóng</button>
      </div>
    </div>`;

  setHTML($("#deviceControls"), devicesHtml + doorHtml);
}

$("#deviceControls").addEventListener("click", async (ev) => {
  const sw = ev.target.closest("[data-device]");
  const door = ev.target.closest("[data-door]");
  if (!sw && !door) return;
  if (state.busy || Number(state.data?.MODE) === MODE_AUTO) return;
  state.busy = true;
  try {
    const updates = sw
      ? { [sw.dataset.device]: Number(state.data?.[sw.dataset.device]) === 1 ? 0 : 1 }
      : door.dataset.door === "MO" ? { MO: 1, DONG: 0 } : { MO: 0, DONG: 1 };
    renderDeviceControls({ ...state.data, ...updates });
    await update(ref(db, "/"), updates);
  } catch (error) {
    console.error("Lỗi điều khiển thiết bị:", error);
    toast("Không gửi được lệnh", "err");
  } finally {
    state.busy = false;
    renderDeviceControls(state.data);
  }
});

/* =========================
   CÀI ĐẶT NGƯỠNG
========================= */
function buildSettings() {
  const form = $("#settingControls");
  form.innerHTML = SETTINGS.map((s) => `
    <label class="setting-item">
      <span class="setting-text">
        <span class="setting-name">${esc(s.name)}</span>
        <small>${esc(s.hint)}</small>
      </span>
      <span class="input-unit">
        <input type="number" step="${s.step}" inputmode="decimal" id="input-${s.key}" />
        ${s.unit ? `<em>${esc(s.unit)}</em>` : ""}
      </span>
    </label>`).join("");
  form.addEventListener("focusin", () => (state.editingSettings = true));
  form.addEventListener("input", () => {
    state.editingSettings = true;
    $("#saveStatus").textContent = "Có thay đổi chưa lưu";
  });
}

function renderSettings(data) {
  if (state.editingSettings) return;
  for (const s of SETTINGS) {
    const input = document.getElementById(`input-${s.key}`);
    if (input && document.activeElement !== input) input.value = data?.[s.key] ?? "";
  }
}

$("#saveSettingsBtn").addEventListener("click", async () => {
  const updates = {};
  for (const s of SETTINGS) {
    const input = document.getElementById(`input-${s.key}`);
    updates[s.key] = input && input.value !== "" ? Number(input.value) : 0;
  }
  if (updates.SET_PHLOW && updates.SET_PHHIGH && updates.SET_PHLOW >= updates.SET_PHHIGH) {
    return toast("pH thấp nhất phải nhỏ hơn pH cao nhất", "err");
  }
  try {
    await update(ref(db, "/"), updates);
    state.editingSettings = false;
    $("#saveStatus").textContent = `Đã lưu lúc ${fmtClock(new Date())}`;
    toast("Đã lưu ngưỡng");
  } catch (error) {
    console.error("Lỗi lưu cài đặt:", error);
    toast("Lưu thất bại", "err");
  }
});

/* =========================
   BIỂU ĐỒ
========================= */
const rangeText = { live: "Dữ liệu trực tiếp từ lúc mở trang", "1h": "1 giờ qua", "6h": "6 giờ qua", "24h": "24 giờ qua", "7d": "7 ngày qua" };

async function renderChartTab() {
  if (!$("#bieudo").classList.contains("active")) return;
  const th = thresholds(state.data);
  const grid = $("#chartGrid");
  const note = $("#rangeNote");

  if (state.range === "live") {
    const labels = state.live.map((p) => fmtClock(p.ts));
    const series = Object.fromEntries(SENSORS.map((s) => [s.key, state.live.map((p) => p[s.key])]));
    note.textContent = `${rangeText.live} · ${state.live.length} điểm`;
    return renderCharts(labels, series, th);
  }

  if (!backend.online) {
    note.textContent = "Cần Máy chủ AI để xem lịch sử dài hạn";
    return renderCharts([], {}, th);
  }
  grid.classList.add("refetching");
  try {
    const hist = await fetchHistory(state.range);
    const long = ["24h", "7d"].includes(state.range);
    const labels = hist.points.map((p) => {
      const d = new Date(p.bucket * 1000);
      return long
        ? d.toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" })
        : d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    });
    const series = Object.fromEntries(SENSORS.map((s) => [s.key, hist.points.map((p) => p[s.key])]));
    note.textContent = `${rangeText[state.range]} · ${hist.points.length} điểm (gộp ${Math.round(hist.bucket_sec)} giây)`;
    renderCharts(labels, series, th);
  } catch (e) {
    note.textContent = "Không tải được lịch sử: " + e.message;
  } finally {
    grid.classList.remove("refetching");
  }
}

$$("[data-range]").forEach((btn) => btn.addEventListener("click", () => {
  $$("[data-range]").forEach((b) => b.classList.toggle("active", b === btn));
  state.range = btn.dataset.range;
  renderChartTab();
}));

/* =========================
   DỮ LIỆU REALTIME
========================= */
function pushLive(data) {
  const now = Date.now();
  const last = state.live[state.live.length - 1];
  if (last && now - last.ts < 2000) return; // tối đa 1 điểm / 2 giây
  const point = { ts: now };
  for (const s of SENSORS) point[s.key] = num(data[s.key]);
  state.live.push(point);
  if (state.live.length > LIVE_MAX_POINTS) state.live.shift();
}

function renderAll() {
  const data = state.data;
  if (!data) return;
  renderMode(data);
  renderHero(data);
  renderSensorTiles(data);
  renderDeviceStrip(data);
  renderDeviceControls(data);
  renderSettings(data);
  $("#lastUpdate").textContent = `Cập nhật lúc ${fmtClock(state.lastDataAt)} · ${new Date().toLocaleDateString("vi-VN", { weekday: "long", day: "2-digit", month: "2-digit" })}`;
}

onValue(ref(db, ".info/connected"), (snap) => {
  $("#connFirebase").classList.toggle("on", snap.val() === true);
});

onValue(ref(db, "/"), (snapshot) => {
  if (!snapshot.exists()) return;
  state.data = snapshot.val();
  state.lastDataAt = Date.now();
  pushLive(state.data);
  renderAll();
  if (state.range === "live") renderChartTab();
}, (error) => {
  console.error("Lỗi đọc Firebase:", error);
  toast("Mất kết nối Firebase", "err");
});

/* ESP gửi dữ liệu theo chu kỳ, Firebase chỉ báo khi giá trị đổi -> vẫn thêm điểm định kỳ */
setInterval(() => {
  if (!state.data) return;
  pushLive(state.data);
  if (state.range === "live") renderChartTab();
  renderSensorTiles(state.data);
  renderHero(state.data);
}, 5000);

/* =========================
   MÁY CHỦ AI
========================= */
async function seedLiveFromServer() {
  if (!backend.online || state.live.length > 10) return;
  try {
    const hist = await fetchHistory("15m");
    const seeded = hist.points.map((p) => {
      const point = { ts: p.bucket * 1000 };
      for (const s of SENSORS) point[s.key] = p[s.key];
      return point;
    });
    state.live = [...seeded, ...state.live].slice(-LIVE_MAX_POINTS);
  } catch { /* không sao */ }
}

async function syncBackend() {
  await refreshSnapshot();
  $("#connServer").classList.toggle("on", backend.online);
  renderAnalysis();
  renderZalo();
  if (state.data) {
    renderHero(state.data);
    renderSensorTiles(state.data);
  }
}

async function initBackend() {
  await connect();
  await seedLiveFromServer();
  await syncBackend();
  refreshAlerts();
}

document.addEventListener("backend-changed", initBackend);

setInterval(syncBackend, 5000);
setInterval(async () => { await refreshHealth(); renderZalo(); }, 15000);
setInterval(refreshAlerts, 20000);
setInterval(() => { if (state.range !== "live") renderChartTab(); }, 60000);

/* =========================
   KHỞI TẠO
========================= */
buildSensorTiles();
buildSettings();
buildChartCards();
bindAIControls();
let savedTheme = null;
try { savedTheme = localStorage.getItem("nhanam.theme"); } catch { /* bỏ qua */ }
applyTheme(savedTheme);
renderAnalysis();
renderEvents([]);
initBackend();
refreshIcons();
