# Bot Qwen Image 2.1

Bot @qwenimage21_bot integrado en qwen-image21-personal-v1. API HTTPS y GPU L4 existentes. No hay app proxy, cron, monitor separado ni fallback a otra GPU. Estado/alias en qwen21-telegram-state; imágenes de referencia y resultados en qwen-image21-vol; salidas mantienen PNG con metadatos, sidecars y R2 mediante qwen_service.

## Uso

Desde texto:

```text
/generar retrato con luz de ventana !mi_alias:0.7 --size 3:4
```

Edición:

1. Enviar hasta 10 fotos, un álbum o archivos PNG/JPEG/WebP. Como documento, Telegram conserva el archivo original; las fotos pueden estar comprimidas por Telegram.
2. Esperar a que el contador muestre la selección completa. El orden se determina por message_id, no por el orden de llegada del webhook. Las referencias repetidas se deduplican por file_unique_id.
3. Enviar la instrucción, por ejemplo `/generar cambia el fondo de la imagen 1 usando la imagen 2 !mi_alias:0.6 --ref 768`. Con referencias activas, el modo pasa a edit.
4. También puede enviarse un álbum con descripción y después `/editar`: usa esa descripción, sin generar automáticamente al llegar la primera imagen.
5. Revisar el número de referencias y LoRAs/pesos de la confirmación. Confirmar Generar. La selección queda congelada en ese trabajo.
6. `/limpiar` quita la selección para volver a generar desde texto; no elimina imágenes del volumen ni altera trabajos confirmados. `/referencias` muestra la selección actual. Las referencias permanecen activas hasta limpiar.

La recepción de imágenes guarda file_id y metadatos, no asigna GPU. Solo al confirmar, CPUWriter descarga mediante getFile, limita el stream a16MiB, valida PNG/JPEG/WebP y guarda cada referencia con el pipeline inmutable existente de /inputs (EXIF, tamaño, hash). Después invoca QwenGPU. Los IDs guardados quedan en la receta y se reutilizan al repetir/variar; no dependen de que el enlace temporal de Telegram siga vigente. El token nunca viaja a GPU ni al navegador. Fallos de preparación no solicitan GPU. Los documentos animados y formatos no admitidos se rechazan.

## Tamaños y edición nativa

--size usa las mismas siete proporciones, alineadas al múltiplo32 más próximo (mitades hacia arriba), para respetar el contrato existente de Qwen2.1:

| proporción | muestreo desde texto |
| --- | --- |
| 1:1 | 1344×1344 |
| 16:9 | 1664×928 |
| 9:16 | 928×1664 |
| 4:3 | 1472×1120 |
| 3:4 | 1120×1472 |
| 3:2 | 1600×1056 |
| 2:3 | 1056×1600 |

-w/-h mantienen prioridad y requieren múltiplos32. En edición, el nodo TextEncodeQwenImage21 devuelve un latent según la primera referencia reescalada, y ese latent se conserva para evitar desplazar la edición. Por tanto --size/-w/-h son para generación desde texto; no fuerzan la salida de una edición. --ref controla la resolución nominal de referencias, por defecto768 y múltiplos32 entre256 y1408. El límite existente es10imágenes y6MP nominales; el presupuesto se comprueba antes de confirmar y antes de inferencia. Más imágenes aumentan memoria/tiempo; no se ha validado con GPU esta nueva integración multimagen.

## Funciones heredadas de Krea2

LoRAs con !alias:peso (@ admitido por compatibilidad), Mini App móvil con búsqueda/paginación/selección, cache compartido del catálogo300s e invalidación por cambios del manager, galería, descarga HF/Civitai, confirmación vinculada al prompt sin repetirlo, un mensaje temporal de progreso que se elimina al entregar, originales PNG con metadatos, repetir/nueva variante/reenvío con file_id, HTTP Session reutilizada y métricas telegram.http / telegram.update.handled. Las referencias tienen un único aviso reutilizado, no un mensaje por archivo.

