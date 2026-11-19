// Proxy server-side a las APIs (Modal + Beam legacy).
//
// Beam responde "Access-Control-Allow-Origin: *, *" (header DUPLICADO), que los
// navegadores rechazan → todo fetch directo desde el browser queda bloqueado.
// Modal (CORSMiddleware allow_origins=["*"]) tiene CORS limpio y permite fetch
// directo, pero el proxy se mantiene para compatibilidad y para unificar auth.
// Este proxy re-emite la llamada desde el server (sin CORS) y devuelve la
// respuesta con CORS limpio. Es el mismo patrón que /api/upload/catbox y koofr.
//
//   GET  /api/beam/ping                       → { ok: true }  (detección de proxy)
//   GET  /api/beam?url=<encoded>&apiKey=<enc> → reenvía un GET server-side
//   POST /api/beam  body={url,method,headers,body,apiKey} → reenvía con método arbitrario
//
// Seguridad: solo se permite forwardear a hosts *.modal.run y *.app.beam.cloud
// (legacy, no es un proxy abierto).
// (El upload a catbox ya NO va por acá: catbox bloquea las IPs de Cloudflare con 520.)
const JSON_HEADERS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function validateUrl(raw) {
  let u;
  try { u = new URL(raw); } catch { return null; }
  if (!/\.modal\.run$/.test(u.hostname) && !/\.app\.beam\.cloud$/.test(u.hostname)) return null;
  return u;
}

// Devuelve el body del upstream tal cual (preserva binarios en downloads).
function corsResponse(upstream) {
  const ct = upstream.headers.get("Content-Type") || "application/json";
  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": ct, "Access-Control-Allow-Origin": "*" },
  });
}

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  if (url.pathname.endsWith("/ping")) return json({ ok: true });

  const target = url.searchParams.get("url") || "";
  const apiKey = url.searchParams.get("apiKey") || "";
  const u = validateUrl(target);
  if (!u) return json({ error: "URL no permitida (solo *.modal.run y *.app.beam.cloud legacy)" }, 403);

  const headers = {};
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
  try {
    const r = await fetch(target, { method: "GET", headers });
    return corsResponse(r);
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 502);
  }
}

export async function onRequestPost(context) {
  let body;
  try { body = await context.request.json(); }
  catch { return json({ error: "JSON requerido" }, 400); }

  const { url, method = "GET", headers = {}, body: data, apiKey = "" } = body || {};
  const u = validateUrl(url);
  if (!u) return json({ error: "URL no permitida (solo *.modal.run y *.app.beam.cloud legacy)" }, 403);

  const h = { ...headers };
  if (apiKey) h["Authorization"] = `Bearer ${apiKey}`;
  let payload;
  if (data !== undefined && data !== null && method !== "GET" && method !== "HEAD") {
    if (typeof data === "string") payload = data;
    else { h["Content-Type"] = h["Content-Type"] || "application/json"; payload = JSON.stringify(data); }
  }
  try {
    const r = await fetch(url, { method, headers: h, body: payload });
    return corsResponse(r);
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 502);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}
