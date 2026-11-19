# Aclaraciones: FP8 fast, TAE, HTTP y elección de previews

Consulta: 15 de noviembre de 2026. Complemento de `optimizacion-worker-krea2.md`. No se convirtió ningún checkpoint ni se ejecutó una GPU.

## FP8: ejecución y archivo son decisiones distintas

El usuario prefiere `fp8_e4m3fn_fast`. El UNETLoader actual traduce esa opción a `torch.float8_e4m3fn` y `fp8_optimizations=True`. No existe un dtype PyTorch llamado `fp8_e4m3fn_fast`: es un preset del loader. La alternativa `fp8_e4m3fn` usa el mismo tipo de pesos sin ese flag de optimización. [Código ComfyUI](https://github.com/Comfy-Org/ComfyUI/blob/master/nodes.py).

Cargar BF16 con ese preset no escribe un nuevo archivo reducido. Puede disminuir residencia de pesos y habilitar operaciones distintas, pero el archivo BF16 original sigue existiendo y se tiene que leer para convertirlo. Para reducir almacenamiento y lectura repetida hace falta crear y conservar un checkpoint cuantizado una vez. Esa preparación puede ejecutarse en CPU con RAM suficiente; no asumir que requiere tener GPU local. La conversión debe conservar las claves/metadatos que Comfy utiliza para reconocer Krea2 y validarse con las LoRAs personales.

No confundir el casteo directo con FP8 escalado: este último incluye información de cuantización/escalas y su tratamiento depende del loader. Para un archivo escalado publicado, conservar su información y comenzar por `weight_dtype=default`; probar la variante fast como experimento separado con la versión Comfy fijada. La implementación reconoce configuración cuantizada y separa dtype de almacenamiento y de cómputo. [Loader](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/sd.py).

Comfy-Org publica RAW BF16 de 26.3 GB y RAW FP8 scaled de 13.1 GB, además de Turbo en ambos formatos. Son tamaños de los archivos publicados, no medición de memoria total ni garantía de velocidad. Cambiar HomoFidelis por Turbo oficial no es convertir HomoFidelis: perdería sus modificaciones. [Archivos oficiales para Comfy](https://huggingface.co/Comfy-Org/Krea-2/tree/main/diffusion_models).

## El TAE concreto solicitado

`AcademiaSD/TAE-Krea-2` publica `TAE_Krea2_AcademiaSD.safetensors`, decoder pequeño para previews. Su autor documenta entradas `x0` normalizadas según Wan21, salida RGB y uso mediante `Model Preview Override` de KJNodes, con archivo en `models/vae_approx`. La selección TAESD nativa de Comfy no elige automáticamente este archivo. Es decoder-only y el autor indica usar el VAE real para la imagen final. Su cifra de aproximadamente 12 ms se midió en RTX 5080; no atribuirla a L40S. [Ficha del autor](https://huggingface.co/AcademiaSD/TAE-Krea-2).

El teacher del TAE es `krea2RealVae_v10.safetensors`; no está establecido por esa ficha que sea idéntico al Wan 2.1 que usa el worker. Para la salida final se conserva el VAE actual del usuario como referencia y se compara calidad antes de cambiarlo. El flujo oficial Comfy utiliza `qwen_image_vae.safetensors`, lo que tampoco implica que debamos sustituir automáticamente el VAE personalizado. [Comfy-Org](https://huggingface.co/Comfy-Org/Krea-2).

Dos integraciones posibles: nodo KJNodes fijado por commit si se usa servidor/workflow Comfy, o decoder explícito en el motor Python. En el segundo caso respetar normalización y formato del tensor. No alimentar directamente cualquier salida del KSampler suponiendo que está en el mismo espacio que el `x0` del callback; comprobar transformaciones del formato latente.

TAE no acelera los pasos del transformer. Una preview de dos pasos sigue necesitando un muestreo corto apropiado; alternativamente TAE puede mostrar una preview durante el muestreo final sin que se ejecute otro trabajo de difusión. Esta última alternativa no permite revisar la imagen tranquilamente con GPU apagada y después continuar como si no hubiera pasado nada.

## HTTP primero, Telegram como otro cliente

La misma API CPU puede atender web y un webhook de Telegram. La elección de interfaz no altera el cómputo del denoiser; el ahorro principal viene de invocar GPU únicamente para generación. Diseño propuesto: CPU valida usuario/configuración, registra trabajo, invoca GPU, recibe resultado y comunica el resultado al cliente. No instalar el bot dentro de la clase GPU.

Si se implementa Telegram: validar `secret_token`, restringir a los IDs del usuario, registrar `update_id` de forma idempotente y devolver respuesta HTTP tras aceptar duraderamente el trabajo. Telegram reintenta webhooks que fallan; un duplicado no debe facturar otra generación. El resultado se envía después mediante Bot API, y la notificación se puede reintentar sin repetir GPU. [Webhook oficial](https://core.telegram.org/bots/api#setwebhook).

El usuario confirmó que Telegram será un añadido y no un reemplazo de la API HTTPS. No se creó bot, webhook ni se enviaron mensajes. Un bot simplifica uso móvil; la web ofrece mejor selección visual y comparación de muchas LoRAs. Ambas interfaces comparten backend.

Catálogo LoRA en CPU, con IDs y rutas inequívocas, trigger words, versión/hash y pesos. El trabajo copia esa configuración completa para no depender de selecciones mutables del bot. Los archivos se conservan en Volume; GPU carga únicamente los adaptadores pedidos. El adaptador de destilación es una configuración adicional del preset preview y no reemplaza las LoRAs personales.

## RAW y Turbo como perfiles explícitos

Propuesta: perfiles `homofidelis-fast` y `raw-fp8-scaled`, cada uno con archivo, precisión, receta de muestreo, VAE/encoder y snapshot identificado. Cargar solo el perfil elegido, sin mantener dos modelos residentes innecesariamente. Se puede parametrizar la clase por perfil o separar clases; ambas opciones requieren limitar el total de trabajos GPU si se quiere como máximo una GPU global. Un límite de un contenedor por perfil no garantiza ese límite global.

RAW FP8 scaled está publicado. Sin embargo, RAW no es Turbo en otro formato: es el checkpoint anterior al post-training/destilación. Krea lo presenta principalmente como base de entrenamiento y muestra inferencia de 52 pasos con CFG 3.5. Es una receta de referencia, no un ajuste obligatorio para todos los fine-tunes. Sus parámetros y costos deben evaluarse separadamente. [Krea RAW](https://huggingface.co/krea/Krea-2-Raw).

La LoRA de dos pasos investigada fue entrenada para Turbo. Su autor señala que cargarla en RAW es una consecuencia de la arquitectura compartida y no un modo soportado. No activar automáticamente el preset preview Turbo sobre RAW. Si se usa Turbo para explorar antes de un final RAW, presentar el borrador como orientación general, no predicción de la misma imagen. [Autor del adaptador](https://huggingface.co/lvladikov/Krea2-Turbo-Distill-2step-LoRA).

## Elegir previews según el uso

Recomendación actual: **final directo por defecto y preview opcional para explorar prompts/pesos de LoRAs**. Si el usuario ya está conforme con la latencia/calidad del flujo final, no imponer dos ejecuciones a cada imagen. El TAE sigue siendo útil para previews de progreso si el motor y el canal HTTP las transportan.

Para selección humana con GPU apagada: preview independiente, persistir resultado/configuración y cerrar; una final posterior restaura otro worker. Si se exploran varias opciones, un pequeño lote de previews por llamada amortiza carga y restaura una vez para ese lote.

La decisión económica depende de la fracción de borradores descartados y del costo total, incluido arranque. Usar la fórmula del informe principal. No se ha establecido todavía si dos pasos más TAE son suficientemente más baratos que el final actual del usuario.