Sin --turbo no se selecciona automáticamente ninguna LoRA y se mantiene el modelo base de25pasos. Con --turbo se aplica el modo oficial Viggle v0.3 descrito debajo.

## Configuración y seguridad

Secret tg-token-image: TELEGRAM_QWEN21_BOT_TOKEN. Secret separado qwen21-telegram-config: TELEGRAM_WEBHOOK_SECRET. Propietario7112930476, solo chat privado. El webhook valida secreto, deduplica update_id y persiste antes de despachar. Mini App verifica initData, HMAC, edad y propietario. Las imágenes no provocan generación automática; cada trabajo requiere confirmación y los envíos ambiguos no se reintentan a ciegas.

URL Mini App: https://kwon-lanxi--qwen-image21-personal-v1-telegram-web.us-east.modal.run/mini . telegram_configure registra webhook, comandos nativos y botón Biblioteca. El botón principal del perfil se configura aparte en BotFather, como en Krea2.

## Modelos, alias y conversión FP8

`/modelos` abre la pestaña Modelos, separada de LoRAs y Galería. Se puede asignar alias y usarlo en generación con `--model`; el ID exacto del modelo también es válido. Ejemplos:

```text
/generar retrato --model mi_alias --profile base
/convertir_modelo --model NREDQW21-UNLOCKED-v1-BF16 --fp8-scaled
/convertir_modelo --model mi_alias --fp8-mixed
```

La conversión corre en CPU con `pytorch-precision-converter==2.3.0`, usando una referencia oficial fija de Qwen Image 2.1 para diffusion models. FP8 mixto conserva en BF16 las capas con más de 5% de error y mantiene la estructura escalada por capa que ComfyUI espera. Si se convierte un checkpoint INT8/FP8 Comfy reconocido, se dequantiza temporalmente a BF16 y el origen cuantizado no se borra. El original BF16/FP16 se elimina únicamente tras validar y guardar el resultado; además se actualizan los perfiles que lo usaban y se migra su alias. El temporal y el informe viven en `_release/jobs` y se limpian al finalizar.

La pestaña permite borrar checkpoints que no estén referenciados por un perfil. Las descargas Civitai/Hugging Face pueden convertirse cuando los pesos y nombres de capas sean compatibles con la arquitectura Qwen seleccionada; conversiones incompatibles fallan y conservan el original.

## Verificación

`python -m pytest lanzamiento/krea2/tests lanzamiento/qwen-image21/tests -q`.

`python -X utf8 lanzamiento/qwen-image21/scripts/check_image_imports.py` verifica los artefactos de las cuatro imágenes en CPU, sin reservar L4. Las pruebas de entrada usan backend/validador/storage reales y transporte Telegram simulado, para cubrir la frontera antes de GPU. La UI se revisa en móvil, horizontal y texto200%.

Fuentes oficiales consultadas: https://core.telegram.org/bots/api#getfile ; https://core.telegram.org/bots/api#message ; https://core.telegram.org/bots/api#file ; https://modal.com/docs/guide/webhooks ; https://github.com/Comfy-Org/ComfyUI/blob/258f880d0c026bfc22acab679858ab2e2c70573d/comfy_extras/nodes_qwen.py ; https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo/blob/main/README.md .

Validación final del despliegue:142pruebas combinadas (54Qwen y88Krea/Telegram), UI móvil revisada y cuatro imágenes comprobadas en Sandboxes CPU. CPUWriter arrancó y guardó una entrada PNG de64×64 por el backend real, sin invocar GPU. Rutas Mini App con firma real:200 en catálogo(11LoRAs), alias, galería y HTML;401 sin firma;403 para webhook falso. getWebhookInfo confirma URL nueva, cero pendientes y sin error; comandos y menú nativos registrados. No se envió una edición/generación real a L4 ni imágenes de prueba a Telegram. La revisión GPU anterior sigue siendo histórica; la integración nueva de edición multimagen requiere una prueba real.

## Turbo automático

