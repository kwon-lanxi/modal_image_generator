// Upload a Koofr vía WebDAV PUT — Koofr no soporta CORS para browser, va server-side.
// POST /api/upload/koofr  (multipart: file, email, appPassword, folder)
// → { url, host: "koofr", name }
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
  try {
    const form = await context.request.formData();
    const file = form.get("file");
    const email = (form.get("email") || "").trim();
    const appPassword = (form.get("appPassword") || "").trim();
    const folder = (form.get("folder") || "/krea2_gallery").trim().replace(/^\/+|\/+$/g, "");

    if (!file) return json({ error: "Falta el campo 'file'" }, 400);
    if (!email || !appPassword) return json({ error: "Faltan email o app password de Koofr" }, 400);

    const name = file.name || `krea2_${Date.now()}.png`;
    const path = folder ? `${folder}/${name}` : name;
    // El mount por defecto de Koofr es "Koofr": /dav/Koofr/<folder>/<file>
    const webdavUrl = `https://app.koofr.net/dav/Koofr/${path.split("/").map(encodeURIComponent).join("/")}`;
    const auth = "Basic " + btoa(`${email}:${appPassword}`);

    const r = await fetch(webdavUrl, {
      method: "PUT",
      headers: {
        Authorization: auth,
        "Content-Type": file.type || "image/png",
      },
      body: file,
    });

    if (!r.ok) {
      const t = (await r.text()).slice(0, 300);
      return json({ error: `koofr HTTP ${r.status}: ${t}` }, r.status);
    }
    return json({ url: webdavUrl, host: "koofr", name });
  } catch (e) {
    return json({ error: String(e && e.message || e) }, 500);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: JSON_HEADERS });
}
