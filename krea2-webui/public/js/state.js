import { DEFAULT_SETTINGS } from "./config.js";

const LS_SETTINGS = "krea2.settings";
const LS_LAST_SEED = "krea2.lastSeed";
const LS_GENERATIONS = "krea2.generations";
const LS_MODELS = "krea2.models";
const LS_OUTPUTS = "krea2.outputs";
const LS_FORMS = "krea2.forms";

function readGenerations() {
  try {
    const raw = localStorage.getItem(LS_GENERATIONS);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* ignore */ }
  return [];
}

// Migración de settings: v1 (sin version) tenía returnBase64:true por defecto, lo que
// hacía viajar la imagen en base64 por el proxy (~50s extra). v2 lo apaga una vez;
// v3 quita credenciales privadas que habían quedado en defaults públicos.
export function migrateSettings(next) {
  const s = { ...next };
  if (!s.settingsVersion || s.settingsVersion < 2) {
    s.returnBase64 = false;
    s.settingsVersion = 2;
  }
  if (s.settingsVersion < 3) {
    // Retirar defaults privados publicados previamente: deben ingresarse de nuevo
    // por el usuario, tras rotar las credenciales expuestas.
    s.syncKey = "";
    s.catboxUserHash = "";
    s.imgchestApiKey = "";
    s.koofrEmail = "";
    s.koofrAppPassword = "";
    s.autoUpload = false;
  }
  s.settingsVersion = 3;
  return s;
}

function readSettings() {
  try {
    const raw = localStorage.getItem(LS_SETTINGS);
    if (raw) return { ...DEFAULT_SETTINGS, ...migrateSettings(JSON.parse(raw)) };
  } catch (e) { /* ignore */ }
  return { ...DEFAULT_SETTINGS };
}

export const state = {
  settings: readSettings(),
  // Catálogo de modelos del manager: { unets: [], clips: [], vaes: [], upscalers: [], loras: [] }
  // Cada entrada de loras puede traer (v2.7+) { version, character, vol_path } —
  // atributos NUEVOS, opcionales: los registros viejos siguen funcionando.
  models: null,
  // Desglose v1/v2 de LoRAs (manager v2.7 /list_models?type=lora).
  // { v1:[], v2:[], general:[] } — atributo opcional; null en managers < v2.7.
  lorasByVersion: null,
  modelsLoadedAt: 0,
  // Lista de outputs del manager (último /list_all o /outputs) — se cachea en localStorage
  outputs: null,
  outputsLoadedAt: 0,
  // Generaciones (local + cloud D1 + galería del manager/volume)
  generations: readGenerations(),
  // Estado del sync: "cloud" (D1 ok) | "beam" (galería del manager) | "local" | null
  syncMode: null,
  // Último resultado para "Enviar a variation/edit"
  lastResult: null,
  // Cola de batch de descargas
  batch: [],
  // Mapa tab → presets guardados
  presets: {},
  // Estado de los formularios (prompt, LoRAs, seed, imágenes…) — persiste en localStorage
  // para que el celular (que recarga la pestaña al cambiar de app) no pierda nada.
  forms: loadForms(),
};

export function saveGenerationsLocal(list) {
  // Sanitiza SIEMPRE acá (única responsable): cualquier registro que quede con base64
  // (edit/variation) revienta el quota de localStorage → "cuando salgo se pierde todo".
  try {
    localStorage.setItem(LS_GENERATIONS, JSON.stringify(list.slice(0, 300).map(sanitizeRecordForStorage)));
  } catch (e) { /* quota */ }
}

// Caché del catálogo de modelos (loras + unets + clips + vaes + upscalers) para
// no esperar a /list_all al abrir. TTL de 300s: si el caché está fresco se usa SIN
// pegarle a la red; pasados los 300s ("si no hay cambios") se vacía y la próxima
// apertura re-escanea el manager.
export const MODELS_CACHE_TTL = 300_000;

export function saveModelsCache(models, lorasByVersion = null) {
  try { localStorage.setItem(LS_MODELS, JSON.stringify({ savedAt: Date.now(), models, lorasByVersion })); } catch (e) {}
}

// Devuelve { models, savedAt } o null si no hay caché o está vencido (ya se vació).
export function loadModelsCache() {
  try {
    const raw = localStorage.getItem(LS_MODELS);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Compat: formato viejo = objeto pelado (loras/unets/…) o array, sin timestamp →
    // se hidrata igual (instantáneo) pero con savedAt=0 el guard de TTL no saltea y
    // el auto-refresh re-escanea y lo re-guarda con timestamp.
    if (parsed && (Array.isArray(parsed) || !parsed.models)) return { models: parsed, lorasByVersion: null, savedAt: 0 };
    if (parsed && parsed.models) {
      const savedAt = parsed.savedAt || 0;
      if (Date.now() - savedAt > MODELS_CACHE_TTL) {
        try { localStorage.removeItem(LS_MODELS); } catch { /* ignore */ }
        return null; // vencido → se vació; hay que re-escancar
      }
      return { models: parsed.models, lorasByVersion: parsed.lorasByVersion || null, savedAt };
    }
  } catch (e) { /* ignore */ }
  return null;
}

