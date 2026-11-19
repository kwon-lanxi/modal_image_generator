import { state, saveSettings, migrateSettings, clearLocalCache } from "./state.js";
import { api, probeBeam, proxyAvailable } from "./api.js";
import { $, el, toast, openModal, escapeHtml } from "./ui.js";
import { initTabs, refreshModels, hydrateModelsCache, hydrateOutputsCache, hydrateFromD1Cache } from "./tabs.js";
import { initWarmBar } from "./warm.js";

// ── Status dots ──  (ok = null → estado idle gris, sin verificar)
function setDot(id, ok, busy = false) {
  const d = $(id);
  if (!d) return;
  const cls = ok === null ? "bg-gray-500" : ok ? "bg-green-500" : "bg-red-500";
  d.className = `dot ${cls}${busy ? " pulse" : ""}`;
}

const SYNC_MODE_LABEL = { cloud: "☁ D1", beam: "▤ volume", local: "💾 local" };
function updateSyncChip() {
  const label = $("#sync-key-label");
  if (!label) return;
  const mode = state.syncMode;
  const icon = SYNC_MODE_LABEL[mode] || "💾";
  if (state.settings.syncKey) label.textContent = `${icon} ${state.settings.syncKey.slice(0, 14)}`;
  else if (mode === "beam") label.textContent = `${icon} volume compartido`;
  else label.textContent = "💾 local";
}

// Healthz del GPU: SOLO manual (click en el chip GPU). El auto-check no lo toca
// para no despertar/mantener caliente la instancia de Beam innecesariamente.
async function checkGpu() {
  setDot("#dot-gpu", null, true); // verificando…
  let gpuOk = false;
  let detail = "";
  if (state.settings.gpuUrl) {
    try {
      const h = await api.gpu.healthz();
      gpuOk = !!(h && (h.ok || h.state));
      detail = h?.state ? `state=${h.state}` : "";
    } catch (e) { gpuOk = false; detail = e.message; }
  } else detail = "Configurá la URL del GPU en ⚙ Settings";
  setDot("#dot-gpu", gpuOk);
  toast(gpuOk ? `GPU OK${detail ? " — " + detail : ""}` : `GPU sin respuesta${detail ? " — " + detail : ""}`, gpuOk ? "ok" : "err", 3500);
  return gpuOk;
}

async function checkHealth() {
  let manOk = false, syncOk = false;
  if (state.settings.managerUrl) {
    try { const h = await api.manager.health(); manOk = h?.status === "ok"; }
    catch { manOk = false; }
  }
  setDot("#dot-manager", manOk);
  // Sync: verde si D1 responde O si el manager (volume compartido) está alcanzable
  if (state.settings.syncKey) {
    try {
      const r = await api.sync.settingsGet(state.settings.syncKey);
      if (r && r.ok === false) throw new Error(r.error || "D1 no configurado"); // no marcar cloud si D1 falta
      syncOk = true; state.syncMode = "cloud";
    } catch { syncOk = manOk; state.syncMode = manOk ? "beam" : "local"; }
  } else {
    syncOk = manOk;
    state.syncMode = manOk ? "beam" : "local";
  }
  setDot("#dot-sync", syncOk);
  updateSyncChip();
}

// ── Settings modal ──
const SET_FIELDS = [
  ["syncKey", "Sync Key (compartila entre tu teléfono y PC)"],
  ["gpuUrl", "URL servicio GPU (Beam)"],
  ["managerUrl", "URL manager (Beam)"],
  ["upscaleUrl", "URL upscaler (Beam, servicio separado)"],
  ["beamApiKey", "API key de Beam (Bearer, misma para ambas APIs)"],
  ["r2Base", "Base R2 / Worker de imágenes (ej: https://img.tudominio.com)"],
  ["catboxUserHash", "CATBOX_USER_HASH"],
  ["imgchestApiKey", "IMGCHEST_API_KEY"],
  ["koofrEmail", "KOOFR_EMAIL"],
  ["koofrAppPassword", "KOOFR_APP_PASSWORD"],
  ["koofrTargetFolder", "KOOFR_TARGET_FOLDER"],
  ["catboxRelay", "Relay CORS para catbox (termina en ?)"],
];
const SECRET_FIELDS = new Set(["beamApiKey", "imgchestApiKey", "koofrAppPassword"]);

