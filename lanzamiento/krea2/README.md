# Krea2 personal · 2.1.1

App Modal con API HTTPS, manager CPU, worker L40S y playground HTML. Telegram está integrado en esta misma App; conserva la API HTTPS. Ver TELEGRAM.md.

## Probar el playground

Abrir `/playground` en la URL publicada por Modal. El HTML también está en `krea_service/playground.html`; es autónomo, sin CDNs ni dependencias JavaScript externas. La API sirve el HTML públicamente, pero todas las operaciones y los archivos del Volume requieren Bearer token.

1. Introducir URL de API y token; pulsar **Conectar y cargar**. El token permanece en memoria del navegador.
2. Elegir preset inicial y, si querés, cambiar modelo, encoder, VAE, precisión y tipo Turbo/RAW.
3. Añadir LoRAs del catálogo y pesos. Usar **Comprobar archivos en CPU** antes de generar.
4. Pulsar **Generar final** o **Preview · 2 pasos**. El formulario muestra imagen, fecha, seed, dimensiones, tiempos, USD estimado, respuesta completa y logs.
5. Descargar el original para conservar sus metadatos. Una transformación WebP o una recompresión externa puede eliminarlos.

Hay secciones de descargas/gestión e historial, request JSON editable, recuperación del último trabajo y reintento de R2. **Dejar de consultar** detiene el polling del navegador; no cancela el trabajo del servidor.

API desplegada: https://kwon-lanxi--krea2-personal-v2-api.us-east.modal.run/playground

El token local, cuando se prepara automáticamente el Secret, se guarda en `.local-api-token` (ignorado por git). No se incrusta en HTML ni se envía a proveedores de modelos. El archivo `.deployment.json`, si existe, conserva la URL y el estado del despliegue, sin credenciales.

## Descargas sencillas

`POST /download`, autenticado, devuelve 202 y un trabajo CPU. No hace falta escribir `source`, repo, filename y versión por separado.

```json
{
  "url": "hf://AcademiaSD/TAE-Krea-2/TAE_Krea2_AcademiaSD.safetensors",
  "name": "mi-tae",
  "target": "tae"
}
```

También acepta:

```json
{
  "url": "https://huggingface.co/AcademiaSD/TAE-Krea-2/resolve/main/TAE_Krea2_AcademiaSD.safetensors",
  "name": "mi-tae",
  "target": "tae"
}
```

Civitai .com y .red: página de modelo, página con `?modelVersionId=...` o enlace directo `/api/download/models/ID`. Se conserva el host .red cuando corresponde.

```json
{
  "url": "https://civitai.red/models/123/mi-modelo?modelVersionId=456",
  "name": "mi-personaje",
  "target": "lora"
}
```

`name` es opcional. Con nombre y versión/direct download se evita la consulta adicional de metadatos Civitai. Sin versión se resuelve una versión publicada y un archivo safetensors; con datos de archivo se conserva SHA256 y trigger words. Los tokens en query strings no se guardan ni reenvían: se usa el Secret del servidor. Las restricciones de acceso/licencia de cada proveedor siguen aplicando.

Opcionales: `subfolder`, `volume` (`main` o `loras`; este último solo para LoRAs), `revision` para HF y `sha256`. En `hf://autor/repo/carpeta/archivo?revision=COMMIT` se admite revisión explícita. Se conservan también los campos avanzados `repo_id`/`filename` y `model_version_id`.

Target opcional/`auto`: detecta carpetas HF y TAE/VAE reconocibles; si no hay información asume LoRA. También se puede elegir `lora`, `unet`, `clip`, `vae` o `tae` explícitamente. Escribe un temporal, verifica tamaño/cabecera/hash cuando existe y publica el archivo terminado. No se sobrescriben modelos: dar otro nombre o eliminar explícitamente el archivo. Las descargas interrumpidas se reinician; esta versión no implementa resume.

## Generación: presets más selección por request

`POST /jobs` (recomendado) o `/generate?async=true`:

