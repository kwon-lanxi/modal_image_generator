import { state, saveLastSeed, getLastSeed, randomSeed, saveGenerationsLocal, saveModelsCache, loadModelsCache, saveOutputsCache, loadOutputsCache, sanitizeRecordForStorage, MODELS_CACHE_TTL, saveForms, loadForms, clearLocalCache } from "./state.js";
import { api, beamImageUrl, displayImg, cachedImgDataUrl, r2ImageUrl, r2FallbackUrl, isR2Url } from "./api.js";
import { animReveal, animTabIn } from "./anim.js";
import { $, $$, el, toast, confirmModal, openModal, accordionHTML, escapeHtml, fmtBytes, copy, uid, debounce } from "./ui.js";
import { createLoraList, createImageInput, modelSelect, ensureLoraDatalist, makeImg } from "./components.js";
import { createGallery } from "./gallery/index.js";
import { downloadOriginal } from "./gallery/viewer.js";
import { DIMENSION_PRESETS, SAMPLERS, SCHEDULERS, TAB_META } from "./config.js";

let currentTab = "generate";

// ══════════════════════════════════════════════════════════════
// NAV
// ══════════════════════════════════════════════════════════════
const CREATE_TABS = new Set(["generate", "variation", "edit", "depth", "upscale"]);

function renderNav() {
  const side = $("#sidebar");
  const bar = $("#tabbar");
  const subnav = $("#create-subnav");
  side.innerHTML = ""; bar.innerHTML = "";
  if (subnav) subnav.innerHTML = "";
  const primaryTabs = [
    { key: "create", label: "Crear", icon: "●" },
    ...TAB_META.filter(t => !CREATE_TABS.has(t.key)),
  ];
  const createTabs = TAB_META.filter(t => CREATE_TABS.has(t.key));
  const makeItem = (t, compact = false) => {
    const item = el("button", {
      type: "button",
      class: `nav-item${t.key === currentTab || (t.key === "create" && CREATE_TABS.has(currentTab)) ? " active" : ""}`,
      "aria-current": t.key === currentTab || (t.key === "create" && CREATE_TABS.has(currentTab)) ? "page" : null,
      title: t.label,
    }, [
      el("span", { class: "nav-icon", "aria-hidden": "true", html: t.icon }),
      el("span", { class: compact ? "nav-label" : "", html: t.label }),
    ]);
    item.addEventListener("click", () => switchTab(t.key === "create" ? "generate" : t.key));
    return item;
  };
  for (const t of primaryTabs) {
    side.append(makeItem(t));
    bar.append(makeItem(t, true));
  }
  for (const t of createTabs) {
    side.append(makeItem(t));
    if (subnav) subnav.append(makeItem(t, true));
  }
}

export function switchTab(key) {
  const moveFocusToMain = document.activeElement?.closest?.("#tabbar, #sidebar, #create-subnav");
  currentTab = key;
  $$(".tab").forEach(s => s.classList.add("hidden"));
  if (key === "create") key = "generate";
  const target = $(`#tab-${key}`);
  if (target) {
    target.classList.remove("hidden");
    animTabIn(target); // anime.js: entrada suave al cambiar de pestaña
  }
  renderNav();
  const createSubnav = $("#create-subnav");
  if (createSubnav) createSubnav.classList.toggle("hidden", !CREATE_TABS.has(key));
  // Llevar el foco al contenido tras una navegación con teclado.
  const main = $("#main-content");
  if (main && moveFocusToMain) main.focus({ preventScroll: true });
  // Recordar en qué pestaña estabas para restaurarla tras un reload del celular
  if (state.forms) { state.forms.currentTab = key; saveForms(state.forms); }
  // v2.3: galería (imágenes) e historial (log GPU) separados; las operaciones
  // del manager viven en Models (donde se administra el manager).
  if (key === "models") { if (!state.models) refreshModels(true); loadManagerHistory(); }
  if (key === "gallery") refreshHistoryGen();
  if (key === "history") loadGpuLog();
}

// ══════════════════════════════════════════════════════════════
// MOTOR DE GENERACIÓN COMPARTIDO
// ══════════════════════════════════════════════════════════════
const GEN = {
  generate:  { endpoint: "generate",  cfg: 1.0, scheduler: "beta",   denoise: 1.0,  imageMode: "references" },
  variation: { endpoint: "variation", cfg: 1.0, scheduler: "beta",   denoise: 0.65, imageMode: "single" },
  edit:      { endpoint: "edit",      cfg: 5.0, scheduler: "normal", denoise: 0.7,  imageMode: "double", editPrompt: true, lockedLoras: [{ name: "krea2_identity_edit_v1_1", weight: 1.0 }] },
  depth:     { endpoint: "depth",     cfg: 1.0, scheduler: "beta",   denoise: 1.0,  imageMode: "depth" },
};
const FORM_CACHE = {};

// ══════════════════════════════════════════════════════════════
// ESTADO DEL FORMULARIO — persiste en localStorage para que el
// celular (que recarga la pestaña al cambiar de app) no pierda
// prompts, LoRAs, seeds ni imágenes al volver.
// ══════════════════════════════════════════════════════════════
let _formSaveTimer = 0;

function collectFormState(tab, { withImages = false } = {}) {
  const f = FORM_CACHE[tab];
  if (!f) return null;
  const v = (id) => $(id)?.value;
  const s = {
    positive_prompt: v(`#f-${tab}-positive`),
    edit_prompt: v(`#f-${tab}-editprompt`),
    negative_prompt: v(`#f-${tab}-negative`),
    seed: v(`#f-${tab}-seed`), steps: v(`#f-${tab}-steps`), cfg: v(`#f-${tab}-cfg`),
    width: v(`#f-${tab}-width`), height: v(`#f-${tab}-height`),
    sampler: v(`#f-${tab}-sampler`), scheduler: v(`#f-${tab}-scheduler`),
    mu: v(`#f-${tab}-mu`), denoise: v(`#f-${tab}-denoise`),
    strength: v(`#f-${tab}-strength`), grounding: v(`#f-${tab}-grounding`),
    upscale_scale: v(`#f-${tab}-upscale_scale`),
    tile_size: v(`#f-${tab}-tile_size`), tile_overlap: v(`#f-${tab}-tile_overlap`),
    useupscale: !!$(`#f-${tab}-useupscale`)?.checked,
    tiled: !!$(`#f-${tab}-tiled`)?.checked,
    b64: !!$(`#f-${tab}-b64`)?.checked,
    preprocessed: !!$(`#f-${tab}-preprocessed`)?.checked,
  };
  s.unet = f.unetSel?.value; s.clip = f.clipSel?.value; s.vae = f.vaeSel?.value; s.upscale_model = f.upScaleSel?.value;
  const loras = f.loraList?.get();
  if (loras && Object.keys(loras).length) s.loras = loras;
  // Imágenes: se guardan solo cuando hace falta (pagehide/switch) porque pesan;
  // el debounce de tipeo guarda texto + LoRAs (rápido).
  if (withImages) {
    try {
      const imgs = {};
      if (f.imageInput) { const im = f.imageInput.get(); if (im?.dataUrl) imgs.single = im.dataUrl; }
      if (f.imageA) {
        const a = f.imageA.get(); if (a?.dataUrl) imgs.a = a.dataUrl;
        const b = f.imageB.get(); if (b?.dataUrl) imgs.b = b.dataUrl;
      }
      if (f.refs) {
        const rr = f.refs.map(r => { const im = r.ii.get(); return im?.dataUrl || ""; }).filter(Boolean);
        if (rr.length) imgs.refs = rr;
      }
      if (Object.keys(imgs).length) s.images = imgs;
    } catch { /* imágenes */ }
  }
  return s;
}

function applyFormState(tab) {
  const f = FORM_CACHE[tab];
  const s = state.forms?.[tab];
  if (!f || !s || typeof s !== "object") return;
  const set = (sel, val) => {
    const e = $(sel);
    if (e && val !== undefined && val !== null && val !== "") e.value = val;
    // si es un sliderNum, sincronizar también el range (el number no dispara input)
    const r = $(sel + "__r");
    if (r && val !== undefined && val !== null && val !== "") r.value = val;
  };
  set(`#f-${tab}-positive`, s.positive_prompt);
  set(`#f-${tab}-editprompt`, s.edit_prompt);
  set(`#f-${tab}-negative`, s.negative_prompt);
  set(`#f-${tab}-seed`, s.seed); set(`#f-${tab}-steps`, s.steps); set(`#f-${tab}-cfg`, s.cfg);
  set(`#f-${tab}-width`, s.width); set(`#f-${tab}-height`, s.height);
  set(`#f-${tab}-sampler`, s.sampler); set(`#f-${tab}-scheduler`, s.scheduler);
  set(`#f-${tab}-mu`, s.mu); set(`#f-${tab}-denoise`, s.denoise);
  set(`#f-${tab}-strength`, s.strength); set(`#f-${tab}-grounding`, s.grounding);
  set(`#f-${tab}-upscale_scale`, s.upscale_scale);
  set(`#f-${tab}-tile_size`, s.tile_size); set(`#f-${tab}-tile_overlap`, s.tile_overlap);
  const c = (sel, val) => { const e = $(sel); if (e && val !== undefined) e.checked = !!val; };
  c(`#f-${tab}-useupscale`, s.useupscale); c(`#f-${tab}-tiled`, s.tiled);
  c(`#f-${tab}-b64`, s.b64); c(`#f-${tab}-preprocessed`, s.preprocessed);
  if (s.useupscale) { const uo = $(`#f-${tab}-upscale-opts`); if (uo) uo.classList.remove("hidden"); }
  if (s.tiled) { const to = $(`#f-${tab}-tiled-opts`); if (to) to.classList.remove("hidden"); }
  if (s.unet && f.unetSel) f.unetSel.value = s.unet;
  if (s.clip && f.clipSel) f.clipSel.value = s.clip;
  if (s.vae && f.vaeSel) f.vaeSel.value = s.vae;
  if (s.upscale_model && f.upScaleSel) f.upScaleSel.value = s.upscale_model;
  if (s.loras && typeof s.loras === "object") f.loraList?.set(s.loras);
  if (s.images) {
    try {
      if (s.images.single && f.imageInput) f.imageInput.set(s.images.single);
      if (s.images.a && f.imageA) f.imageA.set(s.images.a);
      if (s.images.b && f.imageB) f.imageB.set(s.images.b);
      if (Array.isArray(s.images.refs) && f.refs) {
        s.images.refs.forEach((d, i) => { const r = f.refs[i]; if (r && d) r.ii.set(d); });
      }
    } catch (e) { /* dataURLs viejas */ }
  }
}

function saveFormsNow(withImages = false) {
  state.forms = state.forms || {};
  for (const tab of Object.keys(FORM_CACHE)) {
    const s = collectFormState(tab, { withImages });
    if (s) state.forms[tab] = s;
  }
  saveForms(state.forms);
}

function scheduleFormSave() {
  clearTimeout(_formSaveTimer);
  _formSaveTimer = setTimeout(() => saveFormsNow(false), 800);
}

function field(label, control) {
  return el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, [label]), control]);
}
function sliderNum({ id, def, min, max, step }) {
  // El id queda en el input number (fuente de verdad = lo que tipeás); el range lleva
  // `${id}__r`. Antes compartían el MISMO id → $() devolvía el range (clampeado a
  // min/max) y al restaurar el número quedaba con el valor viejo del browser: la
  // causa de que mu/denoise/strength "se movieran de lugar" en el celular.
  const range = el("input", { type: "range", id: id + "__r", min, max, step, value: def, class: "flex-1" });
  const num = el("input", { type: "number", id, min, max, step, value: def, class: "input !py-1 w-20 text-center mono" });
  const sync = (v) => { v = Math.min(max, Math.max(min, parseFloat(v) || def)); range.value = v; num.value = v; };
  range.addEventListener("input", () => sync(range.value));
  num.addEventListener("input", () => sync(num.value));
  return el("div", { class: "flex items-center gap-2" }, [range, num]);
}
function numField(label, id, def, min, max, step = 1) {
  return el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, [label]), el("input", { id, type: "number", value: def, min, max, step, class: "input !py-1.5" })]);
}
function selectField(label, id, options, selected) {
  const sel = el("select", { id, class: "input !py-1.5" });
  for (const o of options) sel.append(el("option", { value: o, selected: o === selected }, [o]));
  return el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, [label]), sel]);
}
// Input con datalist: se puede elegir de la lista o tipear un valor propio.
function comboField(label, id, options, selected) {
  const dlId = `dl-${id}`;
  let dl = document.getElementById(dlId);
  if (!dl) {
    dl = el("datalist", { id: dlId });
    for (const o of options) dl.append(el("option", { value: o }));
    document.body.append(dl);
  }
  const input = el("input", { id, class: "input !py-1.5", list: dlId, value: selected, spellcheck: "false" });
  return el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, [label]), input]);
}
function toggleField(id, labelText, checked = false) {
  return el("label", { class: "flex items-center gap-2 text-sm" }, [
    el("input", { type: "checkbox", id, class: "toggle-check", checked }),
    el("span", { class: "toggle" }), labelText,
  ]);
}

