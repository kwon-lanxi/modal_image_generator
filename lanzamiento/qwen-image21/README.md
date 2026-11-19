# Qwen Image 2.1 personal · 1.0.0

API HTTPS, manager CPU y motor L4 independientes de Krea2. Generación base y edición con hasta 10 imágenes. Conserva el volumen `qwen-image21-vol`, LoRAs, R2, metadatos en PNG, historial, idempotencia, logging por etapas y progreso en vivo. La GPU termina después de cada trabajo; no hay ventana caliente extendida.

## Playground

URL: https://kwon-lanxi--qwen-image21-personal-v1-api.us-east.modal.run/playground

Token en `.local-api-token`, separado del de Krea2. Introducirlo en la interfaz y pulsar Conectar. Elegir modelo/encoder/VAE, prompt, seed y LoRAs. Para editar: seleccionar las imágenes, subirlas con el botón CPU y después pulsar **Editar con referencias**. El orden es importante: la primera determina la geometría de salida. Quitar referencias solo las quita del formulario; conserva las copias privadas del volumen.

Modelo **base**, 25 pasos, CFG1, Euler/simple como punto de partida del workflow oficial. No se añade Turbo, Viggle ni ninguna destilación. No hay preview de dos pasos en esta versión. Resolución inicial de referencias 768; empezar con 512 si se usan muchas en la L4. El VAE propio devuelve RGB/RGBA sin el upscale2x de Krea; PNG conserva alpha.

## Precisión y memoria

Originales conservados: NREDQW21-UNLOCKED-v1-BF16 (14,23 GB), qwen3vl_8b_bf16_heretic (17,53 GB), VAE (0,676 GB). Las nuevas copias FP8 scaled usan metadata por capa de Comfy y se cargan con **weight_dtype=default**. No forzar un cast FP8 adicional sobre un checkpoint ya cuantizado.

La conversión es CPU mediante pytorch-precision-converter2.3.0. La referencia DiT es INT8 ConvRot oficial, usada para seleccionar capas FP8; no sustituye los pesos del fine-tune por el modelo oficial. La del encoder es Qwen3VL8B FP8 de Boogu-Image para conservar visión, embeddings y normas sensibles en BF16. Ambas revisiones HF están fijadas. Antes se fusionan sin pérdida los gate/proj SwiGLU del NRED original en el orden gate_then_up que utiliza Comfy. La copia se escribe por bloques de8MiB, sin cargar14GB de tensores en RAM. Después se cuantiza la copia temporal y se conserva procedencia/hash. Los archivos nunca se sobrescriben. La pestaña Modelos permite alias `--model`, conversión `--fp8-scaled`/`--fp8-mixed` y borrado de checkpoints no referenciados por perfiles. Los BF16/FP16 de origen solo se eliminan al solicitar conversión y después de validar/guardar la salida; entradas INT8/FP8 se conservan.

El cache de Qwen2.1 queda en auto/int8 para reducir memoria de edición; es una elección que debe medirse, distinta del auto/default de la plantilla oficial. Puede elegirse cpu/default, gpu u off mediante request. Int4 es experimental y puede aumentar error. SDPA es el baseline. No se fuerza torch.compile ni se añade prompt enhancer9B: aumentaría carga/costo y no es necesario para generar.

Qwen2.1 tiene7B parámetros DiT pero base usa más pasos que Krea2 Turbo. La L4 es más barata y también tiene menos capacidad de cómputo que L40S. No se promete una mejora de latencia por cambiar de modelo/GPU. Los tiempos y limitaciones de las pruebas figuran en VALIDACION.md.

## Solicitudes

```json
{
  "operation": "generate",
  "profile": "base",
  "positive_prompt": "A red apple on a wooden table, soft daylight, photograph",
  "negative_prompt": "",
  "seed": 42,
  "width": 1024,
  "height": 1024,
  "steps": 25,
  "cfg": 1.0,
  "lora": {},
  "return_base64": false
}
```

POST `/jobs`, Bearer token. Devuelve202 con request_id, call_id y status_url; consultar este último hasta terminar. También `/generate?async=true` y `/edit?async=true`. `Idempotency-Key` permite recuperar una solicitud terminada sin otra inferencia. Tras una caída dura de generación, el estado ambiguo exige revisar records/outputs antes de repetir con otra clave.

Para subir una referencia: POST `/inputs` con bytes PNG/JPEG/WebP en el body (Content-Type image/png, image/jpeg o image/webp). La respuesta es un job CPU202. Su resultado incluye `input.id`. Límite16MiB y16MP por archivo, sin animaciones; se normalizan EXIF/orientación, se reduce lado máximo2048 y se guarda PNG RGB/RGBA identificado por SHA256. No se descargan URLs arbitrarias en el motor GPU.

```json
{
  "operation": "edit",
  "profile": "base",
  "positive_prompt": "Place the object from image 1 on the background from image 2",
  "input_images": ["<sha256>.png", "<sha256>.png"],
  "reference_resolution": 512,
  "kv_cache_device": "auto",
  "kv_cache_dtype": "int8",
  "seed": 42,
  "steps": 25,
  "return_base64": false
}
```

