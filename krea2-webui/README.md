# Krea2 WebUI

Estudio creativo web, oscuro y responsive para usar tus APIs de Krea2 en Beam Cloud. La interfaz conserva el modo dark, ahora con jerarquía visual Bauhaus: geometría funcional, acentos primarios y navegación adaptada a móvil.
- **GPU** (`krea2-v9_1-stable.py`): `/generate`, `/variation`, `/edit`, `/depth-generate`
- **Manager** (`krea2_manager_cpu.py`): descargas, modelos, outputs, historial

Con **sync teléfono ↔ PC** vía Cloudflare D1, **subida de resultados** a imgchest / catbox / koofr, **seeds locales**, historial con **"Rehacer"**, y galería masonry con info desplegable.

## 🎨 Dirección visual

El frontend usa una dirección de estudio creativo oscuro con acentos Bauhaus: superficies carbón, rojo/azul/amarillo como señales puntuales, Outfit y una jerarquía móvil de **Crear / Modelos / Galería / Historial** con subnavegación para las herramientas de creación. La implementación sigue siendo estática y modular, sin framework ni paso de build.

**v2.3 (frontend):** pestañas separadas — **Galería** (imágenes), **Historial** (log físico del servicio: **GPU** vía `/gpu-log` y **CPU** vía `/manager-log`, con selector GPU/CPU) y las **operaciones del manager** viven en la pestaña **Models** (donde se administra el manager). Imágenes servidas desde **Cloudflare R2 + Worker** (webp on-the-fly, `?raw` para el original); el manager ya no sirve imágenes.

**v2.4 (frontend):** pestaña **🔍 Upscale** (endpoint `/upscale` standalone del GPU) y **cero acciones automáticas contra Beam** — fuera los polls de 30s/10min y el auto-refresh del log; una sola llamada por carga de página, el resto es manual.

**v2.5 (frontend):** **cachés arregladas + botón de borrado** y mejoras móviles:
- **"🗑 Borrar caché"** en ⚙ Settings y en ▤ Models: limpia modelos/galería/formularios locales + Cache API de imágenes (los settings no se tocan) y recarga.
- **Formularios versionados** (`_v`): los guardados de versiones viejas de la app se descartan en vez de restaurarse en el campo equivocado (el bug del celular de "la seed aparece en el CFG").
- **Snapshot D1 con TTL**: si el snapshot de `/list_all` en D1 tiene más de 10 min se ignora al hidratar → ya no reviven LoRAs borrados en otro dispositivo.
- **Force-refresh tras mutaciones**: borrar/renombrar/mover/descargar un modelo re-escanea el manager de inmediato (antes la caché local de 300s escondía el cambio hasta 5 min).
- **Slider duplicado arreglado**: los sliders (`mu`, `denoise`, `strength`…) tenían el MISMO id en range y number → al restaurar el número quedaba con el valor viejo del browser. Ahora el id vive en el input number (lo que tipeás) y el slider lo sigue.
- **Móvil**: tabbar con items compactos y scrolleables, cards de galería con badges que no desbordan, botones de card más tocables, masonry con menos gap.

---

## 🚀 Setup (Cloudflare Pages + D1)

```bash
# 1) Instalá wrangler (una vez)
npm i -g wrangler
npx wrangler login

# 2) Creá la base de datos de sync
npx wrangler d1 create krea2-sync
#    → copiá el database_id que te devuelve

# 3) Pegalo en wrangler.toml (database_id = "...")

# 4) Aplicá el schema
npx wrangler d1 execute krea2-sync --remote --file=./schema.sql

# 5) Deploy (desde la carpeta krea2-webui)
npx wrangler pages deploy public
```

## 🧪 Dev local

```bash
# Con funciones (proxy de catbox/koofr + sync) — necesita wrangler y D1 local
npx wrangler pages dev public

# Solo estático (sin proxy ni sync; la UI igual anda para probar diseño)
python -m http.server 8123 -d public
# → http://localhost:8123
```

## ⚙️ Primer uso

