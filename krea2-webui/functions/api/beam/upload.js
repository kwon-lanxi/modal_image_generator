// Proxy de upload multipart al manager (Modal + Beam legacy, outputs/upload).
// Beam tenía el CORS roto → el forward va server-side. Modal tiene CORS limpio
// pero se mantiene el proxy para compatibilidad.
//
// POST /api/beam/upload?url=<encoded>&apiKey=<enc>  (multipart: file)
// → respuesta cruda del upstream (JSON del manager)
const JSON_HEADERS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

export async function onRequestPost(context) {
  const url = new URL(context.request.url);
  const target = url.searchParams.get("url") || "";
  const apiKey = url.searchParams.get("apiKey") || "";
  let u;
  try { u = new URL(target); } catch { return json({ error: "URL inválida" }, 400); }
  if (!/\.modal\.run$/.test(u.hostname) && !/\.app\.beam\.cloud$/.test(u.hostname)) {
    return json({ error: "URL no permitida (solo *.modal.run y *.app.beam.cloud legacy)" }, 403);
  }
  try {
    const form = await context.request.formData();
    const headers = {};
    if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
    const r = await fetch(target, { method: "POST", headers, body: form });
    const text = await r.text();
    const ct = r.headers.get("Content-Type") || "application/json";
    return new Response(text, { status: r.status, headers: { "Content-Type": ct, "Access-Control-Allow-Origin": "*" } });
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 502);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}