```text
/generar retrato con luz natural --turbo --size 3:4
/generar cambia el fondo !mi_estilo:0.6 --turbo
```

El flag no recibe valor. Selecciona loras/Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r256.safetensors a1.0 y fija6pasos, CFG1, Euler, prompt negativo vacío y scheduler viggle_turbo; prevalece sobre --steps/--cfg. Las otras LoRAs se conservan (Turbo ocupa una de8plazas). Otra versión de Viggle en la misma receta se rechaza antes de GPU. Sirve con y sin referencias; repetir/variar conserva el modo. La API HTTPS también acepta turbo:true. Scheduler viggle_turbo sin turbo:true se rechaza.

La LoRA usa ViggleTurboLora del autor como rama runtime unmerged, sin fundirla en el checkpoint FP8. Los sigmas crudos son1,0.9375,0.875,0.75,0.5,0.25, seguidos de0; ViggleTurboSigmas aplica el shift según el tamaño REAL del latent (también en edición). sample_custom usa Euler/CFG1; los pasos y sigmas efectivos quedan en logs/metadatos. El cache KV nativo de Qwen se conserva. El código oficial está vendorizado con LICENSE/NOTICE, revisión fija009a44a895ef85f7e643c80fdca9543795248867 y SHA256 en qwen_service/vendor/viggle_source.json. No se descarga código ni modelos durante una generación.

Las pruebas de Turbo verifican parser/API, referencias, conflictos, idempotencia, sigmas, ramas runtime y SwiGLU fusionado con PyTorch real en CPU, incluyendo pesos base sin cambios y eliminación de hooks al fallar. No equivalen a una validación visual en L4. Fuente: https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo/blob/009a44a895ef85f7e643c80fdca9543795248867/comfyui/viggle_turbo.py .

Validación Turbo:148pruebas combinadas aprobadas. Preflight remoto turbo:true correcto, LoRA resuelta desde Volume, webhook sin errores. La revisión Comfy fijada dispone de DIFFUSION_MODEL y add_wrapper_with_key. Verificación de imports de las cuatro imágenes en CPU; no se solicitó L4 ni se generó una imagen Turbo durante esta comprobación.


## GPU aislada y entrega R2

La GPU vive en qwen_gpu.py y se incluye con App.include en la app principal. include_source=False y una lista explícita de archivos excluyen bot, HTTP, manager, storage CPU y HTML de la imagen GPU. El módulo GPU se puede importar sin Telegram. Las verificaciones de empaquetado comprueban la ausencia de esos archivos y ejercitan la importación real del módulo GPU en un Sandbox CPU con el SDK; no arrancan CUDA/modelos. Las imágenes CPU sí contienen las definiciones del sub-app porque hidratan modal_app. No hay un segundo despliegue GPU ni un proxy HTTP.

El hook snap=True mide snapshot.model_initialize y snapshot.warmup y ejecuta compact_snapshot: sincroniza CUDA, mide RSS/allocated/reserved, recolecta basura, vacía SOLO caché CUDA sin uso y devuelve páginas de heap libres mediante malloc_trim cuando existe. No mueve ni elimina pesos vivos. Registra snapshot.compacted antes/después. El hook snap=False mide los reloads por volumen. Ninguno de esos timers mide la restauración interna de Modal: esa duración se toma de Containers. No se ajustaron reservas CPU/RAM ni warm windows ni tipos GPU. El objetivo8s sigue pendiente de medición con restauraciones reales posteriores; la primera generación de esta nueva versión puede crear snapshots nuevos.

Para archivos superiores a8MiB con R2 uploaded y URL HTTPS disponible se envía solamente el enlace como texto, sin vista previa ni lectura/subida del PNG. Los botones de repetir/variante/reenvío se conservan. Archivos pequeños usan sendDocument y file_id si ya existe. Si el documento falla por transporte/rechazo, se notifica el enlace cuando está disponible;401/403/429 no provocan nuevos mensajes inmediatos. R2 sin URL pública no se sustituye por un enlace relativo protegido del API. Si supera el límite Telegram sin URL pública, se dirige a Biblioteca. Se conservan PNG original, metadatos y copias Volume/R2.

