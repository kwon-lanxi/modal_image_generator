# Krea2 Image Worker (R2 → WebP on-the-fly)

Worker de Cloudflare que sirve las imágenes generadas:

- `https://<worker>.workers.dev/outputs/foto.png` → **WebP** automático (Image Resizing de Cloudflare, `cf.image`), cacheado 31 días.
- `...?raw` o `...?format=png` → **original** tal cual, como descarga (`Content-Disposition: attachment`).

R2 guarda **solo el original**. El WebP se genera en vuelo y lo cachea el CDN: sin duplicar almacenamiento, sin costo por servir.

---

## 🗺 La cuenta importa (R2 ≠ D1)

**Regla de Cloudflare: los bindings de R2 son por-cuenta.** El worker de imágenes y el bucket tienen que estar en la **misma** cuenta (la de R2). El frontend/D1 pueden seguir en la **otra** cuenta (la de D1) sin problema: la UI solo hace `fetch` a la URL pública del worker.

```
┌─ CUENTA R2 (esta) ──────────────────────────┐     ┌─ CUENTA D1 (la de siempre) ─────────┐
│  R2 bucket: krea2-bucket-img-modal          │     │  Cloudflare Pages (frontend)         │
│  Worker:    krea2-img-worker  (binding R2)   │     │  D1: krea2-sync (settings/generations)│
│  URL:       https://krea2-img-worker.<sub>.workers.dev │                                   │
└──────────────────────────────────────────────┘     └────────────────────────────────────┘
        ▲  el frontend carga las imágenes de acá (webp / ?raw)
```

**El webp NO necesita dominio custom**: `cf.image` funciona en `*.workers.dev` (lo confirma la doc de Cloudflare, "Transform via fetch"). Con el patrón del header `Via: image-resizing` el worker se sirve solo (sin custom domain en el bucket).

---

## Setup en la CUENTA R2 (~15 min)

### 1) Bucket
Dashboard → **R2** → bucket `krea2-bucket-img-modal`. No hace falta "Public access" (el worker lee por binding). CORS en el bucket: **no hace falta** para este flujo (las respuestas del worker ya llevan `Access-Control-Allow-Origin: *`).

### 2) Worker
```bash
cd worker-img
npx wrangler login        # elegí la CUENTA R2 (la que tiene el bucket)
npx wrangler whoami       # verificar que es la cuenta correcta
npx wrangler deploy       # crea/actualiza krea2-img-worker con el binding BUCKET
```
Si tenés varias cuentas y wrangler elige la equivocada: `npx wrangler deploy --account-id <id-de-la-cuenta-R2>`.

La URL pública queda `https://krea2-img-worker.<subdominio>.workers.dev`.

### 3) (Opcional) Dominio lindo `img.tudominio.com`
- Si la zona `tudominio.com` está en la cuenta R2 → Workers → krea2-img-worker → **Settings → Domains** → `img.tudominio.com`. Listo.
- Si la zona está en la **cuenta D1** (tu caso): dos opciones:
  - **A. Quedate con workers.dev** (recomendado ahora). Cero DNS.
  - **B. Delegá el subdominio**: en la cuenta R2 agregá la zona `img.tudominio.com` (te da nameservers), y en la cuenta D1 (zona `tudominio.com`) agregá registros **NS** apuntando `img` a esos nameservers. Después Domain al worker y listo.

---

## API token R2 para Modal (se genera en la CUENTA R2)

Dashboard → **My Profile** (avatar) → **API Tokens** → **Create Token** → plantilla **"Object Read & Write"** (o R2 scoped):
- Permissions: **Object Read + Write** (necesario para que el GPU suba).
- Resource: **Apply to** → *Specific bucket* → `krea2-bucket-img-modal` (o *All R2 buckets*).
- Create → copiá **Access Key ID** y **Secret Access Key** (no se vuelven a ver).

El **Account ID** está en la home del dashboard de la cuenta R2 (columna derecha, "Account ID").

---

## Secrets en Modal (GPU usa estos; se crean con `modal secret create r2-secrets ...`)

```bash
# Opcionales (si no los ponés, usan los defaults):
# R2_BUCKET default: "krea2-bucket-img-modal"
# R2_PUBLIC_BASE default: "https://krea2-img-worker.<sub>.workers.dev"
```

> ⚠️ **`R2_PUBLIC_BASE` es el que arma las URLs**: si usás workers.dev, ponelo con tu URL real. Si dejás el default (`https://img.tudominio.com`) y no tenés ese dominio, las URLs que devuelva el GPU no cargan.

Luego **redeployá el GPU y el manager** para que tomen los secrets.

## Migrar las imágenes viejas (una vez)

```bash
beam run krea2-sync-r2
```
Sube todo lo que haya en el volumen (`outputs/` y `archived/`) que no esté ya en R2. Opción `delete_after` si querés borrar del volumen lo que migró.

---

## Frontend

⚙ **Settings** → **r2Base** = `https://krea2-img-worker.<sub>.workers.dev` (misma base que `R2_PUBLIC_BASE`).

- Mostrar: `r2Base/outputs/<archivo>` → webp automático.
- Descargar original: `r2Base/outputs/<archivo>?raw`.

## Verificación rápida

```bash
# Debería devolver image/webp (con el PNG subido previamente)
curl -sI "https://krea2-img-worker.<sub>.workers.dev/outputs/mi_imagen.png" | grep -i content-type
# → Content-Type: image/webp
# El original:
curl -sI "https://krea2-img-worker.<sub>.workers.dev/outputs/mi_imagen.png?raw" | grep -i content-disposition
```

## 🐛 El worker da 500 — diagnóstico

El worker nuevo **devuelve el error real como texto en el cuerpo** y **cae al original** si el webp falla (header `X-WebP-Fallback: true`), así que la imagen siempre carga. Para ver el error exacto:

```bash
# OJO: con -s (cuerpo), NO -sI (head sin cuerpo)
curl -s "https://krea2-img-worker.<sub>.workers.dev/outputs/mi_imagen.png"
```

Errores más comunes y su causa:

| Respuesta | Causa | Fix |
|---|---|---|
| `Worker error: binding R2 'BUCKET' no configurado…` | El binding falta o tiene otro nombre | Workers → krea2-img-worker → **Settings → Bindings** → Add → **R2 bucket**: variable name `BUCKET` (exacto, mayúscula), bucket `krea2-bucket-img-modal` → **Save/Deploy** |
| `Not found in bucket: outputs/…` | La imagen no está en R2 | Dashboard R2 → `krea2-bucket-img-modal` → `outputs/` → Upload |
| `image/webp` + `X-WebP-Fallback: true` | El webp no se pudo generar (plan, formato…) pero el original se sirvió igual | Verificá en **Workers → krea2-img-worker → Logs** el `console.error` con el motivo; el fallback es el modo degradado intencional |
| 500 genérico SIN mensaje propio | Código viejo en el dashboard (versión anterior a este diagnóstico) | Redeployá con el código actual de `src/index.js` |

Pasos en orden: **(1)** confirmá que la imagen exista en el bucket, **(2)** confirmá el binding `BUCKET`, **(3)** redeployá el worker, **(4)** `curl -s` para leer el mensaje.
