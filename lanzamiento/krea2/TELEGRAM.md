# Telegram integrado en Krea2

El bot @imagekrea2_bot vive en la app Modal krea2-personal-v2. La app antigua krea2-telegram quedó detenida. Se conserva el volumen krea2-telegram-state con sus alias y trabajos. No hay cron, tarea programada, worker de vigilancia ni HTTP de vuelta hacia la API Krea.

## Recorrido

Webhook autenticado → TelegramState guarda update_id → telegram_process procesa el evento y termina → CPUWriter.execute_bot prepara/genera/guarda → KreaGPU realiza inferencia → CPUWriter entrega el PNG a Telegram. CPUWriter usa el mismo backend que la API HTTPS. No se evita el guardado CPU ni se escribe R2 desde GPU.

El coordinador CPU ya activo espera el resultado GPU con timeout de2s y consulta el último evento del Dict para editar el mensaje Telegram cuando cambia. No hay otro proceso dedicado a polling del trabajo. Al terminar, la notificación sale de la misma función CPUWriter. No se ejecutan comprobaciones periódicas entre solicitudes. Esta espera sigue consumiendo la reserva CPU/RAM del coordinador; centralizar Apps por sí solo no elimina cargos. La reserva writer bajó a0,25CPU/512MiB, con posibilidad de crecer para conversiones y PNG grandes. Las estimaciones siguen sin equivaler a factura.

Sin cron no hay reanudación automática tras una caída de infraestructura: /estado y el botón Recuperar consultan el trabajo existente, sin crear otra generación. Un spawn ambiguo se marca needs_review y no se repite a ciegas. Telegram puede aceptar un archivo y perder su respuesta: delivery_unknown conserva receta/resultado y permite reenvío explícito, sin GPU. Descartar solo cancela borradores; no cancela una GPU ya iniciada.

## Telegram nativo

Se registran /generar, /loras, /modelos, /convertir_modelo, /galeria, /estado, /trabajos, /descargar y /help mediante setMyCommands. Escribir / muestra autocompletado de Telegram. El botón permanente Biblioteca se configura mediante setChatMenuButton(type=web_app); abre una Mini App con initData, no un enlace web externo. /start actualiza el acceso por mensaje. Los botones viejos conservan la URL antigua: usar el menú nuevo o /start.

Para mostrar también el botón Abrir app en el perfil del bot hace falta configurar Main Mini App desde BotFather; setChatMenuButton no configura ese elemento del perfil. La URL de la Mini App es https://kwon-lanxi--krea2-personal-v2-telegram-web.us-east.modal.run/mini . El código admite acceso desde el menú y botones web_app; las rutas privadas verifican HMAC, edad y el usuario7112930476. La API HTTPS existente conserva su Bearer y sus rutas.

## Biblioteca móvil

12 tarjetas por página, búsqueda por nombre/ruta/alias, filtros Generales/Personalizadas y Con alias/Seleccionadas. Cada tarjeta permite copiar nombre, copiar ID completo, editar alias y peso, usar +/− y copiar !alias:peso. Se propone un alias a partir del nombre pero no se registra hasta guardar/elegir. Las colisiones con otro archivo se rechazan. La selección admite8LoRAs y una barra fija permite copiar toda la combinación o quitar elementos. Pesos actuales de la selección y pesos habituales guardados son datos distintos hasta Guardar alias.

Galería carga originales solo al abrirlos, permite copiar prompt y consultar receta. Usa las últimas100salidas del servidor. Los nombres del archivo y volumen completos se conservan al resolver; dos nombres similares no se mezclan. El portapapeles tiene diálogo alternativo con texto seleccionable.

Diseño aplicado con ui-ux-pro-max: estilo funcional mínimo, lectura en teléfono, botones táctiles de48px, separación de8px, colores con contraste, safe areas, footer medido con ResizeObserver, foco visible y reduced motion. No se incorporó la portada marketing sugerida por la consulta general porque no encaja en una biblioteca personal.

## Modelos y conversión FP8

`/modelos` abre la pestaña Modelos, separada de LoRAs y Galería. Ahí se guardan alias para `--model`, se convierte desde CPU con FP8 escalado o mixto, y se pueden borrar checkpoints que no estén asignados a un perfil.