El POST de archivos pasa de timeout(3,60) a(20,120); texto/control usa(5,15). Un ConnectTimeout tiene UN reintento con sesión nueva porque ocurre antes de enviar HTTP. ConnectionError/ReadTimeout con entrega incierta no reintentan el documento. telegram.http registra método, segundos, bytes, statusHTTP, clase/kind de error y ambigüedad, sin URL/token/cuerpo. Los rechazos registran código y descripción acotada con secretos/queries redactados. CPUWriter registra error_kind/telegram_code/ambiguous. No hay reintentos programados ni cron.

telegram_redeliver(key) permite al operador recuperar explícitamente un resultado existente sin CPUWriter/GPU. Si ya se entregó por enlace, es idempotente. telegram_network_diagnostics comprueba DNS/TCP/TLS y getMe, sin publicar mensajes ni devolver tokens.

Validación de esta revisión:168pruebas combinadas (98Krea y70Qwen), incluyendo enlaces grandes sin leer el archivo, fallback sin repetir documento, rate limits/permisos, redacción y timeouts, reintento exclusivo de ConnectTimeout y conservación de tensores vivos al compactar. Los dos fallos indicados de Krea pesaban13.883.606y13.744.406bytes; ambos R2 HEAD200. Un intento de enlace también dio ConnectTimeout: por tanto el fallo no depende únicamente de tamaño. Otra sonda de lectura en eu-west/Azure estableció TCP/TLS IPv4 en0,039s y getMe correcto; IPv6 falló inmediatamente sin bloquear IPv4. Tras los ajustes, ambos resultados se entregaron por R2 sin regenerar.

Ajuste final de conectividad: las funciones Telegram y CPUWriter quedan en region=eu-west, donde se comprobó TCP/TLS y getMe, manteniendo las URLs públicas us-east. Es una mitigación de variación de rutas entre contenedores, no una garantía de disponibilidad de Telegram. El logger HTTP incluye región. GPU no cambia de región ni tipo ni recursos. Los despachos pequeños y enlaces tienen reintento único de ConnectTimeout; errores de lectura/escritura incierta siguen sin reintentar documentos. Los dos trabajos de Krea indicados terminaron en done con delivery_method=r2_link.


## Lotes con semillas independientes

`--n 4` solicita cuatro imágenes y admite cantidades de1a8. Telegram y API (n:4) generan un batch tensorial en UNA llamada al sampler y UNA asignaciónGPU. Las LoRAs y el prompt se aplican al batch. Se crea ruido independiente por semilla; una semilla base explícita produce base,base+1,…, con vuelta al rango positivo de63bits. La semilla automática sigue siendo aleatoria por solicitud. El máximo práctico de n depende de resolución, referencias, LoRAs y VRAM. No se multiplica el número de contenedores.

Cada imagen tiene su semilla, PNG/sidecar, timestamp, índice batch, hash, R2 y receta. La respuesta incluye images,seeds y batch con metadatos individuales. La duración del batch se conserva en generation.batch_timing; el cálculo por imagen distribuye los tiempos compartidos y la respuesta del lote suma costos. Los nombres llevan batch0,batch1,… y jobID independiente derivado del trabajo padre. El manifiesto batch_partial.json se actualiza al persistir cada imagen. Repetir una solicitud completada recupera todas las imágenes sin inferencia adicional.

Telegram agrupa los lotes nuevos en un álbum visual si cada archivo pesa menos de 5 MB (5.000.000 bytes). La caption enumera solo las semillas, en orden. Telegram procesa las fotos; los PNG originales con metadatos permanecen en Volume/R2. Los controles de repetir/variar/reenviar lote reutilizan el mensaje de estado, porque sendMediaGroup no admite teclado inline. Reenviar usa los file_id de las fotos sin GPU ni descargar originales. Si alguna imagen supera el umbral, se conserva la entrega individual y la política de enlaces R2. Los lotes antiguos parcialmente entregados siguen individualmente para evitar duplicados. Una entrega incierta requiere revisión antes de repetir el álbum. Las referencias de Qwen y el flag Turbo se comparten en el lote.