// Caché de outputs (lista de archivos del manager) — persiste entre sesiones
export function loadOutputsCache() {
  try {
    const raw = localStorage.getItem(LS_OUTPUTS);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* ignore */ }
  return null;
}

export function saveOutputsCache(outputs) {
  try { localStorage.setItem(LS_OUTPUTS, JSON.stringify(outputs)); } catch (e) {}
}

// ─────────────────────────────────────────────────────────────
// ESTADO DE FORMULARIOS — se guarda continuamente (debounced) y al
// esconder/recargar la pestaña. Sobrevive a los reloads del celular.
// ─────────────────────────────────────────────────────────────
// Versión del layout de formularios. Si el guardado local viene de una versión
// anterior de la app (campos que cambiaron de nombre/lugar), se DESCARTA entero
// en vez de restaurar valores en el campo equivocado — el bug del celular de
// "la seed aparece en el CFG" era exactamente eso: datos viejos restaurados en
// el layout nuevo. Si algún día cambiás el layout, bumpéalo y los viejos se limpian solos.
export const FORMS_VERSION = 3;

export function loadForms() {
  try {
    const raw = localStorage.getItem(LS_FORMS);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && parsed._v === FORMS_VERSION) return parsed;
      // Versión vieja/corrupta → descartar (arranque limpio)
      try { localStorage.removeItem(LS_FORMS); } catch (e) { /* ignore */ }
    }
  } catch (e) { /* ignore */ }
  return {};
}

export function saveForms(forms) {
  try {
    localStorage.setItem(LS_FORMS, JSON.stringify({ _v: FORMS_VERSION, ...forms }));
  } catch (e) {
    // Quota llena (imágenes base64): reintentar SIN imágenes → el texto y los LoRAs
    // siempre se guardan; las imágenes se reconstruyen desde la galería al rehacer.
    try {
      const light = {};
      for (const [k, v] of Object.entries(forms)) {
        if (k === "currentTab") { light[k] = v; continue; }
        if (v && typeof v === "object") {
          const { images, ...rest } = v;
          light[k] = { ...rest };
        } else light[k] = v;
      }
      localStorage.setItem(LS_FORMS, JSON.stringify({ _v: FORMS_VERSION, ...light }));
    } catch (e2) { /* no hay espacio */ }
  }
}

export function saveSettings(next) {
  state.settings = migrateSettings({ ...state.settings, ...next });
  try { localStorage.setItem(LS_SETTINGS, JSON.stringify(state.settings)); } catch (e) {}
}

// ─────────────────────────────────────────────────────────────
// BORRAR CACHÉS LOCALES (botón "Borrar caché" en Settings y Models)
// Elimina modelos, outputs, generaciones y formularios cacheados + el
// Cache API de imágenes. NO toca los settings ni las seeds. Después recargar.
// ─────────────────────────────────────────────────────────────
export function clearLocalCache() {
  for (const k of [LS_MODELS, LS_OUTPUTS, LS_GENERATIONS, LS_FORMS]) {
    try { localStorage.removeItem(k); } catch (e) { /* ignore */ }
  }
  state.models = null; state.modelsLoadedAt = 0;
  state.lorasByVersion = null;
  state.outputs = null; state.outputsLoadedAt = 0;
  state.generations = [];
  state.forms = {};
  try { if (typeof caches !== "undefined" && caches.delete) caches.delete("krea2-img-v1"); } catch (e) { /* ignore */ }
}

export function saveLastSeed(seed) {
  try { localStorage.setItem(LS_LAST_SEED, String(seed)); } catch (e) {}
  state.lastSeed = seed;
}

export function getLastSeed() {
  try { return parseInt(localStorage.getItem(LS_LAST_SEED), 10) || 0; } catch (e) { return 0; }
}

// Seed local con criptografía real (el random del server está buggeado)
export function randomSeed() {
  const buf = new Uint32Array(1);
  crypto.getRandomValues(buf);
  return (buf[0] >>> 0) || 1;
}

// ─────────────────────────────────────────────────────────────
// SANITIZAR PAYLOAD PARA PERSISTIR
// Los payloads de edit/variation/depth llevan imágenes base64 (1-4 MB c/u).
// Guardarlos tal cual revienta el quota de localStorage (~5MB) y las filas de
// D1 (2MB) → "cuando salgo se pierde todo". Al persistir se reemplazan por la
// URL del manager (imageUrl) y se reconstruyen al hacer "Rehacer".
// ─────────────────────────────────────────────────────────────
export function sanitizePayloadForStorage(payload) {
  if (!payload || typeof payload !== "object") return payload || {};
  const p = { ...payload };
  // Imágenes base64 pesadas → se dropean (la imagen se re-carga desde imageUrl)
  delete p.image;
  delete p.image_b;
  if (Array.isArray(p.reference_images)) {
    p.reference_images = p.reference_images.map(r => r && typeof r === "object" ? { ...r, image: undefined } : r);
  }
  return p;
}

// Devuelve una versión liviana de un registro para localStorage/D1
// (payload sin base64; metadata completa para el modal).
export function sanitizeRecordForStorage(rec) {
  const copy = { ...rec };
  copy.payload = sanitizePayloadForStorage(rec.payload);
  return copy;
}