```text
/generar retrato --model mi_alias --profile turbo
/convertir_modelo --model HomoFidelis_Krea2_Turbo_BF16_v1 --fp8-scaled
/convertir_modelo --model mi_alias --fp8-mixed
```

FP8 mixto conserva en BF16 las capas cuya desviación relativa supera 5%; ambas salidas siguen la estructura `fp8_scaled` por capa de ComfyUI. Los modelos INT8/FP8 Comfy reconocidos se dequantizan en un temporal BF16, y el origen cuantizado se conserva. Un origen BF16/FP16 solo se elimina después de validar cabecera, capas FP8, escalas y metadata, guardar la salida y actualizar los perfiles. Si el commit o conversión falla, el origen permanece. No se sobrescriben archivos. El borrado manual está limitado a checkpoints y rechaza los referenciados por perfiles.

La conversión usa `pytorch-precision-converter==2.3.0` en el worker CPU, con referencias FP8 oficiales fijadas por revisión y un informe privado por trabajo. Descargas HF/Civitai se guardan como modelos antes de convertir; la conversión presupone una arquitectura compatible con la referencia de Krea2. Un checkpoint de arquitectura distinta se conserva si la conversión falla.

## Configuración y verificación

Secrets: tg-token-image (TELEGRAM_KREA2_BOT_TOKEN), krea2-telegram-config (TELEGRAM_WEBHOOK_SECRET). Qwen queda independiente. Reutiliza el volumen de estado existente sin copiar pesos ni crear otro historial de imágenes. telegram_configure registra webhook/comandos/menú; telegram_diagnostics consulta getWebhookInfo, comandos, menú y salud interna sin GPU.

Fuentes: https://core.telegram.org/bots/api#setmycommands ; https://core.telegram.org/bots/api#setchatmenubutton ; https://core.telegram.org/bots/api#setwebhook ; https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app .

Las pruebas locales cubren firmas/edad/propietario, alias/pesos/tamaños, confirmación duplicada, carrera de finalización antes de devolver spawn, spawn ambiguo sin reintento, webhook falso y sesión MiniApp inválida. La revisión de navegador usa una fixture aislada, sin credenciales ni GPU. El transporte Telegram real se verificó con getMe, setWebhook, getWebhookInfo, getMyCommands y getChatMenuButton. No se lanzó una nueva generación solo para comprobar la migración; falta tu prueba manual de generación/entrega desde Telegram para verificar el recorrido GPU completo.

Formato principal: `/generar retrato !personaje:0.8 !estilo:0.5 --size vertical`. Los alias anteriores con `@` siguen admitidos por compatibilidad. Telegram identifica `@usuario` como entidad de mención pero conserva el texto original; usamos `!` para evitar enlaces y sugerencias de menciones. `+` queda disponible como texto normal del prompt.

Verificación final: 94 pruebas locales aprobadas (74 Krea2/Telegram +20 Qwen). Mini App comprobada a320/375px, horizontal y texto200%, sin desborde horizontal ni errores JS. Despliegue verificado: /mini200 con formato !; /mini/aliases401 sin firma; webhook403 sin secreto; cero actualizaciones Telegram pendientes y sin error de entrega informado. Al guardar otro alias para el mismo archivo, se presenta el último guardado y se mantienen válidos los anteriores.

## Corrección de arranque tras integrar Telegram

El import global de telegram_bot en modal_app necesita estar disponible en TODAS las imágenes que hidratan ese módulo. Antes, CPUReader/API y KreaGPU no lo incluían y podían entrar en crash-loop; el diagnóstico Telegram superficial no lo detectaba. with_app_sources ahora copia krea_service, telegram_bot.py y tg_logic.py a todas las imágenes.

Regresión de empaquetado: `python -X utf8 lanzamiento/krea2/scripts/check_image_imports.py` comprueba los artefactos reales en Sandboxes CPU efímeros, incluida la imagen GPU sin reservar GPU. Reproducción anterior: CPU_IMAGE devolvía ModuleNotFoundError para telegram_bot. Tras corregir, las cuatro imágenes pasan.