function openSettings() {
  const s = state.settings;
  const { close } = openModal(`
    <div class="p-5 space-y-3">
      <h2 class="font-semibold text-[17px]">⚙️ Settings</h2>
      <div class="text-[13px] text-dim">
        Sync: <b>☁ D1</b> (Cloudflare, requiere las Functions deployadas) · <b>▤ volume</b> (galería compartida del manager de Beam, sin deploy) · <b>💾 local</b> (solo este dispositivo).<br/>
        Los resultados siempre se guardan en este dispositivo; si ponés la misma Sync Key en el teléfono y la PC se suman la nube y la galería del manager.
      </div>
      ${SET_FIELDS.map(([k, label]) => `
        <div>
          <label class="block text-xs text-dim mb-1">${escapeHtml(label)}</label>
          <input id="set-${k}" type="${SECRET_FIELDS.has(k) ? "password" : "text"}" value="${escapeHtml(s[k] ?? "")}" class="input mono" spellcheck="false" />
        </div>`).join("")}
      <div class="grid grid-cols-2 gap-3">
        <label class="flex items-center gap-2 text-sm"><input type="checkbox" id="set-autoUpload" class="toggle-check" ${s.autoUpload ? "checked" : ""}/><span class="toggle"></span> auto-subir a imgchest + catbox + koofr al generar</label>
        <div>
          <label class="block text-xs text-dim mb-1">Host default</label>
          <select id="set-defaultHost" class="input">
            <option value="imgchest">imgchest</option>
            <option value="catbox">catbox</option>
            <option value="koofr">koofr</option>
          </select>
        </div>
      </div>
      <label class="flex items-center gap-2 text-sm"><input type="checkbox" id="set-returnBase64" class="toggle-check" ${s.returnBase64 ? "checked" : ""}/><span class="toggle"></span> devolver base64 por defecto</label>
      <div class="flex items-center justify-between pt-2 border-t border-edge">
        <span class="text-xs text-dim">Cachés locales: modelos, galería, formularios (los settings no se tocan)</span>
        <button class="btn-ghost btn-danger text-xs" id="set-clear-cache">🗑 Borrar caché y recargar</button>
      </div>
      <div id="set-test-out" class="text-xs space-y-1"></div>
      <div class="flex gap-2 pt-2">
        <button class="btn-secondary" id="set-test">Probar conexión</button>
        <div class="flex-1"></div>
        <button class="btn-secondary" data-close-modal>Cancelar</button>
        <button class="btn-primary" id="set-save">Guardar y sincronizar</button>
      </div>
    </div>`, { width: "max-w-lg" });

  $("#set-defaultHost").value = s.defaultHost || "imgchest";

  $("#set-clear-cache").addEventListener("click", async () => {
    if (!confirm("¿Borrar cachés locales (modelos, galería, formularios) y recargar? Los settings no se tocan.")) return;
    clearLocalCache();
    location.reload();
  });

  $("#set-test").addEventListener("click", async () => {
    const out = $("#set-test-out");
    out.innerHTML = "";
    const snap = {
      gpuUrl: $("#set-gpuUrl").value.trim(),
      managerUrl: $("#set-managerUrl").value.trim(),
      upscaleUrl: ($(`#set-upscaleUrl`)?.value ?? state.settings.upscaleUrl ?? "").trim(),
      beamApiKey: $("#set-beamApiKey").value.trim(),
    };
    const row = (name, ok, msg) => out.append(el("div", { html: `${ok ? "✅" : "❌"} <b>${name}</b> — ${escapeHtml(msg)}` }));
    if (snap.gpuUrl) {
      try {
        const r = await probeBeam(snap.gpuUrl, "healthz", snap.beamApiKey);
        row("GPU", r.ok, `state=${r.data?.state || "?"}${r.error ? " — " + r.error : ""}`);
      } catch (e) { row("GPU", false, e.message); }
    } else row("GPU", false, "sin URL");
    if (snap.managerUrl) {
      try {
        const r = await probeBeam(snap.managerUrl, "health", snap.beamApiKey);
        row("Manager", r.ok, `v${r.data?.version || "?"} ${r.data?.gpu ? "gpu" : "cpu"}${r.error ? " — " + r.error : ""}`);
      } catch (e) { row("Manager", false, e.message); }
    } else row("Manager", false, "sin URL");
    if (!snap.gpuUrl && !snap.managerUrl) row("Sync", true, "solo sync local");
  });

  $("#set-save").addEventListener("click", async () => {
    const next = {};
    for (const [k] of SET_FIELDS) next[k] = $(`#set-${k}`).value.trim();
    next.autoUpload = $("#set-autoUpload").checked;
    next.defaultHost = $("#set-defaultHost").value;
    next.returnBase64 = $("#set-returnBase64").checked;
    saveSettings(next);
    close();
    toast("Settings guardados", "ok");
    if (next.syncKey) {
      try { await api.sync.settingsSave(next.syncKey, state.settings); toast("Sincronizado ✓", "ok", 1800); }
      catch (e) { toast("Sync: " + e.message, "err"); }
    }
    updateSyncChip();
    checkHealth();
  });
}

