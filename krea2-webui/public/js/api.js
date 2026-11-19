import { state } from "./state.js";

// ─────────────────────────────────────────────────────────────
// Utilidades
// ─────────────────────────────────────────────────────────────
function join(base, path) {
  return String(base || "").replace(/\/+$/, "") + "/" + String(path).replace(/^\/+/, "");
}

function authHeaders(json = true) {
  const h = {};
  if (state.settings.beamApiKey) h["Authorization"] = `Bearer ${state.settings.beamApiKey}`;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function req(url, { method = "GET", headers = {}, body, timeout = 0, raw = false } = {}) {
  const ctrl = new AbortController();
  const t = timeout ? setTimeout(() => ctrl.abort(), timeout) : null;
  try {
    const r = await fetch(url, { method, headers, body, signal: ctrl.signal });
    if (raw) return r;
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!r.ok) {
      const err = new Error((data && (data.error || data.detail)) || `HTTP ${r.status}`);
      err.status = r.status; err.data = data;
      throw err;
    }
    return data;
  } finally { if (t) clearTimeout(t); }
}

// ─────────────────────────────────────────────────────────────
// Proxy (Cloudflare Function /api/beam — Modal + Beam legacy)
// Beam responde "Access-Control-Allow-Origin: *, *" (duplicado) → el browser
// bloquea TODO fetch directo. Modal tiene CORS limpio (allow_origins=["*"])
// y permite fetch directo, pero el proxy se mantiene: unifica auth y da
// compatibilidad. En el deploy (Pages/Workers) existe /api/beam que
// reenvía server-side. En dev local (python -m http.server) no existe → fallback
// a fetch directo.
// ─────────────────────────────────────────────────────────────
let _proxyOk = null; // null = sin detectar, true/false
export async function proxyAvailable() {
  if (_proxyOk !== null) return _proxyOk;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    const r = await fetch("/api/beam/ping", { signal: ctrl.signal });
    clearTimeout(t);
    _proxyOk = r.ok;
  } catch { _proxyOk = false; }
  return _proxyOk;
}

// Hosts que SÍ necesitan el proxy CORS: los de Beam (manager + GPU), que bloquean
// el fetch directo del browser. imgchest/catbox/koofr sirven directo (CDN): la
// imagen se muestra rápido y el browser la cachea sin pasar por el proxy.
function _beamHosts() {
  const hosts = new Set();
  for (const u of [state.settings.managerUrl, state.settings.gpuUrl]) {
    try { if (u) hosts.add(new URL(u).host); } catch { /* ignore */ }
  }
  return hosts;
}
function _needsProxy(url) {
  if (!url || !/^https?:\/\//i.test(url)) return false;
  try { return _beamHosts().has(new URL(url).host); } catch { return false; }
}

// ¿Esta URL debe pasar por el proxy /api/beam? (hosts Beam únicamente).
export function needsBeamProxy(url) {
  return _needsProxy(url);
}

// Para <img src>: devuelve la URL proxied (misma-origen, con la API key) si el
// proxy está disponible; si no, la URL directa. No necesita await (cache síncrona).
// Solo se proxifican URLs de hosts Beam; las de hosts de imágenes (imgchest/catbox/
// koofr) y las relativas se dejan tal cual.
export function beamImageUrl(url) {
  if (!url) return "";
  if (_proxyOk === true && _needsProxy(url)) {
    const q = new URLSearchParams({ url });
    if (state.settings.beamApiKey) q.set("apiKey", state.settings.beamApiKey);
    return `/api/beam?${q}`;
  }
  return url;
}

// Llamada a un host de Beam (GPU o manager) a través del proxy.
async function beamFetch(base, path, { method = "GET", body, timeout = 0, raw = false } = {}) {
  const url = join(base, path);
  if (await proxyAvailable()) {
    const ctrl = new AbortController();
    const t = timeout ? setTimeout(() => ctrl.abort(), timeout) : null;
    try {
      const r = await fetch("/api/beam", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, method, headers: body !== undefined && body !== null ? authHeaders() : authHeaders(false), body: body ?? undefined, apiKey: state.settings.beamApiKey }),
        signal: ctrl.signal,
      });
      if (raw) return r;
      const text = await r.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      if (!r.ok) {
        const err = new Error((data && (data.error || data.detail)) || `HTTP ${r.status}`);
        err.status = r.status; err.data = data;
        throw err;
      }
      return data;
    } finally { if (t) clearTimeout(t); }
  }
  // Fallback directo (dev local)
  const hasBody = body !== undefined && body !== null;
  return req(url, {
    method,
    headers: hasBody ? authHeaders() : authHeaders(false),
    body: hasBody ? JSON.stringify(body) : undefined,
    timeout,
    raw,
  });
}