1. Abrí la UI y entrá a **⚙ Settings**.
2. Cargá la **URL del servicio GPU** y la **URL del manager** (tus deploys de Beam).
3. Si el servicio lo requiere, ingresá la **API key de Beam** (misma para ambas, va como `Authorization: Bearer`).
4. Ingresá las credenciales de los hosts que quieras usar: `CATBOX_USER_HASH`, `IMGCHEST_API_KEY`, `KOOFR_EMAIL` y `KOOFR_APP_PASSWORD`. No se distribuyen credenciales por defecto.
5. Para sincronizar con D1, elegí una **Sync Key larga y privada**; no uses una clave conocida ni la compartas públicamente.
6. Tocá **"Probar conexión"** y verificá el estado de los servicios en el header.

> **Seguridad:** si usabas una versión anterior, las credenciales que habían estado incrustadas en el frontend se retiraron. Rotá esas claves antes de volver a ingresarlas; todo secreto enviado a un navegador es visible por el usuario de ese dispositivo. La Sync Key funciona como credencial de acceso a los datos D1, no como un identificador público.

## 🔁 Cómo funciona el sync (3 modos)

El historial y la configuración se guardan **siempre** en este dispositivo (localStorage).
Además, el chip del header muestra el modo de sync activo:

| Modo | Cuándo | Qué sincroniza |
|---|---|---|
| 💾 **local** | Sin URLs configuradas | Solo este dispositivo (persiste entre recargas) |
| ▤ **volume** | `managerUrl` configurada (no hace falta D1) | **Galería compartida**: las imágenes del volume de Beam se ven en teléfono Y PC (la seed se saca del filename `krea2_<seed>_batch0.png`) |
| ☁ **D1** | Sync Key puesta + Functions/D1 deployados en Cloudflare | Lo del volume **+** payloads completos (prompts, params, LoRAs) para "Rehacer" |

- **El volume de Beam es el sync real entre tus devices**: si configurás el mismo `managerUrl` en el teléfono y la PC, los dos ven la misma galería de generaciones sin desplegar nada.
- La **Sync Key de D1** es una capa extra que además guarda prompts/params (para rehacer exacto). Configurarla requiere el deploy de Cloudflare Pages + D1 (pasos abajo).

## 🪟 Proxy de Beam (`/api/beam`)

**Importante:** tus APIs de Beam responden con `Access-Control-Allow-Origin: *, *` (header **duplicado**), que los navegadores rechazan → **todo fetch directo queda bloqueado** (CORS policy). Por eso la UI enruta **todas** las llamadas al GPU y al manager a través de la Pages Function `functions/api/beam.js`, que reenvía server-side y devuelve la respuesta con CORS limpio.

- Se detecta automáticamente en el arranque (`GET /api/beam/ping`). En dev con `python -m http.server` no existe y se usa fetch directo (con las limitaciones de CORS de siempre).
- Imágenes del volume también se sirven por el proxy (`/api/beam?url=...`) para que carguen en galerías y previews.
- Uploads multipart al manager van por `functions/api/beam/upload.js`.

## 🔌 Cómo se suben las imágenes a los hosts

| Host | Método | Notas |
|---|---|---|
| imgchest | Directo del browser (CORS OK) | API key va en el header |
| catbox | Directo del browser (`mode: no-cors`) | catbox **bloquea las IPs de Cloudflare (520)** y no manda CORS → la subida llega a tu cuenta pero no se puede leer la URL; se marca "subido a tu cuenta" con link a catbox.moe |
| koofr | Proxy `functions/api/upload/koofr.js` (WebDAV PUT) | sube a `KOOFR_TARGET_FOLDER`; es tu backup, no URL pública |

- Botones **⬆ imgchest / catbox / koofr** en cada resultado.
- Toggle **"auto-subir al generar"** en Settings (usa el host default).
- Las URLs quedan guardadas en el historial sincronizado; la galería usa imgchest/koofr para el link directo.
- Las imágenes de las galerías se **cachean en el navegador** (Cache API) y se ordenan por la fecha del nombre del archivo (`20260802_151730_…`).