```json
{
  "operation": "generate",
  "profile": "turbo",
  "model_type": "turbo",
  "unet_name": "HomoFidelis_Krea2_Turbo_BF16_v1",
  "clip_name": "qwen_3vl_4b",
  "vae_name": "Wan2.1_VAE_upscale2x_imageonly_real_v1",
  "weight_dtype": "fp8_e4m3fn_fast",
  "positive_prompt": "TRIGGER, portrait, soft natural light",
  "negative_prompt": "",
  "seed": 42,
  "steps": 8,
  "cfg": 1.0,
  "width": 1024,
  "height": 1024,
  "lora": {"krea2-loras-renew/personaje/checkpoint.safetensors": 0.8},
  "sampler_name": "euler",
  "scheduler": "simple",
  "return_base64": false
}
```

En `/generate` y `/preview` omitir `operation`; sin `?async=true` esperan el resultado HTTP. Los nombres se resuelven por ID relativo, filename o stem exactos. Una coincidencia ambigua exige un ID completo.

`profile` aporta defaults (`turbo`, `turbo-scaled`, `raw`). Los campos `unet_name`, `clip_name`, `vae_name`, `model_type`, `weight_dtype` y `mu` permiten cambiar selección. RAW usa shift dinámico y no admite `mu` fijo. Si no se fuerza precisión y el nombre indica cuantización, se conserva `default`. Para formatos no identificables por nombre, seleccionar `weight_dtype` explícitamente.

La selección efectiva y versiones de archivos identifican el snapshot: no se restaura Turbo para después cargar otro modelo en cada llamada. Pasos/CFG/prompts/LoRAs se aplican por trabajo. Cambiar el contenido de un archivo crea otra identidad por ruta/tamaño/mtime; no preservar artificialmente timestamps al reemplazar pesos. Los archivos descargados/convertidos conservan procedencia SHA256.

Turbo final: ocho pasos y CFG1 por defecto. RAW: 52 y CFG3.5, como referencia inicial. Deben contrastarse con el fine-tune real. Preview Turbo fuerza dos pasos, CFG1, Euler/simple y añade su adaptador de destilación; usa TAE. La compatibilidad visual de destilación con cada fine-tune/LoRA debe comprobarse. Una seed idéntica no garantiza misma composición al cambiar pasos/modelo/resolución.

El VAE 2x real del Volume se usa por defecto. VAE-Utils se mantiene para auto pixel-shuffle, tiled y decode normal. Las dimensiones del request son de muestreo: 1024x1024 puede devolver 2048x2048 con ese VAE. Máximo 2 MP latentes, sin batches en esta versión. El playground conserva seeds de hasta 63 bits: las grandes viajan como string decimal y la API las valida como entero. La respuesta agrega `seed_string` para evitar redondeo de JavaScript. Al mostrar una preview, su seed efectiva se copia al formulario para el final.

En la prueba real a 512 latentes, preview y final ejecutaron su método GPU en 10,3 y 8,2 segundos respectivamente, pero las llamadas completas incluyeron unos 90–114 segundos de cola/arranque/restauración. Son dos muestras y llevan LoRAs distintas. Hacer preview y luego final dispara dos ciclos de GPU: para tu uso personal, empezar por **final** y usar preview cuando ayude a explorar prompts.

## Respuesta y metadatos en originales

La respuesta incluye `images`, seed, `created_at` (ISO con offset), `created_at_utc`, timezone, `timing`, `pricing`, metadatos completos, advertencias y tiempos de entrega separados. `metadata` contiene receta efectiva, prompt/negativo, LoRAs aplicadas y pesos/versiones, assets, precisión, commits, hardware y memoria. El comienzo/fin de generación se registra en UTC; hora local predeterminada `America/Argentina/Buenos_Aires`.

