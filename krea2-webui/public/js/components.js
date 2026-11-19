import { state } from "./state.js";
import { displayImg, needsBeamProxy, beamImageUrl, r2ImageUrl, r2FallbackUrl, isR2Url } from "./api.js";
import { el, $, $$, toast, openModal, escapeHtml, copy, fmtBytes } from "./ui.js";

// ══════════════════════════════════════════════════════════════
// LISTA DE LoRAs — ilimitada, peso individual (slider + input)
// ══════════════════════════════════════════════════════════════
// Acepta name con prefijo "v1/foo" o "v2/foo" (igual que el manager) y
// detecta colisiones de stem entre v1/v2/general para mostrar el chip de versión
// al lado del input y permitir ciclarlas con un click.
// v1.1.0: v2 = personajes (krea2-loras-renew), v1 = descargados (loras/);
// el GPU prioriza v2 por defecto y acepta steps ("name-3000", "name-step00003000").
const LORA_VERSION_PREFIX = /^\s*(v1|v2|general)\s*\/\s*(.+)\s*$/i;

function _parseLoraName(raw) {
  const s = String(raw || "").trim();
  const m = s.match(LORA_VERSION_PREFIX);
  if (m) return { version: m[1].toLowerCase(), name: m[2].trim() };
  return { version: null, name: s };
}

// Devuelve las variantes (mismo stem) disponibles en el catálogo, [{version, name, character, vol_path}, ...]
function _loraVariants(stem) {
  const out = [];
  const seen = new Set();
  const byVer = state.lorasByVersion || {};
  for (const v of ["v1", "v2", "general"]) {
    for (const m of (byVer[v] || [])) {
      const key = `${m.logical}|${v}`;
      if (seen.has(key)) continue;
      if (m.logical === stem || (m.filename && m.filename.startsWith(stem + "."))) {
        seen.add(key);
        out.push({
          version: v,
          name: m.logical,
          character: m.character || "",
          vol_path: m.vol_path || m.relative_path || "",
        });
      }
    }
  }
  // También caer al catálogo plano (sin versión) si no vino de by_version
  if (!out.length) {
    for (const m of (state.models?.loras || [])) {
      if (m.logical === stem) {
        out.push({
          version: m.version || "general",
          name: m.logical,
          character: m.character || "",
          vol_path: m.vol_path || m.relative_path || "",
        });
      }
    }
  }
  return out;
}

