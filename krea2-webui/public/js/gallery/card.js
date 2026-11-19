// ══════════════════════════════════════════════════════════════
// GALERÍA / CARD — imagen, prompt y metadatos visibles; acciones accesibles.
// ══════════════════════════════════════════════════════════════
import { el, escapeHtml, copy } from "../ui.js";
import { makeImg, pickImageUrl, hostBadges } from "../components.js";
import { r2FallbackUrl, isR2Url } from "../api.js";

// Aspecto desde la resolución pedida (payload), clampado a un rango sano.
export function itemAspect(item) {
  const w = Number(item?.payload?.width) || 0;
  const h = Number(item?.payload?.height) || 0;
  if (w > 0 && h > 0) return Math.min(2, Math.max(0.5, w / h));
  return 1;
}

export function itemTitle(item) {
  const p = item?.payload || {};
  return (p.positive_prompt || p.edit_prompt || item?.beam_filename || "").slice(0, 160);
}

function fmtDateShort(ts) {
  if (!ts) return "";
  const t = typeof ts === "number" ? ts : new Date(ts).getTime();
  if (!isFinite(t)) return "";
  return new Date(t).toLocaleString("es-AR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function galCard(item, {
  selected = false,
  selectMode = false,
  onOpen,
  onToggleSelect,
  onRedo,
  onDelete,
} = {}) {
  const imgUrl = pickImageUrl(item);
  const fbk = imgUrl && isR2Url(imgUrl) && item.beam_filename ? r2FallbackUrl(item.beam_filename) : "";
  const seed = item.payload?.seed ?? "—";
  const created = fmtDateShort(item.created_at);
  const size = item.payload?.width && item.payload?.height
    ? `${item.payload.width} × ${item.payload.height}`
    : "";
  const prompt = item?.payload?.positive_prompt || item?.payload?.edit_prompt || "Sin prompt guardado.";
  const label = itemTitle(item);
  const card = el("article", { class: `gal-card${selected ? " is-selected" : ""}` });
  const wrap = el("div", { class: "gal-imgwrap", style: `aspect-ratio:${itemAspect(item)}` });
  const open = () => onOpen && onOpen(item);
  const activateImage = () => selectMode ? onToggleSelect && onToggleSelect(item) : open();
  const openButton = el("button", {
    type: "button", class: "gal-open", "aria-label": `${selectMode ? "Seleccionar" : "Abrir imagen"}${label ? `: ${label}` : ""}`, onclick: activateImage,
  });

  if (imgUrl) {
    const skel = el("div", { class: "gal-skel", "aria-hidden": "true" });
    const img = makeImg(imgUrl, "gal-img", { fallback: fbk });
    img.alt = label ? `Imagen generada: ${label}` : "Imagen generada";
    img.addEventListener("load", () => skel.remove(), { once: true });
    img.addEventListener("error", () => skel.remove(), { once: true });
    if (img.complete && img.naturalWidth) skel.remove();
    openButton.append(skel, img);
  } else {
    openButton.append(el("span", { class: "gal-noimg", html: "Sin imagen" }));
  }
  wrap.append(openButton);
  wrap.append(el("span", {
    class: "gal-badge",
    html: escapeHtml(item.source === "beam" ? "Volume" : (item.tab || "Generada")),
  }));

  if (selectMode) {
    const checkLabel = el("label", { class: "gal-select", title: "Seleccionar imagen" }, [
      el("input", {
        type: "checkbox", class: "gal-check", checked: selected,
        "aria-label": `Seleccionar imagen${label ? `: ${label}` : ""}`,
        onchange: () => onToggleSelect && onToggleSelect(item),
      }),
      el("span", { class: "sr-only", html: "Seleccionar" }),
    ]);
    wrap.append(checkLabel);
  }
  card.append(wrap);

  const meta = el("div", { class: "gal-card-body" }, [
    el("p", { class: "gal-prompt", title: label || "" }, [prompt]),
    el("div", { class: "gal-meta" }, [
      el("span", { class: "gal-meta-txt", html: escapeHtml(`Seed ${seed}${created ? ` · ${created}` : ""}${size ? ` · ${size}` : ""}`) }),
      ...hostBadges(item.hosts || {}, item.catboxAccount),
    ]),
    el("div", { class: "gal-card-actions" }, [
      el("button", { type: "button", class: "gal-card-action is-open", html: "Abrir", onclick: open }),
      onRedo && el("button", { type: "button", class: "gal-card-action", html: "Rehacer", title: "Rehacer generación", onclick: () => onRedo(item) }),
      el("button", {
        type: "button", class: "gal-card-action", html: "Copiar prompt", title: "Copiar prompt",
        onclick: () => copy(item?.payload?.positive_prompt || item?.payload?.edit_prompt || ""),
      }),
      onDelete && el("button", { type: "button", class: "gal-card-action is-danger", html: "Borrar", title: "Borrar imagen", onclick: () => onDelete(item) }),
    ].filter(Boolean)),
  ]);
  card.append(meta);
  card.addEventListener("click", (e) => {
    if (e.target.closest("button, input, a, label")) return;
    if (selectMode) onToggleSelect && onToggleSelect(item);
    else open();
  });
  return card;
}