PNG: JSON comprimido `krea2`, texto legible `parameters` y `Creation Time`. JPEG preview: EXIF DateTimeOriginal/offset, UserComment y segmentos numerados KREA2META para preservar recetas Unicode incluso mayores de 64 KiB. `krea_service.image_metadata.extract(bytes)` recupera el JSON completo de cualquiera de los dos formatos.

La misma receta y tiempos de generación se usan en imagen, respuesta, manifiesto Volume y R2. Hash/tamaño del archivo, confirmación R2 y tiempos posteriores de entrega se añaden al manifiesto/respuesta: no pueden estar todos dentro del propio archivo antes de existir o subirse. La imagen no incorpora su propio SHA256 autorreferente.

`timing.sampling` mide sampler; `gpu_method_total` mide LoRAs+encoder+sample+decode+transferencia; `gpu_call` observa arranque/restauración/transporte desde CPU; `render_total_observed` incluye preparación CPU. Son intervalos anidados: no se suman para inventar un total. La compresión/upload se registra aparte para que los tiempos embebidos no cambien después de guardar.

## Costos con tarifas verificadas

Fuente: https://modal.com/pricing · consulta 2026-11-15. L40S USD 0.000542/s; CPU USD 0.0000131/núcleo físico/s; RAM USD 0.00000222/GiB/s. `GET /pricing` y `pricing.rates` exponen tarifas y fecha.

**`pricing.total` es una estimación, no el importe facturado.** Incluye un desglose de GPU, CPU/RAM del host y coordinador hasta capturar metadatos, usando duración RPC observada y reservas/muestra RSS. Expone supuestos, intervalos, exclusiones, `is_estimate=true` y `billing_verified=false`. No se puede obtener la factura exacta de esta imagen simplemente cronometrando Python: RPC puede incluir esperas sin GPU asignada, CPU/RAM varían y hay almacenamiento, red, teardown y créditos. La API de billing disponible informa agregados de workspace, no este costo exacto en el instante de crear el PNG. El reporte/dashboard Modal determina el cobro real.

La GPU solicita 32 GiB y permite picos de hasta 64 GiB de RAM; no reserva permanentemente 64. El uso superior también se cobra. Sin precios heredados de Beam ni costo ficticio presentado como factura.

## Separación CPU/GPU y persistencia

- API CPU: autenticación, HTTP, HTML, validación y polling.
- CPUReader: catálogo, preflight, logs, historial e imágenes; mounts solo lectura y recarga antes de cada operación.
- CPUWriter: una operación serializada, descargas/eliminaciones/conversión, receta, compresión, metadatos, Volume commit y R2.
- GPU: cargar Krea2, aplicar LoRAs, texto, sampling, decode y transferir píxeles. Sin R2, historial ni rutas de catálogo. `single_use_containers=True`: termina después de cada trabajo.

Las consultas CPU no esperan el trabajo GPU. Las mutaciones del writer no pueden borrar los pesos mientras genera. Usar una sola App escritora sobre estos volúmenes y drenar trabajos antes de redesplegar. La serialización del writer evita GPUs simultáneas desde esta API aunque existan varias combinaciones de parámetros/snapshots.

Se conserva primero imagen+JSON en `krea2-volume/outputs`, hace commit y después intenta R2. R2 usa `krea2/YYYY/MM/DD/archivo` y `archivo.json`. Si falla, hay advertencia y URL protegida `/serve_image`; reintentar `/outputs/retry-upload` sin GPU. `R2_PUBLIC_BASE` debe ser el origen, sin `/outputs` añadido. Los originales permanecen; las transformaciones de tu worker de imágenes pueden no conservar metadatos.

Jobs/logs en `_release/jobs/{request_id}`. Logs JSON por etapas y request ID, progreso y excepciones con credenciales/URLs firmadas redactadas. El prompt no se imprime en logs HTTP; sí queda en la receta privada solicitada. GPU devuelve sus eventos para copia CPU al terminar; ante fallo duro consultar también stdout de Modal. Los commits de progreso pueden retrasarse durante una descarga larga.

## Endpoints