function renderGenForm(tab) {
  if (FORM_CACHE[tab]) return FORM_CACHE[tab];
  const cfg = GEN[tab];
  const f = {};

  // ── Prompt ──
  // Botón que lee el portapapeles: si contiene el JSON { prompt, neg_prompt } rellena los campos.
  const pasteBtn = `<button type="button" id="f-${tab}-pastebtn" class="btn-secondary w-full !py-1.5 text-xs mt-2" title="Pega el JSON {prompt, neg_prompt} desde el portapapeles">📋 pegar prompt (portapapeles)</button>`;
  const promptBody = cfg.editPrompt ? `
    <label class="block text-xs text-dim mb-1">Edit prompt</label>
    <textarea id="f-${tab}-editprompt" class="input" rows="3" placeholder="Qué querés editar…"></textarea>
    <label class="block text-xs text-dim mb-1 mt-2">Negative</label>
    <textarea id="f-${tab}-negative" class="input" rows="2" placeholder="blurry, low quality…"></textarea>
    ${pasteBtn}`
    : `
    <label class="block text-xs text-dim mb-1">Prompt</label>
    <textarea id="f-${tab}-positive" class="input" rows="4" placeholder="Describe la imagen…"></textarea>
    <label class="block text-xs text-dim mb-1 mt-2">Negative</label>
    <textarea id="f-${tab}-negative" class="input" rows="2" placeholder="blurry, low quality…"></textarea>
    ${pasteBtn}`;

  // ── Imagen ──
  const imgAccBody = el("div", { class: "space-y-2" });
  if (cfg.imageMode === "single") {
    const wrap = el("div");
    f.imageInput = createImageInput(wrap, { label: "Imagen a variar" });
    imgAccBody.append(wrap, field("Strength (referencia)", sliderNum({ id: `f-${tab}-strength`, def: 0.5, min: 0, max: 1, step: 0.05 })));
  } else if (cfg.imageMode === "double") {
    const wrapA = el("div"); const wrapB = el("div");
    f.imageA = createImageInput(wrapA, { label: "Imagen A (principal)" });
    f.imageB = createImageInput(wrapB, { label: "Imagen B (opcional, se compone)" });
    imgAccBody.append(wrapA, wrapB, field("Grounding px", el("input", { id: `f-${tab}-grounding`, type: "number", class: "input", value: "64", min: "8", step: "8" })));
  } else if (cfg.imageMode === "depth") {
    const wrap = el("div");
    f.imageInput = createImageInput(wrap, { label: "Imagen (RGB o mapa de depth)" });
    imgAccBody.append(wrap, toggleField(`f-${tab}-preprocessed`, "Ya es un mapa de profundidad (no extraer depth)"));
  } else {
    // generate: lista de reference_images
    const refList = el("div", { class: "space-y-3" });
    const refs = [];
    const addRef = () => {
      const row = el("div", { class: "space-y-1 border border-edge rounded-lg p-2" });
      const ii = createImageInput(row, { label: `Referencia ${refs.length + 1}`, showLast: refs.length === 0 });
      const str = sliderNum({ id: `refstr_${refs.length}`, def: 0.5, min: 0, max: 1, step: 0.05 });
      row.append(field("Strength", str));
      const e = { ii, str, row };
      const rm = el("button", { class: "btn-ghost btn-danger text-xs mt-1", html: "✕ quitar referencia", onclick: () => { row.remove(); refs.splice(refs.indexOf(e), 1); } });
      row.append(rm);
      refs.push(e);
      refList.append(row);
    };
    const addBtn = el("button", { class: "btn-secondary !py-1 text-xs mb-1", html: "＋ Agregar imagen de referencia", onclick: addRef });
    imgAccBody.append(addBtn, refList);
    f.refs = refs;
    addRef();
  }
  f.getImage = () => (f.imageInput ? f.imageInput.get() : null);

  // ── Sampling ──
  const samplingBody = el("div", { class: "space-y-2" }, [
    el("div", { class: "grid grid-cols-2 sm:grid-cols-3 gap-2" }, [
      numField("Steps", `f-${tab}-steps`, 28, 1, 200),
      numField("CFG", `f-${tab}-cfg`, cfg.cfg, 0, 30, 0.5),
      numField("Seed", `f-${tab}-seed`, 0, 0, 4294967295),
    ]),
    el("div", { class: "flex items-center gap-2" }, [
      el("button", { id: `f-${tab}-dice`, class: "btn-secondary !py-1 !px-2 text-sm", html: "🎲" }),
      toggleField(`f-${tab}-reuseseed`, "reusar última seed"),
    ]),
    el("div", { class: "grid grid-cols-2 gap-2" }, [
      comboField("Sampler (tipeable)", `f-${tab}-sampler`, SAMPLERS, "euler"),
      comboField("Scheduler (tipeable)", `f-${tab}-scheduler`, SCHEDULERS, cfg.scheduler),
    ]),
  ]);

  // ── Dimensiones ──
  const dimBody = el("div", { class: "space-y-2" }, [
    el("div", { class: "grid grid-cols-2 gap-2" }, [
      numField("Width", `f-${tab}-width`, 1024, 64, 4096, 8),
      numField("Height", `f-${tab}-height`, 1024, 64, 4096, 8),
    ]),
    el("div", { class: "flex flex-wrap gap-1.5" }, DIMENSION_PRESETS.map(({ w, h, label }) =>
      el("button", { type: "button", "data-dim": `${w}x${h}`, title: `${w}×${h} px`, class: "btn-ghost !px-2.5 !py-0.5 text-xs border border-edge rounded-md" }, [label]))),
    toggleField(`f-${tab}-aspectlock`, "bloquear aspecto"),
  ]);

  // ── Modelos ──
  const unetSel = modelSelect("unet", "Krea2_Turbo_fp8mixed");
  const clipSel = modelSelect("clip", "qwen_3vl_4b");
  const vaeSel = modelSelect("vae", "wan_2.1_vae");
  const upScaleSel = modelSelect("upscale", "");
  const modelsBody = el("div", { class: "grid sm:grid-cols-3 gap-2" }, [
    el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, ["UNET"]), unetSel]),
    el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, ["CLIP"]), clipSel]),
    el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, ["VAE"]), vaeSel]),
  ]);

  // ── LoRAs ──
  const loraWrap = el("div");
  const loraList = createLoraList(loraWrap, { locked: cfg.lockedLoras || [] });
  loraList.set({});

  // ── Opciones ──
  const optsBody = el("div", { class: "space-y-3" }, [
    field("Mu (time shift)", sliderNum({ id: `f-${tab}-mu`, def: 1.15, min: 0, max: 3, step: 0.05 })),
    field("Denoise", sliderNum({ id: `f-${tab}-denoise`, def: cfg.denoise, min: 0, max: 1, step: 0.05 })),
    el("div", { class: "space-y-1.5" }, [
      toggleField(`f-${tab}-useupscale`, "usar upscaler", false),
      el("div", { id: `f-${tab}-upscale-opts`, class: "grid grid-cols-2 gap-2 hidden" }, [
        el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, ["Upscale model"]), upScaleSel]),
        el("div", {}, [el("label", { class: "block text-xs text-dim mb-1" }, ["Upscale scale"]), el("input", { id: `f-${tab}-upscale_scale`, type: "number", step: "0.1", min: "1", value: "1.5", class: "input" })]),
      ]),
    ]),
    el("div", { class: "space-y-1.5" }, [
      toggleField(`f-${tab}-tiled`, "VAE tiled"),
      el("div", { id: `f-${tab}-tiled-opts`, class: "grid grid-cols-2 gap-2 hidden" }, [
        numField("Tile size", `f-${tab}-tile_size`, 512, 256, 2048, 64),
        numField("Overlap", `f-${tab}-tile_overlap`, 64, 8, 512, 8),
      ]),
    ]),
    toggleField(`f-${tab}-b64`, "devolver base64", state.settings.returnBase64),
  ]);

  // ── Reset global (un solo botón, pensado para móvil) ──
  // Limpia TODO el formulario de la pestaña a los valores predeterminados y
  // guarda el estado para que un reload del celular no restaure lo viejo.
  const setVal = (id, val) => { const e = $(id); if (e) e.value = val; const r = $(id + "__r"); if (r) r.value = val; };
  const setCheck = (id, on) => { const e = $(id); if (e) e.checked = !!on; };
  const resetGenForm = () => {
    // Prompt
    setVal(`#f-${tab}-positive`, "");
    setVal(`#f-${tab}-editprompt`, "");
    setVal(`#f-${tab}-negative`, "");
    // Imágenes
    if (f.imageInput) f.imageInput.clear();
    if (f.imageA) { f.imageA.clear(); f.imageB.clear(); }
    if (f.refs) {
      for (let i = f.refs.length - 1; i > 0; i--) { const r = f.refs[i]; r.row.remove(); f.refs.splice(i, 1); }
      const first = f.refs[0]; if (first) first.ii.clear();
    }
    setVal(`#f-${tab}-strength`, 0.5);
    setVal(`#f-${tab}-grounding`, 64);
    setCheck(`#f-${tab}-preprocessed`, false);
    // Sampling
    setVal(`#f-${tab}-steps`, 28);
    setVal(`#f-${tab}-cfg`, cfg.cfg);
    setVal(`#f-${tab}-seed`, 0);
    setVal(`#f-${tab}-sampler`, "euler");
    setVal(`#f-${tab}-scheduler`, cfg.scheduler);
    setCheck(`#f-${tab}-reuseseed`, false);
    // Dimensiones
    setVal(`#f-${tab}-width`, 1024);
    setVal(`#f-${tab}-height`, 1024);
    setCheck(`#f-${tab}-aspectlock`, false);
    // Modelos
    f.unetSel.value = "Krea2_Turbo_fp8mixed";
    f.clipSel.value = "qwen_3vl_4b";
    f.vaeSel.value = "wan_2.1_vae";
    f.upScaleSel.value = "";
    // LoRAs
    f.loraList.set({});
    // Opciones
    setVal(`#f-${tab}-mu`, 1.15);
    setVal(`#f-${tab}-denoise`, cfg.denoise);
    setCheck(`#f-${tab}-useupscale`, false);
    setVal(`#f-${tab}-upscale_scale`, 1.5);
    setCheck(`#f-${tab}-tiled`, false);
    setVal(`#f-${tab}-tile_size`, 512);
    setVal(`#f-${tab}-tile_overlap`, 64);
    setCheck(`#f-${tab}-b64`, state.settings.returnBase64);
    const uo = $(`#f-${tab}-upscale-opts`); if (uo) uo.classList.add("hidden");
    const to = $(`#f-${tab}-tiled-opts`); if (to) to.classList.add("hidden");
    scheduleFormSave();
    toast("Formulario restablecido a valores predeterminados", "ok", 2000);
  };

  const form = el("div", { class: "space-y-2.5" }, [
    accordionHTML({ title: cfg.editPrompt ? "Prompt de edición" : "Prompt", icon: "▤", open: true, body: promptBody }),
    ...(cfg.imageMode !== "references" ? [accordionHTML({ title: "Imagen", icon: "◉", open: true, body: imgAccBody })] : [accordionHTML({ title: "Imágenes de referencia", icon: "◉", open: false, body: imgAccBody })]),
    accordionHTML({ title: "Sampling", icon: "◌", open: true, body: samplingBody }),
    accordionHTML({ title: "Dimensiones", icon: "□", open: true, body: dimBody }),
    accordionHTML({ title: "Modelos", icon: "▦", open: false, body: modelsBody }),
    accordionHTML({ title: "LoRAs", icon: "▧", open: true, body: loraWrap }),
    accordionHTML({ title: "Opciones", icon: "＋", open: false, body: optsBody }),
    el("div", { class: "generation-actions flex gap-2" }, [
      el("button", { id: `f-${tab}-reset`, class: "btn-ghost shrink-0 !py-3 text-sm border border-edge rounded-lg", html: "↺ Limpiar", title: "Resetear TODO el formulario a los valores predeterminados" }),
      el("button", { id: `f-${tab}-go`, class: "btn-primary flex-1 !py-3 text-base", html: "✦ Generar" }),
    ]),
  ]);
  $("#gen-form-" + tab).append(form);

  // ── wire eventos ──
  $(`#f-${tab}-pastebtn`).addEventListener("click", () => pasteFromClipboard(tab));
  $("#f-" + tab + "-reset").addEventListener("click", resetGenForm);
  $("#f-" + tab + "-go").addEventListener("click", () => runGen(tab));
  $("#f-" + tab + "-dice").addEventListener("click", () => {
    const s = randomSeed();
    $("#f-" + tab + "-seed").value = s;
    saveLastSeed(s);
    toast(`Seed local: ${s}`, "ok", 1500);
  });
  $("#f-" + tab + "-reuseseed").addEventListener("change", (e) => {
    if (e.target.checked) $("#f-" + tab + "-seed").value = getLastSeed() || randomSeed();
  });
  $$(`[data-dim]`, form).forEach(b => b.addEventListener("click", () => {
    const [w, h] = b.dataset.dim.split("x");
    $("#f-" + tab + "-width").value = w; $("#f-" + tab + "-height").value = h;
  }));
  const lock = $("#f-" + tab + "-aspectlock");
  let ratio = 1;
  const wIn = $("#f-" + tab + "-width"), hIn = $("#f-" + tab + "-height");
  wIn.addEventListener("input", () => { if (lock.checked) { ratio = (parseInt(hIn.value) || 1024) / (parseInt(wIn.value) || 1024); hIn.value = Math.round(parseInt(wIn.value) * ratio); } });
  hIn.addEventListener("input", () => { ratio = (parseInt(hIn.value) || 1024) / (parseInt(wIn.value) || 1024); });
  $("#f-" + tab + "-tiled").addEventListener("change", (e) => $("#f-" + tab + "-tiled-opts").classList.toggle("hidden", !e.target.checked));
  $("#f-" + tab + "-useupscale").addEventListener("change", (e) => $("#f-" + tab + "-upscale-opts").classList.toggle("hidden", !e.target.checked));

  f.unetSel = unetSel; f.clipSel = clipSel; f.vaeSel = vaeSel; f.upScaleSel = upScaleSel;
  f.loraList = loraList;
  FORM_CACHE[tab] = f;

  // ── Persistencia del formulario (el celular recarga al cambiar de app) ──
  form.addEventListener("input", scheduleFormSave);
  form.addEventListener("change", scheduleFormSave);
  form.addEventListener("click", scheduleFormSave); // agregar/quitar LoRA, presets, pegar prompt
  applyFormState(tab); // restaurar lo que había quedado guardado
  return f;
}

// ── pegar prompt desde el portapapeles ──
// Lee el portapapeles y si contiene el JSON { prompt, neg_prompt } rellena los campos.
// Tolerante: acepta JSON dentro de fences ```json y/o rodeado de texto.
async function pasteFromClipboard(tab) {
  try {
    let raw = "";
    try { raw = (await navigator.clipboard.readText()) || ""; }
    catch { raw = ""; }
    if (!raw) { toast("Portapapeles vacío o sin permiso de lectura", "err", 3500); return; }
    let text = raw;
    const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (fenced) text = fenced[1];
    const s = text.indexOf("{"), e = text.lastIndexOf("}");
    if (s >= 0 && e > s) text = text.slice(s, e + 1);
    let data;
    try { data = JSON.parse(text); }
    catch { toast("El portapapeles no tiene JSON válido", "err", 3500); return; }
    const pos = data && data.prompt !== undefined && data.prompt !== null ? String(data.prompt) : null;
    const neg = data && data.neg_prompt !== undefined && data.neg_prompt !== null ? String(data.neg_prompt) : null;
    if (pos === null && neg === null) { toast("El JSON necesita prompt y/o neg_prompt", "err", 3500); return; }
    // No pegar los placeholders de la plantilla tal cual (el usuario la copió sin editar).
    if (pos !== null && pos !== "[Paste the exact positive prompt here]") {
      const t = $(`#f-${tab}-positive`) || $(`#f-${tab}-editprompt`);
      if (t) t.value = pos;
    }
    if (neg !== null && neg !== "[negative prompt]") {
      const t = $(`#f-${tab}-negative`);
      if (t) t.value = neg;
    }
    toast("Prompt pegado desde portapapeles ✓", "ok", 1800);
  } catch (err) {
    toast("No se pudo leer el portapapeles: " + err.message, "err", 4000);
  }
}

// ── recolectar payload ──
function collectPayload(tab) {
  const f = FORM_CACHE[tab];
  const cfg = GEN[tab];
  const v = (id) => $(id)?.value;
  const num = (id, def) => { const x = parseFloat(v(id)); return Number.isFinite(x) ? x : def; };
  const p = {};

  if (cfg.editPrompt) p.edit_prompt = v(`#f-${tab}-editprompt`) || "";
  else p.positive_prompt = v(`#f-${tab}-positive`) || "";
  p.negative_prompt = v(`#f-${tab}-negative`) || "";

  // seed local SIEMPRE explícita (nunca 0 → evita el random buggeado del server)
  const seedVal = parseInt(v(`#f-${tab}-seed`), 10);
  p.seed = seedVal > 0 ? seedVal : randomSeed();
  saveLastSeed(p.seed);
  const seedInput = $(`#f-${tab}-seed`);
  if (seedInput) seedInput.value = p.seed; // reflejar la seed usada en el input

  p.width = num(`#f-${tab}-width`, 1024);
  p.height = num(`#f-${tab}-height`, 1024);
  p.steps = num(`#f-${tab}-steps`, 28);
  p.cfg = num(`#f-${tab}-cfg`, cfg.cfg);
  p.sampler_name = v(`#f-${tab}-sampler`) || "euler";
  p.scheduler = v(`#f-${tab}-scheduler`) || cfg.scheduler;
  p.mu = num(`#f-${tab}-mu`, 1.15);
  p.denoise = num(`#f-${tab}-denoise`, cfg.denoise);
  p.use_tiled_vae = $("#f-" + tab + "-tiled").checked;
  p.vae_tile_size = num(`#f-${tab}-tile_size`, 512);
  p.vae_tile_overlap = num(`#f-${tab}-tile_overlap`, 64);
  p.return_base64 = $("#f-" + tab + "-b64").checked;
  const useUp = $("#f-" + tab + "-useupscale")?.checked;
  const up = useUp ? f.upScaleSel.value : "";
  if (up) { p.upscale_model = up; p.upscale_scale = num(`#f-${tab}-upscale_scale`, 1.5); }

  p.unet_name = f.unetSel.value || "Krea2_Turbo_fp8mixed";
  p.clip_name = f.clipSel.value || "qwen_3vl_4b";
  p.vae_name = f.vaeSel.value || "wan_2.1_vae";

  const loras = f.loraList.get();
  if (Object.keys(loras).length) p.lora = loras;

  if (cfg.imageMode === "single") {
    const img = f.getImage();
    if (!img) throw new Error("Subí una imagen para /variation");
    p.image = img.dataUrl;
    p.strength = num(`#f-${tab}-strength`, 0.5);
  } else if (cfg.imageMode === "double") {
    const a = f.imageA.get();
    if (!a) throw new Error("Subí la imagen A para /edit");
    p.image = a.dataUrl;
    const b = f.imageB.get();
    if (b) p.image_b = b.dataUrl;
    p.grounding_px = num(`#f-${tab}-grounding`, 64);
  } else if (cfg.imageMode === "depth") {
    const img = f.getImage();
    if (!img) throw new Error("Subí una imagen para /depth-generate");
    p.image = img.dataUrl;
    p.preprocessed = $("#f-" + tab + "-preprocessed").checked;
  } else {
    const refs = [];
    for (const r of (f.refs || [])) {
      const img = r.ii.get();
      if (img) refs.push({ image: img.dataUrl, strength: parseFloat(r.str.querySelector('input[type="number"]').value) || 0.5 });
    }
    if (refs.length) p.reference_images = refs;
  }
  p.task_id = uid("task");
  return p;
}