async function gpuPost(path, payload) {
  const base = state.settings.gpuUrl;
  if (!base) throw new Error("Configurá la URL del servicio GPU en ⚙ Settings");
  return beamFetch(base, path, { method: "POST", body: payload });
}

async function manReq(path, { method = "GET", body, timeout = 0 } = {}) {
  const base = state.settings.managerUrl;
  if (!base) throw new Error("Configurá la URL del manager en ⚙ Settings");
  return beamFetch(base, path, { method, body, timeout });
}

// Test de conexión para el modal de Settings (usa el proxy si existe).
export async function probeBeam(base, path, apiKey = state.settings.beamApiKey) {
  const url = join(base, path);
  if (await proxyAvailable()) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 8000);
    try {
      const r = await fetch("/api/beam", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, method: "GET", headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {}, apiKey }),
        signal: ctrl.signal,
      });
      const text = await r.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      return { ok: r.ok, data, status: r.status, error: (data && (data.error || data.detail)) || (r.ok ? null : `HTTP ${r.status}`) };
    } finally { clearTimeout(t); }
  }
  try {
    const r = await fetch(url, { headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {}, signal: AbortSignal.timeout(8000) });
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    return { ok: r.ok, data, status: r.status, error: (data && (data.error || data.detail)) || (r.ok ? null : `HTTP ${r.status}`) };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// ─────────────────────────────────────────────────────────────
// API GPU (Modal, L40S — krea2-api v1.1.0)
// Endpoints: /healthz, /generate, /variation, /edit, /depth-generate,
// /rescan-loras. El payload usa "lora" (singular), "sampler_name",
// "unet_name"/"clip_name"/"vae_name", "mu", "denoise" — tal cual los manda
// el frontend (collectPayload). /edit inyecta krea2_identity_edit_v1_1 en el
// server; /variation y /depth-generate aceptan "image" (+ "strength").
// (preload/warmup son legacy de Beam y no existen en v1.1.0: se conservan
//  como stubs por compatibilidad, pero nada los llama.)
// ─────────────────────────────────────────────────────────────
export const gpu = {
  healthz: () => beamFetch(state.settings.gpuUrl, "healthz", { timeout: 6000 }),
  generate: (p) => gpuPost("generate", p),
  variation: (p) => gpuPost("variation", p),
  edit: (p) => gpuPost("edit", p),
  depth: (p) => gpuPost("depth-generate", p),
  rescanLoras: () => gpuPost("rescan-loras", {}),
  preload: (p) => gpuPost("preload", p),
  warmup: () => gpuPost("warmup", {}),
};

// ─────────────────────────────────────────────────────────────
// API Manager (Modal, CPU — krea2-manager-cpu v1.1.0)
// ─────────────────────────────────────────────────────────────
export const manager = {
  health: () => manReq("health", { timeout: 6000 }),
  pricing: () => manReq("pricing", { timeout: 6000 }),

  // list_all v1.1.0 (manager CPU): cache TTL en el servidor (default 300s,
  // KREA2_CACHE_TTL). Passá refresh=true para forzar reescaneo en background.
  listAll: (refresh = false) => manReq(`list_all${refresh ? "?refresh=true" : ""}`, { timeout: 60000 }), // cold start de Modal + scan puede tardar
  cacheInvalidate: () => manReq("cache/invalidate?scope=list_all", { method: "POST" }),
  listModels: (type) => manReq(`list_models?type=${encodeURIComponent(type)}`, { timeout: 15000 }),
  resolve: (name, type) => manReq(`resolve?name=${encodeURIComponent(name)}&type=${encodeURIComponent(type)}`),

  // LoRAs v1.1.0: list_models?type=lora devuelve {characters, downloaded}
  // (NO by_version como en el viejo v2.7). v2 = personajes (krea2-loras-renew,
  // con variants[] y default por step/final); v1 = descargados (loras/ del
  // volumen principal). El param ?version se acepta pero el server lo ignora.
  // refreshModels() mapea esa respuesta a {v1, v2, general} para la UI.
  listLoras: (version = null) => {
    const v = version && String(version).toLowerCase();
    const qs = v && v !== "all" ? `&version=${encodeURIComponent(v)}` : "";
    return manReq(`list_models?type=lora${qs}`, { timeout: 15000 });
  },
  resolveLora: (name, version = null) => {
    const v = version ? String(version).toLowerCase() : "";
    const qs = v && v !== "all" ? `&version=${encodeURIComponent(v)}` : "";
    return manReq(`resolve?name=${encodeURIComponent(name)}&type=lora${qs}`);
  },

  download: (item) => manReq("download", { method: "POST", body: item }),
  downloadBatch: (payload) => manReq("download/batch", { method: "POST", body: payload }),
  downloadStatus: (taskId) => manReq(`download/status?task_id=${encodeURIComponent(taskId)}`),
  downloadLog: (taskId, tail = 200) => manReq(`download/log?task_id=${encodeURIComponent(taskId)}&tail=${tail}`, { timeout: 12000 }),

  modelsDelete: (paths) => manReq("models/delete", { method: "POST", body: { paths } }),
  // El manager v1.1.0 lee "target_path" (no "target"): se mandan ambas claves
  // para compatibilidad con managers viejos.
  modelsMove: (payload) => manReq("models/move", { method: "POST", body: { path: payload?.path, target: payload?.target ?? payload?.target_path, target_path: payload?.target_path ?? payload?.target } }),
  modelsRename: (payload) => manReq("models/rename", { method: "POST", body: payload }),

  outputs: (limit = 200) => manReq(`outputs?limit=${limit}`, { timeout: 15000 }),
  outputsDownload: (filenames) => manReq(`outputs/download?filenames=${encodeURIComponent(filenames.join(","))}`, { raw: true }),
  outputsDelete: (filenames) => manReq("outputs/delete", { method: "POST", body: { filenames } }),
  outputsMove: (filenames, target, subfolder = "") => manReq("outputs/move", { method: "POST", body: { filenames, target, subfolder } }),
  outputsRename: (filename, newName) => manReq("outputs/rename", { method: "POST", body: { filename, new_name: newName } }),
  outputsArchive: (filenames) => manReq("outputs/archive", { method: "POST", body: { filenames } }),
  outputsCleanup: (older_than_days, dry_run) => manReq("outputs/cleanup", { method: "POST", body: { older_than_days, dry_run } }),

  history: (action = "", limit = 50, offset = 0) => manReq(`history?action=${encodeURIComponent(action)}&limit=${limit}&offset=${offset}`),
  historyStats: () => manReq("history/stats"),
  historyRebuild: () => manReq("history/rebuild_index", { method: "POST" }),
  historyDownload: () => manReq("history/download", { raw: true }),

  // Logs físicos append-only (los escriben los servicios, los sirve el manager)
  gpuLog: ({ tail = 200, grep = "" } = {}) => manReq(`gpu-log?tail=${tail}${grep ? `&grep=${encodeURIComponent(grep)}` : ""}`, { timeout: 15000 }),
  gpuLogRaw: ({ tail = 200, grep = "" } = {}) => manReq(`gpu-log?tail=${tail}&raw=true${grep ? `&grep=${encodeURIComponent(grep)}` : ""}`, { raw: true, timeout: 20000 }),
  managerLog: ({ tail = 200, grep = "" } = {}) => manReq(`manager-log?tail=${tail}${grep ? `&grep=${encodeURIComponent(grep)}` : ""}`, { timeout: 15000 }),
  managerLogRaw: ({ tail = 200, grep = "" } = {}) => manReq(`manager-log?tail=${tail}&raw=true${grep ? `&grep=${encodeURIComponent(grep)}` : ""}`, { raw: true, timeout: 20000 }),

  // Upload multipart → va por /api/beam/upload (proxy) si está disponible.
  outputsUpload: async (file) => {
    const base = state.settings.managerUrl;
    if (!base) throw new Error("Configurá la URL del manager en ⚙ Settings");
    const target = join(base, "outputs/upload");
    const fd = new FormData();
    fd.append("file", file);
    if (await proxyAvailable()) {
      const q = new URLSearchParams({ url: target });
      if (state.settings.beamApiKey) q.set("apiKey", state.settings.beamApiKey);
      const r = await fetch(`/api/beam/upload?${q}`, { method: "POST", body: fd });
      const text = await r.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
      return data;
    }
    // fallback directo
    const r = await fetch(target, { method: "POST", headers: authHeaders(false), body: fd });
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  },
};

// ─────────────────────────────────────────────────────────────
// API Upscaler (Beam, servicio SEPARADO del GPU)
// SeedVR2 7B + GANs (spandrel). Endpoints: /health, /upscale/modelos,
// /warmup, POST /upscale (JSON o multipart, batch).
// Respuesta /upscale: { status, processed, results:[{filename, data(base64),
// width, height, format, ms}], errors:[{filename, error}] }
// ─────────────────────────────────────────────────────────────
function upsReq(path, { method = "GET", body, timeout = 0, raw = false } = {}) {
  const base = state.settings.upscaleUrl;
  if (!base) throw new Error("Configurá la URL del upscaler en ⚙ Settings");
  return beamFetch(base, path, { method, body, timeout, raw });
}

const upscaler = {
  health: () => upsReq("health", { timeout: 10000 }),
  modelos: () => upsReq("upscale/modelos", { timeout: 30000 }),
  warmup: () => upsReq("warmup", { method: "POST", body: {}, timeout: 900000 }),
  upscale: (p) => upsReq("upscale", { method: "POST", body: p, timeout: 900000 }),
};

// ─────────────────────────────────────────────────────────────
// Sync (Cloudflare D1 vía Pages Functions) — same origin
// ─────────────────────────────────────────────────────────────
const sync = {
  async settingsGet(syncKey) {
    return req(`/api/sync/settings?sync_key=${encodeURIComponent(syncKey)}`, { timeout: 8000 });
  },
  async settingsSave(syncKey, data) {
    return req("/api/sync/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sync_key: syncKey, data }), timeout: 8000 });
  },
  async generationsGet(syncKey, { limit = 100, tab = "" } = {}) {
    const q = new URLSearchParams({ sync_key: syncKey, limit: String(limit) });
    if (tab) q.set("tab", tab);
    return req(`/api/sync/generations?${q}`, { timeout: 8000 });
  },
  async generationsSave(rec) {
    return req("/api/sync/generations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rec), timeout: 8000 });
  },
  async generationsDelete(id, syncKey) {
    return req("/api/sync/generations/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, sync_key: syncKey }), timeout: 8000 });
  },
  async cacheSave(syncKey, key, data) {
    return req("/api/sync/cache", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sync_key: syncKey, key, data }), timeout: 8000 });
  },
  async cacheGet(syncKey, key = "list_all") {
    const q = new URLSearchParams({ sync_key: syncKey, key });
    return req(`/api/sync/cache?${q}`, { timeout: 8000 });
  },
};

// ─────────────────────────────────────────────────────────────
// Caché de imágenes en el navegador (Cache API + memoria)
// Las galerías no re-descargan las imágenes en cada render/refresh:
// se guardan en el Cache API (persiste entre sesiones) y en un Map
// en memoria (instantáneo durante la sesión).
// ─────────────────────────────────────────────────────────────
const _imgMem = new Map();
const _IMG_MEM_MAX = 80;
let _imgCacheP = null;
function imgCacheOpen() {
  if (!_imgCacheP) _imgCacheP = (typeof caches !== "undefined" && caches.open) ? caches.open("krea2-img-v1") : Promise.resolve(null);
  return _imgCacheP;
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = reject;
    fr.readAsDataURL(blob);
  });
}