## 📁 Estructura

```
krea2-webui/
├── public/            → UI (Tailwind CDN + JS vanilla, sin build)
│   ├── index.html
│   ├── css/styles.css
│   └── js/  config, state, api, ui, components, tabs, main
├── worker-img/        → Worker de imágenes R2 (webp on-the-fly + ?raw original)
│   ├── wrangler.toml
│   └── src/index.js
├── functions/         → Pages Functions
│   ├── api/beam.js         → proxy GPU+manager (CORS roto de Beam)
│   ├── api/beam/upload.js  → proxy multipart (outputs/upload)
│   ├── api/upload/koofr.js → proxy WebDAV (catbox ya no va por servidor: bloqueado 520)
│   └── api/sync/  settings.js, generations.js, generations/delete.js
├── schema.sql         → tablas D1 (settings, generations)
└── wrangler.toml
```

## 🖼 Imágenes desde R2 (v9.4)

- El **GPU** sube cada imagen a R2 (`outputs/<archivo>`) y devuelve `https://img.tudominio.com/outputs/<archivo>`.
- El **Worker** (`worker-img/`) sirve **WebP on-the-fly** por defecto (calidad según tamaño: 95/90/80, cacheada 31 días) y el **original** con `?raw` (Content-Disposition: attachment).
- El **manager ya no sirve imágenes** (`/serve_image`, `/outputs/base64`, `/public/output` eliminados).
- El frontend construye las URLs desde `beam_filename` con el setting **r2Base** (default `https://img.tudominio.com`).
- El **Historial** es el viewer del **log físico del servicio** — **GPU** (`/gpu-log`) y **CPU** (`/manager-log`): selector GPU/CPU, tail, grep, raw. Refresco **manual** (⟳): no hay auto-poll.
- Las **operaciones del manager** (historial de downloads/delete/move…, stats, reconstruir índice, JSON) están en la pestaña **Models**.
- Pestaña **🔍 Upscale**: llama al `/upscale` standalone del GPU (imagen + modelo ESRGAN/SeedVR2, escala, formato, tiled). Simple a propósito: el backend va a cambiar pronto. El resultado queda en `outputs/` + R2 → aparece en la Galería.

**v2.4 (anti-costo):** nada se repite en background. Eliminados el auto-check de salud cada 30s y el refresh de `/list_all` cada 10 min (cada poll despierta/mantiene caliente la instancia de Beam y factura segundos). Ahora hay **una sola llamada por carga de página** (con la caché de 60s del manager) y todo lo demás es manual: chips, ⟳ de logs, tabs. Los únicos `setInterval` que quedan son timers locales de UI (elapsed del spinner), sin red.


- **CORS de Beam**: el server duplica `Access-Control-Allow-Origin`, así que la UI usa el proxy `/api/beam` en deploy. No toques los `.py` para esto: ya está resuelto del lado de Pages.
- **D1**: las Functions de sync usan el binding `krea2_sync` (ya corregido). Si D1 no está configurado devuelven `{ ok:false }` y la UI cae al modo volume/local en vez de tirar 500s.
- **Sin SSE**: el progreso es un spinner + timer (el `POST` bloquea hasta terminar).
- **Seeds**: siempre se genera una seed local (crypto) y se manda explícita (nunca `0`), porque el random del server está buggeado.
- **Edit** inyecta `krea2_identity_edit_v1_1` a 1.0 automáticamente (lo ves como chip fijo).
- **Modelos**: el tab Models y los selects de modelos se cargan desde `/list_all` (un solo llamado, formato real del manager).
- **Resultados**: cada generación muestra a la derecha del preview un cuadro con tamaño (`W × H`, bytes) y todos los metadatos (seed, sampler, steps, timing, costo, GPU/VRAM).
- Las credenciales se guardan en `localStorage` + en tu D1 bajo tu Sync Key. Es tu tool privada: no compartas la Sync Key.
