// Mô tả cảm biến / thiết bị / ngưỡng + đánh giá trạng thái (giống server/analyzer.py)

export const SENSORS = [
  { key: "t", name: "Nhiệt độ", unit: "°C", icon: "thermometer", min: 0, max: 60, digits: 1, set: "SET_T", color: "--s-temp" },
  { key: "h", name: "Độ ẩm không khí", unit: "%", icon: "droplets", min: 0, max: 100, digits: 0, set: "SET_H", color: "--s-humi" },
  { key: "ANHSANG", name: "Ánh sáng", unit: "lux", icon: "sun", min: 0, max: 15000, digits: 0, set: "SET_AS", color: "--s-light" },
  { key: "GT_AMDAT", name: "Độ ẩm đất", unit: "%", icon: "sprout", min: 0, max: 100, digits: 0, set: "SET_AMDAT", color: "--s-soil" },
  { key: "GT_DOMAN", name: "Độ mặn", unit: "ppm", icon: "waves", min: 0, max: 1000, digits: 0, set: "SET_DOMAN", color: "--s-salt" },
  { key: "GT_PH", name: "pH", unit: "", icon: "flask-conical", min: 0, max: 14, digits: 2, set: null, color: "--s-ph" },
];
export const SENSOR_BY_KEY = Object.fromEntries(SENSORS.map((s) => [s.key, s]));

export const DEVICES = [
  { key: "BOM", name: "Máy bơm", icon: "droplet", rule: "Bật khi ẩm đất < ngưỡng (ngắt nếu mặn cao / pH lệch)" },
  { key: "DEN", name: "Đèn", icon: "lightbulb", rule: "Bật khi ánh sáng < ngưỡng" },
  { key: "QUAT", name: "Quạt", icon: "fan", rule: "Bật khi nhiệt độ > ngưỡng" },
  { key: "SUONG", name: "Phun sương", icon: "cloud-drizzle", rule: "Bật khi độ ẩm KK < ngưỡng (ngắt nếu pH lệch)" },
];

export const SETTINGS = [
  { key: "SET_T", name: "Nhiệt độ tối đa", unit: "°C", hint: "Quạt bật khi nhiệt độ vượt ngưỡng", step: 0.5 },
  { key: "SET_H", name: "Độ ẩm không khí tối thiểu", unit: "%", hint: "Phun sương bật khi thấp hơn", step: 1 },
  { key: "SET_AS", name: "Ánh sáng tối thiểu", unit: "lux", hint: "Thấp hơn: bật đèn + mở màn che", step: 50 },
  { key: "SET_AMDAT", name: "Độ ẩm đất tối thiểu", unit: "%", hint: "Máy bơm bật khi thấp hơn", step: 1 },
  { key: "SET_DOMAN", name: "Độ mặn tối đa", unit: "ppm", hint: "Ngắt máy bơm khi vượt", step: 10 },
  { key: "SET_PHLOW", name: "pH thấp nhất", unit: "", hint: "Ngắt bơm & phun sương khi pH thấp hơn", step: 0.1 },
  { key: "SET_PHHIGH", name: "pH cao nhất", unit: "", hint: "Ngắt bơm & phun sương khi pH cao hơn", step: 0.1 },
];

export const LEVEL_TEXT = { ok: "Ổn định", info: "Theo dõi", warning: "Cảnh báo", critical: "Nguy hiểm", fault: "Lỗi cảm biến" };
export const LEVEL_ICON = { ok: "circle-check", info: "info", warning: "triangle-alert", critical: "octagon-alert", fault: "wifi-off" };
export const SEV_RANK = { ok: 0, info: 1, warning: 2, critical: 3, fault: 3 };

const PHYS = { t: [-10, 80], h: [0, 100], ANHSANG: [0, 70000], GT_AMDAT: [0, 100], GT_DOMAN: [0, 5000], GT_PH: [0, 14] };

export function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function thresholds(data) {
  const out = {};
  for (const s of SETTINGS) {
    const v = num(data?.[s.key]);
    out[s.key] = v ? v : null; // 0 hoặc thiếu = chưa cài
  }
  return out;
}

export function fmtValue(key, value) {
  const meta = SENSOR_BY_KEY[key];
  if (value === null || value === undefined) return "—";
  return meta.digits ? value.toFixed(meta.digits) : Math.round(value).toLocaleString("vi-VN");
}

export function fmtWithUnit(key, value) {
  const unit = SENSOR_BY_KEY[key].unit;
  const v = fmtValue(key, value);
  if (!unit || v === "—") return v;
  return unit === "°C" || unit === "%" ? `${v}${unit}` : `${v} ${unit}`;
}