function memSet(url, dataUrl) {
  if (_imgMem.has(url)) _imgMem.delete(url);
  _imgMem.set(url, dataUrl);
  if (_imgMem.size > _IMG_MEM_MAX) {
    const oldest = _imgMem.keys().next().value;
    _imgMem.delete(oldest);
  }
}

// Devuelve un data-URL de la imagen (cacheada), o la URL original si falla.
export async function cachedImgDataUrl(url) {
  if (!url) return "";
  // La Cache API exige URLs ABSOLUTAS (con /api/beam... relativa tira TypeError), así que
  // la clave de caché (memoria y Cache API) es SIEMPRE la URL absoluta.
  const abs = /^https?:\/\//i.test(url) ? url : new URL(url, location.origin).href;
  if (_imgMem.has(abs)) return _imgMem.get(abs);
  try {
    const cache = await imgCacheOpen();
    if (cache) {
      let r = await cache.match(abs);
      if (!r) {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        if ((resp.headers.get("Content-Type") || "").startsWith("image/")) {
          try { await cache.put(abs, resp.clone()); } catch { /* cache llena/rota */ }
        }
        r = resp;
      }
      const dataUrl = await blobToDataUrl(await r.blob());
      memSet(abs, dataUrl);
      return dataUrl;
    }
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const dataUrl = await blobToDataUrl(await resp.blob());
    memSet(abs, dataUrl);
    return dataUrl;
  } catch (e) {
    return url; // fallback: la URL directa (puede que el CORS no deje, pero es lo mejor posible)
  }
}

