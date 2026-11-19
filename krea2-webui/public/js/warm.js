// ══════════════════════════════════════════════════════════════
// BARRA DE WARM (ventana caliente después de generar)
// ══════════════════════════════════════════════════════════════
//
// - Al generar una imagen con éxito, el contador se resetea a warmWindowSec
//   y empieza a bajar. Mientras está dentro de la ventana, el siguiente gen
//   va a usar la caché LRU del GPU (UNET/CLIP/VAE/LoRAs en VRAM, sin cold
//   start). Al expirar, el chip pasa a "frío" pero NO se llama /warmup
//   automáticamente — vos decidís si querés gastar segundos facturados.
//
// - El valor del slider persiste en localStorage vía state.settings.warmWindowSec.
//
// - El reset se engancha al evento "krea2:gpu-ok" que ya dispara tabs.js
//   cuando la GPU devuelve una imagen. No hace falta tocar tabs.js.
//
// - Si el GPU está frío (caché vacía), el primer gen siempre va a ser lento
//   (cold start del container). La barra refleja "frío" hasta que llegue
//   la primera imagen.

import { state, saveSettings } from "./state.js";

const SLIDER_MIN = 30;
const SLIDER_MAX = 900;
const SLIDER_STEP = 30;

// Estado interno: tiempo en el que expira la ventana (ms epoch). 0 = nunca hubo gen.
let _expiresAt = 0;
let _tickHandle = 0;

function _clampSec(s) {
  // Si no es número o es 0 (valor inválido) → default 300
  const n0 = Number(s);
  if (!Number.isFinite(n0) || n0 <= 0) return 300;
  const n = Math.round(n0);
  if (n < SLIDER_MIN) return SLIDER_MIN;
  if (n > SLIDER_MAX) return SLIDER_MAX;
  // Alineamos al step (30s) para que el slider no "salte"
  return Math.round(n / SLIDER_STEP) * SLIDER_STEP;
}

function _fmtRemaining(ms) {
  if (ms <= 0) return "0s";
  const total = Math.ceil(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}m ${s.toString().padStart(2, "0")}s` : `${s}s`;
}

function _fmtWindow(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s > 0 ? `${m}m ${s.toString().padStart(2, "0")}s` : `${m}m 0s`;
}

// El estado describe si una imagen generada recientemente puede reutilizar
// la GPU; la ventana configurada es independiente del tiempo restante.
function _stateFor(pctLeft) {
  if (pctLeft <= 0) return { text: "Frío", key: "cold" };
  if (pctLeft <= 0.25) return { text: "Por expirar", key: "expiring" };
  if (pctLeft <= 0.5) return { text: "Tibio", key: "warm" };
  return { text: "Caliente", key: "hot" };
}

function _render() {
  const win = _clampSec(state.settings.warmWindowSec);
  const now = Date.now();
  const msLeft = _expiresAt > 0 ? Math.max(0, _expiresAt - now) : 0;
  const pctLeft = win > 0 ? msLeft / (win * 1000) : 0;

  const slider = $("#warm-slider");
  const label = $("#warm-label");
  const stateEl = $("#warm-state");
  const fill = $("#warm-fill");
  const countdown = $("#warm-countdown");
  const progress = $("#warm-progress");
  if (!slider || !stateEl || !fill || !countdown) return;

  if (slider.value !== String(win)) slider.value = String(win);
  if (label) label.textContent = _fmtWindow(win);

  const pct = _expiresAt === 0 ? 0 : Math.max(0, Math.min(100, pctLeft * 100));
  const st = _expiresAt === 0 ? { text: "Sin actividad", key: "idle" } : _stateFor(pctLeft);
  stateEl.textContent = st.text;
  stateEl.className = `warm-state is-${st.key}`;
  fill.className = `warm-fill is-${st.key}`;
  fill.style.width = `${pct}%`;
  countdown.textContent = _expiresAt === 0 ? "—" : _fmtRemaining(msLeft);
  if (progress) {
    progress.setAttribute("aria-valuenow", String(Math.round(pct)));
    progress.setAttribute("aria-valuetext", _expiresAt === 0 ? "Sin actividad" : `${_fmtRemaining(msLeft)} restantes, GPU ${st.text.toLowerCase()}`);
  }
}

function _tick() {
  if (_expiresAt === 0) return; // no había ventana activa
  const win = _clampSec(state.settings.warmWindowSec) * 1000;
  const msLeft = Math.max(0, _expiresAt - Date.now());
  if (msLeft === 0 && _expiresAt > 0) {
    // Expiró: congelamos la barra en "frío" pero dejamos el listener vivo
    // por si el usuario cambia el slider (ahí re-renderizamos).
    _render();
    return;
  }
  _render();
  _tickHandle = setTimeout(_tick, 250);
}

// Reset público: lo llama tabs.js vía el evento "krea2:gpu-ok".
// También se puede llamar a mano con window.warmReset().
export function resetWindow() {
  const win = _clampSec(state.settings.warmWindowSec);
  _expiresAt = Date.now() + win * 1000;
  if (_tickHandle) clearTimeout(_tickHandle);
  _render();
  _tick();
}

export function clearWindow() {
  _expiresAt = 0;
  if (_tickHandle) clearTimeout(_tickHandle);
  _render();
}

// Devuelve cuánto le queda (ms) y la ventana total (ms) para que otros módulos
// (futuro "auto-merge de galería", "preflight", etc.) puedan saber si el engine
// está caliente.
export function getWarmStatus() {
  const win = _clampSec(state.settings.warmWindowSec);
  const now = Date.now();
  const msLeft = _expiresAt > 0 ? Math.max(0, _expiresAt - now) : 0;
  return {
    totalMs: win * 1000,
    msLeft,
    hot: msLeft > 0,
    expiresAt: _expiresAt,
  };
}

function _onSliderInput(e) {
  const v = _clampSec(e.target.value);
  state.settings = { ...state.settings, warmWindowSec: v };
  saveSettings(state.settings);
  // Cambiar la duración ajusta una ventana que sigue activa; no debe convertir
  // una ventana ya expirada en una GPU "caliente" sin una nueva generación.
  if (_expiresAt > Date.now()) {
    _expiresAt = Date.now() + v * 1000;
    if (_tickHandle) clearTimeout(_tickHandle);
    _render();
    _tick();
  } else {
    _render();
  }
}

function _onGpuOk() {
  // Evento disparado por tabs.js cuando una imagen llega del GPU.
  // Es el momento exacto para empezar (o reiniciar) la ventana caliente.
  resetWindow();
}

export function initWarmBar() {
  const slider = $("#warm-slider");
  if (!slider) return; // el HTML no terminó de cargar (no debería pasar)

  // Inicializar el valor del slider desde settings
  slider.min = String(SLIDER_MIN);
  slider.max = String(SLIDER_MAX);
  slider.step = String(SLIDER_STEP);
  slider.value = String(_clampSec(state.settings.warmWindowSec));
  slider.addEventListener("input", _onSliderInput);

  // Reset al recibir imagen del GPU (cualquier endpoint: generate/variation/edit/depth)
  window.addEventListener("krea2:gpu-ok", _onGpuOk);

  // Render inicial
  _render();
}

// Exponer para debug desde la consola
if (typeof window !== "undefined") {
  window.warmReset = resetWindow;
  window.warmClear = clearWindow;
  window.warmStatus = getWarmStatus;
}
