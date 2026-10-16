import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getDatabase,
  ref,
  update,
  get,
  onValue,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-database.js";

import {
  DEVICES, LEVEL_ICON, LEVEL_TEXT, SENSORS, SETTINGS, WATER_KEYS,
  fmtValue, localIndices, num, sensorStatus, thresholdText, thresholds,
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
  firstDataAt: null,
  sensorChangedAt: null,  // lần cuối số liệu cảm biến thay đổi (ESP32 còn gửi)
  sensorSig: null,
  serverTimeOffset: 0,
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

/* Firmware cũ ghi lại SET_* và MODE lên Firebase mỗi 0.7 giây nên đôi khi đè mất giá trị vừa lưu.
   Ghi xong chờ 2.5 giây rồi đọc lại; bị đè thì ghi lại (tối đa 3 lần). */
async function writeVerified(updates, onRetry) {
  for (let attempt = 1; attempt <= 4; attempt++) {
    await update(ref(db, "/"), updates);
    await new Promise((resolve) => setTimeout(resolve, 2500));
    const current = (await get(ref(db, "/"))).val() || {};
    const lost = Object.keys(updates).filter((k) => Number(current[k]) !== Number(updates[k]));
    if (!lost.length) return { ok: true, retries: attempt - 1 };
    onRetry?.(attempt, lost);
  }
  return { ok: false, retries: 3 };
}

const FIRMWARE_HINT = "ESP32 đang ghi đè — nên nạp firmware mới (không ghi SET_* và MODE)";

async function setMode(mode) {
  try {
    toast("Đang đổi chế độ…");
    const r = await writeVerified({ MODE: Number(mode) });
    const name = mode === MODE_AUTO ? "AUTO" : "MANUAL";
    if (!r.ok) toast(`Không đổi được sang ${name}: ${FIRMWARE_HINT}`, "err");
    else toast(r.retries ? `Đã chuyển sang ${name} (ESP32 ghi đè ${r.retries} lần, đã ghi lại)` : `Đã chuyển sang ${name}`);
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
function tileHTML(s, i) {
  return `
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
    </article>`;
}

function buildSensorTiles() {
  // Hai nhóm tách riêng: môi trường nhà nấm và chất lượng nước cấp
  $("#sensorGridEnv").innerHTML = SENSORS.filter((s) => !WATER_KEYS.includes(s.key)).map(tileHTML).join("");
  $("#sensorGridWater").innerHTML = SENSORS.filter((s) => WATER_KEYS.includes(s.key)).map(tileHTML).join("");
  refreshIcons($("#tongquan"));
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
const LEVEL_BY_LABEL = { "Đạt": "ok", "Cần theo dõi": "warning", "Không đạt": "critical", "Mất kết nối": "off" };

function drawIndex(sel, score, label, level) {
  const el = $(sel);
  const C = 2 * Math.PI * 52;
  const ring = el.querySelector(".ring-value");
  ring.style.strokeDasharray = `${((score ?? 0) / 100) * C} ${C}`;
  ring.style.opacity = score === null ? 0 : 1;  // không có điểm: ẩn cả chấm đầu nét
  el.dataset.level = level;
  el.querySelector("b").textContent = score ?? "—";
  el.querySelector(".health-center span").textContent = label;
}

const scoreLevel = (score) => (score === null ? "off" : score >= 85 ? "ok" : score >= 65 ? "info" : score >= 40 ? "warning" : "critical");

function renderHero(data) {
  const a = backend.online ? backend.snapshot?.analysis : null;
  let idx = a?.indices ?? localIndices(data);
  if (backend.hardwareOffline) {  // ESP32 mất kết nối: không chấm điểm trên số liệu cũ
    const lost = { score: null, label: "Mất kết nối", issues: [] };
    idx = { environment: lost, water: { ...lost, locks: [], lock_text: null } };
  }
  const env = idx.environment, water = idx.water;
  drawIndex("#idxEnv", env.score, env.label, scoreLevel(env.score));
  drawIndex("#idxWater", water.score, water.label, LEVEL_BY_LABEL[water.label] || "info");
  $("#envBadge").textContent = env.score === null ? env.label : `${env.score}/100 · ${env.label}`;
  $("#envBadge").dataset.level = scoreLevel(env.score);
  $("#waterBadge").textContent = water.label;
  $("#waterBadge").dataset.level = LEVEL_BY_LABEL[water.label] || "info";

  // Nước không đạt: cảnh báo riêng, không bị "trung bình hóa" với môi trường
  const banner = $("#waterBanner");
  banner.hidden = !water.lock_text;
  if (water.lock_text) setHTML(banner, `${icon("octagon-alert")}<div><b>${esc(water.lock_text)}</b></div>`);

  let summary = a?.summary;
  if (!summary) {
    const envIssues = env.issues.length ? `Môi trường: ${env.issues.slice(0, 2).join("; ")}.` : "Môi trường trong ngưỡng an toàn.";
    summary = (water.lock_text ? water.lock_text + " " : water.issues.length ? `Nước cấp: ${water.issues.join("; ")}. ` : "") + envIssues;
  }
  $("#heroSummary").textContent = summary + (a ? "" : " (đánh giá cơ bản trên trình duyệt)");

  const llm = backend.health?.llm;
  const zalo = backend.health?.zalo;
  const chips = [
    (() => { const hw = renderHardware(); return [hw.level === "ok" ? "ok" : hw.level === "off" ? "bad" : "warn", "cpu", hw.text]; })(),
    [backend.online ? "ok" : "off", "server", backend.online ? "Máy chủ AI" : "Máy chủ AI tắt"],
    [zalo?.configured ? "ok" : "off", "message-circle", zalo?.configured ? "Zalo đã kết nối" : "Zalo chưa cấu hình"],
    [llm?.enabled ? "ok" : "off", "sparkles", llm?.enabled ? `LLM · ${llm.model}` : "LLM chưa cấu hình"],
  ];
  setHTML($("#statusChips"), chips.map(([cls, ic, text]) => `<span class="chip ${cls}">${icon(ic)}${esc(text)}</span>`).join(""));
}

function renderDeviceStrip(data) {
  const early = backend.online ? backend.snapshot?.predictive?.active || {} : {};
  const items = DEVICES.map((d) => [d.name, d.icon, Number(data?.[d.key]) === 1, d.key in early]);
  const door = Number(data?.MO) === 1 ? "Mở" : Number(data?.DONG) === 1 ? "Đóng" : "—";
  setHTML($("#deviceStrip"), items.map(([name, ic, on, pre]) => `
      <div class="dev ${on ? "on" : ""} ${pre ? "pre" : ""}" ${pre ? 'title="Đang bật sớm theo dự báo AI"' : ""}>
        ${pre ? '<em class="pre-tag">sớm</em>' : ""}${icon(ic)}<span>${esc(name)}</span><b>${on ? "BẬT" : "TẮT"}</b></div>`).join("")
    + `<div class="dev ${door === "Mở" ? "on" : ""} ${"MO" in early ? "pre" : ""}">${"MO" in early ? '<em class="pre-tag">sớm</em>' : ""}`
    + `${icon("blinds")}<span>Màn che</span><b>${door.toUpperCase()}</b></div>`);
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
   BẬT SỚM THEO DỰ BÁO
   Máy chủ AI ghi yêu cầu PRE; firmware ESP32 cộng vào luật ngưỡng (AUTO). Tắt/bật bằng AI_PREDICT.
========================= */
function renderPredict(data) {
  const on = Number(data?.AI_PREDICT ?? 1) !== 0;
  const sw = $("#predictSwitch");
  sw.classList.toggle("on", on);
  sw.setAttribute("aria-checked", on);
  $("#predictCard").classList.toggle("on", on);

  const p = backend.online ? backend.snapshot?.predictive : null;
  const notes = [];
  if (!backend.online) notes.push(["warn", "server-off", "Cần Máy chủ AI chạy thì mới dự báo và bật sớm được."]);
  else if (p) {
    if (p.firmware_ok === false) notes.push(["warn", "cpu", "Firmware trên bo chưa hỗ trợ bật sớm — nạp bản aduno.ino mới (có PRE)."]);
    if (on && p.reason_off) notes.push(["muted", "pause", `Đang tạm dừng: ${p.reason_off}.`]);
    if (on && !p.reason_off) notes.push(["ok", "radar", `Đang theo dõi: bật trước khi chạm ngưỡng ${p.lead_min} phút.`]);
    for (const [, a] of Object.entries(p.active || {})) {
      notes.push(["pre", "zap", `Đang bật sớm ${a.name}: ${a.reason}`]);
    }
  }
  setHTML($("#predictBody"), notes.map(([cls, ic, text]) =>
    `<div class="predict-note ${cls}">${icon(ic)}<span>${esc(text)}</span></div>`).join(""));
}

$("#predictSwitch").addEventListener("click", async () => {
  const next = Number(state.data?.AI_PREDICT ?? 1) !== 0 ? 0 : 1;
  try {
    await update(ref(db, "/"), { AI_PREDICT: next });
    toast(next ? "Đã bật: bật sớm theo dự báo" : "Đã tắt bật sớm theo dự báo");
  } catch (error) {
    console.error("Lỗi đổi AI_PREDICT:", error);
    toast("Không đổi được", "err");
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
  const btn = $("#saveSettingsBtn");
  const status = $("#saveStatus");
  btn.disabled = true;
  status.textContent = "Đang lưu và kiểm tra…";
  try {
    // Giữ nguyên ô nhập trong lúc kiểm tra (không để giá trị bị đè hiện lên ô nhập)
    const r = await writeVerified(updates, (attempt) => {
      status.textContent = `ESP32 vừa ghi đè giá trị cũ — đang lưu lại (lần ${attempt})…`;
    });
    if (r.ok) {
      state.editingSettings = false;
      status.textContent = `Đã lưu lúc ${fmtClock(new Date())}` + (r.retries ? ` (ghi lại ${r.retries} lần do ESP32 ghi đè)` : "");
      toast("Đã lưu ngưỡng");
    } else {
      status.textContent = `Chưa lưu được: ${FIRMWARE_HINT}`;
      toast("Chưa lưu được — ESP32 liên tục ghi đè", "err");
    }
  } catch (error) {
    console.error("Lỗi lưu cài đặt:", error);
    status.textContent = "Lưu thất bại";
    toast("Lưu thất bại", "err");
  } finally {
    btn.disabled = false;
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
  renderPredict(data);
  renderSettings(data);
  $("#lastUpdate").textContent = `Cập nhật lúc ${fmtClock(state.lastDataAt)} · ${new Date().toLocaleDateString("vi-VN", { weekday: "long", day: "2-digit", month: "2-digit" })}`;
}

/* =========================
   KẾT NỐI PHẦN CỨNG (ESP32)
   - Có ESP_TS (firmware gửi nhịp tim, Firebase đóng dấu giờ): đo chính xác.
   - Không có: dựa vào lần cuối số liệu cảm biến thay đổi (pH, ánh sáng là analog nên luôn dao động).
========================= */
function hardwareStatus() {
  const data = state.data;
  if (!data) return { level: "wait", text: "Đang kết nối Firebase…" };
  const now = Date.now();
  const espTs = Number(data.ESP_TS);
  let age, exact = false;
  if (espTs > 0) {
    age = (now + state.serverTimeOffset - espTs) / 1000;
    exact = true;
  } else {
    const serverChange = backend.snapshot?.hardware?.last_change_ts;
    const seen = [state.sensorChangedAt, serverChange ? serverChange * 1000 : null].filter(Boolean);
    if (seen.length) age = (now - Math.max(...seen)) / 1000;
    else if (now - state.firstDataAt < 60000) return { level: "wait", text: "Đang kiểm tra ESP32…" };
    else age = (now - state.firstDataAt) / 1000;
  }
  const [okSec, offSec] = exact ? [20, 120] : [60, 300];
  const ago = timeAgo(now - Math.max(0, age) * 1000);
  if (age < okSec) return { level: "ok", age, text: `ESP32 đã kết nối · ${ago}` };
  if (age < offSec) return { level: "warn", age, text: `ESP32 phản hồi chậm · ${ago}` };
  return { level: "off", age, text: `ESP32 mất kết nối · lần cuối ${ago}` };
}

function renderHardware() {
  const hw = hardwareStatus();
  backend.hardwareOffline = hw.level === "off";
  const conn = $("#connEsp");
  conn.classList.toggle("on", hw.level === "ok");
  conn.classList.toggle("warn", hw.level === "warn" || hw.level === "wait");
  conn.title = hw.text;
  const m = $("#mEsp");  // thanh trên cùng (điện thoại)
  m.className = `m-status ${hw.level}`;
  m.lastChild.textContent = hw.level === "ok" ? "ESP32 online" : hw.level === "off" ? "ESP32 mất kết nối" : "ESP32…";
  const banner = $("#hwBanner");
  banner.hidden = hw.level !== "off";
  if (hw.level === "off") {
    setHTML(banner, `${icon("unplug")}<div><b>${esc(hw.text)}.</b> Số liệu trên trang có thể đã cũ. ` +
      `Kiểm tra nguồn điện và WiFi của bo mạch ESP32, rồi nhấn nút reset trên bo.</div>`);
  }
  return hw;
}

onValue(ref(db, ".info/serverTimeOffset"), (snap) => { state.serverTimeOffset = snap.val() || 0; });

onValue(ref(db, ".info/connected"), (snap) => {
  $("#connFirebase").classList.toggle("on", snap.val() === true);
});

onValue(ref(db, "/"), (snapshot) => {
  if (!snapshot.exists()) return;
  state.data = snapshot.val();
  state.lastDataAt = Date.now();
  state.firstDataAt ??= state.lastDataAt;
  const sig = SENSORS.map((s) => state.data[s.key]).join("|");
  if (state.sensorSig !== null && sig !== state.sensorSig) state.sensorChangedAt = state.lastDataAt;
  state.sensorSig = sig;
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
    renderDeviceStrip(state.data);
    renderPredict(state.data);
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
