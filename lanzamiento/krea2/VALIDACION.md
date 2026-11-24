# Validación de la candidata

16 de noviembre de 2026. Revisión 2.1.0. App HTTPS desplegada. Preview y generación final con LoRA verificadas realmente en L40S; ambas guardadas en Volume y R2.

- App importada con SDK Modal **1.6.4**, registrando las clases, perfiles, imágenes y decoradores sin ejecutar llamadas remotas.
- Sintaxis Python comprobada en la carpeta.
- **54 pruebas CPU**: contrato y límites, autenticación, consultas sin GPU, trabajos asíncronos, idempotencia y solicitud repetida, estado ambiguo sin regeneración, aislamiento de rutas, resolución exacta y ambigüedad LoRA, integridad de safetensors, descarga incompleta sin publicación, preservación del original durante conversión simulada, metadatos PNG/JSON y recuperación ante fallos de cliente/upload R2, redacción de logs.
- Se leyó la cabecera del TAE de la revisión fijada y se verificó **carga estricta de sus pesos reales** contra `Decoder` de la revisión Comfy fijada, ejecutando en CPU con capas PyTorch equivalentes. Entrada `[1,16,8,8]` produjo `[1,3,64,64]` finito. Se descargaron 2.464.478 bytes temporalmente y se eliminó esa copia. Esto comprueba arquitectura/pesos, no calidad ni latencia L40S.
- Se comprobaron las firmas de `VAEUtils_CustomVAELoader` y `VAEUtils_VAEDecodeTiled` contra el commit fijado; el decode conserva su auto pixel-shuffle 2x. El VAE real ya existe en el Volume; el resultado de su decode GPU se documenta en la prueba remota.
- Se verificó que el índice oficial ofrece torch 2.10.0 CUDA 12.8. Se construyó la imagen GPU Linux completa y `pip check` pasó.
- Los dos originales de la raíz siguen intactos y hay copias en `extras/pre-release-2026-11-15`.

Verificado en Modal: Secrets configurados, catálogo real de modelos, selección custom mediante preflight CPU, build Linux GPU y preparación CPU real de TAE y adaptador de dos pasos con hashes/revisiones fijadas. La prueba remota de abajo verificó warmup, carga LoRA, final 2x, preview TAE, restauración, apagado single-use y persistencia de imágenes reales en Volume/R2. La conversión CPU BF16 a FP8 scaled no se ejecutó; se usa el HomoFidelis FP8 ya existente.

Empezar por preflight CPU; después una imagen pequeña con LoRA conocida y la misma llamada tras escalar a cero. Comparar contra la versión anterior antes de cambiar de precisión/modelo principal. Ver instrucciones en README.

## Ampliación 2.1

- Resolver HF acepta URI hf://, resolve/blob HTTPS, subcarpetas y revisión.
- Civitai .com/.red admite enlace directo, página con versión o resolución de versión/archivo. Las pruebas simulan respuestas del proveedor; no certifican disponibilidad/acceso de todos los modelos externos.
- Requests cambian modelo/encoder/VAE/precisión conservando presets. El snapshot se identifica por selección y versiones de archivos, incluido TAE cuando está disponible.
- PNG y JPEG: lectura del JSON embebido, fechas UTC/offset, receta, precios y tiempos coinciden con la respuesta. Un JPEG de metadatos mayores de 64 KiB se recuperó sin truncamiento.
- Playground revisado en navegador: conexión, catálogo, preflight, resultado sintético con métricas, enlace HF/nombre personalizado y layout móvil (sin overflow horizontal). Fixture claramente marcado QA, sin GPU ni descarga real. Captura `playground-desktop.jpg`.
- Las tarifas usadas son las publicadas por Modal al 15/11/2026; el cálculo es estimado, con supuestos/exclusiones, no factura verificada por imagen.
- Se consultaron los volúmenes reales sin GPU: existen HomoFidelis BF16 y FP8, qwen_3vl_4b y Wan upscale2x. No se alteraron esos pesos.

## Despliegue HTTP real

- API y playground: https://kwon-lanxi--krea2-personal-v2-api.us-east.modal.run/playground
- Health autenticado, catálogo, preflight y tarifas devuelven 200; acceso sin token a operaciones devuelve 401. HTML público coincide con el archivo local.
- Entrypoint: `modal_app.py`. Se corrigió una colisión de `app.py` con el paquete `app.governance` de ComfyUI detectada en el primer arranque real. Se detuvo ese despliegue antes de reintentar.
- TAE y LoRA de destilación descargados realmente mediante CPU; no se modificaron pesos existentes.

## Prueba real L40S y correcciones

