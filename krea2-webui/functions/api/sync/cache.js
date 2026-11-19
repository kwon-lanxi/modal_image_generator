// Cache compartida del manager (snapshot de /list_all: modelos + outputs) — Cloudflare D1.
// GET  /api/sync/cache?sync_key=X&key=list_all → { data, updated_at }
// POST /api/sync/cache → { sync_key, key, data } → { ok }
const JSON_HEADERS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function db(context) {
  return context.env.krea2_sync || context.env.DB || null;
}

function cacheKey(syncKey, key) {
  return `${syncKey}:${key}`.slice(0, 200);
}

export async function onRequestGet(context) {
  try {
    const url = new URL(context.request.url);
    const syncKey = url.searchParams.get("sync_key") || "";
    const key = url.searchParams.get("key") || "list_all";
    if (!syncKey) return json({ error: "sync_key requerida" }, 400);
    const DB = db(context);
    if (!DB) return json({ ok: false, error: "D1 no configurado", data: null });
    const { results } = await DB.prepare("SELECT data, updated_at FROM cache WHERE cache_key = ?")
      .bind(cacheKey(syncKey, key)).all();
    if (!results.length) return json({ data: null });
    let data = null;
    try { data = JSON.parse(results[0].data); } catch { data = results[0].data; }
    return json({ data, updated_at: results[0].updated_at });
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestPost(context) {
  try {
    const body = await context.request.json();
    const syncKey = (body.sync_key || "").trim();
    const key = (body.key || "list_all").trim();
    if (!syncKey || body.data === undefined) return json({ error: "sync_key y data requeridos" }, 400);
    const DB = db(context);
    if (!DB) return json({ ok: false, error: "D1 no configurado" });
    const raw = JSON.stringify(body.data);
    if (raw.length > 2_000_000) {
      return json({ error: "data demasiado grande para D1" }, 413);
    }
    await DB.prepare(
      `INSERT INTO cache (cache_key, data, updated_at) VALUES (?, ?, ?)
       ON CONFLICT(cache_key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at`
    ).bind(cacheKey(syncKey, key), raw, Date.now()).run();
    return json({ ok: true });
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}
