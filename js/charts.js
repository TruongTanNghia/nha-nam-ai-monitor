// Biểu đồ Chart.js + đồng hồ cung tròn + sparkline (SVG)
import { SENSORS, SENSOR_BY_KEY, fmtWithUnit, num } from "./sensors.js";
import { $, cssVar, esc, refreshIcons } from "./ui.js";

/* ---------------- Đồng hồ cung tròn ---------------- */
const R = 44, CX = 60, CY = 58, ARC_LEN = Math.PI * R;

export function gaugeSVG() {
  return `
    <svg viewBox="0 0 120 66" class="gauge-svg" aria-hidden="true">
      <path d="M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}" class="g-track" pathLength="${ARC_LEN}"/>
      <path d="M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}" class="g-value" pathLength="${ARC_LEN}"
            stroke-dasharray="0 ${ARC_LEN}"/>
      <g class="g-ticks"></g>
    </svg>`;
}

function arcPoint(frac, radius) {
  const a = Math.PI * (1 - frac);
  return [CX + radius * Math.cos(a), CY - radius * Math.sin(a)];
}

/** Cập nhật đồng hồ: value, giới hạn thang đo, danh sách ngưỡng (vạch). */
export function updateGauge(svg, value, min, max, marks = []) {
  if (!svg) return;
  const clamp = (v) => Math.min(1, Math.max(0, (v - min) / (max - min || 1)));
  const frac = value === null ? 0 : clamp(value);
  svg.querySelector(".g-value").setAttribute("stroke-dasharray", `${(frac * ARC_LEN).toFixed(1)} ${ARC_LEN}`);
  svg.querySelector(".g-ticks").innerHTML = marks
    .filter((m) => m !== null && m !== undefined)
    .map((m) => {
      const f = clamp(m);
      const [x1, y1] = arcPoint(f, R - 8);
      const [x2, y2] = arcPoint(f, R + 8);
      return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`;
    })
    .join("");
}

/* ---------------- Sparkline ---------------- */
export function sparkline(values, w = 120, h = 30) {
  const pts = values.filter((v) => v !== null && v !== undefined);
  if (pts.length < 2) return `<svg viewBox="0 0 ${w} ${h}" class="spark"></svg>`;
  const lo = Math.min(...pts), hi = Math.max(...pts);
  const span = hi - lo || 1;
  const step = w / (pts.length - 1);
  const d = pts.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)} ${(h - 3 - ((v - lo) / span) * (h - 6)).toFixed(1)}`).join(" ");
  const last = d.split(" ").slice(-2);
  return `<svg viewBox="0 0 ${w} ${h}" class="spark" preserveAspectRatio="none">
    <path d="${d}" vector-effect="non-scaling-stroke"/>
    <circle cx="${last[0].replace(/[ML]/, "")}" cy="${last[1]}" r="2.5"/>
  </svg>`;
}

/* ---------------- Biểu đồ đường ---------------- */
const charts = {};

const crosshair = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements?.() || [];
    if (!active.length) return;
    const x = active[0].element.x;
    const { top, bottom } = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    ctx.strokeStyle = cssVar("--ink-3");
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.restore();
  },
};

export function buildChartCards() {
  const grid = $("#chartGrid");
  grid.innerHTML = SENSORS.map((s) => `
    <div class="card chart-card">
      <div class="chart-head">
        <div>
          <h3><span class="swatch" style="background:var(${s.color})"></span>${esc(s.name)}${s.unit ? ` <small>(${esc(s.unit)})</small>` : ""}</h3>
        </div>
        <dl class="chart-stats" id="stats-${s.key}"></dl>
      </div>
      <div class="chart-wrap"><canvas id="chart-${s.key}" aria-label="Biểu đồ ${esc(s.name)}"></canvas></div>
    </div>`).join("");
  refreshIcons(grid);
}

function baseOptions() {
  const ink2 = cssVar("--ink-2"), ink3 = cssVar("--ink-3"), grid = cssVar("--grid");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: cssVar("--tooltip-bg"),
        titleColor: cssVar("--tooltip-ink-2"),
        bodyColor: cssVar("--tooltip-ink"),
        borderColor: cssVar("--line"),
        borderWidth: 1,
        padding: 10,
        displayColors: true,
        boxWidth: 10,
        boxHeight: 2,
        titleFont: { family: "Be Vietnam Pro", size: 11, weight: "500" },
        bodyFont: { family: "JetBrains Mono", size: 12, weight: "700" },
      },
    },
    scales: {
      x: {
        ticks: { color: ink3, maxRotation: 0, autoSkip: true, maxTicksLimit: 6, font: { family: "JetBrains Mono", size: 10 } },
        grid: { display: false },
        border: { color: grid },
      },
      y: {
        ticks: { color: ink2, maxTicksLimit: 5, font: { family: "JetBrains Mono", size: 10 } },
        grid: { color: grid },
        border: { display: false },
      },
    },
  };
}

function thresholdLines(key, th) {
  if (key === "GT_PH") return [["pH thấp nhất", th.SET_PHLOW], ["pH cao nhất", th.SET_PHHIGH]];
  const set = SENSOR_BY_KEY[key].set;
  return [["Ngưỡng", th[set]]];
}

/** labels: string[]; series: {key: number[]}; th: ngưỡng */
export function renderCharts(labels, series, th) {
  const ink3 = cssVar("--ink-3");
  for (const s of SENSORS) {
    const canvas = document.getElementById(`chart-${s.key}`);
    if (!canvas) continue;
    const values = series[s.key] || [];
    const color = cssVar(s.color);
    const datasets = [{
      label: s.name,
      data: values,
      borderColor: color,
      backgroundColor: color + "1f",
      fill: "origin",
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      pointHoverBorderWidth: 2,
      pointHoverBorderColor: cssVar("--surface"),
      pointHoverBackgroundColor: color,
      tension: 0.3,
      spanGaps: true,
    }];
    for (const [label, v] of thresholdLines(s.key, th)) {
      if (!v) continue;
      datasets.push({
        label, data: values.map(() => v), borderColor: ink3, borderDash: [5, 4],
        borderWidth: 1.25, pointRadius: 0, pointHoverRadius: 0, fill: false,
      });
    }

    let chart = charts[s.key];
    if (!chart) {
      chart = charts[s.key] = new Chart(canvas, {
        type: "line",
        data: { labels, datasets },
        options: baseOptions(),
        plugins: [crosshair],
      });
      chart.options.plugins.tooltip.callbacks = {
        label: (ctx) => ` ${ctx.dataset.label}: ${fmtWithUnit(s.key, num(ctx.parsed.y))}`,
      };
    } else {
      chart.data.labels = labels;
      chart.data.datasets = datasets;
      chart.update("none");
    }

    const valid = values.filter((v) => v !== null && v !== undefined);
    const stats = document.getElementById(`stats-${s.key}`);
    if (stats) {
      stats.innerHTML = valid.length
        ? [["Thấp", Math.min(...valid)], ["TB", valid.reduce((a, b) => a + b, 0) / valid.length], ["Cao", Math.max(...valid)]]
            .map(([k, v]) => `<div><dt>${k}</dt><dd>${fmtWithUnit(s.key, v)}</dd></div>`).join("")
        : `<div><dt>Chưa có dữ liệu</dt></div>`;
    }
  }
}

/** Đổi giao diện sáng/tối: dựng lại biểu đồ để lấy màu mới. */
export function resetCharts() {
  for (const k of Object.keys(charts)) {
    charts[k].destroy();
    delete charts[k];
  }
}