// Como beamImageUrl pero espera a que el proxy esté detectado (evita URLs directas
// bloqueadas por CORS cuando el ping todavía no terminó). Solo hosts Beam pasan por
// el proxy; imgchest/catbox/koofr vuelven tal cual (directo = rápido + cacheable).
export async function beamImageUrlAsync(url) {
  if (!url) return "";
  if (_needsProxy(url) && await proxyAvailable()) {
    const q = new URLSearchParams({ url });
    if (state.settings.beamApiKey) q.set("apiKey", state.settings.beamApiKey);
    return `/api/beam?${q}`;
  }
  return url;
}

// Helper de un solo paso: URL cruda → data-URL cacheado (o URL de fallback).
export async function displayImg(rawUrl) {
  if (!rawUrl) return "";
  return cachedImgDataUrl(await beamImageUrlAsync(rawUrl));
}

// ─────────────────────────────────────────────────────────────
// Hosts de imágenes
// ─────────────────────────────────────────────────────────────

// imgchest: CORS ✅ → subida directa desde el browser.
// Acepta opciones { title, description }: el caption con los metadatos de la
// generación (build_metadata_markdown en JS) va como description del post.
async function uploadImgchest(fileBlob, filename, { title = "", description = "" } = {}) {
  const key = state.settings.imgchestApiKey;
  if (!key) throw new Error("Falta IMGCHEST_API_KEY en Settings");
  const fd = new FormData();
  fd.append("images[]", fileBlob, filename || `krea2_${Date.now()}.png`);
  fd.append("privacy", "hidden");
  if (title) fd.append("title", title);
  if (description) fd.append("description", description);
  const r = await fetch("https://api.imgchest.com/v1/post", {
    method: "POST",
    headers: { Authorization: `Bearer ${key}` },
    body: fd,
  });
  const data = await r.json().catch(() => null);
  if (!r.ok || !data || !data.data || !data.data.images || !data.data.images.length) {
    throw new Error("imgchest: " + ((data && data.error) || data && data.message || `HTTP ${r.status}`));
  }
  return { host: "imgchest", url: data.data.images[0].link, postUrl: `https://imgchest.com/p/${data.data.id}`, deleteUrl: data.data.delete_url || "" };
}

