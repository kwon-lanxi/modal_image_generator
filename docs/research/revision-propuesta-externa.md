# Revisión de las dos propuestas externas

15 de noviembre de 2026. Se leyeron los dos textos adjuntos completos y se contrastaron sus aportes nuevos con fuentes primarias y el worker local. No se ejecutó conversión, GPU ni despliegue.

## Requisito confirmado

Telegram es un canal añadido. La API HTTPS continúa disponible y es independiente del bot. Web, scripts y Telegram usan la misma validación de perfiles, LoRAs y trabajos.

```text
Web/scripts → API HTTPS CPU ────────┐
                                  ├→ trabajos → GPU → resultado persistente
Telegram → webhook CPU → adaptador ┘                   ↓
                                         entrega HTTPS / envío Telegram CPU
```

## Aporte nuevo aprovechable: conversión CPU

`pytorch-precision-converter` existe y documenta el comando `precision-convert ... -p fp8_scaled --like REFERENCE -o OUTPUT`. Su versión publicada 2.3.0 procesa safetensors tensor a tensor en CPU; no requiere cargar todo el checkpoint en RAM. El argumento de referencia consulta cabecera y dos capas pequeñas, no descarga íntegro el modelo de referencia. Copia la selección de capas y estrategia de escalado, calculando los pesos/escalas apropiados para el modelo de entrada; no se debe describir como copiar ciegamente las escalas numéricas de otro fine-tune. [Documentación del paquete](https://pypi.org/project/pytorch-precision-converter/), [fuente](https://github.com/angelolamonaca/PyTorch-Precision-Converter).

Esto es un candidato concreto para una función CPU de preparación, con versión y referencia fijadas. Mantener el BF16 original; escribir otro archivo; comprobar nombres, formas, dtypes, escalas finitas y metadatos, y producir informe de error de cuantización. Después probar carga real y LoRAs. El autor describe inspección de cabeceras Krea2 y pruebas de formatos, pero eso no demuestra una conversión visualmente validada de HomoFidelis.

No se puede prometer conversión en 30–60 segundos: los benchmarks publicados usan otros modelos y máquinas. Tampoco están demostrados los 166 tensores sensibles exactos de este checkpoint ni el motivo por el que falló el intento anterior del usuario. Diagnosticar ese intento exige su error o artefacto, no inferirlo del nombre del modelo.

Para archivo FP8 scaled, comenzar con `weight_dtype=default` como indica el conversor. El preset `fp8_e4m3fn_fast` es otra ruta de carga/optimización que se compara separadamente. No forzar el casteo de todas las capas de un archivo mixto como si fuera equivalente a respetar su configuración cuantizada.

## Qué conservar de la propuesta

- Eliminar edición, ControlNet, referencias de imagen y upscalers externos que no se usan.
- Warmup con encoder, latente y sampler reales; fallar visiblemente si no funciona.
- Cachear LoRAs por identidad/versionado de archivos; separar el parche de sampling dependiente de resolución de la combinación de LoRAs.
- TAE concreto para previews, VAE real para finales y posibilidad de preview independiente de dos pasos.
- Autenticación HTTPS, acceso personal al bot e idempotencia de trabajos.
- Descargar/preparar pesos en CPU una vez y conservarlos en Volume.

Si el flujo final usa realmente Wan VAE upscale2x, **conservar ComfyUI-VAE-Utils**. Su repositorio sí implementa ese decoder y declara que no requiere dependencias adicionales fuera de ComfyUI. El archivo local lo instala y usa su loader, pero eso por sí solo no prueba qué checkpoint VAE hay en el Volume. El nombre genérico `wan_2.1_vae` tampoco lo demuestra. Verificar el archivo cargado y las dimensiones de salida. [VAE-Utils](https://github.com/spacepxl/ComfyUI-VAE-Utils).

## Afirmaciones que hay que corregir

| Afirmación externa | Evaluación |
|---|---|
| 28 pasos cuestan 3.5 veces más GPU por imagen que 8 | Son 3.5 veces los pasos. Arranque, encoder, VAE y entrega no escalan así. No es un factor de costo total demostrado. La receta exacta HomoFidelis aún debe validarse. |
| Preview L40S de 0.5–0.8 s, VAE 0.8–1.5 s, upload 400–800 ms | No hay benchmarks del setup que sostengan esas cifras. |
| TAE de 12 ms en L40S | El autor mide RTX 5080, no L40S. |
| FP8 implica snapshot casi dos veces más rápido | Menor volumen de pesos puede ayudar; restauración tiene más costos y depende de residencia real. Modal advierte que snapshots no aceleran necesariamente lectura de pesos. |
| 33 GB a 1–2 GB/s restauran en 6–12 s | Esas cifras no son consistentes si se leen literalmente: 33/2=16.5 s y 33/1=33 s. Además el ancho de banda efectivo no está medido. |
| scaledown_window=2 mata exactamente a los dos segundos | Es política de escalado, no garantía de tiempo físico exacto. Single-use expresa mejor cierre tras un trabajo. |
| uv_pip_install evita que se reemplace torch | Elegir instalador no fija dependencias. Se requieren versiones/constraints compatibles. |
| Eliminar init_extra_nodes y ya | También registra nodos extra nativos. Sustituirlo por importación/registro explícito de los módulos realmente necesarios. No intercambiar clases latentes de arquitecturas distintas sin verificar canales y escalas. |
| No codificar negativo ahorra 50% del encoder | Depende de longitud, caché y ejecución. Para Turbo a CFG=1 se puede evitar; no generalizar a CFG<=1 ni aplicar a RAW con CFG. |
| Model preview/full con la misma seed reproduce composición exacta | Cambiar pasos, destilación, resolución o modelo cambia la trayectoria; no garantizar composición idéntica. |
| La selección del bot en modal.Dict queda guardada permanentemente | La referencia actual expira entradas después de siete días sin lecturas/escrituras. Sirve para estado temporal, no archivo permanente de recetas/historial. |
| Webhook + spawn garantiza respuesta menor de 100 ms y sin duplicados | Latencia incluye cold start, almacenamiento y red; spawn no deduplica por sí mismo. Registrar y reclamar el trabajo de forma idempotente antes de ejecutarlo. |

Fuentes: [snapshots Modal](https://modal.com/docs/guide/memory-snapshots), [Image SDK](https://modal.com/docs/sdk/py/latest/Image), [Dict SDK](https://modal.com/docs/sdk/py/latest/Dict), [TAE](https://huggingface.co/AcademiaSD/TAE-Krea-2), [webhook Telegram](https://core.telegram.org/bots/api#setwebhook).

## Correcciones concretas de arquitectura

La GPU no debe hacer `sendPhoto`. Devuelve el resultado al coordinador CPU, que lo conserva y lo entrega. Un fallo de Telegram no debe prolongar innecesariamente la reserva GPU ni causar nueva inferencia. Envío y generación tienen estados/reintentos separados. Para calidad final se puede entregar el archivo original como documento o URL, además de la foto de vista previa.

El webhook valida secreto y usuario para mensajes **y callback queries**. La receta completa vive en el backend y el botón lleva un identificador opaco, no todo el prompt y LoRAs. Responder tras aceptar duraderamente el trabajo; deduplicar tanto actualizaciones repetidas como disparos repetidos del mismo botón, con una política explícita. Una lectura seguida de escritura sin exclusión no constituye una reclamación atómica bajo concurrencia.

`/healthz` y catálogo/rescan pertenecen a CPU, aunque estén dentro de la misma App Modal. Un archivo Python único puede definir funciones CPU y clase GPU separadas, pero no hay beneficio de rendimiento por juntar todo en un archivo. No conservar health en la clase GPU.

Cambiar de RAW a Turbo merece perfiles/snapshots distintos o carga explícita según perfil. Si cada trabajo RAW restaura primero un snapshot Turbo para luego descargar Turbo y cargar RAW, paga trabajo inútil cada vez. No mantener ese patrón por comodidad. Un límite de un contenedor por clase/perfil tampoco garantiza una sola GPU entre todos: coordinar exclusión/cola compartida si ese es el objetivo.

La LoRA de dos pasos es para Turbo; RAW con Turbo-LoRA a peso reducido es un experimento distinto, no un preset garantizado. El tamaño publicado RAW FP8 scaled es 13.1 GB, no 7 GB. [Archivos Comfy-Org](https://huggingface.co/Comfy-Org/Krea-2/tree/main/diffusion_models), [autor de la LoRA dos pasos](https://huggingface.co/lvladikov/Krea2-Turbo-Distill-2step-LoRA).

El tiempo entre preview y final puede hacer conveniente una reserva breve en ciertos usos, pero el requisito del usuario prioriza apagar tras cada trabajo. Mantener single-use como configuración inicial y comparar costo total antes de cambiar esa decisión. No reintroducir una ventana de 15–20 segundos como requisito obligatorio.

## Decisión propuesta tras la revisión

API HTTPS preservada; Telegram añadido mediante CPU; generación serial y resultados persistentes; cierre GPU tras trabajo; conversión CPU a FP8 scaled como candidato preferido para reducir también archivo; fp8 fast como comparación; TAE para previews y VAE final actual preservado. Preview opcional, sin forzar dos generaciones por imagen. Ningún tiempo de ahorro se presenta como medido.