Verificación del despliegue corregido: CPUReader arranca y devuelve92LoRAs; preflight turbo correcto sin GPU; solicitudes Mini App con initData firmado real devuelven200 para LoRAs(92), alias y galería(100). getWebhookInfo sin error de entrega ni actualizaciones pendientes. Las94pruebas locales siguen aprobadas. No se ejecutó inferencia GPU en esta corrección.

## Mensajes compactos, proporciones y latencia

`--size` acepta 1:1=1328×1328, 16:9=1664×928, 9:16=928×1664, 4:3=1472×1104, 3:4=1104×1472, 3:2=1584×1056, 2:3=1056×1584. El valor inicial es1:1. Se mantienen square/vertical/horizontal como compatibilidad, ahora mapeados a estas dimensiones. -w/-h siguen teniendo prioridad. Son dimensiones de muestreo: VAE2x puede duplicar la salida. El nuevo cuadrado contiene68,2%más píxeles que1024²; no implica una mejora de velocidad.

La confirmación responde al mensaje original usando reply_parameters; no copia el prompt. Presenta tamaño, pasos y LoRAs/pesos. Ese mismo mensaje pasa a mostrar progreso al confirmar y pierde sus botones de generación. Se elimina tras entregar el documento, conservando el prompt y el original con Repetir/Nueva variante/Reenviar. Descartar elimina solo la confirmación. Los errores quedan visibles. La limpieza es best-effort (Telegram puede rechazar mensajes viejos); no se borran imágenes ni prompts.

Se reutiliza requests.Session. Cada llamada Telegram registra telegram.http con método y segundos, sin URL/token/cuerpo. Se quita la consulta de SQLite que se hacía antes de CADA llamada a Telegram. Los get/list/active del estado no hacen commit del Volume. Las ediciones de progreso no se repiten y se limitan a una cada2s; CPUWriter consulta mientras espera el resultado cada2s, sin monitor separado ni cron. El resultado conserva su file_id Telegram para reenviar sin volver a leer/subir el PNG. No se reintentan a ciegas envíos con respuesta incierta.

El catálogo combinado de ambas raíces LoRA queda en un Modal Dict con TTL300s, compartido entre arranques. Actualizar archivos fuerza refresco; guardar alias usa el catálogo cacheado. Descargas/eliminaciones/conversiones por CPUWriter invalidan el catálogo. Los pesos permanecen en Volumes; no se mantiene una GPU viva para cachearlos. Los alias permanecen en el volumen SQLite y se leen autoritativamente, sin un cache cliente que pueda aplicar pesos antiguos.

Ejemplo real anterior a esta revisión, job ab376ddfcdfb25b54d3962362b51ddc2: prepare6,0635s; gpu_call589,725s; dentro de GPU lora1,0226s, encode0,2038s, sampling14,3231s, decode0,9376s, transfer_cpu0,0657s; persist_and_upload12,4461s. gpu_call incluye espera/arranque/restauración/transferencia RPC y NO equivale a segundos GPU facturados. Estos logs no permiten separar con exactitud cola de restauración ni medir el envío Telegram anterior; el nuevo contador mide este último. Cambiar el transporte del bot no elimina falta de capacidad L40S.

Referencias: https://core.telegram.org/bots/api#replyparameters ; https://core.telegram.org/bots/api#deletemessage ; https://core.telegram.org/bots/api#senddocument ; https://modal.com/docs/guide/cold-start .

Verificación de esta revisión:108pruebas aprobadas (88Krea/Telegram y20Qwen), UI móvil sin errores/desborde, cuatro imágenes comprobadas en Sandboxes CPU. Rutas MiniApp autenticadas200: catálogo con refresco8,184s (incluye arranque/escaneo), catálogo cacheado0,225s para92LoRAs, alias3,168s, galería1,089s y HTML0,104s, en una sola muestra. La carga de catálogo y alias ahora va en paralelo. Son tiempos HTTP de esta comprobación, no un SLA ni una medida de latencia del chat. Se agregan telegram.update.handled queue_seconds/seconds para distinguir espera del procesador de HTTP Telegram. Webhook sin error ni pendientes. No se generaron imágenes para verificar esta revisión.


## GPU aislada y entrega R2