// catbox: bloquea las IPs de Cloudflare (520) y NO manda CORS, así que un fetch directo
// del browser no puede leer la respuesta. Solución: un relay CORS (por defecto el
// playground oficial de Cloudflare, configurable en Settings) que reenvía el multipart
// a catbox desde OTRO egress y nos devuelve la URL con headers CORS correctos.
// Si el relay falla, la subida directa no-cors igual llega a tu cuenta (sin URL).
async function uploadCatbox(fileBlob, filename) {
  const fd = new FormData();
  fd.append("reqtype", "fileupload");
  fd.append("userhash", state.settings.catboxUserHash || "");
  fd.append("fileToUpload", fileBlob, filename || `krea2_${Date.now()}.png`);
  const relayBase = (state.settings.catboxRelay || "https://test.cors.workers.dev/?").replace(/\/?$/, "?");
  const target = encodeURIComponent("https://catbox.moe/user/api.php");
  let relayErr = "";
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 25000);
    const r = await fetch(`${relayBase}${target}`, { method: "POST", body: fd, signal: ctrl.signal });
    clearTimeout(t);
    const text = (await r.text()).trim();
    if (r.ok && /^https?:\/\/files\.catbox\.moe\/\S+$/i.test(text)) {
      return { host: "catbox", url: text, accountUrl: "https://catbox.moe" }; // URL real ✓
    }
    relayErr = !r.ok ? `relay HTTP ${r.status}` : (text ? text.slice(0, 140) : "respuesta vacía");
  } catch (e) {
    relayErr = "red: " + (e && e.message || e);
  }
  // Solo si el relay no llegó a tocar a catbox (red/HTTP del relay), intentamos la
  // subida directa no-cors (el archivo queda en la cuenta, sin URL capturable).
  if (/^(relay HTTP|red:)/.test(relayErr)) {
    try {
      await fetch("https://catbox.moe/user/api.php", { method: "POST", body: fd, mode: "no-cors" });
      return { host: "catbox", url: "", uploaded: true, accountUrl: "https://catbox.moe" };
    } catch (e2) { /* fallar abajo */ }
  }
  throw new Error("catbox: " + relayErr);
}

