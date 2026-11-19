/**
 * Krea2 Image Worker — sirve imágenes de Cloudflare R2.
 *
 *  - GET /outputs/foto.png            → WebP on-the-fly (Image Resizing / cf.image),
 *                                       cacheado en el CDN 31 días (immutable).
 *  - GET /outputs/foto.png?raw        → PNG/JPG original tal cual, como descarga
 *                                       (Content-Disposition: attachment).
 *  - GET /outputs/foto.png?format=png → igual que ?raw (sin conversión).
 *
 * El original NUNCA se modifica: R2 solo guarda el archivo original y el WebP
 * se genera en vuelo y se cachea en el edge. Si se purga la caché, se regenera.
 *
 * ⚠️ CUENTA: este worker DEBE vivir en la misma cuenta Cloudflare que el bucket
 * R2 (los bindings son por-cuenta). El frontend/D1 pueden estar en OTRA cuenta:
 * el frontend solo hace fetch a la URL pública del worker.
 *
 * Loop-proof (patrón oficial de Cloudflare): la transformación se hace con un
 * fetch al MISMO hostname con `cf: { image: ... }`; el pipeline de Image
 * Resizing re-entra al worker como subrequest con el header `Via: image-resizing`
 * y ese branch sirve el ORIGINAL desde R2. Así funciona en `*.workers.dev` SIN
 * necesidad de custom domain en el bucket ni en el worker.
 *
 * Diagnóstico: cualquier error se devuelve como texto en el cuerpo de la
 * respuesta (además de console.error → Logs del worker en el dashboard). Si el
 * webp falla por cualquier motivo (binding faltante, plan, formato), se sirve
 * el ORIGINAL con header `X-WebP-Fallback: true` — la imagen siempre carga.
 */

const IMAGE_EXT = /\.(png|jpe?g|webp|bmp)$/i;

export default {
  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (err) {
      const msg = err && err.message ? `${err.name}: ${err.message}` : String(err);
      console.error("Krea2 worker error:", err);
      return new Response(`Worker error: ${msg}`, {
        status: 500,
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "no-store",
        },
      });
    }
  },
};

async function handle(request, env) {
  const url = new URL(request.url);
  let key = decodeURIComponent(url.pathname).replace(/^\/+/, ""); // "outputs/foto.png"

  // Seguridad: path válido y sin traversal
  if (!key || key.includes("..") || !IMAGE_EXT.test(key)) {
    return new Response("Invalid path", { status: 400 });
  }

  // Binding R2 obligatorio: si falta O no es un R2 bucket real, avisamos con
  // el mensaje exacto. El caso típico: se agregó 'BUCKET' como variable de
  // texto/secret en vez de como binding tipo R2 bucket → env.BUCKET es un
  // string y no tiene .get/.head.
  if (!env.BUCKET || typeof env.BUCKET.get !== "function") {
    return new Response(
      "Worker error: 'BUCKET' no es un binding R2. En el dashboard: " +
        "Workers → krea2-img-worker → Settings → Bindings → BORRÁ lo que haya " +
        "como BUCKET (si quedó como variable de texto/secret) y agregalo como " +
        "tipo 'R2 bucket': variable name = BUCKET, bucket = krea2-bucket-img-modal → " +
        "Save and Deploy.",
      { status: 500, headers: { "Content-Type": "text/plain; charset=utf-8" } }
    );
  }

  // Subrequest del pipeline de Image Resizing → servir el ORIGINAL desde R2
  const isResizerSubrequest = /image-resizing/i.test(request.headers.get("via") || "");

  const format = url.searchParams.get("format");
  const wantRaw = url.searchParams.has("raw") || (format && format.toLowerCase() !== "webp");

  // ── 1) Original (descarga ?raw o subrequest interno del resizer) ────
  if (wantRaw || isResizerSubrequest) {
    const obj = await env.BUCKET.get(key);
    if (!obj) return new Response("Not found", { status: 404 });
    const headers = new Headers();
    obj.writeHttpMetadata(headers);
    headers.set("Access-Control-Allow-Origin", "*");
    if (wantRaw && !isResizerSubrequest) {
      headers.set("Content-Disposition", `attachment; filename="${key.split("/").pop()}"`);
      headers.set("Cache-Control", "public, max-age=86400");
    } else {
      // Respuesta interna para el resizer: cacheable (el archivo es inmutable)
      headers.set("Cache-Control", "public, max-age=31536000, immutable");
    }
    return new Response(obj.body, { headers });
  }

  // ── 2) Display: WebP on-the-fly, con fallback al original ───────────
  const head = await env.BUCKET.head(key);
  if (!head) {
    // El archivo no está en el bucket: el mensaje ayuda a distinguirlo del 500
    return new Response(
      `Not found in bucket: ${key} (¿subiste la imagen a R2 → krea2-bucket-img-modal → ${key}?)`,
      { status: 404 }
    );
  }

  const sizeMB = head.size / (1024 * 1024);
  const quality = sizeMB > 8 ? 80 : sizeMB > 2 ? 90 : 95;

  // Transformación sobre el MISMO hostname: la subrequest re-entra con
  // `Via: image-resizing` y el branch de arriba sirve el original.
  let resp;
  try {
    resp = await fetch(url.href, {
      cf: { image: { format: "webp", quality } },
    });
  } catch (err) {
    console.error("Transform fetch failed, fallback to original:", err);
    return serveOriginal(env, key);
  }
  if (!resp.ok) {
    console.error(`Transform returned ${resp.status}, fallback to original`);
    return serveOriginal(env, key);
  }

  const headers = new Headers(resp.headers);
  headers.set("Content-Type", "image/webp");
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Cache-Control", "public, max-age=31536000, immutable");
  headers.set("X-Original-Size-MB", sizeMB.toFixed(2));
  headers.set("X-WebP-Quality", String(quality));
  return new Response(resp.body, { headers });
}

/** Sirve el original tal cual (sin conversión) cuando el webp no se pudo generar. */
async function serveOriginal(env, key) {
  const obj = await env.BUCKET.get(key);
  if (!obj) return new Response("Not found", { status: 404 });
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Cache-Control", "public, max-age=86400");
  headers.set("X-WebP-Fallback", "true"); // diagnóstico: el webp NO se aplicó
  return new Response(obj.body, { headers });
}
