// CDP test: galería unificada, list_all alimenta modelos+outputs, sanitize base64, descargas async.
// Uso: node cdp-unify.mjs  (requiere Chrome headless en :9232 apuntando a http://localhost:8141)
// Usa el WebSocket NATIVO de Node 22+ (no requiere el paquete ws).

const WS = "ws://127.0.0.1:9232/devtools/page/";
const results = [];
function check(name, ok, extra = "") {
  results.push({ name, ok, extra });
  console.log(`${ok ? "✅" : "❌"} ${name}${extra ? " — " + extra : ""}`);
}

async function getTargets() {
  const r = await fetch("http://127.0.0.1:9232/json/list");
  return r.json();
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    ws.addEventListener("open", () => resolve(ws));
    ws.addEventListener("error", reject);
  });
}

let seq = 0;
const pending = new Map();
function send(ws, method, params = {}) {
  return new Promise((resolve) => {
    const id = ++seq;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
}

const wait = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  const targets = await getTargets();
  const page = targets.find(t => t.type === "page" && t.url.startsWith("http://localhost:8141")) || targets.find(t => t.type === "page");
  if (!page) { console.log("NO PAGE TARGET"); process.exit(1); }
  const ws = await connect(page.webSocketDebuggerUrl);
  ws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });

  // mock fetch: intercepta list_all/outputs/generations y respuestas de imágenes
  const mock = `
    (() => {
      const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
      const files = [
        { filename: '20260804_010000_krea2_111_batch0.png', size_bytes: 100, size_human: '100B', mtime: 1785850000, url: '/serve_image?filename=20260804_010000_krea2_111_batch0.png' },
        { filename: '20260803_230000_krea2_222_batch0.png', size_bytes: 200, size_human: '200B', mtime: 1785830000, url: '/serve_image?filename=20260803_230000_krea2_222_batch0.png' },
        { filename: '20260802_120000_krea2_333_batch0.png', size_bytes: 300, size_human: '300B', mtime: 1785750000, url: '/serve_image?filename=20260802_120000_krea2_333_batch0.png' },
      ];
      const listAll = {
        success: true, ts: Date.now()/1000,
        loras: { v: { mount: '/krea2-beam-volume', path: '/krea2-beam-volume/loras', tree: { _root: [
          { name: 'A.safetensors', logical_name: 'A', relative_path: 'A.safetensors', absolute_path: '/krea2-beam-volume/loras/A.safetensors', size_bytes: 100, size_human: '100B' },
          { name: 'B.safetensors', logical_name: 'B', relative_path: 'B.safetensors', absolute_path: '/krea2-beam-volume/loras/B.safetensors', size_bytes: 200, size_human: '200B' },
        ], folders: {} } } },
        unets: { v: { mount: '/krea2-beam-volume', path: '/krea2-beam-volume/diffusion_models', tree: { _root: [
          { name: 'U.safetensors', logical_name: 'Krea2_Turbo_fp8mixed', relative_path: 'U.safetensors', absolute_path: '/krea2-beam-volume/diffusion_models/U.safetensors', size_bytes: 300, size_human: '300B' },
        ], folders: {} } } },
        clips: { v: { mount: '/krea2-beam-volume', path: '/krea2-beam-volume/text_encoders', tree: { _root: [
          { name: 'C.safetensors', logical_name: 'qwen_3vl_4b', relative_path: 'C.safetensors', absolute_path: '/krea2-beam-volume/text_encoders/C.safetensors', size_bytes: 300, size_human: '300B' },
        ], folders: {} } } },
        vaes: { v: { mount: '/krea2-beam-volume', path: '/krea2-beam-volume/vae', tree: { _root: [
          { name: 'V.safetensors', logical_name: 'wan_2.1_vae', relative_path: 'V.safetensors', absolute_path: '/krea2-beam-volume/vae/V.safetensors', size_bytes: 300, size_human: '300B' },
        ], folders: {} } } },
        upscalers: { v: { mount: '/krea2-beam-volume', path: '/krea2-beam-volume/upscale', tree: { _root: [], folders: {} } } },
        outputs: { path: '/krea2-beam-volume/outputs', count: files.length, files },
      };
      const realFetch = window.fetch.bind(window);
      window.__listAllCalls = 0;
      window.__listAllRefreshCalls = 0;
      window.__outputsCalls = 0;
      window.__syncCalls = 0;
      window.fetch = async (url, opts = {}) => {
        const u = String(url);
        if (u.includes('/api/beam/ping')) return new Response(JSON.stringify({ ok: true }), { status: 200 });
        if (u.includes('/api/beam')) {
          let body = {};
          try { body = JSON.parse(opts.body); } catch {}
          const target = body.url || u;
          if (target.includes('/list_all')) {
            window.__listAllCalls++;
            window.__listAllUrls = (window.__listAllUrls || []);
            window.__listAllUrls.push(target);
            if (target.includes('refresh=true')) window.__listAllRefreshCalls++;
            return new Response(JSON.stringify({ ...listAll, cached: !target.includes('refresh=true'), cache_age_sec: 7 }), { status: 200 });
          }
          if (target.includes('/outputs')) { window.__outputsCalls++; return new Response(JSON.stringify({ success: true, files }), { status: 200 }); }
          if (target.includes('/download/batch')) return new Response(JSON.stringify({ success: true, task_id: 't123', items_count: 1 }), { status: 200 });
          if (target.includes('/download/status')) return new Response(JSON.stringify({ task_id: 't123', status: 'done', progress: { current: 1, total: 1, current_item: 'x' }, results: [{ filename: 'x.safetensors', size_human: '1.0MB' }], errors: [], skipped: [] }), { status: 200 });
          if (target.includes('/health')) return new Response(JSON.stringify({ status: 'ok' }), { status: 200 });
          if (target.includes('/healthz')) return new Response(JSON.stringify({ ok: true }), { status: 200 });
          return new Response(JSON.stringify({ success: true }), { status: 200 });
        }
        if (u.includes('/api/sync/generations')) {
          window.__syncCalls++;
          return new Response(JSON.stringify({ items: [{
            id: 'd1_1', tab: 'generate', beam_filename: '20260804_010000_krea2_111_batch0.png',
            image_url: 'https://man.app.beam.cloud/serve_image?filename=20260804_010000_krea2_111_batch0.png',
            payload: { positive_prompt: 'hola', seed: 111, width: 1024, height: 1024, steps: 28, lora: { A: 1.0 } },
            hosts: {}, created_at: 1785850000000,
          }], total: 1 }), { status: 200 });
        }
        if (u.includes('/api/sync/cache')) {
          window.__syncCalls++;
          return new Response(JSON.stringify({ ok: true }), { status: 200 });
        }
        if (u.includes('/api/sync/settings')) return new Response(JSON.stringify({ data: null }), { status: 200 });
        // imágenes (proxied o data:) → PNG
        return new Response(png, { status: 200, headers: { 'Content-Type': 'image/png' } });
      };
      // Caches viejos entre corridas (el user-data-dir persiste) → estado limpio y determinista
      localStorage.removeItem('krea2.models');
      localStorage.removeItem('krea2.outputs');
      localStorage.removeItem('krea2.generations');
      localStorage.setItem('krea2.settings', JSON.stringify({
        syncKey: 'test-key', gpuUrl: 'https://gpu.app.beam.cloud', managerUrl: 'https://man.app.beam.cloud',
        beamApiKey: 'key', autoUpload: false, returnBase64: true,
      }));
      window.__mockReady = true;
    })();
  `;

  await send(ws, "Page.enable");
  await send(ws, "Runtime.enable");
  await send(ws, "Page.addScriptToEvaluateOnNewDocument", { source: mock });
  await send(ws, "Page.navigate", { url: "http://localhost:8141/index.html" });
  await wait(3500);

  const evalJs = async (expr) => {
    const r = await send(ws, "Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result?.result?.value;
  };

  // 1) Models cached + populated from list_all (state via import de módulo)
  const counts = await evalJs(`(async () => {
    const st = await import('/js/state.js');
    return {
      listAll: window.__listAllCalls, outputsCalls: window.__outputsCalls, syncCalls: window.__syncCalls,
      models: !!st.state.models, unets: (st.state.models?.unets||[]).length, loras: (st.state.models?.loras||[]).length,
      outputs: (st.state.outputs||[]).length,
    };
  })()`);
  check("list_all puebla modelos + outputs (una llamada)", counts.listAll >= 1 && counts.unets >= 1 && counts.loras >= 2 && counts.outputs >= 3, JSON.stringify(counts));

  // 2) Ir a Galería: sin request extra de /outputs (usa cache del list_all)
  await evalJs(`document.querySelectorAll('.nav-item')[5]?.click()`); // Galería
  await wait(2000);
  const gal = await evalJs(`(() => {
    const grid = document.querySelector('#hist-gen-grid');
    const cards = grid ? grid.querySelectorAll('.card-img').length : 0;
    return { cards, outputsCalls: window.__outputsCalls, hasCount: !!document.querySelector('#hist-count')?.textContent };
  })()`);
  check("galería muestra tarjetas (D1 + volume, sin duplicar)", gal.cards >= 3, JSON.stringify(gal));
  check("la galería NO llama /outputs (usa cache del list_all)", gal.outputsCalls === 0, `outputsCalls=${gal.outputsCalls}`);

  // 3) Las tarjetas tienen imagen (image_url reconstruida)
  const imgs = await evalJs(`(() => {
    const imgs = [...document.querySelectorAll('#hist-gen-grid img')];
    return { total: imgs.length, withSrc: imgs.filter(i => i.src && i.src.startsWith('data:')).length };
  })()`);
  check("tarjetas con imagen data-URL", imgs.withSrc >= 3, JSON.stringify(imgs));

  // 4) Modal de detalle con metadata al hacer click (el handler está en el <img>)
  await evalJs(`document.querySelector('#hist-gen-grid .card-img img')?.click()`);
  await wait(1500);
  const modal = await evalJs(`(() => {
    const m = document.querySelector('#modal-root');
    return { open: !!m?.querySelector('.modal-panel'), hasSeed: /seed/i.test(m?.textContent || ''), hasLoras: /LoRAs|A 1/i.test(m?.textContent || '') };
  })()`);
  check("click en imagen → modal con metadata", modal.open && modal.hasSeed && modal.hasLoras, JSON.stringify(modal));
  await evalJs(`document.querySelector('#modal-root [data-close-modal]')?.click()`);
  await wait(300);

  // 5) Descargas: va por /download/batch (async) no /download directo
  await evalJs(`document.querySelectorAll('.nav-item')[4]?.click()`); // Models
  await wait(800);
  await evalJs(`document.querySelector('#models-show-download')?.click()`);
  await wait(300);
  const dl = await evalJs(`(() => {
    const set = (id, v) => { const e = document.querySelector(id); if (e) e.value = v; };
    set('#dl-name', 'prueba');
    set('#dl-url', 'https://civitai.com/api/download/models/123');
    document.querySelector('#dl-go').click();
    return true;
  })()`);
  await wait(3500);
  const dlState = await evalJs(`(() => ({
    status: document.querySelector('#dl-batch-status')?.textContent || '',
    task: document.querySelector('#dl-batch-box')?.textContent?.slice(0, 120) || '',
  }))()`);
  check("descarga individual va por batch async", /done|1\/1|t123/.test(dlState.status + dlState.task), JSON.stringify(dlState));

  // 6) sanitize: guardar registro con payload base64 NO debe persistir el base64
  const sanit = await evalJs(`(async () => {
    const st = await import('/js/state.js');
    const clean = st.sanitizeRecordForStorage({ payload: { image: 'data:image/png;base64,XXX', image_b: 'data:image/png;base64,YYY', reference_images: [{ image: 'data:image/png;base64,ZZZ', strength: 0.5 }], lora: { A: 1 } } });
    return { image: clean.payload.image, imgB: clean.payload.image_b, ref: clean.payload.reference_images[0].image, lora: clean.payload.lora.A };
  })()`);
  check("sanitize: payload sin base64 (queda lora)", sanit.image === undefined && sanit.ref === undefined && sanit.lora === 1, JSON.stringify(sanit));

  // 7) Refresco manual → list_all con ?refresh=true (el manager tiene caché TTL)
  const before = await evalJs(`window.__listAllRefreshCalls`);
  await evalJs(`document.querySelector('#models-refresh')?.click()`);
  await wait(2500);
  const ref = await evalJs(`(async () => {
    const st = await import('/js/state.js');
    return { refreshCalls: window.__listAllRefreshCalls, fromCache: !!st.state.modelsFromManagerCache, urls: (window.__listAllUrls || []).slice(-4) };
  })()`);
  check("refresco manual fuerza ?refresh=true en list_all", ref.refreshCalls > before, JSON.stringify(ref));

  // 8) No hay pestaña Outputs duplicada (solo Galería)
  const tabs = await evalJs(`[...document.querySelectorAll('.nav-item span:nth-child(2)')].map(s => s.textContent)`);
  check("nav sin pestaña Outputs duplicada", !tabs.includes('Outputs'), tabs.join(','));

  // 9) Cache de modelos/loras con TTL: { savedAt, models }, y refresh NO pega a la red si está fresco
  const ttl = await evalJs(`(async () => {
    const st = await import('/js/state.js');
    const c = st.loadModelsCache();
    const before = window.__listAllCalls;
    const { refreshModels } = await import('/js/tabs.js');
    await refreshModels(true); // silencioso, cache fresco (savedAt reciente)
    await new Promise(r => setTimeout(r, 400));
    return {
      shape: !!(c && c.models && c.savedAt && c.models.loras),
      fresh: Date.now() - c.savedAt < st.MODELS_CACHE_TTL,
      callsBefore: before, callsAfter: window.__listAllCalls,
    };
  })()`);
  check("cache de modelos con TTL 300s + refresh fresco no pega a la red", ttl.shape && ttl.fresh && ttl.callsAfter === ttl.callsBefore, JSON.stringify(ttl));

  // 10) imgchest/catbox directo (sin proxy); solo hosts Beam pasan por /api/beam
  const direct = await evalJs(`(async () => {
    try {
      const api = await import('/js/api.js');
      const cmp = await import('/js/components.js');
      const img = cmp.makeImg('https://imgchest.com/i/abc123.png');
      return {
        imgchestProxy: api.needsBeamProxy('https://imgchest.com/i/abc123.png'),
        beamProxy: api.needsBeamProxy('https://man.app.beam.cloud/serve_image?filename=x.png'),
        directSrc: img.src,
      };
    } catch (e) { return { err: String(e && e.stack || e) }; }
  })()`);
  check("imgchest directo (sin proxy); solo Beam proxied", !direct.imgchestProxy && direct.beamProxy && direct.directSrc === 'https://imgchest.com/i/abc123.png', JSON.stringify(direct));

  // 11) Galería ordenada por fecha Y hora (más recientes arriba)
  const order = await evalJs(`(async () => {
    const st = await import('/js/state.js');
    const t = st.state.generations.map(g => g.created_at || 0);
    let sorted = true;
    for (let i = 1; i < t.length; i++) if (t[i] > t[i - 1]) sorted = false;
    return { sorted, n: t.length, first: t[0], last: t[t.length - 1] };
  })()`);
  check("galería ordenada por fecha+hora (desc)", order.sorted && order.n >= 3, JSON.stringify(order));

  // 12) Botón de portapapeles: lee { prompt, neg_prompt } y rellena los campos
  await evalJs(`document.querySelectorAll('.nav-item')[0]?.click()`); // Generar
  await wait(500);
  const paste = await evalJs(`(async () => {
    const btn = document.querySelector('#f-generate-pastebtn');
    if (!btn) return { btn: false };
    const clip = JSON.stringify({ prompt: 'un astronauta en marte', neg_prompt: 'borroso, feo' });
    // mock de readText del portapapeles dentro del page
    window.__clip = clip;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true, value: { readText: async () => window.__clip, writeText: async () => {} },
    });
    btn.click();
    await new Promise(r => setTimeout(r, 400));
    const pos = document.querySelector('#f-generate-positive')?.value;
    const neg = document.querySelector('#f-generate-negative')?.value;
    return { btn: true, pos, neg };
  })()`);
  check("botón pega JSON del portapapeles → prompt + neg_prompt", paste.btn && paste.pos === 'un astronauta en marte' && paste.neg === 'borroso, feo', JSON.stringify(paste));

  // 13) Estado del formulario persiste en localStorage y sobrevive a un reload (celular)
  const formSaved = await evalJs(`(async () => {
    const pos = document.querySelector('#f-generate-positive');
    if (!pos) return { err: 'no textarea' };
    pos.value = 'prompt persistente de prueba';
    pos.dispatchEvent(new Event('input', { bubbles: true }));
    // agregar un LoRA por el input + botón ＋
    const addIn = [...document.querySelectorAll('#gen-form-generate input[type=text]')].find(i => (i.placeholder || '').includes('Agregar LoRA'));
    if (addIn) {
      addIn.value = 'MiLora';
      const addBtn = [...document.querySelectorAll('#gen-form-generate button')].find(b => b.textContent.trim() === '＋');
      if (addBtn) { addBtn.click(); addIn.dispatchEvent(new Event('input', { bubbles: true })); }
    }
    await new Promise(r => setTimeout(r, 1400)); // debounce de guardado
    let forms = null;
    try { forms = JSON.parse(localStorage.getItem('krea2.forms') || 'null'); } catch {}
    return {
      prompt: forms?.generate?.positive_prompt,
      lora: !!(forms?.generate?.loras && forms.generate.loras.MiLora !== undefined),
      currentTab: forms?.currentTab,
    };
  })()`);
  check("formulario guardado en localStorage (prompt + LoRA + tab)", formSaved.prompt === 'prompt persistente de prueba' && formSaved.lora && formSaved.currentTab === 'generate', JSON.stringify(formSaved));
  await send(ws, "Page.reload", {});
  await wait(3500);
  const restored = await evalJs(`(() => ({
    pos: document.querySelector('#f-generate-positive')?.value,
    loraRow: [...document.querySelectorAll('#gen-form-generate input[type=text]')].some(i => i.value === 'MiLora'),
    tabVisible: !document.querySelector('#tab-generate')?.classList.contains('hidden'),
  }))()`);
  check("tras reload: prompt, LoRA y pestaña restaurados", restored.pos === 'prompt persistente de prueba' && restored.loraRow && restored.tabVisible, JSON.stringify(restored));

  // 14) anim.js: helpers exportados y no tiran error (fallback si no hay CDN)
  const anim = await evalJs(`(async () => {
    try {
      const a = await import('/js/anim.js');
      a.animReveal(document.body);
      a.animStagger([document.body]);
      a.animBurst(document.body);
      a.animTabIn(document.body);
      return { revealFn: typeof a.animReveal === 'function', staggerFn: typeof a.animStagger === 'function', animeGlobal: typeof window.anime === 'function' };
    } catch (e) { return { err: String((e && e.stack) || e) }; }
  })()`);
  check("anim.js exporta animaciones sin errores", anim.revealFn && anim.staggerFn, JSON.stringify(anim));

  const failed = results.filter(r => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks OK`);
  process.exit(failed.length ? 1 : 0);
}

main().catch(e => { console.error("TEST CRASH:", e); process.exit(2); });