// koofr: WebDAV sin CORS → proxy CF Function
async function uploadKoofr(fileBlob, filename) {
  const fd = new FormData();
  fd.append("file", fileBlob, filename || `krea2_${Date.now()}.png`);
  fd.append("email", state.settings.koofrEmail);
  fd.append("appPassword", state.settings.koofrAppPassword);
  fd.append("folder", state.settings.koofrTargetFolder);
  return req("/api/upload/koofr", { method: "POST", body: fd, timeout: 30000 });
}

export const hosts = {
  imgchest: uploadImgchest,
  catbox: uploadCatbox,
  koofr: uploadKoofr,
};

// ─────────────────────────────────────────────────────────────
// Cloudflare R2 + Worker de imágenes (v9.4)
// Las imágenes se sirven desde el Worker (img.tudominio.com):
//   · display (default): WebP on-the-fly, cacheada en el CDN
//   · ?raw: el ORIGINAL tal cual (descarga, calidad 100%)
// El manager ya NO sirve imágenes; /serve_image ya no existe.
// ─────────────────────────────────────────────────────────────
export function r2BaseUrl() {
  return String(state.settings.r2Base || "https://krea2-img-worker.imagenessd11.workers.dev").replace(/\/+$/, "");
}

// Claves candidatas del archivo en el bucket R2 (mismatch v9.5/v9.6 vs v9.7):
//  · v9.7+ (GPU actual): krea2/{YYYY}/{MM}/{DD}/{archivo}
//  · v9.5/v9.6 y archivos viejos: outputs/{archivo}
// La primera es la primaria (la usa el GPU desplegado); la segunda es el fallback.
function r2Keys(filename) {
  const safe = String(filename || "").replace(/^\/+|\/+$/g, "");
  if (!safe) return [];
  const m = /^(\d{4})(\d{2})(\d{2})_/.exec(safe);
  const keys = [];
  if (m) keys.push(`krea2/${m[1]}/${m[2]}/${m[3]}/${safe}`);
  keys.push(`outputs/${safe}`);
  return keys;
}

// URL del Worker para un filename del volume. Primaria: la key del GPU v9.7
// (krea2/YYYY/MM/DD/). Si el nombre no tiene fecha, cae a outputs/ (v9.5/v9.6).
// Para display, usar r2FallbackUrl() como respaldo cuando la primaria da 404.
export function r2ImageUrl(filename, { raw = false } = {}) {
  const keys = r2Keys(filename);
  if (!keys.length) return "";
  return `${r2BaseUrl()}/${keys[0]}${raw ? "?raw" : ""}`;
}

// URL alternativa (la otra convención de key) para usar como fallback de display
export function r2FallbackUrl(filename, { raw = false } = {}) {
  const keys = r2Keys(filename);
  if (keys.length < 2) return "";
  return `${r2BaseUrl()}/${keys[1]}${raw ? "?raw" : ""}`;
}

// ¿Esta URL es del worker de R2? (los img del CDN van directos, sin data-URL ni proxy)
export function isR2Url(url) {
  if (!url) return false;
  try { return new URL(url).host === new URL(r2BaseUrl()).host; } catch { return false; }
}

export const api = { gpu, manager, upscaler, sync, hosts };
export default api;
