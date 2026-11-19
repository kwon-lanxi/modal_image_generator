// Detección de proxy: la UI pinguea este endpoint al arrancar.
// Existe como archivo explícito (no depende del fallback de routing de Pages)
// para que /api/beam/ping responda { ok: true } siempre.
const JSON_HEADERS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export async function onRequestGet() {
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers: JSON_HEADERS });
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}
