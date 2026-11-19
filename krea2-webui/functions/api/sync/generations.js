// Historial de generaciones — Cloudflare D1
// GET  /api/sync/generations?sync_key=X&limit=100&offset=0&tab= → { items: [...], total }
// POST /api/sync/generations  → { sync_key, id, tab, payload, hosts, beam_filename, image_url } → { ok }
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

// La DB remota puede no tener la columna image_url si la migración
// (ALTER TABLE generations ADD COLUMN image_url TEXT) todavía no corrió.
// Se consulta PRAGMA una vez por request y se adapta el SQL: mientras falte
// la columna la API sigue funcionando (sin image_url); una vez migrada se
// lee/guarda el campo. Evita el 500 "no such column: image_url".
async function hasImageUrlColumn(DB) {
  try {
    const { results } = await DB.prepare("PRAGMA table_info(generations)").all();
    return Array.isArray(results) && results.some(r => r && r.name === "image_url");
  } catch {
    return false;
  }
}

export async function onRequestGet(context) {
  try {
    const url = new URL(context.request.url);
    const syncKey = url.searchParams.get("sync_key") || "";
    const limit = Math.min(parseInt(url.searchParams.get("limit") || "100", 10) || 100, 500);
    const offset = parseInt(url.searchParams.get("offset") || "0", 10) || 0;
    const tab = url.searchParams.get("tab") || "";
    if (!syncKey) return json({ error: "sync_key requerida" }, 400);
    const DB = db(context);
    if (!DB) return json({ ok: false, error: "D1 no configurado (agregá el binding krea2_sync en wrangler.toml)", items: [], total: 0, limit, offset });

    const withImg = await hasImageUrlColumn(DB);
    let sql = "SELECT id, tab, payload, hosts, beam_filename, created_at FROM generations WHERE sync_key = ?";
    if (withImg) sql = "SELECT id, tab, payload, hosts, beam_filename, image_url, created_at FROM generations WHERE sync_key = ?";
    const binds = [syncKey];
    if (tab) { sql += " AND tab = ?"; binds.push(tab); }
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?";
    binds.push(limit, offset);

    const { results } = await DB.prepare(sql).bind(...binds).all();
    const items = results.map(r => {
      let payload = {}, hosts = {};
      try { payload = JSON.parse(r.payload); } catch {}
      try { hosts = JSON.parse(r.hosts); } catch {}
      // Nunca devolver nulls: las filas viejas tienen image_url/beam_filename en
      // NULL y el cliente los usa directo (src, textos) → se veía "null" literal.
      if (!payload || typeof payload !== "object") payload = {};
      if (!hosts || typeof hosts !== "object") hosts = {};
      return { id: r.id, tab: r.tab, payload, hosts, beam_filename: r.beam_filename || "", image_url: (withImg && r.image_url) || "", created_at: r.created_at };
    });
    return json({ items, total: items.length, limit, offset });
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestPost(context) {
  try {
    const body = await context.request.json();
    const syncKey = (body.sync_key || "").trim();
    const id = (body.id || "").trim();
    const tab = (body.tab || "generate").trim();
    if (!syncKey || !id) return json({ error: "sync_key e id requeridos" }, 400);
    const DB = db(context);
    if (!DB) return json({ ok: false, error: "D1 no configurado (agregá el binding krea2_sync en wrangler.toml)" });
    const withImg = await hasImageUrlColumn(DB);
    if (withImg) {
      await DB.prepare(
        `INSERT OR REPLACE INTO generations (id, sync_key, tab, payload, hosts, beam_filename, image_url, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
      ).bind(
        id,
        syncKey,
        tab,
        JSON.stringify(body.payload || {}),
        JSON.stringify(body.hosts || {}),
        body.beam_filename || null,
        body.image_url || null,
        body.created_at || Date.now()
      ).run();
    } else {
      await DB.prepare(
        `INSERT OR REPLACE INTO generations (id, sync_key, tab, payload, hosts, beam_filename, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`
      ).bind(
        id,
        syncKey,
        tab,
        JSON.stringify(body.payload || {}),
        JSON.stringify(body.hosts || {}),
        body.beam_filename || null,
        body.created_at || Date.now()
      ).run();
    }
    return json({ ok: true });
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}
