// Smoke test: importa todos los módulos del front con stubs mínimos.
// Uso: node smoke.mjs  (desde krea2-webui)
globalThis.localStorage = { getItem: () => null, setItem: () => {}, };
// crypto nativo de Node 24 ya provee getRandomValues
globalThis.fetch = async () => ({ ok: true, json: async () => ({}), text: async () => "", blob: async () => new Blob(), status: 200 });
globalThis.URL.createObjectURL = () => "blob:x";
globalThis.document = {
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  createElement: () => ({
    style: {}, dataset: {}, children: [], classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, setAttribute() {}, append() {}, appendChild() {}, remove() {},
    querySelector: () => null, querySelectorAll: () => [], cloneNode: () => ({}),
  }),
  body: { append() {}, appendChild() {} },
};
globalThis.Blob = globalThis.Blob || class {};

await import("./public/js/config.js");
await import("./public/js/state.js");
await import("./public/js/api.js");
await import("./public/js/ui.js");
await import("./public/js/components.js");
await import("./public/js/tabs.js");
console.log("SMOKE OK: todos los módulos importan sin errores");