La GPU vive en krea_gpu.py y se incluye con App.include en la app principal. include_source=False y una lista explícita de archivos excluyen bot, HTTP, manager, storage CPU y HTML de la imagen GPU. El módulo GPU se puede importar sin Telegram. Las verificaciones de empaquetado comprueban la ausencia de esos archivos y ejercitan la importación real del módulo GPU en un Sandbox CPU con el SDK; no arrancan CUDA/modelos. Las imágenes CPU sí contienen las definiciones del sub-app porque hidratan modal_app. No hay un segundo despliegue GPU ni un proxy HTTP.

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

VAE base predeterminado wan_2.1_vae (Wan2.1 normal). --vae base, --vae wanx2, --vae realvae y --vae HDvae eligen el decoder, con alias sin distinción de mayúsculas. Wanx2 conserva el VAE2x anterior. Los archivos realvae y HDvae se descargaron a Volume en CPU desde los repositorios proporcionados, con revisión fijada por las descargas. Cambiar VAE cambia la identidad del snapshot.

```text
/generar retrato !estilo:0.7 --n 4 --size 3:4
/generar retrato --n 4 --vae realvae
/generar paisaje --n 4 --vae HDvae
```

Validación final del lote:176pruebas CPU aprobadas y una generación real n=2,256×256 por modelo. Krea usó Wan2.1normal y Qwen usó texture-fix con Turbo. Dos semillas distintas y PNG distintos por modelo, nombres batch0/batch1, metadatos y R2 uploaded verificados. Se crearon cuatro imágenes pequeñas de prueba en Volume/R2. Los VAE alternativos de Krea se descargaron y resolvieron; esta prueba de inferencia cubre el VAE predeterminado. La configuración de GPU editada por el usuario se conservó.


El estado muestra la GPU real de gpu.ready durante muestreo y persistencia CPU, conservada en el registro de progreso; antes de asignarse indica pendiente de asignación. La caption de archivos individuales también contiene solo su semilla. Validación de esta revisión: 188 pruebas CPU combinadas; no se disparó una nueva generación GPU ni se publicaron álbumes de prueba en el chat.

Wan base actualizado a Wan2_1_VAE_fp32.safetensors (Kijai/WanVideo_comfy, revisión 8260d429d19fd7a72304cad059160b95d843913f). Alias base y tres presets lo seleccionan; el loader fuerza precision=fp32 solamente para ese archivo. Los otros VAE mantienen su precisión automática. Ambos despliegues excluyen B200.


## Semillas independientes (18-11-2026)

En lotes `n>1`, ya no se construyen como `seed`, `seed+1`, etc. Cada imagen obtiene su propia semilla aleatoria criptográfica mediante `secrets`, y se comprueba que sean distintas dentro del lote. Si se manda una semilla explícita con `n>1`, se conserva solo para la primera imagen; las restantes son aleatorias y distintas. La API también acepta `seeds` explícitas, pero exige exactamente `n` valores únicos entre 1 y 2^63−1. Repetir en Telegram conserva exactamente las semillas de todo el lote; Nueva variante genera un lote nuevo.


## Progreso de descargas en Telegram (Krea2)

Durante /descargar y /descargar_modelo el bot edita el mensaje temporal cada vez que avanza la descarga. Muestra porcentaje, cantidad descargada, tamaño total y velocidad en MB/GB; al terminar, el mensaje de progreso se limpia y queda la confirmación habitual. Los logs estructurados conservan también los bytes exactos.


## Entrega de lotes y descargas (actualización 2026-11-22)

Los lotes se envían en un álbum con copias JPEG de hasta 1600 px y calidad 88, preparadas en CPU con hasta tres lecturas concurrentes. Los originales PNG y sus metadatos permanecen en R2/Volume y en Biblioteca. Los reenvíos del álbum reutilizan file_id. Los logs `telegram.album.prepare` permiten comparar bytes originales, bytes enviados y tiempo de preparación; `telegram.http` mide el envío real. La CPU del bot dispone de 0.5 cores y 1 GiB, con escala a cero.

`/descargar_modelo ENLACE [nombre]` descarga checkpoints en CPU a diffusion_models, con el mismo progreso y fuentes HF/Civitai que las LoRAs. `/descargar` sigue descargando LoRAs.


## SmartSeedVariance y elección de muestreo

