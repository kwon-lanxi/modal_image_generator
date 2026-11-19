// Sync de settings — Cloudflare D1
// GET  /api/sync/settings?sync_key=X      → { data, updated_at }
// POST /api/sync/settings                  → { sync_key, data } → { ok }
const JSON_HEADERS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

// El binding en wrangler.toml se llama krea2_sync; se acepta también DB por compat.
function db(context) {
  return context.env.krea2_sync || context.env.DB || null;
}

export async function onRequestGet(context) {
  try {
    const syncKey = new URL(context.request.url).searchParams.get("sync_key") || "";
    if (!syncKey) return json({ error: "sync_key requerida" }, 400);
    const DB = db(context);
    if (!DB) return json({ ok: false, error: "D1 no configurado (agregá el binding krea2_sync en wrangler.toml)" });
    const { results } = await DB.prepare(
      "SELECT data, updated_at FROM settings WHERE sync_key = ?"
    ).bind(syncKey).all();
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
    if (!syncKey || body.data === undefined) return json({ error: "sync_key y data requeridos" }, 400);
    const DB = db(context);
    if (!DB) return json({ ok: false, error: "D1 no configurado (agregá el binding krea2_sync en wrangler.toml)" });
    await DB.prepare(
      `INSERT INTO settings (sync_key, data, updated_at) VALUES (?, ?, ?)
       ON CONFLICT(sync_key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at`
    ).bind(syncKey, JSON.stringify(body.data), Date.now()).run();
    return json({ ok: true });
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}