export function createLoraList(container, { locked = [] } = {}) {
  const rows = [];
  let _seq = 0;

  const rowWrap = el("div", { class: "space-y-1.5" });

  function makeRow(name, weight, { locked: isLocked = false } = {}) {
    const id = `lora_${Date.now()}_${_seq++}`;
    const initial = _parseLoraName(name);
    const variants = _loraVariants(initial.name);
    const hasCollision = variants.length > 1;

    const nameInput = el("input", {
      type: "text", class: "input !py-1.5 text-[13px]", placeholder: "nombre_lora",
      value: initial.name, list: "lora-options", spellcheck: "false",
    });

    // Chip de versión (solo si hay colisión de stem entre v1/v2/general).
    // Click cicla: v1 → v2 → general → (sin prefijo) → v1 …
    let versionChip = null;
    const refreshChip = () => {
      if (!versionChip) return;
      const stem = _parseLoraName(nameInput.value).name;
      const vs = _loraVariants(stem);
      if (vs.length > 1) {
        const cur = _parseLoraName(nameInput.value).version;
        const active = vs.find(v => v.version === cur) || vs[0];
        versionChip.textContent = active.version.toUpperCase();
        versionChip.title = vs.map(v => `${v.version.toUpperCase()}: ${v.character || v.name}`).join("\n");
        versionChip.classList.remove("hidden");
      } else if (vs.length === 1) {
        versionChip.textContent = vs[0].version.toUpperCase();
        versionChip.title = vs[0].character || vs[0].name;
        versionChip.classList.remove("hidden");
      } else {
        versionChip.classList.add("hidden");
      }
    };
    if (hasCollision || variants.length) {
      versionChip = el("button", {
        type: "button",
        class: "badge !text-[10px] !px-1.5 !py-0.5 mono hover:border-accent-500/60",
        title: "Ciclar versión",
      }, ["…"]);
      nameInput.addEventListener("input", refreshChip);
      versionChip.addEventListener("click", () => {
        const cur = _parseLoraName(nameInput.value);
        const vs = _loraVariants(cur.name);
        if (vs.length < 2) return; // no hay a qué ciclar
        const idx = vs.findIndex(v => v.version === cur.version);
        const next = vs[(idx + 1) % vs.length];
        nameInput.value = `${next.version}/${cur.name}`;
        refreshChip();
      });
    }

    const slider = el("input", { type: "range", min: "-2", max: "2", step: "0.05", value: weight, class: "flex-1" });
    const num = el("input", { type: "number", step: "0.05", value: weight, class: "input !py-1.5 w-20 text-center mono" });

    // El slider es para ajuste fino (-2..2), pero el valor tipeado NO tiene límite:
    // los LoRA tipo slider usan pesos como 30 o -10.
    // Importante: al tipear en el input NUNCA se reescribe num.value (eso rompería
    // escribir "30" o "-10" tecla a tecla); solo se clampea el slider para mostrar.
    const clampSlider = (val) => slider.value = String(Math.min(2, Math.max(-2, val)));
    slider.addEventListener("input", () => {
      const val = parseFloat(slider.value) || 0;
      num.value = val.toFixed(2);
    });
    num.addEventListener("input", () => {
      const val = parseFloat(num.value);
      if (Number.isFinite(val)) clampSlider(val);
    });
    clampSlider(parseFloat(weight) || 0);

    const del = el("button", { class: "btn-ghost btn-danger !p-1.5 text-sm", title: "Quitar", html: "🗑" });
    del.addEventListener("click", () => { row.remove(); rows.splice(rows.indexOf(row), 1); });

    const row = el("div", { class: "flex items-center gap-2" }, [
      el("span", { class: "text-dim text-xs", html: locked.length ? "🔒" : "🧩" }),
      el("div", { class: "flex-1 flex items-center gap-1.5 min-w-0" }, [
        el("div", { class: "flex-1 min-w-0" }, [nameInput]),
        versionChip || null,
      ].filter(Boolean)),
      slider,
      num,
      isLocked ? el("span", { class: "badge text-[9px] text-dim", html: "auto" }) : del,
    ].filter(Boolean));
    row.dataset.loraRow = id;

    // lock fijo: no se puede editar nombre ni quitar
    if (isLocked) { nameInput.disabled = true; slider.disabled = true; num.disabled = true; if (versionChip) versionChip.disabled = true; }

    // get(): devuelve "v1/foo" si el usuario tipeó un prefijo de versión explícito
    // (aunque no haya colisión en el catálogo → el manager resolverá por nombre +
    // prefijo). Si no hay prefijo y hay UNA variante, le pone el prefijo de esa
    // variante (más explícito). Si no hay prefijo y hay varias, deja el stem pelado
    // (el manager prioriza v2 por defecto).
    // EXCEPCIÓN: los LoRAs locked (preset interno) conservan su nombre original —
    // ya vienen con la versión correcta elegida por el sistema.
    const entry = {
      id, name: name, weight, row, isLocked,
      get: () => {
        if (isLocked) return { name, weight: parseFloat(num.value) || weight };
        const cur = _parseLoraName(nameInput.value);
        const vs = _loraVariants(cur.name);
        let outName = cur.name;
        if (cur.version) {
          outName = `${cur.version}/${cur.name}`;
        } else if (vs.length === 1) {
          outName = `${vs[0].version}/${cur.name}`;
        }
        return { name: outName, weight: parseFloat(num.value) || 0 };
      },
    };
    rows.push(entry);
    rowWrap.append(row);
    refreshChip();
    return entry;
  }

  // input para agregar
  const addInput = el("input", {
    type: "text", class: "input !py-1.5 text-[13px]", placeholder: "Agregar LoRA… (elige o tipea)",
    list: "lora-options",
  });
  const addBtn = el("button", { class: "btn-secondary !px-2.5 !py-1.5 text-sm", html: "＋" });
  const addRow = () => {
    const v = addInput.value.trim();
    if (!v) return;
    makeRow(v, 1.0);
    addInput.value = "";
    addInput.focus();
  };
  addBtn.addEventListener("click", addRow);
  addInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addRow(); } });

  const wrapper = el("div", { class: "space-y-2" }, [
    el("div", { class: "flex gap-2" }, [addInput, addBtn]),
    rowWrap,
  ]);
  container.append(wrapper);

  return {
    add: makeRow,
    get: () => {
      const out = {};
      for (const r of rows) {
        const { name, weight } = r.get();
        if (name && weight !== 0) out[name] = weight;
      }
      return out;
    },
    set(map) {
      rowWrap.innerHTML = "";
      rows.length = 0;
      if (map) for (const [n, w] of Object.entries(map)) makeRow(n, w);
      for (const l of locked) makeRow(l.name, l.weight, { locked: true });
    },
    count: () => rows.length,
  };
}

