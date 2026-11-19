// ── Helpers DOM ──
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "checked" || k === "disabled" || k === "selected") node[k] = !!v;
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

// ── Toasts ──
import { animToast } from "./anim.js";

export function toast(msg, type = "info", ms = 4200) {
  const colors = { info: "border-accent-500/50", ok: "border-green-500/60", err: "border-red-500/70" };
  const icons = { info: "ℹ️", ok: "✅", err: "⛔" };
  const t = el("div", {
    class: `pointer-events-auto px-3 py-2 rounded-lg border bg-[#222] text-[13px] shadow-xl ${colors[type] || colors.info}`,
    role: "status",
    html: `<span class="mr-1" aria-hidden="true">${icons[type] || "ℹ️"}</span>${escapeHtml(msg)}`,
  });
  $("#toast-root").append(t);
  animToast(t); // anime.js: desliza desde la derecha
  const close = () => { t.style.transition = "opacity .3s, transform .3s"; t.style.opacity = "0"; t.style.transform = "translateY(-6px)"; setTimeout(() => t.remove(), 320); };
  setTimeout(close, ms);
  t.addEventListener("click", close);
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ── Modales ──
export function openModal(html, { width = "max-w-2xl", onClose } = {}) {
  const backdrop = $("#modal-backdrop");
  const root = $("#modal-root");
  const returnFocus = document.activeElement;
  const modal = el("div", { class: `fixed inset-0 z-[60] flex items-center justify-center p-3` });
  modal.innerHTML = `
    <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" data-close></div>
    <div class="modal-panel relative w-full ${width} bg-[#222] border border-edge rounded-xl overflow-y-auto">
      ${html}
    </div>`;
  const panel = $(".modal-panel", modal);
  const heading = $("h1, h2, h3", panel);
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-label", heading?.textContent?.trim() || "Diálogo");
  if (!panel.hasAttribute("tabindex")) panel.setAttribute("tabindex", "-1");
  const focusable = () => $$('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])', panel)
    .filter(node => node.getClientRects().length > 0);
  const onBackdropClick = (e) => { if (e.target.hasAttribute("data-close")) close(); };
  const onModalKeydown = (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key !== "Tab") return;
    const nodes = focusable();
    if (!nodes.length) { e.preventDefault(); panel.focus(); return; }
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (!nodes.includes(document.activeElement)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    } else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  const close = () => {
    modal.removeEventListener("click", onBackdropClick);
    modal.removeEventListener("keydown", onModalKeydown);
    modal.remove();
    backdrop.classList.add("hidden");
    if (returnFocus?.isConnected) returnFocus.focus();
    if (onClose) onClose();
  };
  modal.addEventListener("click", onBackdropClick);
  modal.addEventListener("keydown", onModalKeydown);
  $$("[data-close-modal]", modal).forEach(b => b.addEventListener("click", close));
  backdrop.classList.remove("hidden");
  root.append(modal);
  (focusable().find(node => node.hasAttribute("autofocus")) || panel).focus();
  return { close, modal };
}

export function confirmModal(msg, { okText = "Confirmar", danger = false } = {}) {
  return new Promise((resolve) => {
    const { close } = openModal(`
      <div class="p-5 space-y-4">
        <div class="text-sm text-txt">${msg}</div>
        <div class="flex gap-2 justify-end">
          <button class="btn-secondary" data-close-modal>Cancelar</button>
          <button class="${danger ? "bg-red-600 hover:bg-red-500 text-white font-medium px-4 py-2 rounded-lg" : "btn-primary"}" id="confirm-ok">${okText}</button>
        </div>
      </div>`, { width: "max-w-md" });
    $("#confirm-ok").addEventListener("click", () => { close(); resolve(true); });
    $$("[data-close-modal]", document).slice(-1)[0]?.addEventListener("click", () => resolve(false));
  });
}

// ── Accordion (estilo A1111) — devuelve un elemento real ──
// body puede ser un string (HTML) o un elemento DOM (se adjunta tal cual,
// preservando sus listeners).
export function accordionHTML({ title, icon = "", open = false, id, body, badge = "" }) {
  const det = el("details", { class: "acc", ...(open ? { open: "" } : {}), ...(id ? { "data-acc": id } : {}) });
  det.append(
    el("summary", { html: `<span class="text-dim">${icon}</span> ${escapeHtml(title)}${badge ? ` <span class="text-[10px] px-1.5 py-0.5 rounded bg-accent-500/20 text-accent-500 border border-accent-500/30">${escapeHtml(badge)}</span>` : ""}<span class="chev">▾</span>` }),
    el("div", { class: "acc-body" }),
  );
  const bodyDiv = det.querySelector(".acc-body");
  if (typeof body === "string") bodyDiv.innerHTML = body;
  else if (body) bodyDiv.append(body);
  return det;
}

// ── Formatos ──
export function fmtBytes(n) {
  if (!n) return "0B";
  for (const u of ["B", "KB", "MB", "GB", "TB"]) { if (n < 1024) return `${n.toFixed(1)}${u}`; n /= 1024; }
  return `${n.toFixed(1)}PB`;
}
export function fmtTime(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}
export function fmtDate(ms) {
  return new Date(ms).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}

export async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copiado ✓", "ok", 1600); return true; }
  catch { const ta = el("textarea", { value: text, class: "sr-only" }); document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast("Copiado ✓", "ok", 1600); return true; }
}

// ── Util ──
export function debounce(fn, ms = 300) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function uid(prefix = "g") {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}