// ── ejecutar generación ──
async function runGen(tab) {
  const cfg = GEN[tab];
  let payload;
  try { payload = collectPayload(tab); }
  catch (e) { toast(e.message, "err"); return; }

  const go = $("#f-" + tab + "-go");
  const resultBox = $(`#gen-result-${tab}`);
  go.disabled = true;
  go.classList.add("btn-gen-busy"); // pulso anime.css mientras la GPU trabaja
  const t0 = Date.now();

  const status = el("div", { class: "panel p-4 space-y-3" }, [
    el("div", { class: "flex items-center gap-2 text-sm" }, [el("span", { class: "spinner" }), el("span", { html: `Generando (${cfg.endpoint})…` })]),
    el("div", { class: "progress-ind" }),
    el("div", { class: "text-xs text-dim mono", id: `timer-${tab}` }),
  ]);
  resultBox.innerHTML = "";
  resultBox.append(status);
  const timer = setInterval(() => {
    const s = $(`#timer-${tab}`);
    if (s) s.textContent = `${((Date.now() - t0) / 1000).toFixed(1)}s transcurridos (sin SSE)`;
  }, 250);

  try {
    const res = await api.gpu[cfg.endpoint](payload);
    clearInterval(timer);
    if (!res || res.success === false) throw new Error((res && res.error) || "Fallo desconocido");
    fillResult(tab, res, payload, resultBox);
    window.dispatchEvent(new CustomEvent("krea2:gpu-ok")); // el GPU respondió → dot verde (sin healthz)
  } catch (e) {
    clearInterval(timer);
    resultBox.innerHTML = "";
    const tb = e.data?.traceback ? e.data.traceback.slice(0, 4000) : "";
    resultBox.append(el("div", { class: "panel p-3 border-red-500/40 text-sm space-y-2" }, [
      el("div", { class: "text-red-400 font-medium", html: "⛔ Error: " + escapeHtml(e.message) }),
      tb ? el("details", { class: "acc" }, [el("summary", { html: "traceback" }), el("div", { class: "acc-body mono text-[10px] overflow-x-auto max-h-40", html: escapeHtml(tb) })]) : null,
    ]));
  } finally {
    go.classList.remove("btn-gen-busy");
    go.disabled = false;
  }
}

function metaRow(label, value) {
  return el("div", { class: "flex justify-between gap-2 py-0.5 border-b border-edge/40 last:border-0" }, [
    el("span", { class: "text-dim shrink-0", html: label }),
    el("span", { class: "text-right break-all mono", html: value }),
  ]);
}

// Replica en JS el build_metadata_markdown del usuario (plantilla de post): el caption
// que se guarda como description en imgchest.
function buildMetadataCaption(payload, res, tab, img0) {
  const tim = res.timing || {};
  const pric = res.pricing || {};
  const seed = res.seed ?? payload.seed ?? 0;
  const loras = payload.lora && Object.keys(payload.lora).length ? payload.lora : null;
  const lorasMd = loras
    ? "| **_LoRA_**   | **_Peso_** |\n|--------------|------------|\n" + Object.entries(loras).map(([n, w]) => `| ${n} | ${w} |`).join("\n")
    : "_Sin LoRAs_";
  const created = img0?.filename ? dateFromFilename(img0.filename) : null;
  const fmt = { dateStyle: "short", timeStyle: "short" };
  const creacion = created ? new Date(created).toLocaleString("es-AR", fmt) : new Date().toLocaleString("es-AR", fmt);
  const height = img0?.height || payload.height || 0;
  const width = img0?.width || payload.width || 0;
  return `# Creado el: ${creacion}
**Modo**: ${tab}
**Model**: Krea2
**UNET**: ${payload.unet_name || ""}
**CLIP**: ${payload.clip_name || ""}
**VAE**: ${payload.vae_name || ""}
**μ (time shift)**: ${payload.mu ?? 0}
**Tiempo total del lote**: ${(tim.total || 0).toFixed(2)} s
**Costo**: $${(pric.total || 0).toFixed(6)}

# Prompts
${payload.positive_prompt || payload.edit_prompt || ""}

# Negative prompt
${payload.negative_prompt || ""}

---

# Parámetros
| **_Parámetro_**      | **_Valor_**        |
|----------------------|--------------------|
| **height**           | ${height}           |
| **width**            | ${width}            |
| **Seed**             | ${seed}             |
| **Steps**            | ${payload.steps ?? 0}            |
| **guidance_scale**   | ${payload.cfg ?? 0}   |
| **sampler**          | ${payload.sampler_name || ""}          |
| **scheduler**        | ${payload.scheduler || ""}        |

# LoRAs
${lorasMd}`;
}

function fillResult(tab, res, payload, resultBox) {
  const img0 = res.images?.[0];
  // v9.4/R2: el GPU sube a R2 y devuelve la URL del Worker (webp on-the-fly) cuando R2
  // está configurado; si no, cae a una Beam signed URL. El manager YA NO sirve imágenes
  // (/serve_image eliminado) → nada de construir URLs del manager acá.
  const r2Url = img0?.filename ? r2ImageUrl(img0.filename) : "";       // display (webp)
  const rawUrl = img0?.filename ? r2ImageUrl(img0.filename, { raw: true }) : ""; // original
  const gpuRawUrl = img0?.url && /^https?:\/\//i.test(img0.url) ? img0.url : "";
  // La URL del GPU acaba de generarse y está caliente; con R2 es el Worker (rápido, CDN).
  const imageSrc = img0?.base64 ? `data:image/png;base64,${img0.base64}` : (beamImageUrl(r2Url || gpuRawUrl) || null);
  // Cadena de fallbacks del <img>: 1) la OTRA key del R2 (outputs/ vs krea2/YYYY/MM/DD/),
  // 2) la URL que devolvió el GPU. Cada error del <img> consume el siguiente.
  const fallbackChain = [];
  if (!img0?.base64) {
    const alt = img0?.filename ? r2FallbackUrl(img0.filename) : "";
    if (alt && alt !== r2Url) fallbackChain.push(beamImageUrl(alt));
    if (gpuRawUrl && gpuRawUrl !== r2Url && gpuRawUrl !== alt) fallbackChain.push(beamImageUrl(gpuRawUrl));
  }
  const tim = res.timing || {};
  const pric = res.pricing || {};
  const meta = res.metadata || {};
  const hw = meta.hardware || {};

  const rec = {
    id: payload.task_id,
    sync_key: state.settings.syncKey,
    tab,
    payload,
    hosts: {},
    beam_filename: img0?.filename || null,
    image_url: r2Url || gpuRawUrl || null, // para mostrar la tarjeta aunque no haya uploads
    imageUrl: r2Url || gpuRawUrl || null,
    r2Url,
    rawUrl,
    size_bytes: img0?.size_bytes ?? null,
    size_human: img0?.size_human ?? null,
    width: img0?.width ?? payload.width ?? null,
    height: img0?.height ?? payload.height ?? null,
    seed: res.seed ?? payload.seed ?? null,
    timing: tim,
    pricing: pric,
    created_at: Date.now(),
  };

  // Caption de metadatos para imgchest (build_metadata_markdown en JS)
  const caption = buildMetadataCaption(payload, res, tab, img0);
  const seed = res.seed ?? payload.seed;
  const hostRow = el("div", { class: "flex gap-1.5 flex-wrap" });
  for (const host of ["imgchest", "catbox", "koofr"]) {
    const b = el("button", { class: "btn-secondary !py-1 text-xs", html: `⬆ ${host}`, onclick: () => uploadToHost(tab, host, img0, hostRow, rec.id, { caption, seed }) });
    hostRow.append(b);
  }

  // ── Contenido de metadatos (va dentro de la pestaña plegable) ──
  const metaBox = el("div", { class: "space-y-2 min-w-0" }, [
    // Tamaño destacado
    el("div", { class: "flex items-center gap-2 rounded-lg bg-accent-500/10 border border-accent-500/30 px-3 py-2" }, [
      el("span", { class: "text-[11px] text-dim uppercase tracking-wide", html: "Tamaño" }),
      el("span", { class: "text-lg font-bold mono leading-none", html: `${img0?.width ?? "?"} × ${img0?.height ?? "?"}` }),
      el("span", { class: "text-xs text-dim", html: img0?.size_bytes != null ? `(${fmtBytes(img0.size_bytes)})` : "" }),
    ]),
    el("div", { class: "space-y-1" }, [
      metaRow("Seed", String(res.seed ?? "—")),
      metaRow("Archivo", escapeHtml(img0?.filename || "—")),
      metaRow("SHA256", img0?.sha256 ? `<span title="${escapeHtml(img0.sha256)}">${escapeHtml(img0.sha256.slice(0, 16))}…</span>` : "—"),
      metaRow("Steps", String(payload.steps ?? "—")),
      metaRow("CFG", String(payload.cfg ?? "—")),
      metaRow("Sampler", escapeHtml(payload.sampler_name || "—")),
      metaRow("Scheduler", escapeHtml(payload.scheduler || "—")),
      metaRow("Mu", String(payload.mu ?? "—")),
      metaRow("Denoise", String(payload.denoise ?? "—")),
      metaRow("Resolución pedida", `${payload.width ?? "?"} × ${payload.height ?? "?"}`),
      metaRow("UNET", escapeHtml(payload.unet_name || "—")),
      metaRow("CLIP", escapeHtml(payload.clip_name || "—")),
      metaRow("VAE", escapeHtml(payload.vae_name || "—")),
      ...(payload.upscale_model ? [metaRow("Upscale", `${escapeHtml(payload.upscale_model)} ×${payload.upscale_scale ?? ""}`)] : []),
      ...(Object.keys(payload.lora || {}).length ? [metaRow("LoRAs", Object.entries(payload.lora).map(([n, w]) => `${escapeHtml(n)} ${w}`).join(", "))] : []),
    ]),
    el("details", { class: "acc" }, [
      el("summary", { html: `⏱️ Timing (total ${(tim.total || 0).toFixed(2)}s) <span class='chev'>▾</span>` }),
      el("div", { class: "acc-body mono text-[12px] grid grid-cols-2 gap-x-3" }, [
        ...Object.entries(tim).map(([k, val]) => el("div", { class: "flex justify-between" }, [el("span", { class: "text-dim" }, [k]), el("span", {}, [typeof val === "number" ? val.toFixed(2) + "s" : String(val)])])),
        ...(pric.config ? [el("div", { class: "col-span-2 text-dim pt-1", html: `gpu/s ${escapeHtml(pric.config.gpu_per_sec)} · total/s ${escapeHtml(pric.config.total_per_sec)}` })] : []),
      ]),
    ]),
    el("details", { class: "acc" }, [
      el("summary", { html: `💰 Costo ($${(pric.total ?? 0).toFixed(4)}) <span class='chev'>▾</span>` }),
      el("div", { class: "acc-body mono text-[12px] space-y-0.5" }, [
        metaRow("sampling", `$${(pric.sampling ?? 0).toFixed(4)}`),
        metaRow("total", `$${(pric.total ?? 0).toFixed(4)}`),
      ]),
    ]),
    el("details", { class: "acc" }, [
      el("summary", { html: "🖥️ Sistema <span class='chev'>▾</span>" }),
      el("div", { class: "acc-body mono text-[12px] space-y-0.5" }, [
        metaRow("GPU", escapeHtml(hw.gpu || "—")),
        metaRow("VRAM", hw.vram_gb != null ? `${hw.vram_gb} GB` : "—"),
        metaRow("CUDA", escapeHtml(hw.cuda || "—")),
        metaRow("VRAM after", meta.vram_after_gb != null ? `${meta.vram_after_gb} GB` : "—"),
        metaRow("Sage", meta.sage_attention_active != null ? String(meta.sage_attention_active) : "—"),
        metaRow("Software", escapeHtml(String(meta.software || "—"))),
      ]),
    ]),
  ]);

  // ── Layout: imagen PRIMERO (arriba en celular); debajo solo ⏱ tiempo · 💰 precio · 📍 dónde está;
  //    todos los metadatos van en una pestaña plegable. ──
  // Imagen en grande, prioridad absoluta (con fallback automático a otra fuente)
  const panel = el("div", { class: "panel p-3 space-y-3" }, [
    imageSrc
      ? (() => {
          // Skeleton visible mientras carga + animación anime.js al aparecer.
          // eager + fetchpriority=high: la imagen empieza a bajar ya, sin esperar.
          // min-height: el skeleton es absolute (no da altura al padre) → sin esto el
          // cuadro colapsa a 0px mientras carga y la imagen aparece con un salto.
          const wrap = el("div", { class: "relative overflow-hidden rounded-lg", style: "min-height:220px" });
          const skel = el("div", { class: "img-skeleton" });
          const im = el("img", { class: "img-fit border border-edge rounded-lg", src: imageSrc, loading: "eager", decoding: "async", fetchpriority: "high" });
          wrap.append(skel, im);
          im.addEventListener("load", () => {
            skel.remove();
            animReveal(im);
            // cache-warm en segundo plano: la próxima vez (redo/galería) es instantáneo.
            // Para URLs de R2 no hace falta (el CDN + la caché HTTP del browser alcanzan;
            // además el data-URL ocupa RAM y exige CORS en el bucket).
            if (!isR2Url(imageSrc)) cachedImgDataUrl(imageSrc).catch(() => {});
          });
          im.addEventListener("error", () => {
            const next = fallbackChain.shift();
            if (next) { im.src = next; return; }
            skel.remove();
          });
          return wrap;
        })()
      : el("div", { class: "text-dim text-xs", html: "sin imagen" }),
    // Barra compacta: SOLO cuánto tardó · precio · dónde está guardada
    el("div", { class: "flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" }, [
      el("span", { class: "badge mono", html: `⏱ ${(tim.total || 0).toFixed(1)}s` }),
      pric.total !== undefined ? el("span", { class: "badge mono", html: `💰 $${pric.total.toFixed(4)}` }) : null,
      el("span", { class: "text-dim text-xs", html: "📍" }),
      el("div", { id: `hosts-${tab}`, class: "flex flex-wrap gap-1.5 min-w-0" }),
    ]),
    // Acciones rápidas + botones de subida manual
    el("div", { class: "flex gap-1.5 flex-wrap" }, [
      el("button", { class: "btn-ghost !py-1 text-xs", html: "📋 copiar prompt", onclick: () => copy(payload.positive_prompt || payload.edit_prompt || "") }),
      el("button", { class: "btn-ghost !py-1 text-xs", html: "⧉ Variation", onclick: () => sendTo("variation", imageSrc) }),
      el("button", { class: "btn-ghost !py-1 text-xs", html: "✎ Edit", onclick: () => sendTo("edit", imageSrc) }),
      rawUrl ? el("a", { class: "btn-ghost !py-1 text-xs inline-flex", href: rawUrl, target: "_blank", rel: "noopener", html: "⬇ Original (R2)" }) : null,
      hostRow,
    ]),
    // Pestaña plegable con TODOS los metadatos
    el("details", { class: "acc" }, [
      el("summary", { html: "📊 Metadatos y detalles <span class='chev'>▾</span>" }),
      el("div", { class: "acc-body space-y-2" }, [metaBox]),
    ]),
  ]);
  resultBox.innerHTML = "";
  resultBox.append(panel);

  state.lastResult = { imageUrl: r2Url || gpuRawUrl || imageSrc, rawUrl, payload, tab, seed: res.seed };
  saveGeneration(rec); // guarda TODO: urls, loras, modelos, metadata (payload sanitizado)

  // Auto-subida: SIEMPRE a los 3 hosts cuando autoUpload está activo.
  // Los uploads corren en paralelo y refrescan el historial UNA sola vez al final.
  if (state.settings.autoUpload) {
    Promise.allSettled(["imgchest", "catbox", "koofr"].map(h =>
      uploadToHost(tab, h, img0, hostRow, rec.id, { refresh: false, caption, seed })
    )).then(() => {
      const i = state.generations.findIndex(g => g.id === rec.id);
      if (i >= 0) refreshHistoryGen();
    });
  }
}