// ── Sync pull inicial ──
async function pullSync() {
  if (!state.settings.syncKey) { updateSyncChip(); checkHealth(); return; }
  try {
    const r = await api.sync.settingsGet(state.settings.syncKey);
    if (r && r.ok === false) {
      // D1 no configurado → el sync cae al volume del manager (beam) o local
      console.warn("sync pull:", r.error);
    } else if (r && r.data && Object.keys(r.data).length) {
      saveSettings(migrateSettings(r.data)); // el remoto gana, pero migrado (base64 v1 → v2)
      toast("Settings sincronizados desde la nube", "ok", 2500);
    } else if (r && r.data === null) {
      await api.sync.settingsSave(state.settings.syncKey, state.settings);
    }
    if (!(r && r.ok === false)) state.syncMode = "cloud";
  } catch (e) {
    console.warn("sync pull:", e.message);
    if (!state.syncMode) state.syncMode = "local";
  }
  updateSyncChip();
  checkHealth();
}

// ── Init ──
$("#btn-settings").addEventListener("click", openSettings);
$("#sync-chip").addEventListener("click", openSettings);
$("#gpu-chip").addEventListener("click", checkGpu);
setDot("#dot-gpu", null); // GPU arranca en idle gris hasta verificación manual
updateSyncChip();
initTabs();
initWarmBar(); // barra superior de ventana warm (depende del header en el DOM)
// Catálogo + outputs: caché local instantánea → snapshot de D1 (otro dispositivo).
// Una SOLA llamada al manager por carga de página (con su caché de 60s, no re-escaneo).
// NADA se repite en background: cada poll automático despierta/mantiene caliente la
// instancia de Beam y cuesta segundos facturados (keep_warm=0). Todo lo demás es manual.
hydrateModelsCache();
hydrateOutputsCache();
hydrateFromD1Cache();
refreshModels(true);
proxyAvailable(); // detectar /api/beam (CORS roto de Beam → proxy) lo antes posible
pullSync();
checkHealth(); // un solo check por carga de página (dots) — sin intervalos
// El historial puede cambiar el modo de sync (cloud → beam/local); actualizá el chip
window.addEventListener("krea2:syncmode", updateSyncChip);
// Tras una generación exitosa el GPU respondió de verdad → dot verde sin healthz extra
window.addEventListener("krea2:gpu-ok", () => setDot("#dot-gpu", true));
