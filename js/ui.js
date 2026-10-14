// Tiện ích giao diện dùng chung

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export function refreshIcons(root) {
  if (window.lucide) window.lucide.createIcons({ root: root || document });
}

export function icon(name, cls = "") {
  return `<i data-lucide="${name}" class="${cls}"></i>`;
}

let toastTimer;
export function toast(message, type = "ok") {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = `toast show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

export function fmtClock(ts) {
  const d = ts instanceof Date ? ts : new Date(ts);
  return d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function fmtDateTime(ts) {
  const d = new Date(ts);
  return d.toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}

export function timeAgo(ts) {
  const sec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (sec < 10) return "vừa xong";
  if (sec < 60) return `${sec} giây trước`;
  if (sec < 3600) return `${Math.floor(sec / 60)} phút trước`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} giờ trước`;
  return `${Math.floor(sec / 86400)} ngày trước`;
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Chỉ cập nhật innerHTML khi nội dung thay đổi (tránh nháy icon). */
export function setHTML(el, html) {
  if (!el || el._html === html) return false;
  el._html = html;
  el.innerHTML = html;
  refreshIcons(el);
  return true;
}