/** Đánh giá 1 giá trị cảm biến -> { level, text } */
export function sensorStatus(key, v, th) {
  if (v === null) return { level: "fault", text: "Không có dữ liệu" };
  const [lo, hi] = PHYS[key];
  if (v < lo || v > hi) return { level: "fault", text: "Giá trị phi lý" };

  switch (key) {
    case "t": {
      const s = th.SET_T;
      if (v >= 40 || (s && v >= s + 6)) return { level: "critical", text: "Quá nóng" };
      if (v < 10) return { level: "critical", text: "Quá lạnh" };
      if ((s && v > s + 3) || v > 36) return { level: "warning", text: "Nhiệt độ cao" };
      if (v < 15) return { level: "warning", text: "Nhiệt độ thấp" };
      if (s && v > s) return { level: "info", text: "Trên ngưỡng · quạt xử lý" };
      return { level: "ok", text: "Ổn định" };
    }
    case "h": {
      const s = th.SET_H;
      if (s && v < s - 25) return { level: "critical", text: "Quá khô" };
      if (s && v < s - 10) return { level: "warning", text: "Không khí khô" };
      if (s && v < s) return { level: "info", text: "Dưới ngưỡng · phun sương" };
      return { level: "ok", text: "Ổn định" };
    }
    case "GT_AMDAT": {
      const s = th.SET_AMDAT;
      if (v < 10 || (s && v < s - 25)) return { level: "critical", text: "Đất quá khô" };
      if (s && v < s - 10) return { level: "warning", text: "Đất khô" };
      if (s && v < s) return { level: "info", text: "Dưới ngưỡng · bơm xử lý" };
      return { level: "ok", text: "Ổn định" };
    }
    case "GT_DOMAN": {
      const s = th.SET_DOMAN;
      if (s && v > s * 1.3) return { level: "critical", text: "Rất mặn" };
      if (s && v > s) return { level: "warning", text: "Vượt ngưỡng mặn" };
      return { level: "ok", text: "Ổn định" };
    }
    case "GT_PH": {
      const low = th.SET_PHLOW, high = th.SET_PHHIGH;
      if (v < 4 || v > 9.5 || (low && v < low - 1) || (high && v > high + 1)) return { level: "critical", text: "pH nguy hiểm" };
      if (low && v < low) return { level: "warning", text: "Chua (pH thấp)" };
      if (high && v > high) return { level: "warning", text: "Kiềm (pH cao)" };
      return { level: "ok", text: "Ổn định" };
    }
    case "ANHSANG": {
      const s = th.SET_AS;
      if (s && v < s) return { level: "info", text: "Thiếu sáng · đèn hỗ trợ" };
      return { level: "ok", text: "Đủ sáng" };
    }
  }
  return { level: "ok", text: "Ổn định" };
}

/** Chuỗi mô tả ngưỡng cho thẻ cảm biến */
export function thresholdText(key, th) {
  if (key === "GT_PH") {
    if (!th.SET_PHLOW && !th.SET_PHHIGH) return "Chưa cài ngưỡng";
    return `Khoảng ${th.SET_PHLOW ?? "?"} – ${th.SET_PHHIGH ?? "?"}`;
  }
  const set = SENSOR_BY_KEY[key].set;
  const v = th[set];
  if (!v) return "Chưa cài ngưỡng";
  const dir = key === "t" || key === "GT_DOMAN" ? "Tối đa" : "Tối thiểu";
  return `${dir} ${fmtWithUnit(key, v)}`;
}

/* ===== Hai chỉ số tách riêng (giống server/indices.py) ===== */
export const WATER_KEYS = ["GT_PH", "GT_DOMAN"];

/** Thiết bị firmware đang khóa vì nước: pH ngoài khoảng -> bơm + phun sương; mặn vượt ngưỡng -> bơm */
export function waterLocks(data, th) {
  const locks = [];
  const ph = num(data?.GT_PH), sal = num(data?.GT_DOMAN);
  if (ph !== null) {
    if (!th.SET_PHLOW || !th.SET_PHHIGH) {
      locks.push({ devices: ["máy bơm tưới", "phun sương"], reason: "chưa cài đủ ngưỡng pH nên ESP32 coi pH là lệch" });
    } else if (ph < th.SET_PHLOW || ph > th.SET_PHHIGH) {
      locks.push({ devices: ["máy bơm tưới", "phun sương"], reason: `pH ${fmtValue("GT_PH", ph)} ngoài khoảng ${th.SET_PHLOW}–${th.SET_PHHIGH}` });
    }
  }
  if (sal !== null && th.SET_DOMAN && sal > th.SET_DOMAN) {
    locks.push({ devices: ["máy bơm tưới"], reason: `độ mặn ${fmtWithUnit("GT_DOMAN", sal)} vượt ${fmtWithUnit("GT_DOMAN", th.SET_DOMAN)}` });
  }
  return locks;
}

export function lockText(locks) {
  const devices = [...new Set(locks.flatMap((l) => l.devices))];
  if (!devices.length) return null;
  return `Nước cấp không đạt — ${devices.join(" và ")} đang bị khóa (${locks.map((l) => l.reason).join("; ")}). ` +
    "Kiểm tra nguồn nước, lọc nước hoặc thay nước.";
}

/** Đánh giá cơ bản trên trình duyệt khi không có máy chủ AI */
export function localIndices(data) {
  const th = thresholds(data);
  const pen = { env: 0, water: 0 };
  const issues = { env: [], water: [] };
  for (const s of SENSORS) {
    const st = sensorStatus(s.key, num(data?.[s.key]), th);
    const g = WATER_KEYS.includes(s.key) ? "water" : "env";
    if (SEV_RANK[st.level] >= 2) {
      pen[g] += st.level === "warning" ? 12 : 25;
      issues[g].push(`${s.name}: ${st.text.toLowerCase()}`);
    }
  }
  const locks = waterLocks(data, th);
  const env = Math.max(0, 100 - pen.env);
  let water = Math.max(0, 100 - pen.water);
  if (locks.length) water = Math.min(water, 35);
  return {
    environment: { score: env, label: env >= 85 ? "Tốt" : env >= 65 ? "Khá" : env >= 40 ? "Cần chú ý" : "Nguy hiểm", issues: issues.env },
    water: {
      score: water, label: locks.length || water < 50 ? "Không đạt" : water < 85 ? "Cần theo dõi" : "Đạt",
      issues: issues.water, locks, lock_text: lockText(locks),
    },
  };
}