Todos, salvo el HTML estático, requieren Bearer token.

| Ruta | Uso |
|---|---|
| `GET /health`, `/healthz` | Salud CPU; no probe GPU |
| `GET /profiles`, `/pricing` | Presets y tarifas |
| `GET /preflight?profile=turbo`, `POST /preflight` | Validación CPU, incluido modelo custom por request |
| `POST /jobs`, `/generate`, `/preview` | Generar o preview |
| `GET /jobs/{call_id}` | 202 pendiente o resultado temporal Modal |
| `GET /records/{request_id}` | Estado persistente |
| `GET /records/{request_id}/logs`, `/conversion` | Logs/informe privado |
| `GET /list_models?type=lora`, `/list_all`, `/resolve` | Archivos y resolución |
| `POST /rescan-loras` | Escaneo CPU |
| `POST /download`, `/download/batch` | Enlace simple o `{"items":[...]}` |
| `POST /models/delete` | `{"paths":["loras/archivo.safetensors"]}` |
| `POST /assets/prepare`, `/models/convert-fp8` | Preparación CPU |
| `GET /outputs`, `/history`, `/serve_image?filename=...` | Resultados y originales |
| `POST /outputs/retry-upload` | `{"filename":"...png"}` sin inferencia |

`call_id` es de Modal para polling; `request_id` identifica archivos/receta persistentes. Resultados de llamadas pueden expirar, pero las copias Volume no dependen de ese TTL. `Idempotency-Key` permite recuperar/reintentar una solicitud sin duplicar GPU; usar la misma clave para la misma operación/payload. Un trabajo ambiguo tras caída dura no se regenera automáticamente: revisar estado y outputs.