// ══════════════════════════════════════════════════════════════
// INPUT DE IMAGEN — drag&drop / click / pegar / URL / último output
// ══════════════════════════════════════════════════════════════
export function createImageInput(container, { label = "Imagen", maxDim = 2048, showLast = true } = {}) {
  let current = null; // { dataUrl, name }

  const preview = el("div", { class: "relative rounded-lg overflow-hidden border border-edge bg-black/40 hidden", style: "min-height:90px" });
  const img = el("img", { class: "w-full h-auto max-h-64 object-contain" });
  const clearBtn = el("button", { class: "absolute top-1.5 right-1.5 btn-ghost bg-black/60 !p-1 text-xs", html: "✕" });
  clearBtn.addEventListener("click", () => { current = null; preview.classList.add("hidden"); preview.innerHTML = ""; preview.append(img, clearBtn); input.value = ""; });

  const input = el("input", { type: "file", accept: "image/*", class: "hidden" });
  const drop = el("div", {
    class: "image-drop border border-dashed border-edge rounded-lg p-3 text-center text-dim text-xs cursor-pointer hover:border-accent-500 transition-colors select-none",
    role: "button", tabindex: "0", "aria-label": `${label}: elegir imagen o arrastrar archivo`,
    html: `<span class="image-drop-mark" aria-hidden="true">↑</span> <b>${escapeHtml(label)}</b> — arrastrá, click para elegir, o Ctrl+V<br/><span class="opacity-70">(también podés pegar una URL abajo)</span>`,
  });
  const urlRow = el("div", { class: "flex gap-1.5 mt-1.5" }, [
    el("input", { type: "url", class: "input !py-1 text-xs", placeholder: "https://… o data:image/…", spellcheck: "false" }),
  ]);
  const urlBtn = el("button", { class: "btn-secondary !px-2 !py-1 text-xs", html: "Usar URL" });

  async function fromFile(file) {
    if (!file || !file.type.startsWith("image/")) return;
    const dataUrl = await compressImage(file, maxDim);
    current = { dataUrl, name: file.name.replace(/[^a-zA-Z0-9._-]/g, "_") };
    img.src = dataUrl;
    preview.classList.remove("hidden");
    toast("Imagen cargada", "ok", 1400);
  }
  function fromDataUrl(dataUrl) {
    current = { dataUrl, name: `img_${Date.now()}.png` };
    img.src = dataUrl;
    preview.classList.remove("hidden");
  }
  // Acepta URLs http(s) también: las convierte a data-URL (el GPU necesita base64).
  async function fromUrl(url) {
    const src = await displayImg(url);
    fromDataUrl(src.startsWith("data:") ? src : url);
  }

  input.addEventListener("change", () => fromFile(input.files[0]));
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  ["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("border-accent-500"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("border-accent-500"); }));
  drop.addEventListener("drop", (e) => fromFile(e.dataTransfer.files[0]));
  urlBtn.addEventListener("click", () => {
    const v = urlRow.querySelector("input").value.trim();
    if (v) (/^data:/i.test(v) ? fromDataUrl(v) : fromUrl(v));
  });

  const lastBtn = el("button", { class: "btn-ghost !py-1 text-xs mt-1.5", html: "↺ usar último output" });
  if (showLast) {
    lastBtn.addEventListener("click", () => {
      if (state.lastResult?.imageUrl) fromDataUrl(state.lastResult.imageUrl);
      else toast("No hay último output", "info");
    });
  }

  preview.append(img, clearBtn);
  container.append(preview, el("div", {}, [drop, input, urlRow && el("div", { class: "flex gap-1.5" }, [urlRow.querySelector("input"), urlBtn]), lastBtn]));

  // pegar (Ctrl+V) — solo si el contenedor está en la pestaña visible
  const onPaste = (e) => {
    const tab = container.closest(".tab");
    if (tab && tab.classList.contains("hidden")) return;
    const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith("image/"));
    if (item) { e.preventDefault(); fromFile(item.getAsFile()); }
  };
  document.addEventListener("paste", onPaste);
  container._cleanupPaste = () => document.removeEventListener("paste", onPaste);

  return {
    get: () => current,
    getDataUrl: () => current?.dataUrl || null,
    getBlob: async () => (current ? await (await fetch(current.dataUrl)).blob() : null),
    getName: () => current?.name || `krea2_${Date.now()}.png`,
    // set() acepta data-URL o URL http(s) (la convierte a base64 para el payload)
    set: (src) => { if (/^https?:\/\//i.test(src)) fromUrl(src); else fromDataUrl(src); },
    clear: () => { current = null; preview.classList.add("hidden"); input.value = ""; },
    cleanup: () => { container._cleanupPaste && container._cleanupPaste(); },
  };
}

function compressImage(file, maxDim) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const imgEl = new Image();
      imgEl.onload = () => {
        const scale = Math.min(1, maxDim / Math.max(imgEl.width, imgEl.height));
        const w = Math.round(imgEl.width * scale);
        const h = Math.round(imgEl.height * scale);
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        c.getContext("2d").drawImage(imgEl, 0, 0, w, h);
        const isPng = file.type === "image/png";
        resolve(c.toDataURL(isPng ? "image/png" : "image/jpeg", 0.92));
      };
      imgEl.onerror = reject;
      imgEl.src = reader.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ══════════════════════════════════════════════════════════════
// GALERÍA — masonry + modal con info desplegable
// ══════════════════════════════════════════════════════════════
export function renderMasonry(container, items, renderCard) {
  container.innerHTML = "";
  if (!items.length) return;
  for (const it of items) container.append(renderCard(it));
}

// <img> que se carga según el origen de la URL:
//  · hosts CDN (imgchest/catbox/koofr) → src directo (rápido, el browser cachea)
//  · hosts Beam (CORS roto) → displayImg() (proxy /api/beam + data-URL cacheado)
//  · fallback: si la primaria (R2) da 404 (key vieja outputs/ vs krea2/…), cambia
//    al fallback automáticamente (una sola vez).
export function makeImg(url, cls = "", { onclick, title = "", fallback = "" } = {}) {
  const img = el("img", { class: cls, alt: "", loading: "lazy", decoding: "async", ...(title ? { title } : {}) });
  if (onclick) img.addEventListener("click", onclick);
  const apply = (src) => { if (src && img.src !== src) img.src = src; };
  if (url) {
    if (/^https?:\/\//i.test(url) && !needsBeamProxy(url)) apply(url); // CDN directo
    else displayImg(url).then(src => apply(src));
  }
  if (fallback && fallback !== url) {
    img.addEventListener("error", () => { if (!img.dataset.fbk) { img.dataset.fbk = "1"; apply(fallback); } });
  }
  return img;
}

export function genCard(item, { onOpen, onRedo, onDelete } = {}) {
  const imgUrl = pickImageUrl(item);
  // Si la fuente es el Worker de R2, el fallback es la otra convención de key
  // (outputs/ vs krea2/YYYY/MM/DD/) para que las imágenes viejas también carguen.
  const fbk = imgUrl && isR2Url(imgUrl) && item.beam_filename ? r2FallbackUrl(item.beam_filename) : "";
  const size = item.payload?.width && item.payload?.height
    ? `${item.payload.width} × ${item.payload.height}`
    : (item.size_human || (item.size_bytes ? fmtBytes(item.size_bytes) : null));
  // Fecha Y hora (el orden de la galería es por fecha+hora de creación).
  const created = item.created_at ? new Date(item.created_at).toLocaleString("es-AR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : null;
  const card = el("div", {
    class: "card-img rounded-lg overflow-hidden border border-edge bg-panel cursor-pointer hover:border-accent-500/60 transition-all group",
  });
  card.append(
    el("div", { class: "relative" }, [
      imgUrl
        ? makeImg(imgUrl, "thumb w-full h-auto", { onclick: () => onOpen && onOpen(item), fallback: fbk })
        : el("div", { class: "h-24 flex items-center justify-center text-dim text-xs", html: "sin imagen" }),
      el("div", { class: "absolute top-1.5 left-1.5 flex gap-1 flex-wrap" }, [
        el("span", { class: "badge bg-black/60", html: item.source === "beam" ? "volume" : (item.tab || "gen") }),
        created ? el("span", { class: "badge bg-black/60 mono", html: created }) : null,
        el("span", { class: "badge bg-black/60 mono", html: `seed ${item.payload?.seed ?? "?"}` }),
        size ? el("span", { class: "badge bg-black/60 mono", html: size }) : null,
      ]),
    ]),
    el("div", { class: "p-2 space-y-1.5" }, [
      el("div", { class: "text-[12px] leading-snug line-clamp-2 text-dim", html: escapeHtml(item.payload?.positive_prompt || item.payload?.edit_prompt || "—") }),
      el("div", { class: "flex items-center gap-1" }, [
        ...hostBadges(item.hosts || {}, item.catboxAccount),
        el("div", { class: "flex-1" }),
        onRedo && el("button", { class: "btn-ghost !px-1.5 !py-0.5 text-[11px]", html: "↻ Rehacer", onclick: () => onRedo(item) }),
        onDelete && el("button", { class: "btn-ghost btn-danger !px-1.5 !py-0.5 text-[11px]", html: "🗑", onclick: (e) => { e.stopPropagation(); onDelete(item); } }),
      ]),
    ]),
  );
  return card;
}

export function pickImageUrl(item) {
  const h = item.hosts || {};
  if (h.imgchest) return h.imgchest;
  if (h.catbox) return h.catbox;
  if (h.koofr) return h.koofr;
  // v9.4/R2: si hay filename, la URL del Worker (webp) es la fuente más confiable
  // (el manager ya no sirve imágenes). Cae antes que las URLs viejas de Beam.
  if (item.r2Url) return item.r2Url;
  if (item.beam_filename) {
    const r2 = r2ImageUrl(item.beam_filename);
    if (r2) return r2;
  }
  if (item.displayUrl) return item.displayUrl; // proxy same-origin (CORS roto de Beam)
  if (item.imageUrl) return item.imageUrl;
  if (item.image_url) return item.image_url;   // D1 trae image_url
  return null;
}

export function hostBadges(hosts, catboxAccount = false) {
  const map = { imgchest: "imgchest", catbox: "catbox", koofr: "koofr" };
  return Object.entries(map)
    .filter(([k]) => hosts[k] || (k === "catbox" && catboxAccount))
    .map(([k, label]) =>
      el("span", { class: `badge ${label === "imgchest" ? "text-purple-300 border-purple-400/40" : label === "catbox" ? "text-orange-300 border-orange-400/40" : "text-sky-300 border-sky-400/40"}` }, [label])
    );
}

// Modal de detalle con mucha info desplegable
export function genDetailModal(item, { onRedo, onDelete } = {}) {
  const p = item.payload || {};
  const h = item.hosts || {};
  const rows = (label, value) => `<div class="flex justify-between gap-3 py-0.5"><span class="text-dim">${label}</span><span class="mono text-right break-all">${escapeHtml(String(value ?? "—"))}</span></div>`;
  const acc = (title, body) => `<details class="acc"><summary>${escapeHtml(title)}<span class="chev">▾</span></summary><div class="acc-body">${body}</div></details>`;

  const hostLinks = Object.entries({ catbox: h.catbox, imgchest: h.imgchest, koofr: h.koofr })
    .filter(([, v]) => v)
    .map(([k, url]) => `
      <div class="flex items-center gap-2 py-1">
        <span class="badge shrink-0">${k}</span>
        <a class="mono flex-1 text-accent-500 truncate" href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>
        <button class="btn-ghost !py-0.5 text-xs" data-copy="${escapeHtml(url)}">copiar</button>
      </div>`).join("") + (item.catboxAccount ? `
      <div class="flex items-center gap-2 py-1">
        <span class="badge shrink-0">catbox</span>
        <a class="mono flex-1 text-accent-500 truncate" href="https://catbox.moe" target="_blank" rel="noopener">subido a tu cuenta (sin URL — CORS)</a>
      </div>` : "") || `<div class="text-dim text-xs">Sin subir a ningún host.</div>`;

  const params = `
    ${rows("Seed", p.seed)}${rows("Steps", p.steps)}${rows("CFG", p.cfg)}${rows("Sampler", p.sampler_name)}${rows("Scheduler", p.scheduler)}${rows("Mu", p.mu)}
    ${rows("Denoise", p.denoise)}${rows("Ancho × Alto", `${p.width} × ${p.height}`)}${rows("UNET", p.unet_name)}${rows("CLIP", p.clip_name)}${rows("VAE", p.vae_name)}${rows("Tiled VAE", p.use_tiled_vae)}${rows("Tamaño", item.size_human || (item.size_bytes ? fmtBytes(item.size_bytes) : "—"))}${rows("Archivo", item.beam_filename)}`;

  const loras = p.lora && Object.keys(p.lora).length
    ? Object.entries(p.lora).map(([n, w]) => `<div class="flex justify-between py-0.5 text-[13px]"><span class="mono">${escapeHtml(n)}</span><span class="text-accent-500 mono">${w}</span></div>`).join("")
    : `<div class="text-dim text-xs">Sin LoRAs</div>`;

  const { close } = openModal(`
    <div class="p-4">
      <div class="flex items-start gap-3">
        <img src="${escapeHtml(pickImageUrl(item) || "")}" class="w-32 h-32 object-cover rounded-lg border border-edge" />
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="badge">${escapeHtml(item.tab || "gen")}</span>
            <span class="badge mono">seed ${p.seed ?? "?"}</span>
            <span class="badge">${escapeHtml(fmtDate2(item.created_at))}</span>
          </div>
          <div class="mt-2 text-sm leading-snug">${escapeHtml(p.positive_prompt || p.edit_prompt || "—")}</div>
          ${p.negative_prompt ? `<div class="mt-1 text-xs text-red-400/80">${escapeHtml(p.negative_prompt)}</div>` : ""}
        </div>
      </div>
      <div class="mt-3 space-y-2">
        <details class="acc" open><summary>📤 Hosts <span class="chev">▾</span></summary><div class="acc-body">${hostLinks}</div></details>
        ${acc("🎛️ Parámetros", `<div class="grid grid-cols-1 sm:grid-cols-2">${params}</div>`)}
        ${acc("🧩 LoRAs", loras)}
        ${acc("⏱️ Timing y costo", `${Object.entries(item.timing || p.timing || {}).map(([k, v]) => rows(k, typeof v === "number" ? v.toFixed(2) : v)).join("")}`)}
        ${item.beam_filename ? acc("🗄️ En Beam", `${rows("filename", item.beam_filename)}`) : ""}
      </div>
      <div class="mt-3 flex gap-2 justify-end flex-wrap">
        ${item.beam_filename ? `<button class="btn-secondary" data-dl-orig>⬇ Original (R2)</button>` : ""}
        <button class="btn-secondary" data-close-modal>Cerrar</button>
        ${onDelete ? `<button class="btn-ghost btn-danger" data-del>Borrar registro</button>` : ""}
        ${onRedo ? `<button class="btn-primary" data-redo>↻ Rehacer esta generación</button>` : ""}
      </div>
    </div>`, { width: "max-w-2xl" });

  $$("[data-copy]", document).forEach(b => b.addEventListener("click", () => copy(b.dataset.copy)));
  const redoBtn = $("#modal-root [data-redo]");
  if (redoBtn) redoBtn.addEventListener("click", () => { close(); onRedo && onRedo(item); });
  const delBtn = $("#modal-root [data-del]");
  if (delBtn) delBtn.addEventListener("click", async () => { close(); onDelete && onDelete(item); });
  // ⬇ Original: baja del R2 el archivo tal cual (webp NO: calidad original, ?raw).
  // Fallback a la URL que haya (hosts/imageUrl) si no hay filename.
  const dlBtn = $("#modal-root [data-dl-orig]");
  if (dlBtn) dlBtn.addEventListener("click", async () => {
    const fn = item.beam_filename;
    const h = item.hosts || {};
    // Prueba las dos keys del R2 (v9.7 krea2/… y vieja outputs/) + hosts de respaldo.
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
        return;
      } catch { /* probar siguiente */ }
    }
    toast("Descarga: ninguna URL respondió", "err");
  });
  return { close };
}

function fmtDate2(ts) {
  return new Date(ts).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}

// Select de modelos reutilizable (popula de state.models)
export function modelSelect(type, current = "") {
  const key = { unet: "unets", clip: "clips", vae: "vaes", upscale: "upscalers" }[type] || "loras";
  const list = (state.models && state.models[key]) || [];
  const sel = el("select", { class: "input !py-1.5" });
  if (!list.length) sel.append(el("option", { value: "" }, [`sin datos — usá Models ↗`]));
  for (const m of list) {
    sel.append(el("option", { value: m.logical, selected: m.logical === current }, [m.logical]));
  }
  return sel;
}

// Datalist global de LoRAs (para los inputs de la lista)
// Cuando hay colisión de stem entre v1/v2/general, las opciones se muestran
// con el prefijo "v1/" / "v2/" para que el usuario elija la que quiere.
// Si no hay colisión (stem único), el comportamiento viejo sigue igual.
export function ensureLoraDatalist() {
  let dl = $("#lora-options");
  if (!dl) { dl = el("datalist", { id: "lora-options" }); document.body.append(dl); }
  const loras = state.models?.loras || [];
  // Mapa stem → cantidad de versiones
  const stemCount = new Map();
  for (const m of loras) stemCount.set(m.logical, (stemCount.get(m.logical) || 0) + 1);
  const html = loras.map(m => {
    const value = stemCount.get(m.logical) > 1
      ? `${(m.version || "general")}/${m.logical}`
      : m.logical;
    const label = m.character ? `${m.logical}  · ${m.character}` : m.logical;
    return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
  }).join("");
  dl.innerHTML = html;
  return dl;
}
