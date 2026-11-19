// Test de interacción vía Chrome DevTools Protocol (Node 22+ con WebSocket nativo).
// Requiere Chrome headless con --remote-debugging-port=9222 apuntando a la app.
const port = 9222;
const list = await (await fetch(`http://localhost:${port}/json`)).json();
const page = list.find(t => t.type === "page");
if (!page) { console.log("NO PAGE"); process.exit(1); }

const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const errors = [];
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id) { const p = pending.get(m.id); if (p) { pending.delete(m.id); p.resolve(m); } }
  else if (m.method === "Runtime.exceptionThrown") {
    errors.push("EXC: " + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  }
  else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
    errors.push("CONSOLE: " + m.params.args.map(a => a.value ?? a.description ?? "").join(" "));
  }
};
function send(method, params = {}) {
  return new Promise((resolve) => { const i = ++id; pending.set(i, { resolve }); ws.send(JSON.stringify({ id: i, method, params })); });
}
async function evalJs(expression) {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) { errors.push("EVAL: " + expression); return null; }
  return r.result?.result?.value;
}
await new Promise(r => ws.onopen = r);
await send("Runtime.enable"); await send("Log.enable");
await new Promise(r => setTimeout(r, 1800));

const out = {};
out.diceSeed = await evalJs(`(() => { document.getElementById('f-generate-dice').click(); return document.getElementById('f-generate-seed').value; })()`);
out.loraAdd = await evalJs(`(() => {
  const inp = document.querySelector('#gen-form-generate input[list="lora-options"]');
  if (!inp) return 'NO-INPUT';
  inp.value = 'test_lora'; inp.dispatchEvent(new Event('input'));
  inp.nextElementSibling.click();
  return document.querySelectorAll('[data-lora-row]').length;
})()`);
out.loraWeightValue = await evalJs(`(() => { const n = document.querySelector('[data-lora-row] input[type="number"]'); return n ? n.value : 'NO-NUM'; })()`);
out.loraAdd2 = await evalJs(`(() => {
  const inp = document.querySelector('#gen-form-generate input[list="lora-options"]');
  inp.value = 'test_lora2'; inp.dispatchEvent(new Event('input'));
  inp.nextElementSibling.click();
  return document.querySelectorAll('[data-lora-row]').length;
})()`);
out.loraDelete = await evalJs(`(() => {
  const del = document.querySelector('[data-lora-row] .btn-danger');
  if (del) del.click();
  return document.querySelectorAll('[data-lora-row]').length;
})()`);
out.sliderSync = await evalJs(`(() => {
  const r = document.getElementById('f-generate-mu');
  r.value = 2; r.dispatchEvent(new Event('input'));
  return r.nextElementSibling.value;
})()`);
out.settingsModal = await evalJs(`(() => {
  document.getElementById('btn-settings').click();
  const h = document.getElementById('set-catboxUserHash');
  return { exists: !!h, catboxDefault: h ? h.value : null, hasKoofr: !!document.getElementById('set-koofrEmail'), hasTestBtn: !!document.getElementById('set-test') };
})()`);
out.settingsClose = await evalJs(`(() => {
  const c = document.querySelector('#modal-root [data-close-modal]');
  if (c) c.click();
  return document.querySelectorAll('#modal-root > div').length;
})()`);
out.tabVariation = await evalJs(`(() => {
  document.querySelectorAll('#sidebar .nav-item')[1].click();
  return {
    genHidden: document.getElementById('tab-generate').classList.contains('hidden'),
    varVisible: !document.getElementById('tab-variation').classList.contains('hidden'),
    hasForm: !!document.getElementById('f-variation-go'),
  };
})()`);
out.tabEditLocked = await evalJs(`(() => {
  document.querySelectorAll('#sidebar .nav-item')[2].click();
  const rows = [...document.querySelectorAll('[data-lora-row]')];
  return rows.map(r => ({ name: r.querySelector('input').value, disabled: r.querySelector('input').disabled }));
})()`);
out.tabDepth = await evalJs(`(() => {
  document.querySelectorAll('#sidebar .nav-item')[3].click();
  return { depthVisible: !document.getElementById('tab-depth').classList.contains('hidden'), hasForm: !!document.getElementById('f-depth-go') };
})()`);
out.tabModelsOutputsHistory = await evalJs(`(() => {
  document.querySelectorAll('#sidebar .nav-item')[4].click();
  const m = !!document.getElementById('models-tbody');
  document.querySelectorAll('#sidebar .nav-item')[5].click();
  const o = !!document.getElementById('outputs-grid');
  document.querySelectorAll('#sidebar .nav-item')[6].click();
  const h = !!document.getElementById('hist-gen-grid');
  return { models: m, outputs: o, history: h };
})()`);

console.log(JSON.stringify({ out, errors }, null, 2));
process.exit(0);