Hasta10 referencias, redimensionadas con proporción conservada y múltiplos32. En edición ancho/alto de generación no cambian la geometría: se usa el latent que produce el nodo oficial para la primera referencia. Las imágenes y sus hashes quedan identificados en receta/metadatos; no se incrustan sus bytes base64 en la receta. El resultado no mantiene garantía de composición al cambiar seed, precisión, LoRA o resolución.

`unet_name`, `clip_name`, `vae_name`, `weight_dtype`, steps, CFG, sampler, scheduler, tiled y LoRAs se pueden cambiar por request. El snapshot se identifica por selección y versiones de pesos; no por prompt ni imágenes. Perfil base-bf16 es fallback experimental de más memoria y puede provocar offload; no es el recomendado para L4.

## Gestión CPU

Descargas con `hf://autor/repo/carpeta/archivo.safetensors` o enlaces HF resolve/blob, Civitai .com/.red y nombre personalizado. Targets lora/unet/clip/vae. `/download`, `/download/batch`, `/models/delete`, `/list_all`, `/list_models`, `/resolve`, `/preflight` y `/history` conservan el contrato de Krea. Un solo writer serializa mutaciones y generación para que no se borren pesos durante una inferencia. Reader separado atiende consultas sin GPU.

`POST /assets/prepare` prepara ambas copias FP8 y reutiliza las ya terminadas cuya procedencia coincide. `POST /models/convert-fp8` admite target=unet/clip, name y output; no sobrescribe. Informes privados en `/records/{request_id}/conversion`. La conversión streaming requiere temporales grandes en el volumen; se eliminan al terminar.

## Salidas, logs y costos

PNG + JSON con hora local y UTC, seed exacta como string, prompts, LoRAs/pesos, inputs/hashes, versiones de assets, precisión, commits, VRAM, tiempos y referencias de costos. Nombres `YYYYMMDD_HHMMSS_qwen21[_edit]_SEED_batch0_REQUESTID.png`. Copia Volume primero y R2 después, bajo `qwen21/YYYY/MM/DD/`; se conserva el bucket de los Secrets existentes. Si falla R2, se entrega la copia protegida de Volume y `/outputs/retry-upload` reintenta sin GPU. Los campos SHA/tamaño/confirmación de upload se añaden al manifiesto después de codificar; no son autorreferentes dentro del propio PNG.

`/records/{request_id}/progress` consulta Modal Dict sin arrancar GPU/Reader. Pasos completos en stdout y logs finales del Volume; publicación remota limitada a aproximadamente1Hz, siempre primer/último paso. `/records/{request_id}/logs` incluye URLs del evento output.ready. El token nunca se inyecta en URLs. `/serve_image` requiere Bearer.

L4 USD0,000222/s, CPU USD0,0000131/core/s, RAM USD0,00000222/GiB/s, verificados16/11/2026. `method_only_total` excluye arranque/restauración y `rpc_scenario_total` puede incluir cola sin GPU; ambos son referencias, no límites ni factura. La GPU solicita4 núcleos y24GiB RAM con pico48; writer CPU0,25 núcleos y0,5GiB con pico32. Uso superior/reserva se cobra según Modal. Polling, almacenamiento, egreso, entrega y créditos tienen límites/supuestos descritos en pricing.

## Despliegue

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python -X utf8 -m modal deploy modal_app.py
```

Secrets: qwen21-api (`QWEN21_API_TOKEN`), r2-secrets, huggingface-secret, civitai-apikey. `QWEN21_CORS_ORIGINS` opcional. El HTML público no contiene secretos. Modal ya configurado; URL/estado local en `.deployment.json`.

Investigación y referencias: `docs/research/qwen-image21-2026-11-16.md` en la raíz. Comfy pin258f880d y dependencias fijadas en constraints; las rutas y nodos Qwen se contrastaron con código oficial. No se modifica la aplicación Krea2.


Se añadió el reintento acotado de cuInit documentado por Modal para ciertos hosts L4 (10 intentos, pausas de0,5s). No oculta un fallo persistente de CUDA. Fuente: https://modal.com/docs/guide/troubleshooting#cuda-driver-initialization-failed-on-l4-gpu-type

El writer permite reanudar operaciones CPU seguras tras preemption (subida, bootstrap/conversión, descarga, reintentoR2). Generación/edición con estado running ambiguo siguen bloqueadas contra regeneración automática. Los temporales privados de una conversión interrumpida se reconstruyen; no se sobrescriben checkpoints terminados.


## Telegram integrado

Bot independiente @qwenimage21_bot, dentro de esta misma app Modal. Comparte el backend/API existentes y acepta fotos, álbumes y documentos de imagen para edición. Ver [TELEGRAM.md](TELEGRAM.md) para comandos, selección de referencias, límites y verificación.