// Descarga la imagen generada como blob probando fuentes en orden:
// R2 ?raw (original, CDN) → URL del GPU (R2 o signed) → base64 local. Devuelve el blob.
// El manager ya NO sirve imágenes (/serve_image eliminado).
async function getUploadBlob(img0) {
  const cands = [];
  if (img0?.filename) cands.push(r2ImageUrl(img0.filename, { raw: true })); // original, calidad 100%
  if (img0?.url && /^https?:\/\//i.test(img0.url)) cands.push(img0.url);
  for (const c of cands) {
    try {
      const r = await fetch(beamImageUrl(c));
      if (!r.ok) continue;
      const b = await r.blob();
      if ((b.type || "").startsWith("image/") && b.size) return b;
    } catch { /* probar siguiente */ }
  }
  if (img0?.base64) {
    const b = await (await fetch(`data:image/png;base64,${img0.base64}`)).blob();
    if (b && b.size) return b;
  }
  throw new Error("No se pudo descargar la imagen generada");
}

async function uploadToHost(tab, host, img0, hostRow, recId, { refresh = true, caption = "", seed = null } = {}) {
  const idx = ["imgchest", "catbox", "koofr"].indexOf(host);
  const btn = hostRow.querySelectorAll("button")[idx];
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spinner !w-3 !h-3 !border-2"></span>`; }
  try {
    const blob = await getUploadBlob(img0);
    const fn = img0?.filename || `krea2_${Date.now()}.png`;
    const res = host === "imgchest"
      ? await api.hosts.imgchest(blob, fn, { title: `Krea2 · ${tab} · seed ${seed ?? "?"}`, description: caption })
      : await api.hosts[host](blob, fn);
    const box = $(`#hosts-${tab}`);
    if (box) {
      if (res.uploaded && !res.url) {
        // catbox: la subida llega a la cuenta pero catbox no manda CORS → no hay URL
        box.prepend(el("div", { class: "flex items-center gap-2 text-xs" }, [
          el("span", { class: "badge shrink-0", html: "catbox" }),
          el("span", { class: "mono text-dim truncate flex-1", html: "subido a tu cuenta (catbox no da la URL por CORS)" }),
          el("a", { class: "btn-ghost !py-0.5 text-accent-500", href: res.accountUrl || "https://catbox.moe", target: "_blank", rel: "noopener", html: "abrir catbox" }),
        ]));
      } else {
        box.prepend(el("div", { class: "flex items-center gap-2 text-xs" }, [
          el("span", { class: "badge shrink-0", html: host === "koofr" ? "koofr (backup)" : host }),
          el("a", { class: "mono text-accent-500 truncate flex-1", href: res.url, target: "_blank", rel: "noopener", html: escapeHtml(res.url) }),
          el("button", { class: "btn-ghost !py-0.5", html: "copiar", onclick: () => copy(res.url) }),
        ]));
      }
    }
    toast(res.uploaded && !res.url ? "catbox: subido a tu cuenta" : `${host}: subida OK`, "ok", 2500);
    const rec = state.generations.find(g => g.id === recId) || state.generations.find(g => g.id === state.lastResult?.payload?.task_id);
    if (rec) {
      rec.hosts = rec.hosts || {};
      if (res.url) rec.hosts[host] = res.url;
      else if (host === "catbox") rec.catboxAccount = true; // sin URL, pero quedó en la cuenta
      saveGeneration(rec, { refresh });
    }
    return res.url;
  } catch (e) {
    toast(`${host}: ${e.message}`, "err", 5000);
    if (btn) { btn.disabled = false; btn.innerHTML = "⬆ " + host; }
    throw e;
  }
}

function saveGeneration(rec, { refresh = true } = {}) {
  const i = state.generations.findIndex(g => g.id === rec.id);
  if (i >= 0) state.generations[i] = rec; else state.generations.unshift(rec);
  if (state.generations.length > 300) state.generations.length = 300;
  // Persistir en localStorage/D1 SIN el base64 gigante (revienta el quota y las filas D1):
  // la imagen se re-carga desde image_url/beam_filename al reabrir la app.
  const light = sanitizeRecordForStorage(rec);
  saveGenerationsLocal(state.generations); // SIEMPRE queda guardado en este dispositivo (sanitiza base64)
  if (state.settings.syncKey) {
    api.sync.generationsSave(light)
      .then(r => { if (!(r && r.ok === false)) state.syncMode = "cloud"; }) // no marcar cloud si D1 falta
      .catch(e => console.warn("sync save:", e.message));
  }
  if (refresh) refreshHistoryGen();
}

export async function sendTo(tab, imageUrl) {
  if (imageUrl) state.lastResult = { ...(state.lastResult || {}), imageUrl };
  switchTab(tab);
  setTimeout(async () => {
    const f = FORM_CACHE[tab];
    if (f?.imageInput && imageUrl) {
      try { f.imageInput.set(await displayImg(imageUrl)); } catch (e) { console.warn(e); }
    }
  }, 60);
}

// Enviar una imagen de la galería al tab Upscale (vía ?raw del Worker para
// no recomprimir). El input la convierte a base64 para el payload.
export async function sendToUpscale(imageUrl) {
  if (!imageUrl) { toast("Sin URL original para upscalear", "err"); return; }
  switchTab("upscale");
  toast("Imagen cargada en Upscale — elegí modelo y dale Upscalear", "ok", 2500);
  setTimeout(async () => {
    const f = FORM_CACHE.upscale;
    if (f?.imageInput) {
      try { f.imageInput.set(await displayImg(imageUrl)); } catch (e) { console.warn(e); }
    }
  }, 60);
}

// ══════════════════════════════════════════════════════════════
// REDO — cargar payload de vuelta y regenerar
// ══════════════════════════════════════════════════════════════
// La imagen se reconstruye desde image_url/beam_filename porque el payload persistido
// NO lleva base64 (se sanitiza para no reventar localStorage/D1).
async function redoImageUrl(item) {
  // Prioridad: URL de host (imgchest/catbox/koofr = CDN rápido, el GPU la resuelve
  // del lado del server) → image_url persistida → Worker de R2 (webp) desde filename
  // probando las dos keys (krea2/YYYY/MM/DD/ de v9.7 y outputs/ de v9.5/v9.6).
  const h = item?.hosts || {};
  const cands = [h.imgchest, h.catbox, h.koofr, item?.imageUrl, item?.image_url].filter(Boolean);
  if (item?.beam_filename) cands.push(r2ImageUrl(item.beam_filename), r2FallbackUrl(item.beam_filename));
  for (const url of [...new Set(cands)]) {
    try {
      const src = await displayImg(url); // data-URL cacheado (hosts CDN van directos, sin proxy)
      if (src && src.startsWith("data:")) return src; // cargó de verdad
    } catch { /* siguiente */ }
  }
  return "";
}

export async function redoGeneration(item) {
  const p = item.payload || {};
  const tab = item.tab || "generate";
  switchTab(tab);
  setTimeout(async () => {
    const f = FORM_CACHE[tab];
    if (!f) return;
    if (p.positive_prompt !== undefined) { const t = $(`#f-${tab}-positive`); if (t) t.value = p.positive_prompt; }
    if (p.edit_prompt !== undefined) { const t = $(`#f-${tab}-editprompt`); if (t) t.value = p.edit_prompt; }
    if (p.negative_prompt !== undefined) { const t = $(`#f-${tab}-negative`); if (t) t.value = p.negative_prompt; }
    const set = (sel, val) => { const e = $(sel); if (e && val !== undefined) e.value = val; };
    set(`#f-${tab}-seed`, p.seed); set(`#f-${tab}-width`, p.width); set(`#f-${tab}-height`, p.height);
    set(`#f-${tab}-steps`, p.steps); set(`#f-${tab}-cfg`, p.cfg);
    set(`#f-${tab}-sampler`, p.sampler_name); set(`#f-${tab}-scheduler`, p.scheduler);
    set(`#f-${tab}-mu`, p.mu); set(`#f-${tab}-denoise`, p.denoise);
    if (p.lora) f.loraList.set(p.lora);
    if (p.unet_name && f.unetSel) f.unetSel.value = p.unet_name;
    if (p.clip_name && f.clipSel) f.clipSel.value = p.clip_name;
    if (p.vae_name && f.vaeSel) f.vaeSel.value = p.vae_name;
    if (p.upscale_model) {
      const upTog = $(`#f-${tab}-useupscale`);
      if (upTog) upTog.checked = true;
      const uo = $(`#f-${tab}-upscale-opts`);
      if (uo) uo.classList.remove("hidden");
      if (f.upScaleSel) f.upScaleSel.value = p.upscale_model;
      set(`#f-${tab}-upscale_scale`, p.upscale_scale);
    }
    set(`#f-${tab}-strength`, p.strength);
    set(`#f-${tab}-grounding`, p.grounding_px);
    const pre = $(`#f-${tab}-preprocessed`);
    if (pre && p.preprocessed !== undefined) pre.checked = !!p.preprocessed;
    // Imagen: primero el base64 que haya en el payload; si no, la URL persistida
    const src = p.image || await redoImageUrl(item);
    if (src && f.imageInput) { try { f.imageInput.set(src); } catch (e) {} }
    if (p.image_b && f.imageB) { try { f.imageB.set(p.image_b); } catch (e) {} }
    if (p.reference_images && f.refs) {
      f.refs[0]?.ii.set(p.reference_images[0]?.image || src);
    }
    toast(`Parámetros cargados de ${tab}`, "ok", 1800);
  }, 80);
}

// ══════════════════════════════════════════════════════════════
// MODELS TAB
// ══════════════════════════════════════════════════════════════
// Aplana el árbol real de /list_all: { mount: { tree: { _root: [...], folders: {...} } } }
// En Modal los volúmenes son mounts SEPARADOS (/krea2-beam-volume y
// /krea2-loras-renew) — ya no hay embedding como en Beam. El dedupe por
// logical+tamaño se conserva por seguridad.
// Convierte la ruta del tree (relativa al mount) en la ruta que espera el manager
// (relativa a VOL_MAIN, ej: "loras/x.safetensors").
// NOTA: el manager v1.1.0 NO manda absolute_path en los nodos del tree
// (solo name/logical_name/relative_path/size_*) → se sintetiza como
// mount.mount + vol_path para que copiar/debugging sigan funcionando.
function toVolumePath(mount, relPath) {
  const treeRoot = String(mount?.path || "").replace(/\/+$/, "");
  const mountRoot = String(mount?.mount || "/krea2-beam-volume").replace(/\/+$/, "");
  let prefix = "";
  if (treeRoot.startsWith(mountRoot)) prefix = treeRoot.slice(mountRoot.length).replace(/^\/+|\/+$/g, "");
  return prefix ? `${prefix}/${relPath}` : relPath;
}

function flattenTree(tree, mountName, mount, seen) {
  const out = [];
  const mountRoot = String(mount?.mount || "").replace(/\/+$/, "");
  const walk = (node) => {
    for (const f of (node?._root || [])) {
      // Dedupe por logical+tamaño (el mismo archivo podía aparecer en varios
      // mounts en el setup viejo de Beam).
      const logical = f.logical_name || String(f.name).replace(/\.(safetensors|ckpt|pt|bin|gguf)$/i, "");
      const key = `${logical}|${f.size_bytes}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const volPath = toVolumePath(mount, f.relative_path); // para delete/move/rename
      out.push({
        logical,
        filename: f.name,
        relative_path: f.relative_path,
        vol_path: volPath,
        absolute_path: f.absolute_path || (mountRoot ? `${mountRoot}/${volPath}` : volPath),
        size_bytes: f.size_bytes,
        size_human: f.size_human,
        _root_volume: mountName,
      });
    }
    for (const sub of Object.values(node?.folders || {})) walk(sub);
  };
  walk(tree);
  return out;
}

let _modelsBusy = false;

// Guarda un snapshot liviano del /list_all en D1 (compartido entre dispositivos) y en
// localStorage, para hidratar la UI sin pegarle al manager en cada carga.
// TTL del snapshot D1: si tiene más de 10 min se IGNORA al hidratar (un snapshot viejo
// revive LoRAs que ya borraste en otro dispositivo — el bug de "los loras quedan").
const D1_SNAPSHOT_TTL = 600_000;
let _snapshotTimer = 0;
function pushSnapshotToD1(models, outputs) {
  if (!state.settings.syncKey || !outputs) return;
  clearTimeout(_snapshotTimer);
  _snapshotTimer = setTimeout(() => {
    api.sync.cacheSave(state.settings.syncKey, "list_all", {
      models, outputs, lorasByVersion: state.lorasByVersion || null, ts: Date.now(),
    }).catch(e => console.warn("snapshot d1:", e.message));
  }, 2500);
}

// force=true → refresco manual (?refresh=true): el manager re-escanea en background.
// force=false (auto-refresh) → el manager devuelve su caché (default 300s TTL) = menos trabajo.
export async function refreshModels(quiet, force = false) {
  if (!state.settings.managerUrl) { if (!quiet) toast("Configurá la URL del manager", "err"); return; }
  if (_modelsBusy) return;
  // Caché local fresco (TTL 300s, "si no hay cambios"): no pegarle a la red.
  // El refresco manual (force) y el intervalo de 10 min sí revalidan.
  if (!force && state.models && state.modelsLoadedAt && Date.now() - state.modelsLoadedAt < MODELS_CACHE_TTL) {
    if (!quiet) toast("Modelos desde caché (frescos)", "ok", 1200);
    return;
  }
  _modelsBusy = true;
  // Si una llamada se cuelga (manager lento), el guard no debe quedar trabado:
  // el botón manual y el auto-refresh tienen que poder reintentar.
  const guardTimer = setTimeout(() => { _modelsBusy = false; }, 25000);
  const out = { loras: [], unets: [], clips: [], vaes: [], upscalers: [] };
  let outputs = null;
  let listAllMeta = null;
  let lorasByVersion = null;
  try {
    // Endpoint real: /list_all (un solo llamado, formato exacto del manager).
    // Devuelve modelos Y outputs → de acá sale TODO: catálogo + galería + snapshot.
    const r = await api.manager.listAll(force);
    listAllMeta = { cached: !!r.cached, cache_age_sec: r.cache_age_sec };
    const seenAll = new Set(); // compartido entre mounts (el mismo archivo aparece en varios)
    for (const [key, apiKey] of Object.entries({ loras: "loras", unets: "unets", clips: "clips", vaes: "vaes", upscalers: "upscalers" })) {
      const mounts = r[apiKey] || {};
      for (const [mountName, mount] of Object.entries(mounts)) {
        out[key].push(...flattenTree(mount.tree, mountName, mount, seenAll));
      }
    }
    if (r.outputs && Array.isArray(r.outputs.files)) outputs = r.outputs.files;
    // LoRAs v1.1.0: list_models?type=lora devuelve {characters, downloaded}.
    // El manager viejo (v2.7) devolvía {by_version:{v1,v2,general}} o {models:[]}.
    // Se aceptan las 3 formas y se normalizan a {v1, v2, general} para la UI
    // (filtro por versión, chips, datalist con prefijos v1/ v2/).
    try {
      const lr = await api.manager.listLoras(null);
      lorasByVersion = _normalizeLorasResponse(lr);
    } catch { /* sin desglose: compat, la UI usa el catálogo plano */ }
  } catch (e) {
    if (!quiet) toast(`list_all: ${e.message} → list_models`, "err", 5000);
    listAllMeta = null; // no vino del list_all → sin info de caché del manager
    await Promise.all(Object.entries({ lora: "loras", unet: "unets", clip: "clips", vae: "vaes", upscale: "upscalers" }).map(async ([type, key]) => {
      try { const r = await api.manager.listModels(type); out[key] = r.models || []; }
      catch (e2) { if (!quiet) toast(`models ${type}: ${e2.message}`, "err"); }
    }));
  } finally {
    clearTimeout(guardTimer);
    _modelsBusy = false;
  }
  state.models = out;
  state.lorasByVersion = lorasByVersion;
  state.modelsLoadedAt = Date.now();
  state.modelsFromManagerCache = listAllMeta && listAllMeta.cached ? listAllMeta : null;
  saveModelsCache(out, lorasByVersion); // caché local → la próxima apertura es instantánea
  // Los outputs del mismo /list_all → caché local + snapshot en D1 (una sola request)
  if (outputs) {
    state.outputs = outputs;
    state.outputsLoadedAt = Date.now();
    saveOutputsCache(outputs);
    pushSnapshotToD1(out, outputs);
  }
  // Anotar version+character en cada lora del catálogo plano (usado por tabla y datalist)
  if (lorasByVersion) _annotateLorasWithVersion(out.loras, lorasByVersion);
  ensureLoraDatalist();
  repopulateModelSelects();
  renderModelsTable();
  updateModelsStamp();
  updateV27UI();
  if (!quiet) toast("Modelos cargados", "ok", 1500);
}

// Normaliza la respuesta de list_models?type=lora a {v1, v2, general}.
// Formas aceptadas:
//  · v1.1.0 (actual): {characters:{models:[{name, character, variants:[{name,
//    filename, steps, size_*, absolute_path}], default}]}, downloaded:{models:[...]}}
//    → v2 = variantes de personajes, v1 = descargados.
//  · v2.7 legacy: {by_version:{v1, v2, general}}.
//  · legacy plano: {models:[...]} → todo a "general".
// Devuelve null si no reconoce ninguna forma (la UI cae al catálogo plano).
function _normalizeLorasResponse(lr) {
  if (!lr) return null;
  if (lr.by_version && (lr.by_version.v1 || lr.by_version.v2 || lr.by_version.general)) {
    return {
      v1: (lr.by_version.v1 || []).map(_decorateLoraVariant),
      v2: (lr.by_version.v2 || []).map(_decorateLoraVariant),
      general: (lr.by_version.general || []).map(_decorateLoraVariant),
    };
  }
  if (Array.isArray(lr.characters?.models) || Array.isArray(lr.downloaded?.models)) {
    const v2 = [];
    for (const ch of (lr.characters?.models || [])) {
      const character = ch.character || ch.name || "";
      for (const v of (ch.variants || [])) {
        const logical = v.name || String(v.filename || "").replace(/\.(safetensors|ckpt|pt|bin|gguf)$/i, "");
        v2.push(_decorateLoraVariant({
          logical,
          name: logical,
          filename: v.filename || "",
          character,
          version: "v2",
          // absolute_path del server (/krea2-loras-renew/...) → ruta relativa
          // al volumen para ops; el manager solo resuelve dentro de VOL_MAIN
          // (los de personajes pueden fallar al borrar: limitación del backend).
          relative_path: _stripVolumeRoot(v.absolute_path) || v.filename || "",
          vol_path: _stripVolumeRoot(v.absolute_path) || v.filename || "",
          size_bytes: v.size_bytes || 0,
          size_human: v.size_human || "",
          _root_volume: "krea2-loras-renew",
        }));
      }
    }
    const v1 = (lr.downloaded?.models || []).map((m) => _decorateLoraVariant({
      logical: m.name || String(m.filename || "").replace(/\.(safetensors|ckpt|pt|bin|gguf)$/i, ""),
      name: m.name || "",
      filename: m.filename || "",
      character: "",
      version: "v1",
      relative_path: m.relative_path || m.filename || "",
      vol_path: m.relative_path
        ? (String(m.relative_path).startsWith("loras/") ? m.relative_path : `loras/${m.relative_path}`)
        : (m.filename ? `loras/${m.filename}` : ""),
      size_bytes: m.size_bytes || 0,
      size_human: m.size_human || "",
      _root_volume: "krea2-beam-volume",
    }));
    return { v1, v2, general: [] };
  }
  if (Array.isArray(lr.models)) {
    return { v1: [], v2: [], general: lr.models.map(_decorateLoraVariant) };
  }
  return null;
}

// "/krea2-loras-renew/Seungcheol/f.safetensors" → "Seungcheol/f.safetensors".
// Si ya es relativa, la devuelve tal cual.
function _stripVolumeRoot(abs) {
  const s = String(abs || "").replace(/\\/g, "/");
  if (!s) return "";
  const m = s.match(/^\/(?:krea2-loras-renew|krea2-beam-volume)\/(.+)$/);
  if (m) return m[1];
  if (s.startsWith("/")) {
    const parts = s.split("/").filter(Boolean);
    return parts.slice(1).join("/") || s;
  }
  return s;
}

// Normaliza una entrada de by_version a la forma {logical, filename, character, version, vol_path, size_bytes, size_human}
function _decorateLoraVariant(m) {
  return {
    logical: m.logical || m.name || (m.filename || "").replace(/\.(safetensors|ckpt|pt|bin|gguf)$/i, ""),
    filename: m.filename || "",
    character: m.character || "",
    version: (m.version || "general").toLowerCase(),
    relative_path: m.relative_path || "",
    vol_path: m.absolute_path || m.relative_path || "",
    size_bytes: m.size_bytes || 0,
    size_human: m.size_human || "",
    _root_volume: m._root_volume || "krea2-loras-renew",
  };
}

// Match por stem+filename entre el catálogo plano (list_all) y el by_version.
// Si el lora plano coincide con uno de by_version, le anotamos version+character.
function _annotateLorasWithVersion(loras, byVer) {
  if (!loras || !byVer) return;
  for (const l of loras) {
    for (const v of ["v1", "v2", "general"]) {
      for (const m of (byVer[v] || [])) {
        if (m.logical === l.logical && (m.filename === l.filename || m.vol_path === l.vol_path || (m.filename && l.filename && m.filename === l.filename))) {
          l.version = v;
          if (m.character && !l.character) l.character = m.character;
          break;
        }
      }
      if (l.version) break;
    }
  }
}

// Hidrata outputs desde la caché local (sin esperar a la red).
export function hydrateOutputsCache() {
  const cached = loadOutputsCache();
  if (!cached) return false;
  state.outputs = cached;
  state.outputsLoadedAt = 0; // se marca fresco cuando venga de la red
  return true;
}

// Hidrata modelos+outputs desde el snapshot de D1 (compartido entre teléfono y PC)
// cuando no hay caché local. Devuelve true si encontró datos.
export async function hydrateFromD1Cache() {
  if (!state.settings.syncKey) return false;
  try {
    const r = await api.sync.cacheGet(state.settings.syncKey, "list_all");
    const snapTs = r && r.data && Number(r.data.ts) || 0;
    if (snapTs && Date.now() - snapTs > D1_SNAPSHOT_TTL) return false; // snapshot viejo → pegarle al manager
    if (r && r.data && r.data.models && r.data.outputs) {
      if (!state.models) {
        state.models = r.data.models;
        if (r.data.lorasByVersion) {
          state.lorasByVersion = r.data.lorasByVersion;
          _annotateLorasWithVersion(state.models.loras, state.lorasByVersion);
        }
        ensureLoraDatalist(); repopulateModelSelects(); renderModelsTable(); updateModelsStamp(); updateV27UI();
      }
      if (!state.outputs) {
        state.outputs = r.data.outputs;
        state.outputsLoadedAt = 0;
      }
      return true;
    }
  } catch (e) { /* D1 puede faltar */ }
  return false;
}

// Hidrata selects/datalist desde la caché local sin esperar a la red.
// Devuelve true si había caché (fresco o no); con savedAt=0 el guard de TTL de
// refreshModels no saltea → el arranque re-escanea y re-guarda con timestamp.
export function hydrateModelsCache() {
  const cached = loadModelsCache();
  if (!cached) return false;
  state.models = cached.models;
  state.lorasByVersion = cached.lorasByVersion || null;
  if (state.lorasByVersion) _annotateLorasWithVersion(state.models.loras, state.lorasByVersion);
  state.modelsLoadedAt = cached.savedAt; // el guard de TTL de refreshModels lo usa
  ensureLoraDatalist();
  repopulateModelSelects();
  renderModelsTable();
  updateModelsStamp();
  updateV27UI();
  return true;
}

function updateModelsStamp() {
  const stamp = $("#models-stamp");
  if (!stamp) return;
  if (!state.models) { stamp.textContent = ""; return; }
  if (!state.modelsLoadedAt) { stamp.textContent = "desde caché · refrescando…"; return; }
  const age = Date.now() - state.modelsLoadedAt;
  let txt = age < 60000 ? "actualizado ahora" : `actualizado hace ${Math.round(age / 60000)}m`;
  // v1.1.0 del manager: list_all viene de su caché TTL (default 300s) salvo refresco manual.
  if (state.modelsFromManagerCache) {
    txt += ` · caché manager ${Math.round(state.modelsFromManagerCache.cache_age_sec || 0)}s`;
  }
  stamp.textContent = txt;
}

function repopulateModelSelects() {
  const spec = [["unet", "unets"], ["clip", "clips"], ["vae", "vaes"], ["upscale", "upscalers"]];
  for (const tab of Object.keys(FORM_CACHE)) {
    const f = FORM_CACHE[tab];
    for (const [type, key] of spec) {
      const sel = { unet: f.unetSel, clip: f.clipSel, vae: f.vaeSel, upscale: f.upScaleSel }[type];
      if (!sel) continue;
      const keep = sel.value;
      sel.innerHTML = "";
      const list = state.models?.[key] || [];
      if (!list.length) sel.append(el("option", { value: "" }, ["sin datos — Models ↗"]));
      for (const m of list) sel.append(el("option", { value: m.logical, selected: m.logical === keep }, [m.logical]));
    }
  }
  // Upscale: repoblar el datalist (upscalers + seedvr) sin perder lo tipeado
  FORM_CACHE.upscale?.refreshModels?.();
}

let modelFilter = { q: "", type: "lora", version: "all", groupByChar: false };
function renderModelsTable() {
  const tb = $("#models-tbody");
  if (!tb) return;
  const key = { lora: "loras", unet: "unets", clip: "clips", vae: "vaes", upscale: "upscalers" }[modelFilter.type];
  const list = (state.models?.[key]) || [];
  const q = modelFilter.q.toLowerCase();
  // Filtro por texto
  let rows = list.filter(m => !q || m.logical.toLowerCase().includes(q) || m.filename.toLowerCase().includes(q));
  // Filtro por versión (solo aplica a LoRAs; si no hay anotado, lo manda a "general")
  if (modelFilter.type === "lora" && modelFilter.version && modelFilter.version !== "all") {
    rows = rows.filter(m => (m.version || "general").toLowerCase() === modelFilter.version.toLowerCase());
  }
  $("#models-count").textContent = `${rows.length} modelos`;
  tb.innerHTML = "";
  // Agrupar por personaje solo si type==lora y el toggle está activo
  if (modelFilter.type === "lora" && modelFilter.groupByChar) {
    const groups = new Map();
    for (const m of rows) {
      const c = m.character || "(sin personaje)";
      if (!groups.has(c)) groups.set(c, []);
      groups.get(c).push(m);
    }
    // Ordenar grupos por nombre, "(sin personaje)" al final
    const sortedChars = [...groups.keys()].sort((a, b) => {
      if (a === "(sin personaje)") return 1;
      if (b === "(sin personaje)") return -1;
      return a.localeCompare(b);
    });
    for (const char of sortedChars) {
      const ms = groups.get(char);
      const trHead = el("tr", { class: "bg-panel2/40 border-b border-edge" }, [
        el("td", { class: "p-2 font-semibold text-xs", colspan: "5", html: `🧑 <span class="mono">${escapeHtml(char)}</span> · ${ms.length} LoRA${ms.length === 1 ? "" : "s"}` }),
      ]);
      tb.append(trHead);
      for (const m of ms) tb.append(_buildModelRow(m));
    }
  } else {
    for (const m of rows) tb.append(_buildModelRow(m));
  }
}

function _buildModelRow(m) {
  const versionBadge = modelFilter.type === "lora" && m.version
    ? el("span", { class: `badge !text-[9px] !px-1 !py-0 mono ml-1 ${_versionBadgeClass(m.version)}`, html: m.version.toUpperCase() })
    : null;
  const charHint = modelFilter.type === "lora" && m.character
    ? el("span", { class: "text-dim text-[11px] ml-2", html: `· ${escapeHtml(m.character)}` })
    : null;
  return el("tr", { class: "border-b border-edge/50" }, [
    el("td", { class: "p-2" }, [el("span", { class: "mono" }, [m.logical]), versionBadge, charHint].filter(Boolean)),
    el("td", { class: "p-2 text-dim" }, [modelFilter.type]),
    el("td", { class: "p-2 text-dim mono" }, [fmtBytes(m.size_bytes)]),
    el("td", { class: "p-2 text-dim" }, [m._root_volume || "—"]),
    el("td", { class: "p-2 text-right whitespace-nowrap" }, [
      el("button", { class: "btn-ghost !p-1 text-xs", html: "📋", title: "Copiar ruta", onclick: () => copy(m.vol_path || m.relative_path) }),
      el("button", { class: "btn-ghost !p-1 text-xs", html: "✏️", title: "Renombrar", onclick: () => renameModel(m) }),
      el("button", { class: "btn-ghost !p-1 text-xs", html: "↗", title: "Mover", onclick: () => moveModel(m) }),
      el("button", { class: "btn-ghost btn-danger !p-1 text-xs", html: "🗑", title: "Borrar", onclick: () => deleteModel(m) }),
    ]),
  ]);
}

function _versionBadgeClass(v) {
  switch ((v || "").toLowerCase()) {
    case "v1": return "!bg-orange-500/20 !text-orange-300 !border-orange-500/30";
    case "v2": return "!bg-accent-500/20 !text-accent-500 !border-accent-500/30";
    case "general": return "!bg-gray-500/20 !text-dim !border-edge";
    default: return "";
  }
}

// Habilita/deshabilita los controles v1/v2 según si hay desglose de LoRAs.
// v1.1.0 lo arma desde {characters, downloaded}; el viejo v2.7 desde by_version.
// Llamado desde refreshModels() después de hidratar state.lorasByVersion.
function updateV27UI() {
  const supported = !!state.lorasByVersion;
  const v = $("#models-version"), g = $("#models-group-char");
  if (v) { v.disabled = !supported; v.title = supported ? "Filtrar LoRAs por versión (v2 = personajes, v1 = descargados)" : "Sin desglose de LoRAs del manager"; }
  if (g) { g.disabled = !supported; g.title = supported ? "Agrupar LoRAs por personaje en la tabla" : "Sin desglose de LoRAs del manager"; }
}

async function deleteModel(m) {
  const p = m.vol_path || m.relative_path;
  if (!await confirmModal(`¿Borrar <b class="mono">${escapeHtml(p)}</b>?`, { okText: "Borrar", danger: true })) return;
  try {
    const r = await api.manager.modelsDelete([p]);
    toast(`Borrados: ${r.deleted}`, "ok"); refreshModels(false, true); // force: la caché local (300s) no debe esconder el borrado
  } catch (e) { toast(e.message, "err"); }
}
async function renameModel(m) {
  const { value } = await promptModal(`Nuevo nombre para <span class="mono">${escapeHtml(m.logical)}</span>`, m.filename);
  if (!value) return;
  const p = m.vol_path || m.relative_path;
  try { await api.manager.modelsRename({ path: p, new_name: value }); toast("Renombrado", "ok"); refreshModels(false, true); }
  catch (e) { toast(e.message, "err"); }
}
async function moveModel(m) {
  const p = m.vol_path || m.relative_path;
  const { value } = await promptModal(`Mover <span class="mono">${escapeHtml(m.logical)}</span> a…`, p,
    { options: ["loras", "diffusion_models", "text_encoders", "vae", "upscale"].map(t => ({ value: t, label: t })) });
  if (!value) return;
  try { await api.manager.modelsMove({ path: p, target: value }); toast("Movido", "ok"); refreshModels(false, true); }
  catch (e) { toast(e.message, "err"); }
}

function promptModal(msg, initial = "", { options = [] } = {}) {
  return new Promise((resolve) => {
    const { close } = openModal(`
      <div class="p-5 space-y-4">
        <div class="text-sm">${msg}</div>
        <div class="space-y-1.5">
          ${options.map(o => `<button data-opt="${escapeHtml(o.value)}" class="btn-ghost w-full text-left !py-1.5 text-xs">${escapeHtml(o.label)}</button>`).join("")}
          <input id="prompt-input" type="text" value="${escapeHtml(initial)}" class="input" />
        </div>
        <div class="flex gap-2 justify-end">
          <button class="btn-secondary" data-close-modal>Cancelar</button>
          <button class="btn-primary" id="prompt-ok">OK</button>
        </div>
      </div>`, { width: "max-w-md" });
    $("#prompt-ok").addEventListener("click", () => { const v = $("#prompt-input").value.trim(); close(); resolve({ value: v }); });
    $$("[data-opt]", document).forEach(b => b.addEventListener("click", () => { $("#prompt-input").value = b.dataset.opt; }));
    const cancels = $$("[data-close-modal]", document);
    if (cancels.length) cancels[cancels.length - 1].addEventListener("click", () => resolve({ value: null }));
  });
}

// ── descargas ──
// El /download directo BLOQUEA hasta terminar (un modelo de 1.7GB cuelga el proxy y
// da timeout → "no se descarga nada"). Todo va por /download/batch (async, devuelve
// task_id) y se pollea /download/status para mostrar progreso real.
// v3.1: input hf:// (mismo formato que `hf download`) + cola + lista de tareas
// persistente (sobrevive al recargar la pestaña) con log por tarea.
let dlSource = "civitai";
let dlTasks = loadDlTasks(); // [{taskId,label,startedAt,status,current,total,currentItem,results,skipped,errors,logOpen}]

const DL_TASKS_LS = "krea2.dlTasks";
function loadDlTasks() {
  try {
    const raw = localStorage.getItem(DL_TASKS_LS);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) return arr.filter(t => t && t.taskId);
    }
  } catch { /* ignore */ }
  return [];
}
function persistDlTasks() {
  try { localStorage.setItem(DL_TASKS_LS, JSON.stringify(dlTasks.slice(0, 20))); } catch { /* quota */ }
}

// Parsea el formato de `hf download`: acepta el comando completo
// `hf download hf://repo/sub/archivo`, la URI sola (hf://, hf:/, repo/sub/archivo)
// o https://huggingface.co/repo/resolve/main/sub/archivo
function parseHfUri(input) {
  const s = String(input || "").trim();
  if (!s) return null;
  let path = s;
  // Si pegaron el comando entero (`hf download hf://...`), sacar el prefijo
  path = path.replace(/^hf\s+download\s+/i, "").trim();
  if (/^hf:\/\//i.test(path)) path = path.replace(/^hf:\/\//i, "");
  else if (/^hf:\//i.test(path)) path = path.replace(/^hf:\//i, ""); // hf:/repo/...
  else if (/^https?:\/\//i.test(path)) {
    try {
      const u = new URL(path);
      if (!/huggingface\.co/i.test(u.hostname)) return null;
      const m = /^\/(.+?)\/resolve\/[^/]+(\/.*)?$/.exec(u.pathname);
      path = (m ? m[1] + (m[2] || "") : u.pathname.replace(/^\//, "")).replace(/\/+$/, "");
    } catch { return null; }
  }
  const parts = path.replace(/^\/+/, "").split("/").filter(Boolean);
  if (parts.length < 2) return null;
  return {
    repo_id: parts.slice(0, 2).join("/"),
    filename: parts[parts.length - 1],
    hf_subfolder: parts.slice(2, -1).join("/"),
  };
}

const DL_STATUS_LABEL = {
  running: "Descargando…",
  done: "✓ Completado",
  done_with_errors: "⚠ Con errores",
  all_skipped: "⏭ Ya existían",
  error: "✗ Error",
};

function isTerminal(status) {
  return ["done", "done_with_errors", "all_skipped", "error"].includes(status);
}

function wireModelsTab() {
  $("#models-refresh").addEventListener("click", () => refreshModels(false, true)); // fuerza reescaneo del manager
  $("#models-clear-cache").addEventListener("click", async () => {
    if (!await confirmModal("Borrar cachés locales (modelos, galería, formularios)?<br/><span class='text-xs text-dim'>Los settings no se tocan. La página se recarga y re-escanea el manager.</span>", { okText: "Borrar", danger: true })) return;
    clearLocalCache();
    location.reload();
  });
  $("#models-search").addEventListener("input", (e) => { modelFilter.q = e.target.value; renderModelsTable(); });
  $("#models-type").addEventListener("change", (e) => { modelFilter.type = e.target.value; renderModelsTable(); });
  $("#models-version").addEventListener("change", (e) => { modelFilter.version = e.target.value; renderModelsTable(); });
  $("#models-group-char").addEventListener("change", (e) => { modelFilter.groupByChar = !!e.target.checked; renderModelsTable(); });
  $("#models-show-download").addEventListener("click", () => $("#models-download-panel").classList.toggle("hidden"));
  $("#dl-source").addEventListener("change", (e) => {
    dlSource = e.target.value;
    $("#dl-civitai-fields").classList.toggle("hidden", dlSource !== "civitai");
    $("#dl-hf-fields").classList.toggle("hidden", dlSource !== "huggingface");
  });
  // URI hf:// → autocompleta repo/filename/carpeta del repo (sigue editable a mano).
  let _uriTimer = 0;
  $("#dl-hf-uri").addEventListener("input", (e) => {
    clearTimeout(_uriTimer);
    _uriTimer = setTimeout(() => {
      const p = parseHfUri(e.target.value);
      if (p) {
        $("#dl-repo").value = p.repo_id;
        $("#dl-hf-subfolder").value = p.hf_subfolder || "";
        $("#dl-filename").value = p.filename;
      }
    }, 250);
  });
  // Descarga individual: entra directo al batch async (1 item) para no colgar el proxy.
  $("#dl-go").addEventListener("click", () => {
    const item = buildDownloadItem();
    if (!item) return;
    runDownloads([item]);
  });
  $("#dl-batch-go").addEventListener("click", () => {
    const item = buildDownloadItem();
    if (!item) return;
    state.batch.push(item);
    renderQueue();
  });
  $("#dl-batch-clear").addEventListener("click", () => { state.batch = []; renderQueue(); });
  $("#dl-batch-run").addEventListener("click", () => {
    const items = state.batch.slice();
    state.batch = [];
    renderQueue();
    runDownloads(items);
  });
  $("#dl-tasks-clear-done").addEventListener("click", () => {
    dlTasks = dlTasks.filter(t => !isTerminal(t.status));
    persistDlTasks();
    renderDlTasks();
  });
  renderDlTasks();
  resumeDlTasks(); // tareas que quedaron corriendo en el manager (reload)
}

function buildDownloadItem() {
  const v = (id) => $(id).value.trim();
  let name = v("#dl-name");
  if (!name) { toast("Necesitás un nombre personalizado (se usa como nombre del archivo)", "err"); return null; }
  const item = {
    source: dlSource,
    name,
    target: $("#dl-target").value,
    overwrite: $("#dl-overwrite").checked,
    skip_duplicate: !$("#dl-overwrite").checked, // si no sobrescribe, que saltee duplicados
  };
  if (dlSource === "civitai") {
    const url = v("#dl-url");
    if (!url) { toast("Necesitás la URL de CivitAI (https://civitai.com/api/download/models/ID)", "err"); return null; }
    item.url = url;
  } else {
    // Formato `hf download`: la URI autocompleta, pero lo tipeado a mano manda.
    const parsed = parseHfUri(v("#dl-hf-uri"));
    let repo = v("#dl-repo") || (parsed && parsed.repo_id) || "";
    let filename = v("#dl-filename") || (parsed && parsed.filename) || "";
    let hfSub = v("#dl-hf-subfolder") || (parsed && parsed.hf_subfolder) || "";
    if (!repo || !filename) {
      toast("Pegá la URI hf://usuario/repo/…/archivo.safetensors (o completá repo_id + filename)", "err");
      return null;
    }
    item.repo_id = repo;
    item.filename = filename;
    if (hfSub) item.hf_subfolder = hfSub; // carpeta DENTRO del repo HF
    const dest = v("#dl-subfolder");
    if (dest) item.subfolder = dest; // carpeta destino dentro del volumen
  }
  return item;
}

function renderQueue() {
  const box = $("#dl-batch-list");
  if (!box) return;
  box.innerHTML = "";
  $("#dl-batch-box").classList.toggle("hidden", !state.batch.length);
  state.batch.forEach((b, i) => box.append(el("div", { class: "flex items-center gap-1" }, [
    el("span", { class: "mono flex-1 truncate", html: `${i + 1}. ${escapeHtml(b.name)} (${b.source}) → ${b.target}` }),
    el("button", { class: "btn-ghost !p-0.5 text-xs", html: "✕", title: "Quitar", onclick: () => { state.batch.splice(i, 1); renderQueue(); } }),
  ])));
  $("#dl-batch-run").textContent = `▶ Ejecutar cola (${state.batch.length})`;
}

// ── tareas de descarga ──
function renderDlTasks() {
  const box = $("#dl-tasks-box");
  const list = $("#dl-tasks-list");
  if (!box || !list) return;
  box.classList.toggle("hidden", !dlTasks.length);
  list.innerHTML = "";
  dlTasks.slice(0, 15).forEach(t => list.append(dlTaskCard(t)));
  const clearBtn = $("#dl-tasks-clear-done");
  if (clearBtn) clearBtn.disabled = !dlTasks.some(t => isTerminal(t.status));
}

function dlTaskCard(t) {
  const running = !isTerminal(t.status);
  const pct = t.total ? Math.min(100, Math.round((t.current / t.total) * 100)) : 0;
  const badge = running ? DL_STATUS_LABEL.running : (DL_STATUS_LABEL[t.status] || t.status);
  const sub = running
    ? (t.currentItem
        ? `item ${t.current}/${t.total} · <span class="mono">${escapeHtml(String(t.currentItem))}</span>`
        : `${t.current}/${t.total}`)
    : `${(t.results || []).length} ok · ${(t.skipped || []).length} ya existían · ${(t.errors || []).length} errores`;
  const summary = [
    ...(t.results || []).map(x => `✅ ${escapeHtml(x.filename || x.name || "ok")} (${x.size_human || ""})`),
    ...(t.skipped || []).map(s => `⏭ ya existe: ${escapeHtml(s.relative_path || s.path || s.name || "")}`),
    ...(t.errors || []).map(e => `❌ ${escapeHtml(e.name || "?")}: ${escapeHtml(e.error || "")}`),
  ].join("");
  return el("div", { class: "rounded-lg border border-edge bg-elevated/50 p-2 space-y-1.5" }, [
    el("div", { class: "flex items-center gap-2 text-xs" }, [
      running ? el("span", { class: "spinner !w-3 !h-3" }) : null,
      el("span", { class: "mono flex-1 truncate font-medium", html: escapeHtml(t.label || t.taskId) }),
      el("span", { class: "text-dim whitespace-nowrap", html: badge }),
      el("button", { class: "btn-ghost !p-1 text-[10px]", html: t.logOpen ? "▾ log" : "▸ log",
        onclick: () => { t.logOpen = !t.logOpen; renderDlTasks(); if (t.logOpen) fetchDlLog(t); } }),
    ]),
    running ? el("div", { class: "progress-ind" }, [el("div", { class: "progress-bar", style: `width:${pct}%` })]) : null,
    el("div", { class: "text-[11px] text-dim", html: sub }),
    ...(t.logOpen ? [el("div", { class: "space-y-1" }, [
      el("pre", { id: `dl-log-${t.taskId}`, class: "mono text-[10px] max-h-40 overflow-y-auto border border-edge rounded p-1.5 bg-black/30", html: escapeHtml((t._logLines || []).join("\n")) || "sin log…" }),
      el("div", { class: "flex gap-2 items-center" }, [
        el("button", { class: "btn-ghost !p-1 text-[10px]", html: "⟳ log", onclick: () => fetchDlLog(t) }),
        el("button", { class: "btn-ghost !p-1 text-[10px]", html: "copiar", onclick: () => copy((t._logLines || []).join("\n")) }),
      ]),
    ])] : []),
    ...(!running && summary ? [el("div", { class: "mono text-[10px] space-y-0.5 max-h-32 overflow-y-auto", html: summary })] : []),
  ]);
}

async function fetchDlLog(t) {
  try {
    const r = await api.manager.downloadLog(t.taskId, 200);
    t._logLines = (r.log || []).map(l => String(l).replace(/\s+$/, ""));
  } catch { /* mantiene lo que haya */ }
  const pre = $(`#dl-log-${t.taskId}`);
  if (pre) pre.textContent = (t._logLines || []).join("\n") || "sin log…";
}

// Arranca /download/batch con los items y pollea /download/status hasta terminar.
// Cada tarea vive en dlTasks (persistido en localStorage) y se muestra con su
// progreso + log. No bloquea el proxy.
async function runDownloads(items) {
  if (!items.length) return;
  const overwrite = !!$("#dl-overwrite")?.checked;
  const skipDup = !overwrite; // si no sobrescribís, que saltee duplicados
  let r;
  try {
    r = await api.manager.downloadBatch({ items, overwrite, skip_duplicate: skipDup });
  } catch (e) {
    toast(e.message, "err", 6000);
    return;
  }
  const t = {
    taskId: r.task_id,
    label: items.length === 1 ? items[0].name : `${items.length} descargas`,
    startedAt: Date.now(),
    status: "running",
    current: 0,
    total: items.length,
    currentItem: "",
    results: [],
    skipped: [],
    errors: [],
    logOpen: false,
  };
  dlTasks.unshift(t);
  persistDlTasks();
  renderDlTasks();
  pollDlTask(t);
}

async function pollDlTask(t) {
  while (dlTasks.includes(t) && !isTerminal(t.status)) {
    try {
      const st = await api.manager.downloadStatus(t.taskId);
      t.current = st.progress?.current || 0;
      t.total = st.progress?.total || t.total;
      t.currentItem = st.progress?.current_item || "";
      if (isTerminal(st.status)) {
        t.status = st.status;
        t.results = st.results || [];
        t.skipped = st.skipped || [];
        t.errors = st.errors || [];
        t.finishedAt = Date.now();
        persistDlTasks();
        renderDlTasks();
        if (t.logOpen) fetchDlLog(t);
        const nOk = t.results.length, nSk = t.skipped.length, nEr = t.errors.length;
        toast(`Descarga: ${nOk} ok, ${nSk} ya existían, ${nEr} errores`, nEr ? "err" : (nOk ? "ok" : "info"), 5000);
        refreshModels(false, true); // actualiza catálogo + galería (force: el modelo nuevo ya está en el manager)
        return;
      }
      renderDlTasks();
    } catch { /* timeout transitorio del manager → reintentar */ }
    await new Promise(res => setTimeout(res, 2500));
  }
}

// Re-engancha tareas que quedaron corriendo en el manager (sobreviven al reload).
function resumeDlTasks() {
  for (const t of dlTasks) {
    if (!isTerminal(t.status) && !t._resumed) {
      t._resumed = true;
      pollDlTask(t);
    }
  }
}

// ══════════════════════════════════════════════════════════════
// GALERÍA — la grilla de imágenes (generaciones D1 + volume del manager).
// v2.3: separada del Historial (operaciones del manager + log GPU).
// ══════════════════════════════════════════════════════════════
function volumeFiles() {
  // Orden: fecha de creación del filename (más recientes arriba).
  return (state.outputs || []).slice().sort((a, b) =>
    (dateFromFilename(b.filename) || (b.mtime || 0) * 1000) - (dateFromFilename(a.filename) || (a.mtime || 0) * 1000));
}

function wireGalleryTab() {
  $("#outputs-upload").addEventListener("click", () => $("#outputs-upload-input").click());
  $("#outputs-upload-input").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const j = await api.manager.outputsUpload(file); // vía proxy multipart
      if (j && j.success) { toast(`Subida: ${j.filename}`, "ok"); refreshHistoryGen(); }
      else toast((j && j.error) || "Error de subida", "err");
    } catch (err) { toast(err.message, "err"); }
    e.target.value = "";
  });
  $("#outputs-zip").addEventListener("click", async () => {
    const files = volumeFiles();
    if (!files.length) { toast("No hay archivos en el volume", "info"); return; }
    const names = files.slice(0, 100).map(f => f.filename);
    try {
      const r = await api.manager.outputsDownload(names); // ZIP del manager
      const blob = await r.blob();
      const a = el("a", { href: URL.createObjectURL(blob), download: `krea2_${Date.now()}.zip` });
      document.body.append(a); a.click(); a.remove();
      toast(`ZIP con ${names.length} imágenes`, "ok", 3000);
    } catch (e) { toast(e.message, "err"); }
  });
  $("#outputs-cleanup").addEventListener("click", async () => {
    try {
      const r = await api.manager.outputsCleanup(7, true);
      const { close } = openModal(`
        <div class="p-5 space-y-3">
          <h3 class="font-semibold">🧹 Cleanup dry-run (7 días)</h3>
          <div class="text-sm">Se borrarían <b>${r.would_delete || 0}</b> archivos (${r.space_freed || "0B"}).</div>
          <div class="mono text-[10px] max-h-32 overflow-y-auto border border-edge rounded p-2">${(r.files || []).map(escapeHtml).join("<br/>")}</div>
          <div class="flex gap-2 justify-end">
            <button class="btn-secondary" data-close-modal>Cancelar</button>
            <button class="bg-red-600 hover:bg-red-500 text-white px-4 py-2 rounded-lg" id="cleanup-do">Borrar igual</button>
          </div>
        </div>`);
      $("#cleanup-do").addEventListener("click", async () => {
        try { const x = await api.manager.outputsCleanup(7, false); toast(`Borrados ${x.deleted}`, "ok"); close(); refreshHistoryGen(); }
        catch (e) { toast(e.message, "err"); }
      });
    } catch (e) { toast(e.message, "err"); }
  });
  // Refrescar la grilla + filtro por pipeline
  $("#hist-refresh-gen").addEventListener("click", () => { refreshModels(true, true); refreshHistoryGen(); });
  $("#hist-gen-tab-filter").addEventListener("change", refreshHistoryGen);
}

// ══════════════════════════════════════════════════════════════
// HISTORY TAB
// ══════════════════════════════════════════════════════════════
// Extrae la seed del filename del volume: 20260802_151730_krea2_1837911584_batch0.png → 1837911584
export function seedFromFilename(fn) {
  const m = /krea2_(\d+)_batch/i.exec(fn || "");
  return m ? parseInt(m[1], 10) : null;
}

// Fecha de creación desde el filename: 20260802_151730_... → timestamp.
// Las imágenes se ordenan por esto (lo pedido): más recientes arriba.
export function dateFromFilename(fn) {
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/.exec(fn || "");
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  return new Date(+y, +mo - 1, +d, +h, +mi, +s).getTime();
}

let _histRun = 0; // guard anti-carrera

// Normaliza la URL de la imagen: D1 trae image_url (campo nuevo), los registros viejos
// solo beam_filename. Asegura que imageUrl/displayUrl existan para que las tarjetas y el
// modal muestren la imagen aunque no haya uploads a hosts.
function ensureImageUrl(it) {
  if (it.imageUrl) return it;
  // v9.4/R2: los registros viejos solo tienen beam_filename → la imagen se construye
  // desde el Worker de R2 (el manager ya no tiene /serve_image).
  const src = it.image_url || (it.beam_filename ? r2ImageUrl(it.beam_filename) : "");
  if (src) return {
    ...it,
    imageUrl: src,
    displayUrl: beamImageUrl(src),
    r2Url: it.beam_filename ? r2ImageUrl(it.beam_filename) : "",
    rawUrl: it.beam_filename ? r2ImageUrl(it.beam_filename, { raw: true }) : "",
  };
  return it;
}

export async function refreshHistoryGen() {
  const grid = $("#hist-gen-grid");
  const empty = $("#hist-gen-empty");
  if (!grid) return;
  const run = ++_histRun;

  const filter = $("#hist-gen-tab-filter")?.value || "";
  let items = [...state.generations]; // base: caché local (incluye lo recién generado)

  // 1) Cloud D1 (si hay Sync Key configurada y las Functions están deployadas)
  if (state.settings.syncKey) {
    try {
      const r = await api.sync.generationsGet(state.settings.syncKey, { limit: 300, tab: "" });
      if (r && r.ok === false) throw new Error(r.error || "D1 no configurado"); // no marcar cloud
      const server = r.items || [];
      const byId = new Map(items.map(g => [g.id, g]));
      for (const s of server) {
        // Normalizar: el server puede traer payload/hosts no-objeto o nulls
        // (filas viejas) → el spread los propagaría y la galería muestra "null".
        const clean = { ...s };
        if (!clean.payload || typeof clean.payload !== "object") clean.payload = {};
        if (!clean.hosts || typeof clean.hosts !== "object") clean.hosts = {};
        if (clean.image_url == null) delete clean.image_url;
        if (clean.beam_filename == null) delete clean.beam_filename;
        if (byId.has(clean.id)) byId.set(clean.id, { ...byId.get(clean.id), ...clean });
        else byId.set(clean.id, clean);
      }
      items = [...byId.values()];
      state.syncMode = "cloud";
    } catch (e) {
      if (!state.syncMode) state.syncMode = "local";
      console.warn("sync get:", e.message);
    }
  }

  // 2) Galería del manager (volume Beam) — se usa el CACHE de outputs (viene del /list_all)
  //    y solo se pega al manager si no hay caché. Así la galería no hace requests extras.
  if (state.settings.managerUrl && !state.outputs?.length) {
    try {
      const o = await api.manager.outputs(200);
      state.outputs = o.files || [];
      state.outputsLoadedAt = Date.now();
      saveOutputsCache(state.outputs);
    } catch (e) { console.warn("beam outputs:", e.message); }
  }
  if (state.settings.managerUrl && state.outputs?.length) {
    const knownFiles = new Set(items.map(g => g.beam_filename).filter(Boolean));
    const beamItems = volumeFiles()
      .filter(f => !knownFiles.has(f.filename))
      .map(f => ({
        id: "beam_" + f.filename,
        tab: "beam",
        payload: { seed: seedFromFilename(f.filename) },
        // v9.4/R2: la imagen se sirve desde el Worker (webp display; ?raw original)
        imageUrl: r2ImageUrl(f.filename),
        displayUrl: r2ImageUrl(f.filename),
        r2Url: r2ImageUrl(f.filename),
        rawUrl: r2ImageUrl(f.filename, { raw: true }),
        beam_filename: f.filename,
        size_bytes: f.size_bytes,
        size_human: f.size_human,
        created_at: dateFromFilename(f.filename) || (f.mtime || 0) * 1000, // fecha del nombre
        source: "beam",
      }));
    const byId = new Map(items.map(g => [g.id, g]));
    for (const b of beamItems) if (!byId.has(b.id)) byId.set(b.id, b);
    items = [...byId.values()];
    if (state.syncMode !== "cloud") state.syncMode = "beam";
  }

  // 3) Registros viejos sin image_url → reconstruir desde beam_filename (R2)
  items = items.map(it => ensureImageUrl(it));

  // Si hay filtro por pestaña, los ítems del volume solo aparecen en "Todas"
  // (el filename no codifica la pipeline con la que se generó).
  if (filter) items = items.filter(g => g.tab === filter && g.source !== "beam");
  // Orden por fecha Y hora de creación (más recientes arriba). Los timestamps pueden
  // venir como número (ms) o string ISO (D1) → se normalizan.
  const ts = (v) => (typeof v === "number" && isFinite(v) ? v : v ? new Date(v).getTime() || 0 : 0);
  items.sort((a, b) => ts(b.created_at) - ts(a.created_at));
  if (run !== _histRun) return; // llegó una versión más nueva mientras hacíamos red
  state.generations = items.slice(0, 400);
  saveGenerationsLocal(state.generations); // sanitiza base64 internamente
  window.dispatchEvent(new CustomEvent("krea2:syncmode"));
  const countEl = $("#hist-count");
  if (countEl) countEl.textContent = `${state.generations.length} imágenes`;

  if (!state.generations.length) {
    empty.textContent = "Todavía no hay imágenes en la galería ni en el volume. Generá algo o subí un archivo y aparece acá (y en tu otro dispositivo).";
  }
  // La galería nueva (js/gallery/): toolbar + grid + visor + selección.
  // El filtro legacy por tab se sincroniza al store antes de pintar.
  const g = ensureGallery();
  g.store.setTab(filter);
  g.setItems(state.generations);
}

// ── Controlador de galería (lazy: los nodos existen cuando se abre la pestaña) ──
let _gallery = null;
function ensureGallery() {
  if (_gallery) return _gallery;
  _gallery = createGallery({
    toolbarEl: $("#gal-toolbar"),
    selbarEl: $("#gal-selbar"),
    gridEl: $("#hist-gen-grid"),
    emptyEl: $("#hist-gen-empty"),
    countEl: $("#hist-count"),
    pagerEl: $("#gal-pager"),
    onRedo: redoGeneration,
    onSendTo: sendTo,
    onSendToUpscale: sendToUpscale,
    onDeleteOne: deleteGeneration,
    onDeleteMany: deleteGenerations,
    onDownloadMany: downloadGenerations,
  });
  return _gallery;
}

// Borrado en lote (selección múltiple): una sola confirmación, un solo refresh.
async function deleteGenerations(items) {
  const list = (items || []).filter(Boolean);
  if (!list.length) return;
  if (!await confirmModal(`¿Borrar <b>${list.length}</b> generaciones?`, { okText: "Borrar", danger: true })) return;
  const beamFiles = list.filter(g => g.source === "beam" && g.beam_filename).map(g => g.beam_filename);
  if (beamFiles.length) {
    try { await api.manager.outputsDelete(beamFiles); }
    catch (e) { toast("Volume: " + e.message, "err"); return; }
  }
  const ids = new Set(list.map(g => g.id));
  state.generations = state.generations.filter(g => !ids.has(g.id));
  saveGenerationsLocal(state.generations);
  if (state.settings.syncKey) {
    for (const g of list) {
      if (g.source === "beam") continue;
      try { await api.sync.generationsDelete(g.id, state.settings.syncKey); } catch (e) { /* sigue */ }
    }
  }
  if (_gallery) _gallery.clearSelection();
  toast(`Borradas: ${list.length}`, "ok");
  refreshHistoryGen();
}

// Descarga en lote: secuencial con pausa corta (los navegadores bloquean
// descargas múltiples simultáneas). Usa la misma estrategia que el visor.
async function downloadGenerations(items) {
  const list = (items || []).filter(Boolean);
  if (!list.length) return;
  toast(`Descargando ${list.length}…`, "info", 2000);
  let ok = 0;
  for (const it of list) {
    try { if (await downloadOriginal(it)) ok++; } catch { /* sigue */ }
    await new Promise(r => setTimeout(r, 600));
  }
  toast(`Descargadas: ${ok}/${list.length}`, ok ? "ok" : "err");
}

async function deleteGeneration(item) {
  if (!await confirmModal("¿Borrar esta generación del historial?", { okText: "Borrar", danger: true })) return;
  // Si viene de la galería del volume, se borra del manager (y del D1 si existía)
  if (item.source === "beam" && item.beam_filename) {
    try { await api.manager.outputsDelete([item.beam_filename]); toast("Borrado del volume", "ok"); }
    catch (e) { toast("Volume: " + e.message, "err"); }
    state.generations = state.generations.filter(g => g.id !== item.id);
    saveGenerationsLocal(state.generations);
    refreshHistoryGen();
    return;
  }
  state.generations = state.generations.filter(g => g.id !== item.id);
  saveGenerationsLocal(state.generations);
  try { await api.sync.generationsDelete(item.id, state.settings.syncKey); } catch (e) {}
  refreshHistoryGen();
}

// ── Log físico del servicio (GPU y CPU) — los escriben los servicios (append-only),
// los sirve el manager (/gpu-log y /manager-log). Render compacto: una línea por
// evento, click para ver el JSON completo.
let logService = "gpu"; // "gpu" | "manager"

const LOG_SERVICE_API = {
  gpu:     { get: api.manager.gpuLog,     raw: api.manager.gpuLogRaw,     file: "gpu_service.log" },
  manager: { get: api.manager.managerLog, raw: api.manager.managerLogRaw, file: "manager_service.log" },
};

function formatGpuLogEntry(e) {
  if (e.raw !== undefined) return el("div", { class: "text-dim", html: escapeHtml(e.raw) });
  const okCls = e.ok === false ? "text-red-400" : e.ok ? "text-green-400" : "text-dim";
  const okTxt = e.ok === undefined ? "·" : (e.ok ? "✓" : "✗");
  const ts = e.ts ? new Date(e.ts).toLocaleTimeString("es-AR") : "";
  const bits = [
    ts ? `<span class="text-dim">${ts}</span>` : "",
    `<span class="${okCls}">${okTxt}</span>`,
    e.endpoint ? `<span class="text-accent-500">${escapeHtml(e.endpoint)}</span>` : "",
    e.phase ? `<span class="text-dim">${escapeHtml(e.phase)}</span>` : "",
    e.duration_s != null ? `<span>${e.duration_s}s</span>` : "",
    e.task_id ? `<span class="text-dim">${escapeHtml(String(e.task_id).slice(0, 14))}</span>` : "",
    e.result?.seed != null ? `<span>seed ${e.result.seed}</span>` : "",
    // Manager: cada request trae method + status en extra
    e.extra?.method ? `<span class="text-dim">${escapeHtml(e.extra.method)}</span>` : "",
    e.extra?.status != null ? `<span class="${e.extra.status >= 400 ? "text-red-400" : ""}">→ ${e.extra.status}</span>` : "",
    e.error ? `<span class="text-red-400">${escapeHtml(String(e.error).slice(0, 140))}</span>` : "",
  ].filter(Boolean).join(" ");
  const det = el("div", { class: "text-dim hover:text-txt cursor-pointer whitespace-pre-wrap break-all", html: bits });
  det.addEventListener("click", () => {
    const full = el("details", { class: "acc" });
    full.innerHTML = `<summary><span class="text-dim">ver JSON completo</span></summary><div class="acc-body mono text-[10px] whitespace-pre-wrap break-all">${escapeHtml(JSON.stringify(e, null, 2))}</div>`;
    det.replaceWith(full); full.open = true;
  });
  return det;
}

async function loadGpuLog() {
  const box = $("#gpu-log-box");
  const empty = $("#gpu-log-empty");
  if (!box) return;
  const svc = LOG_SERVICE_API[logService] || LOG_SERVICE_API.gpu;
  const src = $("#gpu-log-src");
  if (src) src.textContent = svc.file;
  const tail = parseInt($("#gpu-log-tail")?.value || "200", 10);
  const grep = ($("#gpu-log-grep")?.value || "").trim();
  try {
    const r = await svc.get({ tail, grep });
    box.innerHTML = "";
    empty.classList.add("hidden");
    const entries = r.entries || [];
    if (!entries.length) {
      empty.textContent = grep ? `Sin entradas para “${grep}”.` : `El servicio todavía no escribió nada en ${svc.file}.`;
      empty.classList.remove("hidden");
      return;
    }
    for (const e of [...entries].reverse()) box.append(formatGpuLogEntry(e));
  } catch (e) {
    box.innerHTML = "";
    empty.classList.remove("hidden");
    empty.textContent = "Error: " + e.message;
  }
}

// v2.3: el historial de operaciones del manager se administra en la pestaña Models.
function wireManagerHistory() {
  $("#hist-action-filter").addEventListener("change", () => loadManagerHistory());
  $("#hist-stats").addEventListener("click", async () => {
    try {
      const s = await api.manager.historyStats();
      const box = $("#hist-stats-box");
      box.classList.remove("hidden");
      box.innerHTML = [
        ["Entradas", s.total_entries], ["Hashes", s.total_hashes], ["Descargado", s.total_downloaded_human],
        ["Última act.", new Date(s.updated_at * 1000).toLocaleString()],
      ].map(([k, v]) => `<div><div class="text-dim text-xs">${escapeHtml(k)}</div><div class="font-semibold">${escapeHtml(String(v))}</div></div>`).join("");
    } catch (e) { toast(e.message, "err"); }
  });
  $("#hist-rebuild").addEventListener("click", async () => {
    try { const r = await api.manager.historyRebuild(); toast(`Índice reconstruido: ${r.scanned} archivos`, "ok"); }
    catch (e) { toast(e.message, "err"); }
  });
  $("#hist-download").addEventListener("click", async () => {
    try {
      const r = await api.manager.historyDownload();
      const blob = await r.blob();
      const a = el("a", { href: URL.createObjectURL(blob), download: "krea2_history.json" });
      document.body.append(a); a.click(); a.remove();
    } catch (e) { toast(e.message, "err"); }
  });
  loadManagerHistory();
}

async function wireHistoryTab() {
  // ── Log físico del servicio (GPU/CPU) ──
  const doGpuLog = () => loadGpuLog();
  $$("#log-service-seg [data-service]").forEach(b => b.addEventListener("click", () => {
    logService = b.dataset.service;
    $$("#log-service-seg [data-service]").forEach(x => x.classList.toggle("active", x === b));
    loadGpuLog();
  }));
  $("#gpu-log-refresh").addEventListener("click", doGpuLog);
  $("#gpu-log-tail").addEventListener("change", doGpuLog);
  $("#gpu-log-grep").addEventListener("input", debounce(doGpuLog, 500));
  // Sin auto-refresh: cada poll despierta/mantiene caliente la instancia del manager
  // (segundos facturados con keep_warm=0). Refrescar es manual (botón ⟳).
  $("#gpu-log-dl").addEventListener("click", async () => {
    try {
      const svc = LOG_SERVICE_API[logService] || LOG_SERVICE_API.gpu;
      const tail = parseInt($("#gpu-log-tail")?.value || "200", 10);
      const grep = ($("#gpu-log-grep")?.value || "").trim();
      const r = await svc.raw({ tail, grep });
      const blob = await r.blob();
      const a = el("a", { href: URL.createObjectURL(blob), download: svc.file });
      document.body.append(a); a.click(); a.remove();
      toast(`${svc.file} descargado (raw)`, "ok", 1800);
    } catch (e) { toast(e.message, "err"); }
  });
  loadGpuLog();
}

async function loadManagerHistory() {
  const tb = $("#hist-tbody");
  if (!tb) return;
  try {
    const action = $("#hist-action-filter").value;
    const r = await api.manager.history(action, 50, 0);
    tb.innerHTML = "";
    for (const e of r.entries || []) {
      const color = e.result === "success" ? "text-green-400" : e.result === "error" ? "text-red-400" : "text-yellow-400";
      const detail = e.details ? Object.entries(e.details).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k}=${v}`).join(" ") : "";
      // El manager v2.4 dejó de mandar ts_human (lo mandaba el viejo) → fallback a iso o ts.
      const tsHuman = e.ts_human || e.iso || (e.ts ? new Date(e.ts * 1000).toLocaleString() : "");
      tb.append(el("tr", { class: "border-b border-edge/50" }, [
        el("td", { class: "p-2 whitespace-nowrap" }, [tsHuman]),
        el("td", { class: "p-2" }, [e.action]),
        el("td", { class: `p-2 ${color}` }, [e.result]),
        el("td", { class: "p-2 mono text-[10px] max-w-xs truncate", title: detail }, [escapeHtml(detail)]),
      ]));
    }
  } catch (e) {
    tb.innerHTML = `<tr><td class="p-2 text-red-400">${escapeHtml(e.message)}</td></tr>`;
  }
}

// ══════════════════════════════════════════════════════════════
// UPSCALE standalone — servicio Beam SEPARADO (upscale-api, SeedVR2+ESRGAN):
//   GET  /upscale/modelos → { models:[{name,scale,architecture}], seedvr2:{available} }
//   POST /upscale  { mode:"seedvr2"|"esrgan", model, images:[dataUrl],
//                    factor_upscaler, output_format:"png"|"jpeg",
//                    jpeg_quality, tile_size (solo GAN), response:"json" }
//     → { status, processed, results:[{filename,data(base64),width,height,format,ms}], errors }
// El resultado NO pasa por R2: viaja como base64 y queda en el navegador
// (descarga / hosts / enviar a Variation / Edit).
// ══════════════════════════════════════════════════════════════
function upscaleModelList() {
  const remote = FORM_CACHE.upscale?.remoteModels || [];
  if (remote.length) return remote.map((m) => m.name);
  const list = [];
  for (const m of (state.models?.upscalers || [])) list.push(m.logical);
  return [...new Set(list)];
}

async function loadUpscalerCatalog(f) {
  try {
    const r = await api.upscaler.modelos();
    f.remoteModels = r?.models || [];
    f.seedvr2Available = !!(r?.seedvr2?.available);
    const dl = document.getElementById("dl-f-upscale-model");
    if (dl) {
      const keep = $("#f-upscale-model")?.value || "";
      dl.innerHTML = "";
      for (const o of upscaleModelList()) dl.append(el("option", { value: o }));
      const inp = $("#f-upscale-model");
      if (inp) inp.value = keep;
    }
    const hint = $("#f-upscale-hint");
    if (hint) {
      const n = f.remoteModels.length;
      hint.textContent = `GANs en el volume: ${n} · SeedVR2 ${f.seedvr2Available ? "✓ disponible" : "✗ no disponible"}`;
    }
  } catch (e) { /* sin catálogo remoto: se usa el del manager */ }
}

function renderUpscaleForm() {
  if (FORM_CACHE.upscale) return FORM_CACHE.upscale;
  const f = {};
  const imgWrap = el("div");
  f.imageInput = createImageInput(imgWrap, { label: "Imagen a upscalar" });

  const modeSel = selectField("Motor (esrgan = GAN rápido · seedvr2 = IA pesado)", "f-upscale-mode", ["esrgan", "seedvr2"], "esrgan");
  const modelSel = comboField("Modelo GAN (tipeable)", "f-upscale-model", upscaleModelList(), "");
  const modelInput = modelSel.querySelector("input");
  modelInput.placeholder = "ej: 4x-UltraSharp.pth";
  // Al refrescar el catálogo (Models), se repuebla el datalist sin perder lo tipeado.
  f.refreshModels = () => loadUpscalerCatalog(f);

  const setVal = (id, val) => { const e = $(id); if (e) e.value = val; };
  const setCheck = (id, on) => { const e = $(id); if (e) e.checked = !!on; };
  const syncModeUI = () => {
    const mode = $("#f-upscale-mode")?.value || "esrgan";
    const gan = $("#f-upscale-gan-opts");
    if (gan) gan.classList.toggle("hidden", mode !== "esrgan");
    modelSel.classList.toggle("hidden", mode !== "esrgan");
  };
  // Reset global (un solo botón, pensado para móvil): limpia todo el formulario.
  const resetUpscaleForm = () => {
    modelInput.value = "";
    if (f.imageInput) f.imageInput.clear();
    setVal("#f-upscale-mode", "esrgan");
    setVal("#f-upscale-scale", 4);
    setVal("#f-upscale-format", "png");
    setVal("#f-upscale-quality", 92);
    setVal("#f-upscale-tile_size", 512);
    setCheck("#f-upscale-tiled", true);
    syncModeUI();
    toast("Formulario de upscale restablecido", "ok", 2000);
  };

  const form = el("div", { class: "space-y-2.5" }, [
    accordionHTML({ title: "Imagen", icon: "◉", open: true, body: imgWrap }),
    el("div", { class: "panel p-3 space-y-3" }, [
      modeSel,
      modelSel,
      el("div", { class: "text-[11px] text-dim", id: "f-upscale-hint" }, ["cargando catálogo del upscaler…"]),
      el("div", { class: "grid grid-cols-2 gap-2" }, [
        numField("Factor de escala", "f-upscale-scale", 4, 1, 8, 1),
        selectField("Formato de salida", "f-upscale-format", ["png", "jpeg"], "png"),
      ]),
      el("div", { id: "f-upscale-gan-opts", class: "grid grid-cols-2 gap-2" }, [
        numField("Tile size px (0 = sin tiles)", "f-upscale-tile_size", 512, 0, 4096, 64),
        numField("Calidad jpeg", "f-upscale-quality", 92, 1, 100),
      ]),
      toggleField("f-upscale-tiled", "preprocess denoise+sharpen (solo GAN)", false),
    ]),
    el("div", { class: "generation-actions flex gap-2" }, [
      el("button", { id: "f-upscale-reset", class: "btn-ghost shrink-0 !py-3 text-sm border border-edge rounded-lg", html: "↺ Limpiar", title: "Resetear TODO el formulario a los valores predeterminados" }),
      el("button", { id: "f-upscale-go", class: "btn-primary flex-1 !py-3 text-base", html: "🔍 Upscalear" }),
    ]),
  ]);
  $("#gen-form-upscale").append(form);
  $("#f-upscale-mode").addEventListener("change", syncModeUI);
  $("#f-upscale-tiled").addEventListener("change", (e) => {
    // reuse del toggle como preprocess para GAN
    e.target.dataset.preprocess = e.target.checked ? "denoise_sharpen" : "none";
  });
  $("#f-upscale-reset").addEventListener("click", resetUpscaleForm);
  $("#f-upscale-go").addEventListener("click", () => runUpscale());
  f.modelSel = modelSel;
  FORM_CACHE.upscale = f;
  loadUpscalerCatalog(f); // catálogo remoto async (GANs + disponibilidad SeedVR2)
  return f;
}

async function runUpscale() {
  const f = FORM_CACHE.upscale;
  if (!f) return;
  const img = f.imageInput.get();
  if (!img?.dataUrl) { toast("Subí o pegá una imagen primero", "err"); return; }
  const mode = $("#f-upscale-mode")?.value || "esrgan";
  let model = "";
  if (mode === "esrgan") {
    model = $("#f-upscale-model").value.trim();
    if (!model) { toast("Elegí un modelo GAN de upscale", "err"); return; }
  } else if (f.seedvr2Available === false) {
    toast("SeedVR2 no está disponible en el servicio (mirá el hint)", "err"); return;
  }

  const go = $("#f-upscale-go");
  const resultBox = $("#gen-result-upscale");
  go.disabled = true;
  go.classList.add("btn-gen-busy");
  const t0 = Date.now();
  const status = el("div", { class: "panel p-4 space-y-3" }, [
    el("div", { class: "flex items-center gap-2 text-sm" }, [el("span", { class: "spinner" }), el("span", { html: `Upscaleando (${escapeHtml(mode === "seedvr2" ? "SeedVR2" : model)})… si el servicio estaba frío puede tardar varios minutos` })]),
    el("div", { class: "progress-ind" }),
    el("div", { class: "text-xs text-dim mono", id: "timer-upscale" }),
  ]);
  resultBox.innerHTML = "";
  resultBox.append(status);
  const timer = setInterval(() => {
    const s = $("#timer-upscale");
    if (s) s.textContent = `${((Date.now() - t0) / 1000).toFixed(1)}s transcurridos`;
  }, 250);

  // Payload del upscale-api v1.6 (JSON): images acepta dataURL/URL/base64.
  const fmt = $("#f-upscale-format").value === "jpg" ? "jpeg" : ($("#f-upscale-format").value || "png");
  const factor = parseFloat($("#f-upscale-scale").value);
  const payload = {
    mode,
    images: [img.dataUrl],
    factor_upscaler: Number.isFinite(factor) && factor > 0 ? factor : undefined,
    output_format: fmt,
    jpeg_quality: parseInt($("#f-upscale-quality").value, 10) || 92,
    response: "json",
    task_id: uid("upscale"),
  };
  if (mode === "esrgan") {
    payload.model = model;
    const tile = parseInt($("#f-upscale-tile_size").value, 10);
    payload.tile_size = Number.isFinite(tile) && tile > 0 ? tile : 0;
    payload.preprocess = $("#f-upscale-tiled")?.dataset.preprocess || "none";
  }

  // Dimensiones de la entrada (para el cuadro de detalles)
  let srcW = null, srcH = null;
  try {
    const probe = new Image();
    await new Promise((res) => { probe.onload = res; probe.onerror = res; probe.src = img.dataUrl; });
    srcW = probe.naturalWidth || null; srcH = probe.naturalHeight || null;
  } catch { /* opcional */ }

  try {
    const res = await api.upscaler.upscale(payload);
    clearInterval(timer);
    if (!res || res.status === "error" || (!res.results?.length)) {
      const err0 = res?.errors?.[0]?.error || res?.error || "Fallo desconocido";
      throw new Error(err0);
    }
    fillUpscaleResult(res, resultBox, { mode, model, srcW, srcH, factor: payload.factor_upscaler });
  } catch (e) {
    clearInterval(timer);
    resultBox.innerHTML = "";
    const tb = e.data?.traceback ? e.data.traceback.slice(0, 4000) : "";
    resultBox.append(el("div", { class: "panel p-3 border-red-500/40 text-sm space-y-2" }, [
      el("div", { class: "text-red-400 font-medium", html: "⛔ Error: " + escapeHtml(e.message) }),
      tb ? el("details", { class: "acc" }, [el("summary", { html: "traceback" }), el("div", { class: "acc-body mono text-[10px] overflow-x-auto max-h-40", html: escapeHtml(tb) })]) : null,
    ]));
  } finally {
    go.classList.remove("btn-gen-busy");
    go.disabled = false;
  }
}

function fillUpscaleResult(res, resultBox, ctx = {}) {
  const r = res.results?.[0];
  if (!r?.data) { resultBox.innerHTML = ""; resultBox.append(el("div", { class: "panel p-3 text-sm text-red-400" }, ["Respuesta sin imagen"])); return; }
  const mime = r.format === "jpeg" ? "image/jpeg" : "image/png";
  const imageSrc = `data:${mime};base64,${r.data}`;
  const sizeBytes = Math.floor((r.data.length * 3) / 4);

  const panel = el("div", { class: "panel p-3 space-y-3" }, [
    el("img", { class: "img-fit border border-edge rounded-lg", src: imageSrc, loading: "eager", decoding: "async" }),
    el("div", { class: "flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" }, [
      el("span", { class: "badge mono", html: `⏱ ${((r.ms || 0) / 1000).toFixed(1)}s` }),
      el("span", { class: "badge mono", html: `${r.width ?? "?"} × ${r.height ?? "?"}` }),
      el("span", { class: "badge mono", html: escapeHtml(ctx.mode === "seedvr2" ? "SeedVR2" : (ctx.model || "GAN")) }),
      res.status === "partial" ? el("span", { class: "badge mono text-orange-300", html: "partial" }) : null,
    ]),
    el("div", { class: "flex gap-1.5 flex-wrap" }, [
      el("a", { class: "btn-ghost !py-1 text-xs inline-flex", href: imageSrc, download: r.filename || "upscale.png", html: "⬇ Descargar" }),
      imageSrc ? el("button", { class: "btn-ghost !py-1 text-xs", html: "⧉ Variation", onclick: () => sendTo("variation", imageSrc) }) : null,
      imageSrc ? el("button", { class: "btn-ghost !py-1 text-xs", html: "✎ Edit", onclick: () => sendTo("edit", imageSrc) }) : null,
    ]),
    el("details", { class: "acc" }, [
      el("summary", { html: "📊 Detalles <span class='chev'>▾</span>" }),
      el("div", { class: "acc-body mono text-[12px] space-y-0.5" }, [
        metaRow("Motor", ctx.mode === "seedvr2" ? "SeedVR2 7B" : "ESRGAN/spandrel"),
        metaRow("Modelo", escapeHtml(ctx.model || "—")),
        metaRow("Factor", `×${ctx.factor ?? "—"}`),
        metaRow("Entrada", `${ctx.srcW ?? "?"} × ${ctx.srcH ?? "?"}`),
        metaRow("Salida", `${r.width ?? "?"} × ${r.height ?? "?"}`),
        metaRow("Formato", escapeHtml(r.format || "—")),
        metaRow("Archivo", escapeHtml(r.filename || "—")),
        metaRow("Tamaño", fmtBytes(sizeBytes)),
        ...(res.errors?.length ? [metaRow("Errores", escapeHtml(res.errors.map((e) => e.error).join("; ").slice(0, 200)))] : []),
      ]),
    ]),
  ]);
  resultBox.innerHTML = "";
  resultBox.append(panel);

  // Para que "↺ usar último output" del formulario tome el resultado
  state.lastResult = { imageUrl: imageSrc, rawUrl: imageSrc, payload: null, tab: "upscale", seed: null };
}

// ══════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════
export function initTabs() {
  state.forms = loadForms() || {}; // estado de formularios persistido (reloads del celular)
  renderNav();
  for (const k of ["generate", "variation", "edit", "depth"]) renderGenForm(k);
  renderUpscaleForm();
  wireModelsTab();
  wireManagerHistory(); // operaciones del manager → pestaña Models
  wireGalleryTab();
  wireHistoryTab();
  switchTab(state.forms.currentTab || "generate");
  // Guardar antes de que el celular esconda/mate la pestaña (reload al cambiar de app)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") saveFormsNow(true);
  });
  window.addEventListener("pagehide", () => saveFormsNow(true));
}