## Instalación/despliegue y cliente

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python -X utf8 -m modal deploy modal_app.py
```

Secrets: `krea2-api` (`KREA2_API_TOKEN`, opcional `KREA2_CORS_ORIGINS`), `r2-secrets` (variables R2 anteriores), `huggingface-secret` (`HF_TOKEN`) y `civitai-apikey` (`CIVITAI_API_KEY`, opcional `CIVITAI_RED_API_KEY`). Se reutilizan los Secrets y Volumes anteriores; no se cambian sus credenciales. El token nuevo se prepara por separado. El HTML servido en la propia API no necesita CORS; un HTML en otro origen requiere configurar sus orígenes permitidos.

```powershell
python client.py health
python client.py loras
python client.py preflight
python client.py generate --prompt 'a portrait in soft natural light' --seed 42
python client.py prepare-assets
python client.py preview --prompt 'a portrait in soft natural light' --seed 42
```

El cliente usa `KREA2_URL` y `KREA2_API_TOKEN` del entorno o archivos locales de despliegue, si existen. Opcionales `--unet`, `--clip`, `--vae`, `--model-type`, `--precision` y `--lora ID=0.8`.

El writer CPU reserva 2 GiB y permite picos hasta 64 GiB para poder convertir pesos BF16 grandes; ese pico solo se utiliza cuando la operación lo necesita.

La conversión CPU usa `pytorch-precision-converter==2.3.0`, referencia oficial fijada, archivo nuevo y reporte. El perfil `turbo-scaled` sirve para el resultado; no equivale a elegir `fp8_e4m3fn_fast` sobre BF16. El HomoFidelis FP8 antiguo del Volume contiene escalas `.weight.scale` que este Comfy no interpreta: la prueba real produjo una salida defectuosa. Preflight/generación lo rechazan antes de iniciar GPU. Desde la pestaña Modelos o `/convertir_modelo --model alias --fp8-scaled` se puede crear una copia compatible; `--fp8-mixed` deja en BF16 las capas con más de5% de error. La salida no sobrescribe el origen. Solo al convertir un modelo BF16/FP16 se elimina el origen, y únicamente después de validar/guardar la salida y actualizar perfiles. INT8/FP8 de origen se conserva. La administración no permite borrar modelos referenciados por un perfil.

Los commits Comfy/VAE-Utils y paquetes principales están fijados; el build GPU guarda su inventario resuelto en `/opt/build-packages.txt`. Baseline SDPA y compilador automático desactivado; Sage/TeaCache/compile requieren mediciones posteriores. Las pruebas reales y sus límites están en VALIDACION.md.

## Validación y alcance

Pruebas CPU y revisión de navegador descritas en `VALIDACION.md`. El fixture `tests/playground_server.py` solo genera una imagen sintética marcada QA, sin Modal/R2/GPU; no usarlo como prueba de inferencia. Se puede abrir el HTML real con la API desplegada para probar generación.

No hay edición, ControlNet, referencias de imagen, ESRGAN, ZIP, SSE ni gestión recursiva de carpetas. Los datos antiguos no se borran ni migran silenciosamente. La web anterior necesita adaptar sus payloads; el playground incluido ya usa el contrato actual. Qwen Image 2.1 y MiniMax H3 quedan para otro motor validado.

Los originales y propuestas anteriores están en `extras/pre-release-2026-11-15` en la raíz; las investigaciones en `docs/research`.


## Actualización 2.1.1: archivos, progreso y lectura de costos

Los nuevos originales usan hora local de creación, seed, batch0 e ID completo: `20261116_104321_krea2_42_batch0_<request_id>.png`; previews añaden `krea2_preview`. El JSON sidecar comparte stem. No se renombran resultados anteriores.

`GET /records/{request_id}/progress` consulta el último evento desde un Modal Dict, sin iniciar GPU ni CPUReader/Volume. El playground lo consulta durante el polling y muestra etapa/paso/porcentaje. Todos los pasos permanecen en stdout y en el log GPU final; publicación remota limitada a aproximadamente una por segundo (siempre primer y último paso), por lo que una UI puede saltar pasos breves. El fallo del canal de progreso no aborta la imagen. `gpu.ready` indica que el método ya está listo para generar; el estado anterior no permite distinguir por sí solo cola de restauración. La inicialización del primer snapshot sucede antes de ese evento. `output.ready` agrega URL de imagen, copia protegida y JSON. El endpoint de logs convierte las rutas protegidas a URL completa; siguen requiriendo Bearer.

La estimación agrega `method_only_total` (método observado y writer) y `rpc_scenario_total` (todo el intervalo RPC y writer). `total` se conserva por compatibilidad y equivale al escenario RPC: `total_basis=rpc_wall_scenario_not_billed_time`. La UI muestra ambas referencias, sin presentarlas como factura ni como límites garantizados. La primera excluye arranque/restauración y la segunda puede contar cola/transporte sin GPU. Los metadatos históricos mantienen su estimación original.

Modal cobra los recursos usados o solicitados, con CPU y RAM por max(reserva, uso). La cola antes de asignar una GPU no equivale a GPU ocupada; el CPUWriter ya iniciado sí sigue reservando CPU/RAM mientras espera. Una GPU asignada durante carga, warmup/restauración e inferencia puede tener tiempo cobrable aunque no haya comenzado el sampler. Después del retorno/teardown de este worker single-use ya no queda GPU ociosa reservada. El dashboard de uso agrega varias invocaciones y puede actualizarse con retraso.

Fuentes consultadas el 16/11/2026: [tarifas](https://modal.com/pricing), [billing](https://modal.com/docs/guide/billing), [recursos](https://modal.com/docs/guide/resources), [cold starts](https://modal.com/docs/guide/cold-start), [snapshots](https://modal.com/docs/guide/memory-snapshots).


Integración Telegram: ver [TELEGRAM.md](TELEGRAM.md). Sin cron ni app proxy. La reserva mínima actual de CPUWriter es0,25CPU y512MiB; el límite de RAM para tareas grandes se conserva. Las versiones anteriores del documento que mencionan reserva de2GiB quedan reemplazadas por este ajuste. Secrets adicionales tg-token-image y krea2-telegram-config.