- El FP8 antiguo `HomoFidelis_Krea2_Turbo_FP8_v1` produjo una imagen defectuosa: Comfy ignoraba escalas `.weight.scale`. Ahora preflight y preparación CPU lo rechazan antes de GPU; el original permanece intacto.
- Se corrigió serialización de `torch.__version__` (`TorchVersion`) a tipos JSON simples para que la API sin PyTorch pueda consultar resultados. Regresión cubierta por test.
- Preview correcta: `342a048f8f9788ce359c8012e16f39ec`, BF16 con `fp8_e4m3fn_fast`, seed42, 512×512, LoRA de destilación (228 claves), TAE. Imagen inspeccionada: manzana coherente con el prompt. Método GPU 10,3448 s; sampler 8,4448 s; decode 0,338 s; RPC GPU 91,9418 s incluyendo primera captura/arranque; preparación+RPC 112,8289 s. Son muestras puntuales, no benchmark estable.
- HTTP polling 200, JPEG original recuperado de Volume, hash SHA256 válido y metadatos embebidos coincidentes (fecha y tiempos). R2 confirmó upload. Copia local `smoke-preview-fast.jpg`.
- Writer CPU permite pico de RAM hasta 64 GiB (reserva 2 GiB) para conversiones BF16 grandes; no se ejecutó una conversión real.
- Comfy advierte que algunos kernels CUDA optimizados requieren cu130; el baseline instalado cu128 funcionó con SDPA. No se validó mejora de velocidad de cu130, Sage o compilación.

- Final correcta: `90278ea31c6ed86f5d9985efc41a53e6`, mismo modelo/precisión, seed42, muestreo512×512, 8 pasos, DetailSliderv1=0,3 (96 claves aplicadas), Wan2.1 VAE upscale2x → PNG 1024×1024. Inspección visual correcta. Método GPU 8,2252 s; sampler 6,7113 s; decode 0,0964 s. RPC GPU 113,9441 s y preparación+RPC 118,1191 s; el RPC incluye cola/restauración/transporte, no es tiempo facturado comprobado.
- Los logs confirman `Restoring Function from memory snapshot.` antes de la final. Así se probó una restauración real y cambio de LoRA respecto a la preview.
- Polling final 200, R2 uploaded, hash de original Volume y JSON embebido (fechas/timings) coinciden con respuesta. Copia local `smoke-final-fast.png`.
- Tras finalizar: `FunctionCurrentStats(backlog=0, num_total_runners=0, num_running_inputs=0, input_headroom=0)` para la configuración probada. Sin GPU ociosa.
- Preflight HTTP del FP8 antiguo devuelve success=false y explicación de escalas incompatibles sin iniciar GPU.
- Con estas dos muestras, no hay evidencia de ahorro por hacer preview y después final en llamadas separadas. Para pocas imágenes personales, conviene comenzar con final y reservar preview para explorar prompts. No extrapolar estos tiempos a 1024 latentes ni a otros LoRAs/modelos.

Quedan sin validar: conversión CPU real a FP8 scaled en disco, inferencia RAW, todos los demás LoRAs, descarga real desde Civitai, comparación cu130/Sage/compile, latencia sostenida y factura exacta. El preset Turbo validado mantiene BF16 en Volume y lo carga con fp8_e4m3fn_fast; esto reduce precisión en ejecución y no crea por sí solo un nuevo archivo FP8 en disco.


## Revisión 2.1.1 · nombres y observabilidad

- 59 tests CPU aprobados: incluidos fecha/seed/ID único en nombres, URLs del evento output.ready, progreso autenticado sin invocar GPU/Volume, publicación limitada y tolerancia a caída del canal.
- JavaScript del playground pasó `node --check`.
- Desplegada en el mismo endpoint. Health200 informa2.1.1; HTML público coincide con el local.
- Canal de progreso comprobado HTTP200 contra Modal Dict real con evento sintético identificado como fixture. Sin token401. Se eliminó la entrada de prueba. No se lanzó otra generación GPU para esta revisión; el callback está cubierto con tests CPU, y la generación real corresponde a2.1.0.
- El redespliegue cambia código/imagen e invalida snapshots anteriores según Modal; la primera generación posterior puede volver a capturar el modelo. No se precalentó para evitar gasto adicional.
- Logs reales de generación del usuario90398fbb3e9a070b328f64b47f4b9315: sampler18,3124s, métodoGPU19,3479s, RPC126,8042s, preparación+RPC135,4336s, persistencia/upload17,1793s. Los107,4563s entre RPC y método no permiten separar cola, restauración, transferencias y tiempo cobrable.


## SmartSeedVariance (2026-11-24)

- `python -m pytest lanzamiento/krea2/tests/test_variance.py lanzamiento/krea2/tests/test_service.py lanzamiento/krea2/tests/test_batches.py -q -k 'not conversion_keeps_original and not batch_delivery_resumes'`: 95 passed, 2 deselected.
- Los dos casos excluidos fallan también ejecutando HEAD anterior en un directorio temporal: conversión legacy y fixture de entrega de batches. No se modificaron para ocultar errores.
- El módulo upstream se comparó con el archivo de la revisión 187da1cbfd80a116cc13389ef22b9978ad01783b: contenido idéntico.
- Playground JS: `node --check` pasó.
- Despliegue Modal completado; `/sampling-options` devuelve HTTP200, revisión fijada y default off. Menú Telegram actualizado con /variacion y /muestreo; webhook sin pendientes ni error registrado.
- Prueba GPU real por API: 2 imágenes, 512×512, 2 pasos, Euler/simple, variance subtle, step_cutoff1. Success; ambas imágenes subidas a R2. GPU asignada: NVIDIA RTX PRO 6000 Blackwell Server Edition.
- Se descargaron ambos originales y se extrajeron sus metadatos PNG: configuración de variación y semilla efectiva independientes por imagen correctas.
- La prueba verifica integración y persistencia, no calidad visual de todos los samplers, LoRAs o presets. El warmup con variación off también completó durante la creación del snapshot.