Desactivado por defecto: omitir `--variance` o usar `--variance off` conserva el conditioning original. Se aplica solo al conditioning positivo, después del encoder y de las LoRAs. Cada imagen del lote recibe su propia perturbación reproducible; el muestreo sigue siendo un batch. No requiere pesos extra.

```text
retrato en una cafetería !mi_estilo:0.7 --n 6 --variance balanced
retrato !mi_persona:0.8 --variance subtle --variance-protect first-half
escena fantástica --variance creative --variance-direction composition --sample euler --screluder beta
retrato --variance custom --variance-strength 25 --variance-protect custom --variance-regions "0-5,15-20"
retrato --steps 8 --variance balanced --variance-schedule step_cutoff --variance-cutoff 3 --variance-tail 0
```

`/variacion` lista los controles y `/muestreo` lista todos los samplers/schedulers disponibles en la revisión fijada de ComfyUI. `--sample` equivale a `--sampler`; `--screluder` equivale a `--scheduler`. Sin flags: Euler/simple. La preview de dos pasos mantiene Euler/simple aunque se pidan otros.

| Flag | Valores / efecto |
|---|---|
| `--variance` | off, subtle, balanced, creative, bold, wild, custom |
| `--variance-strength` | 0–100, solo afecta al preset custom |
| `--variance-seed` | Entero opcional; se mezcla con la semilla de cada imagen para mantener independencia y repetición |
| `--variance-protect` | none, first-quarter, first-half, last-quarter, last-half, custom, random |
| `--variance-regions` | Índices/rangos entre comillas para protect custom; son tokens, no palabras ni detección automática de identidad |
| `--variance-direction` | Patrones del nodo; ver lista completa en /variacion |
| `--variance-shift` | 0–200; multiplicador del patrón de dirección |
| `--variance-fade` | instant, linear, ease-out, ease-in, ease-in-out, smooth-step, burst |
| `--variance-timing` | all, beginning, ending |
| `--variance-schedule` | constant, decreasing, step_cutoff, tiered_release, hard_lock |
| `--variance-cutoff` | 0 hasta pasos efectivos; default 4; solo schedules avanzados |
| `--variance-tail` | 0–1; intensidad posterior al cutoff |
| `--variance-freeze` | Flag booleano; conserva estructura según el algoritmo del nodo |
| `--variance-normalize` | Flag booleano; normaliza la intensidad con el conditioning |
| `--variance-vibe` | Prompt de referencia entre comillas, codificado con el mismo encoder/LoRAs |
| `--variance-blend` | 0–1; mezcla de referencia, default 0.5 |

Los schedules avanzados tienen prioridad sobre timing. El nodo representa las ventanas como porcentajes de progreso de ComfyUI (`cutoff/steps`), no como un contador que cambie exactamente después del N-ésimo callback. Beginning/ending usa el umbral 20% del código upstream. Reducir el cutoff al usar preview de 2 pasos.

Empezar con subtle/balanced. Mayor intensidad puede alterar identidad, composición y seguimiento del prompt; la compatibilidad matemática con LoRAs no garantiza la calidad visual de cada combinación. No usar wild como default. El negativo permanece intacto.

Los metadatos de originales PNG/JSON y R2 guardan los campos `variance*` de la solicitud y `generation.smart_seed_variance`: versión, configuración y semilla efectiva por imagen. Repetir/nueva variante y las recetas guardadas retienen los ajustes. El cache del encoder conserva el conditioning sin modificar. El log `variance.applied` y la etapa `smart_seed_variance` permiten medir el coste.

API: mismos nombres con guiones bajos (`variance_protect`, `variance_schedule`, etc.); `GET /sampling-options` devuelve opciones sin iniciar GPU. El playground incluye controles. El adaptador carga de forma diferida el módulo upstream, sin inicializar la UI ni escanear custom_nodes.

Fuente fijada: https://github.com/RamonGuthrie/ComfyUI-RBG-SmartSeedVariance/tree/187da1cbfd80a116cc13389ef22b9978ad01783b . Código upstream sin cambios en `krea_service/vendor/smart_seed_variance.py`; licencia GPL-3.0-or-later y atribución en la misma carpeta. El adaptador local está en `krea_service/variance.py`.
