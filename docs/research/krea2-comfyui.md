# Krea 2, previews y ComfyUI: investigación

Fecha de consulta: 15 de noviembre de 2026. Fuentes primarias; sin benchmarks propios ni ejecución GPU. Las propuestas de rendimiento son hipótesis para medir, no tiempos prometidos.

## Recomendación para este uso personal

Mantener un único checkpoint Turbo y dos perfiles: borrador con adaptador de destilación a dos pasos, y final con los LoRAs personales y ocho pasos. La selección humana entre ambos debe ocurrir fuera de una llamada GPU activa. Un preview no debe provocar automáticamente el final: solo se paga ese trabajo si se elige generarlo.

Para comprobar identidad, empezar con previews de la misma resolución y relación de aspecto del final. Una versión de 512–768 px puede servir para cribar prompts, pero cambia el espacio de ruido y la composición: conservar seed no garantiza conservar la imagen. No presentar la preview como una promesa exacta del final. Son dos generaciones distintas; cambiar número de pasos, adaptadores o resolución cambia el resultado.

## Qué modelo está realmente instalado

La receta oficial de Krea Turbo utiliza ocho pasos y `mu=1.15`; Diffusers expresa ausencia de CFG como `guidance_scale=0.0`. El repositorio oficial ofrece además un camino SGLang. Esto da alternativas al motor Comfy, pero no demuestra que migrar a ellas mejore un worker pequeño. [Krea: model card](https://huggingface.co/krea/Krea-2-Turbo).

Comfy documenta un flujo Turbo nativo con archivos de Comfy-Org: `krea2_turbo_fp8_scaled.safetensors`, `qwen3vl_4b_fp8_scaled.safetensors`, `qwen_image_vae.safetensors`. Explica que los LoRAs entrenados en RAW se usan para inferencia Turbo. No hace falta instalar nodos de edición o ControlNet para ese flujo. [Tutorial oficial Comfy](https://github.com/Comfy-Org/docs/blob/main/tutorials/image/krea/krea-2.mdx).

No encontré una fuente pública inequívoca para el nombre exacto `HomoFidelis_Krea2_Turbo_BF16_v1`. No debe asumirse que sea idéntico al checkpoint oficial ni que cualquier benchmark oficial se transfiera. Antes de cambiar precisión, sampler o decoder: registrar ruta exacta, tamaño, SHA-256 y metadata safetensors; conservarlo como referencia visual. No sustituirlo silenciosamente por Turbo oficial.

## Preview de dos pasos: existe un adaptador específico

El autor de `lvladikov/Krea2-Turbo-Distill-2step-LoRA` publica `krea2_turbo_2step_rank_64_lora_comfyui.safetensors`, un workflow con nodos de serie y una versión diferente para Diffusers. Recomienda ComfyUI `steps=2`, `cfg=1.0`, `euler/simple`, strength `1.0`; el entrenamiento usa Turbo con shift 1.15. Su finalidad declarada son borradores rápidos. No basta reducir a dos pasos un LoRA de personaje. [Modelo e instrucciones del autor](https://huggingface.co/lvladikov/Krea2-Turbo-Distill-2step-LoRA).

Propuesta a validar: cargar el adaptador de destilación junto a los LoRAs personales solo en el perfil preview; retirarlo en el perfil final nativo. La combinación sobre este fine-tune particular no está validada. Probar caras, anatomía, ropa y trigger words antes de adoptarla. El ahorro afecta sobre todo al denoising; cargar modelos, texto y decodificar siguen costando tiempo. No trasladar los tiempos Apple Silicon del autor a una L40S. El adaptador publicado se identifica como checkpoint `chk00051195` y debe fijarse por revisión/hash para reproducibilidad. [Misma fuente](https://huggingface.co/lvladikov/Krea2-Turbo-Distill-2step-LoRA).

Un adaptador de cuatro pasos puede ser un punto intermedio para renders finales, pero requiere su propia comparación con ocho pasos sobre los LoRAs personales. [Repositorio del autor](https://huggingface.co/lvladikov/Krea2-Turbo-Distill-4step-LoRA).

## Decodificación: ganar tiempo después del sampler

Comfy incorpora referencias a `lighttaew2_1` en el formato latente Wan21 y reconoce TAEs de vídeo en su infraestructura de preview. Eso permite investigar un decoder pequeño sin agregar un paquete grande de nodos. Hay que respetar el formato latente y la dimensión temporal, aunque se genere una sola imagen. [Formatos latentes oficiales](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/latent_formats.py), [preview oficial](https://github.com/Comfy-Org/ComfyUI/blob/master/latent_preview.py).

Hay un reporte reproducible de colores lavados en previews `lighttaew2_1` para Krea2: el sampler entrega latentes normalizados y el decoder espera el espacio original. El reporte corresponde a Comfy 0.35 y un master de septiembre de 2026; es evidencia de una integración a comprobar, no prueba de que todas las revisiones actuales fallen. Para un endpoint propio conviene decodificar el resultado final de los dos pasos mediante la ruta VAE correcta y contrastarlo visualmente con el VAE de referencia. [Issue Comfy #16593](https://github.com/Comfy-Org/ComfyUI/issues/16593).

`ModelTC/ComfyUI-LightVAE` ofrece LightVAE y LightTAE. Sus cifras de aceleración se midieron con vídeo de 81 frames en H100, no con una imagen Krea2 en L40S. El README instala también LightX2V y WanVideoWrapper: para un worker mínimo ese coste de dependencias puede ser excesivo si la ruta nativa ya carga el decoder necesario. [Repositorio del autor](https://github.com/ModelTC/ComfyUI-LightVAE).

El proyecto LightVAE más reciente mantiene interfaces latentes compatibles para Wan, pero el nodo Comfy que documenta actualmente es H3-only. No confundir ambos repositorios ni instalarlo suponiendo soporte directo para Krea2. [ModelTC/LightVAE](https://github.com/ModelTC/LightVAE).

El modo TAESD de Comfy muestra el progreso de una generación activa. Es distinto de producir una imagen independiente a dos pasos, devolverla y liberar el worker. No resuelve por sí mismo el tiempo GPU ocioso mientras se decide un final.

## Atención y precisión en L40S

SageAttention documenta soporte Ada FP8 con CUDA >=12.4 y un selector automático de kernels. También aconseja reemplazar únicamente la atención DiT para modelos de imagen/vídeo, en vez de parchear globalmente todo SDPA. La publicidad de aceleración de kernels no equivale al mismo factor en una imagen completa. [SageAttention, autores](https://github.com/thu-ml/SageAttention).

Existe un issue abierto específico de L40S/sm89 en Modal con SageAttention 2.2.0: compilación aparentemente exitosa seguida de instrucción CUDA ilegal, o fallo al compilar. Es un reporte de usuario sobre una combinación concreta, no una prohibición universal. Recomiendo baseline SDPA y después probar una compilación Sage fijada, con salida comparada y fallback explícito. Un error CUDA ilegal puede inutilizar el contexto; un `except` no siempre basta para continuar usando GPU. [SageAttention #360](https://github.com/thu-ml/SageAttention/issues/360).

Comfy dispone de opciones nativas `--use-sage-attention` y `--use-pytorch-cross-attention`. Para el uso embebido hay que establecer los argumentos que su código consulta antes de importar los módulos afectados; no asumir que una variable inventada activa el backend. [Argumentos oficiales](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/cli_args.py).

Mantener BF16 como referencia y medir FP8 scaled oficial o una cuantización compatible del mismo fine-tune. Reducir pesos puede disminuir lecturas de almacenamiento, tamaño del snapshot y offload; la velocidad depende del kernel efectivo y del parcheo LoRA. Forzar `weight_dtype=fp8_e4m3fn` a pesos BF16 no equivale a seleccionar un checkpoint con escalas de cuantización calibradas. La calidad debe medirse con los LoRAs reales.

## Compile y cachés: después de corregir el camino básico

En el worker actual no aparece una llamada `torch.compile`; `compileall` genera bytecode Python y no compila el transformer. Un comentario que promete conservar kernels compilados no establece que existan. Además, el warmup dummy usa otra forma que la imagen final, por lo que no valida que el primer render esté preparado.

PyTorch ofrece cachés de compilación en disco y mecanismos para reutilizar artefactos. Deben considerarse parte del conjunto de versiones, hardware y formas del modelo; un directorio `/tmp` nuevo por contenedor no es una caché persistente entre contenedores. Medir costo de compilación inicial, restauración y recompilación al cambiar resolución/LoRAs. [Cachés de torch.compile](https://docs.pytorch.org/tutorials/recipes/torch_compile_caching_tutorial.html).

Comfy-WaveSpeed ofrece First Block Cache y Compile Model+. La existencia del nodo no establece soporte efectivo para Krea2 ni su rentabilidad a dos pasos. La compilación puede evaluarse separadamente de la aproximación por caché. [Repositorio del autor](https://github.com/chengzeyi/Comfy-WaveSpeed).

TeaCache decide reutilización entre timesteps a partir de embeddings temporales. Con dos evaluaciones queda muy poco trabajo redundante y cada aproximación puede pesar más en la calidad. Es una inferencia técnica: no encontré un benchmark primario que demuestre beneficio Krea2+LoRAs a dos pasos. No lo pondría en la primera implementación. [TeaCache, autores](https://github.com/ali-vilab/TeaCache).

SPEED aumenta progresivamente la resolución del latente durante el muestreo. Su nodo muestra un benchmark FLUX y presets flux/wan21; no prueba compatibilidad con el adaptador de dos pasos. Cambia la trayectoria sobre la que se entrenó la destilación. Dejarlo como experimento posterior para finales, sin prometer su aceleración en Krea2. [ComfyUI-SPEED](https://github.com/ruwwww/ComfyUI-SPEED).

## Matriz de medición propuesta

1. Mismo checkpoint actual, LoRA personal, seed, prompt y resolución; baseline SDPA, ocho pasos.
2. Mismo conjunto, dos pasos más adaptador de destilación; verificar que todos los pesos LoRA pertinentes se aplican.
3. Mantener sampler y cambiar solo decoder para previews; evaluar color, cara y detalles.
4. Mantener decoder y comparar atención SDPA/Sage; sincronizar CUDA al medir etapas.
5. Comparar BF16 y FP8 compatible; registrar VRAM/RAM pico, transferencias y calidad.
6. Solo entonces medir compile con resoluciones fijas y restauración real de snapshot.

Separar cold start, restore, lectura LoRA, aplicación LoRA, texto, denoise, VAE y retorno; registrar tiempo total facturable y fallos, no solo segundos del sampler. Evaluar varias semillas y prompts con caras pequeñas, varias personas, manos y texto. No ejecutar estas pruebas automáticamente sin un presupuesto de GPU definido.
