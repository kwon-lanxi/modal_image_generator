// ══════════════════════════════════════════════════════════════
// GALERÍA / VIEWER — lightbox profesional:
// imagen grande + panel lateral por pestañas + filmstrip +
// teclado (←/→/Esc). El prompt largo vive acá, con scroll propio
// y botón copiar — no en la grilla.
// ══════════════════════════════════════════════════════════════
import { el, $, escapeHtml, copy, toast } from "../ui.js";
import { makeImg, pickImageUrl, hostBadges } from "../components.js";
import { r2ImageUrl, r2FallbackUrl, beamImageUrl, isR2Url } from "../api.js";

const FILM_HALF = 25; // thumbs a cada lado del actual

function fmtDateFull(ts) {
  if (!ts) return "—";
  const t = typeof ts === "number" ? ts : new Date(ts).getTime();
  if (!isFinite(t)) return "—";
  return new Date(t).toLocaleString("es-AR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function kvRow(label, value) {
  return el("div", { class: "gal-kv" }, [
    el("span", { class: "gal-kv-k", html: label }),
    el("span", { class: "gal-kv-v", html: escapeHtml(String(value ?? "—")) }),
  ]);
}

export function openViewer({
  items = [],
  index = 0,
  onRedo,
  onSendTo,
  onSendToUpscale,
  onDelete,
  onDownload,
} = {}) {
  if (!items.length) return { close: () => {} };
  let i = Math.max(0, Math.min(index, items.length - 1));
  let tab = "prompt"; // prompt | params | loras | hosts
  const returnFocus = document.activeElement;
  const previousOverflow = document.body.style.overflow;

  const overlay = el("div", {
    class: "gal-viewer", role: "dialog", "aria-modal": "true",
    "aria-label": "Visor de galería", tabindex: "-1",
  });
  const shell = el("div", { class: "gal-viewer-shell" });
  overlay.append(shell);
  document.body.append(overlay);
  document.body.style.overflow = "hidden";

  function close() {
    window.removeEventListener("keydown", onKey);
    overlay.remove();
    document.body.style.overflow = previousOverflow;
    if (returnFocus?.isConnected) returnFocus.focus();
  }

  function onKey(e) {
    const tag = (e.target?.tagName || "").toLowerCase();
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key === "Tab") {
      const focusable = [...shell.querySelectorAll('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])')]
        .filter(node => node.getClientRects().length > 0);
      if (!focusable.length) { e.preventDefault(); overlay.focus(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && (document.activeElement === first || !shell.contains(document.activeElement))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !shell.contains(document.activeElement))) {
        e.preventDefault(); first.focus();
      }
      return;
    }
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      if (e.target?.closest?.('[role="tab"]')) return;
      go(i + (e.key === "ArrowRight" ? 1 : -1));
    }
  }
  window.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

  function go(n) {
    i = (n + items.length) % items.length;
    render();
  }

  function cur() { return items[i]; }

  function imgUrlOf(it) { return pickImageUrl(it); }
  // Original del Worker (?raw): la fuente para upscalear después.
  function rawUrlOf(it) {
    if (it?.beam_filename) return r2ImageUrl(it.beam_filename, { raw: true }) || "";
    return pickImageUrl(it) || "";
  }

  function render() {
    const it = cur();
    const p = it.payload || {};
    const h = it.hosts || {};
    const url = imgUrlOf(it);
    const fbk = url && isR2Url(url) && it.beam_filename ? r2FallbackUrl(it.beam_filename) : "";
    const prompt = p.positive_prompt || p.edit_prompt || "";

    // ── header ──
    const header = el("div", { class: "gal-v-head" }, [
      el("div", { class: "flex items-center gap-2 min-w-0" }, [
        el("span", { class: "badge", html: escapeHtml(it.source === "beam" ? "volume" : (it.tab || "gen")) }),
        el("span", { class: "badge mono", html: `seed ${p.seed ?? "?"}` }),
        el("span", { class: "badge mono hidden sm:inline-flex", html: escapeHtml(fmtDateFull(it.created_at)) }),
      ]),
      el("div", { class: "flex items-center gap-2" }, [
        el("span", { class: "text-xs text-dim mono", html: `${i + 1} / ${items.length}` }),
        el("button", { type: "button", class: "btn-ghost gal-v-close", html: "✕", title: "Cerrar (Esc)", "aria-label": "Cerrar visor", onclick: close }),
      ]),
    ]);

    // ── imagen ──
    // Si la URL no carga (proxy caído, key vieja, CORS), antes quedaba un
    // cuadro negro sin explicación. Ahora hay estado de error visible con
    // reintentar + abrir el ?raw del Worker en pestaña nueva.
    const imgBox = el("div", { class: "gal-v-imgbox", "aria-label": "Imagen de la galería" });
    const raw = rawUrlOf(it);
    const errBox = el("div", { class: "gal-v-err hidden" }, [
      el("div", { class: "text-sm", html: "No se pudo cargar la imagen" }),
      el("div", { class: "mono text-[11px] text-dim break-all", html: escapeHtml(url || "sin URL") }),
      el("div", { class: "flex gap-2 justify-center flex-wrap" }, [
        el("button", { class: "btn-secondary !py-1 text-xs", html: "Reintentar", onclick: () => go(i) }),
        raw && el("a", { class: "btn-ghost !py-1 text-xs", href: raw, target: "_blank", rel: "noopener", html: "Abrir ?raw" }),
      ].filter(Boolean)),
    ]);
    if (url) {
      const skel = el("div", { class: "gal-skel" });
      const img = makeImg(url, "gal-v-img", { fallback: fbk });
      let settled = false;
      const showErr = () => {
        if (settled) return;
        settled = true;
        skel.remove();
        errBox.classList.remove("hidden");
      };
      img.addEventListener("load", () => { settled = true; skel.remove(); }, { once: true });
      img.addEventListener("error", () => {
        // Si hay fallback pendiente, makeImg lo intenta solo; el error final
        // (con dataset.fbk) o la falta total de src muestra el estado.
        if (img.dataset.fbk || !img.getAttribute("src")) showErr();
        else setTimeout(() => { if (!img.currentSrc && !settled) showErr(); }, 4000);
      });
      if (img.complete && img.naturalWidth) { settled = true; skel.remove(); }
      // URLs proxied (displayImg async): si en 12s no hay nada, mostrar error
      setTimeout(() => { if (!settled && !img.currentSrc) showErr(); }, 12000);
      imgBox.append(skel, img, errBox);
    } else {
      errBox.classList.remove("hidden");
      imgBox.append(errBox);
    }
    // flechas laterales (PC + táctil)
    if (items.length > 1) {
      imgBox.append(
        el("button", { type: "button", class: "gal-v-nav left", html: "‹", title: "Anterior (←)", "aria-label": "Imagen anterior", onclick: (e) => { e.stopPropagation(); go(i - 1); } }),
        el("button", { type: "button", class: "gal-v-nav right", html: "›", title: "Siguiente (→)", "aria-label": "Imagen siguiente", onclick: (e) => { e.stopPropagation(); go(i + 1); } }),
      );
    }

    // ── panel lateral ──
    const tabs = el("div", { class: "gal-v-tabs", role: "tablist", "aria-label": "Detalles de la imagen" }, [
      ...["prompt", "params", "loras", "hosts"].map(t => {
        const selectTab = () => { tab = t; render(); shell.querySelector('.gal-v-tab[aria-selected="true"]')?.focus(); };
        const button = el("button", {
          type: "button", class: `gal-v-tab${t === tab ? " active" : ""}`,
          id: `gal-v-tab-${t}`, role: "tab", "aria-controls": "gal-v-tabpanel",
          "aria-selected": String(t === tab), tabindex: t === tab ? "0" : "-1",
          html: { prompt: "Prompt", params: "Params", loras: "LoRAs", hosts: "Hosts" }[t],
          onclick: selectTab,
        });
        button.addEventListener("keydown", (e) => {
          const tabNames = ["prompt", "params", "loras", "hosts"];
          const current = tabNames.indexOf(t);
          const next = e.key === "ArrowRight" ? (current + 1) % tabNames.length
            : e.key === "ArrowLeft" ? (current + tabNames.length - 1) % tabNames.length
              : e.key === "Home" ? 0 : e.key === "End" ? tabNames.length - 1 : -1;
          if (next >= 0) {
            e.preventDefault();
            tab = tabNames[next];
            render();
            shell.querySelector('.gal-v-tab[aria-selected="true"]')?.focus();
          }
        });
        return button;
      }),
    ]);
    const body = el("div", { id: "gal-v-tabpanel", class: "gal-v-tabbody", role: "tabpanel", "aria-labelledby": `gal-v-tab-${tab}`, tabindex: "0" });
    if (tab === "prompt") {
      body.append(
        el("div", { class: "gal-v-prompt", html: escapeHtml(prompt || "—") }),
        el("div", { class: "flex gap-1.5 flex-wrap" }, [
          prompt && el("button", { class: "btn-ghost !py-1 text-xs", html: "Copiar prompt", onclick: () => copy(prompt) }),
          p.negative_prompt && el("button", { class: "btn-ghost !py-1 text-xs", html: "Copiar negativo", onclick: () => copy(p.negative_prompt) }),
        ].filter(Boolean)),
        p.negative_prompt ? el("div", { class: "gal-v-neg", html: escapeHtml(p.negative_prompt) }) : null,
      );
    } else if (tab === "params") {
      const rows = [
        ["Seed", p.seed], ["Steps", p.steps], ["CFG", p.cfg],
        ["Sampler", p.sampler_name], ["Scheduler", p.scheduler], ["Mu", p.mu],
        ["Denoise", p.denoise], ["Tamaño", p.width && p.height ? `${p.width} × ${p.height}` : null],
        ["UNET", p.unet_name], ["CLIP", p.clip_name], ["VAE", p.vae_name],
        ["Upscale", p.upscale_model ? `${p.upscale_model} ×${p.upscale_scale ?? ""}` : null],
        ["Archivo", it.beam_filename],
      ].filter(([, v]) => v !== undefined && v !== null && v !== "");
      body.append(
        ...rows.map(([k, v]) => kvRow(k, v)),
        el("button", {
          class: "btn-ghost !py-1 text-xs mt-2", html: "Copiar params (JSON)",
          onclick: () => copy(JSON.stringify({ seed: p.seed, steps: p.steps, cfg: p.cfg, sampler: p.sampler_name, scheduler: p.scheduler, width: p.width, height: p.height, unet: p.unet_name, clip: p.clip_name, vae: p.vae_name, lora: p.lora }, null, 1)),
        }),
      );
    } else if (tab === "loras") {
      const entries = p.lora ? Object.entries(p.lora) : [];
      // OJO: append() no acepta arrays (los stringifica a "[object HTMLDivElement]") → spread.
      body.append(...(entries.length
        ? entries.map(([n, w]) => el("div", { class: "gal-v-lora" }, [
            el("span", { class: "mono", html: escapeHtml(n) }),
            el("span", { class: "mono text-accent-500", html: String(w) }),
          ]))
        : [el("div", { class: "text-dim text-xs", html: "Sin LoRAs" })]));
    } else {
      const links = Object.entries({ imgchest: h.imgchest, catbox: h.catbox, koofr: h.koofr })
        .filter(([, v]) => v)
        .map(([k, linkUrl]) => el("div", { class: "gal-v-host" }, [
          el("span", { class: "badge shrink-0", html: k }),
          el("a", { class: "mono gal-v-link", href: linkUrl, target: "_blank", rel: "noopener", html: escapeHtml(linkUrl) }),
          el("button", { class: "btn-ghost !py-0.5 text-xs shrink-0", html: "copiar", onclick: () => copy(linkUrl) }),
        ]));
      // Worker R2 directo (display + ?raw original) — siempre disponible si hay filename
      const wdisplay = it.beam_filename ? r2ImageUrl(it.beam_filename) : "";
      const wraw = it.beam_filename ? r2ImageUrl(it.beam_filename, { raw: true }) : "";
      const workerRows = [wdisplay && el("div", { class: "gal-v-host" }, [
        el("span", { class: "badge shrink-0", html: "worker" }),
        el("a", { class: "mono gal-v-link", href: wdisplay, target: "_blank", rel: "noopener", html: escapeHtml(wdisplay) }),
        el("button", { class: "btn-ghost !py-0.5 text-xs shrink-0", html: "copiar", onclick: () => copy(wdisplay) }),
      ]), wraw && el("div", { class: "gal-v-host" }, [
        el("span", { class: "badge shrink-0", html: "raw" }),
        el("a", { class: "mono gal-v-link", href: wraw, target: "_blank", rel: "noopener", html: escapeHtml(wraw) }),
        el("button", { class: "btn-ghost !py-0.5 text-xs shrink-0", html: "copiar", onclick: () => copy(wraw) }),
      ])].filter(Boolean);
      body.append(
        // OJO: append() no acepta arrays → spread (mismo bug que el tab LoRAs).
        ...(links.length ? links : [el("div", { class: "text-dim text-xs", html: "Sin subir a ningún host." })]),
        it.catboxAccount ? el("div", { class: "text-dim text-xs mt-1", html: "catbox: subido a tu cuenta (sin URL — CORS)" }) : null,
        ...workerRows,
        el("div", { class: "flex gap-1.5 flex-wrap mt-2" }, [...hostBadges(h, it.catboxAccount)]),
      );
    }

    // ── acciones ──
    // Upscale: manda el ?raw del Worker al tab Upscale (misma fuente que
    // después se upscalea → sin recompresión intermedia).
    const vraw = rawUrlOf(it);
    const actions = el("div", { class: "gal-v-actions" }, [
      onRedo && el("button", { class: "btn-primary !py-1.5 text-sm", html: "Rehacer", onclick: () => { const x = cur(); close(); onRedo(x); } }),
      el("button", { class: "btn-secondary !py-1.5 text-sm", html: "Variation", onclick: () => { const x = cur(); close(); onSendTo && onSendTo("variation", imgUrlOf(x)); } }),
      el("button", { class: "btn-secondary !py-1.5 text-sm", html: "Edit", onclick: () => { const x = cur(); close(); onSendTo && onSendTo("edit", imgUrlOf(x)); } }),
      (onSendToUpscale && vraw) && el("button", { class: "btn-secondary !py-1.5 text-sm", html: "Upscale", title: "Enviar al tab Upscale", onclick: () => { const x = cur(); const r = rawUrlOf(x); close(); onSendToUpscale(r); } }),
      vraw && el("a", { class: "btn-ghost !py-1.5 text-sm", html: "?raw", title: "Abrir original del Worker en pestaña nueva", href: vraw, target: "_blank", rel: "noopener" }),
      el("button", { class: "btn-ghost !py-1.5 text-sm", html: "Original", title: "Descargar original", onclick: () => onDownload && onDownload(cur()) }),
      onDelete && el("button", { class: "btn-ghost btn-danger !py-1.5 text-sm", html: "Borrar", onclick: () => { const x = cur(); close(); onDelete(x); } }),
    ].filter(Boolean));

    const panel = el("div", { class: "gal-v-panel" }, [tabs, body, actions]);

    // ── filmstrip ──
    let film = null;
    if (items.length > 1) {
      const lo = Math.max(0, i - FILM_HALF);
      const hi = Math.min(items.length, i + FILM_HALF + 1);
      const strip = el("div", { class: "gal-film" });
      for (let k = lo; k < hi; k++) {
        const th = items[k];
        const tUrl = imgUrlOf(th);
        const tFbk = tUrl && isR2Url(tUrl) && th.beam_filename ? r2FallbackUrl(th.beam_filename) : "";
        const cell = el("button", {
          type: "button", class: `gal-thumb${k === i ? " active" : ""}`,
          title: `seed ${th.payload?.seed ?? "?"}`,
          "aria-label": `Abrir imagen ${k + 1}, seed ${th.payload?.seed ?? "?"}`,
          "aria-current": k === i ? "true" : null,
          onclick: () => go(k),
        });
        if (tUrl) cell.append(makeImg(tUrl, "gal-thumb-img", { fallback: tFbk }));
        strip.append(cell);
      }
      film = strip;
      // centrar el activo
      requestAnimationFrame(() => {
        const act = $(".gal-thumb.active", strip);
        act?.scrollIntoView({ block: "nearest", inline: "center" });
      });
    }

    shell.innerHTML = "";
    shell.append(header, el("div", { class: "gal-v-main" }, [imgBox, panel]), film || el("div"));
  }

  render();
  shell.querySelector(".gal-v-close")?.focus();
  return { close, go };
}

// Descarga el original con la misma estrategia de candidatos que el modal viejo.
export async function downloadOriginal(item) {
  const fn = item.beam_filename;
  const h = item.hosts || {};
  const cands = [];
  if (fn) cands.push(r2ImageUrl(fn, { raw: true }), r2FallbackUrl(fn, { raw: true }));
  cands.push(h.imgchest, h.catbox, h.koofr, item.imageUrl, item.image_url);
  for (const url of [...new Set(cands.filter(Boolean))]) {
    try {
      const r = await fetch(beamImageUrl(url));
      if (!r.ok) continue;
      const blob = await r.blob();
      const a = el("a", { href: URL.createObjectURL(blob), download: fn || `krea2_${Date.now()}.png` });
      document.body.append(a); a.click(); a.remove();
      toast("Descargando original…", "ok", 1800);
      return true;
    } catch { /* probar siguiente */ }
  }
  toast("Descarga: ninguna URL respondió", "err");
  return false;
}
