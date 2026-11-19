// ── Configuración por defecto (se puede editar en ⚙ Settings) ──
// settingsVersion: la migración v2 apaga returnBase64 en instalaciones viejas
// (respuestas livianas → la imagen se muestra desde el GPU, ~50s más rápido).
export const DEFAULT_SETTINGS = {
  settingsVersion: 3,
  // Sync entre teléfono y PC (Cloudflare D1). Configurá una clave privada en Settings.
  syncKey: "",

  // APIs de Modal (predeterminadas — pegá tus URLs *.modal.run en ⚙ Settings)
  // Legacy Beam (*.app.beam.cloud) sigue permitido en el proxy durante la transición.
  gpuUrl: "",
  managerUrl: "",
  // Upscaler: servicio separado (SeedVR2 + ESRGAN/spandrel). No va al GPU.
  upscaleUrl: "",
  // Modal con @modal.asgi_app() es público (CORS allow_origins=["*"]): no requiere
  // Bearer. Se mantiene el campo por compatibilidad con Beam legacy.
  beamApiKey: "",

  // Hosts de imágenes. Las credenciales se configuran en Settings y nunca se
  // incluyen en el cliente estático ni en el repositorio.
  catboxUserHash: "",
  imgchestApiKey: "",
  koofrEmail: "",
  koofrAppPassword: "",
  koofrTargetFolder: "/krea2_gallery",

  // Comportamiento
  autoUpload: false,          // habilitar después de configurar al menos un host
  defaultHost: "imgchest",    // imgchest | catbox | koofr (host que se muestra primero)
  reuseLastSeed: false,       // reutilizar la última seed generada
  returnBase64: false,        // respuestas livianas: la imagen se muestra desde el GPU (rápido)
                              // en vez de viajar como base64 por el proxy (eso tardaba ~50s más)

  // Ventana de warm: tiempo en segundos durante el cual el GPU se considera
  // "caliente" después de la última imagen generada. Mientras está dentro, el
  // siguiente gen usa la caché LRU (UNET/CLIP/VAE/LoRAs en VRAM, sin cold start).
  // Pasado el tiempo, el chip pasa a "frío" pero no se llama /warmup auto
  // (gastaría segundos facturados). Default 5 min.
  warmWindowSec: 300,

  // Relay CORS para catbox (catbox no manda CORS y bloquea las IPs de Cloudflare):
  // el browser sube por este relay y recibe la URL real. Es el playground oficial de
  // Cloudflare; si cambia, se puede poner otro relay acá.
  catboxRelay: "https://test.cors.workers.dev/?",

  // Cloudflare R2 + Worker de imágenes (v9.4): las imágenes se sirven desde acá
  // (webp on-the-fly; ?raw devuelve el original). El manager ya no sirve imágenes.
  // Worker real de la cuenta imagenessd11 (krea2-img-worker).
  r2Base: "https://krea2-img-worker.imagenessd11.workers.dev",
};

// Parámetros por defecto de las pestañas de generación
export const DEFAULT_PARAMS = {
  width: 1024,
  height: 1024,
  steps: 28,
  cfg: 1.0,
  mu: 1.15,
  denoise: 1.0,
  sampler: "euler",
  scheduler: "beta",
  seed: 0,
};

// Presets de ~1MP (≈1.048.576 px, múltiplos de 16) en todas las relaciones de aspecto.
// Se muestran por su ratio (ej: "4:3"); w × h queda de título.
export const DIMENSION_PRESETS = [
  { w: 1024, h: 1024, label: "1:1" },
  { w: 1152, h: 864,  label: "4:3" },
  { w: 864,  h: 1152, label: "3:4" },
  { w: 1248, h: 832,  label: "3:2" },
  { w: 832,  h: 1248, label: "2:3" },
  { w: 1360, h: 768,  label: "16:9" },
  { w: 768,  h: 1360, label: "9:16" },
  { w: 1568, h: 672,  label: "21:9" },
  { w: 672,  h: 1568, label: "9:21" },
  { w: 1120, h: 896,  label: "5:4" },
  { w: 896,  h: 1120, label: "4:5" },
];

export const SAMPLERS = ["euler", "euler_ancestral", "heun", "dpm_2", "dpm_2_ancestral", "lms", "ddim", "uni_pc", "uni_pc_bh2"];
export const SCHEDULERS = ["beta", "normal", "karras", "exponential", "sgm_uniform", "simple", "ddim_uniform"];

// Metadata de hosts para badges / labels
export const HOSTS = {
  catbox:   { label: "catbox",   color: "text-orange-300 border-orange-400/40" },
  imgchest: { label: "imgchest", color: "text-purple-300 border-purple-400/40" },
  koofr:    { label: "koofr",    color: "text-sky-300 border-sky-400/40" },
};

export const TAB_META = [
  { key: "generate",  label: "Generar",   icon: "●" },
  { key: "variation", label: "Variar",    icon: "◒" },
  { key: "edit",      label: "Editar",    icon: "■" },
  { key: "depth",     label: "Profundidad", icon: "▲" },
  { key: "upscale",   label: "Upscale",   icon: "◉" },
  { key: "models",    label: "Modelos",   icon: "▦" },
  // v2.3: la galería (imágenes) y el historial (operaciones + log GPU) van SEPARADOS
  { key: "gallery",   label: "Galería",   icon: "▧" },
  { key: "history",   label: "Historial", icon: "≡" },
];