VAE base nuevo texture_fix_vae_for_qwen_image_2.1_bf16 de madebyollin, archivo ComfyUI recomendado por el autor. Se descargó en CPU y se usa en ambos presets. La selección del modelo sigue en presets/unet_name para cambios posteriores de checkpoint realista.

```text
/generar retrato !estilo:0.7 --turbo --n 4
```

Con imágenes de referencia activas, el mismo comando edita un lote con referencias/LoRAs comunes y semillas distintas.

Validación final del lote:176pruebas CPU aprobadas y una generación real n=2,256×256 por modelo. Krea usó Wan2.1normal y Qwen usó texture-fix con Turbo. Dos semillas distintas y PNG distintos por modelo, nombres batch0/batch1, metadatos y R2 uploaded verificados. Se crearon cuatro imágenes pequeñas de prueba en Volume/R2. Los VAE alternativos de Krea se descargaron y resolvieron; esta prueba de inferencia cubre el VAE predeterminado. La configuración de GPU editada por el usuario se conservó.


El estado muestra la GPU real de gpu.ready durante muestreo y persistencia CPU, conservada en el registro de progreso; antes de asignarse indica pendiente de asignación. La caption de archivos individuales también contiene solo su semilla. Validación de esta revisión: 188 pruebas CPU combinadas; no se disparó una nueva generación GPU ni se publicaron álbumes de prueba en el chat.


## Semillas independientes (18-11-2026)

En lotes `n>1`, cada imagen obtiene una semilla aleatoria criptográfica mediante `secrets`; no se usa una serie consecutiva. Las semillas son únicas dentro del lote. Un `--seed` explícito fija la primera imagen y las siguientes reciben semillas aleatorias únicas. La API acepta además una lista `seeds` exacta de `n` semillas únicas. Repetir conserva la lista del lote; Nueva variante la vuelve a sortear.


## Progreso de descargas en Telegram (Qwen)

Durante /descargar y /descargar_modelo el bot edita el mensaje temporal cada vez que avanza la descarga. Muestra porcentaje, cantidad descargada, tamaño total y velocidad en MB/GB; al terminar, el mensaje de progreso se limpia y queda la confirmación habitual. Los logs estructurados conservan también los bytes exactos.


## Entrega de lotes y descargas (actualización 2026-11-22)

Los lotes se envían en un álbum con copias JPEG de hasta 1600 px y calidad 88, preparadas en CPU con hasta tres lecturas concurrentes. Los originales PNG y sus metadatos permanecen en R2/Volume y en Biblioteca. Los reenvíos del álbum reutilizan file_id. Los logs `telegram.album.prepare` permiten comparar bytes originales, bytes enviados y tiempo de preparación; `telegram.http` mide el envío real. La CPU del bot dispone de 0.5 cores y 1 GiB, con escala a cero.

`/descargar_modelo ENLACE [nombre]` descarga checkpoints en CPU a diffusion_models, con el mismo progreso y fuentes HF/Civitai que las LoRAs. `/descargar` sigue descargando LoRAs.

## Edición explícita por respuesta

El nuevo flujo reemplaza la selección persistente anterior. Un prompt sin respuesta genera desde texto aunque hayas enviado fotos antes. Respondé a una foto o a cualquier elemento de un álbum con la instrucción (opcionalmente `/editar`). Se usa ese álbum completo, sin mezclar otros. También podés enviar una imagen/álbum con caption: crea la confirmación de edición con ese texto. Los elementos tardíos del álbum se vuelven a leer antes de preparar referencias en CPU. Podés responder a álbumes generados por el bot tras esta actualización. `/referencias` muestra ayuda del flujo y `/limpiar` ya no es necesario para generar desde texto.
