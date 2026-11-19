// Borrar un registro de generación — Cloudflare D1
// DELETE /api/sync/generations/delete?id=X&sync_key=Y  (también acepta POST con JSON)
const JSON_HEADERS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "DELETE, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

async function doDelete(context, id, syncKey) {
  if (!id || !syncKey) return json({ error: "id y sync_key requeridos" }, 400);
  const DB = context.env.krea2_sync || context.env.DB || null;
  if (!DB) return json({ ok: false, error: "D1 no configurado (agregá el binding krea2_sync en wrangler.toml)" });
  await DB.prepare(
    "DELETE FROM generations WHERE id = ? AND sync_key = ?"
  ).bind(id, syncKey).run();
  return json({ ok: true });
}

export async function onRequestDelete(context) {
  try {
    const url = new URL(context.request.url);
    return await doDelete(context, url.searchParams.get("id") || "", url.searchParams.get("sync_key") || "");
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestPost(context) {
  try {
    const body = await context.request.json();
    return await doDelete(context, body.id || "", body.sync_key || "");
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}
