# Bot MOSS personal

Bot: https://t.me/moss_imagebot. Abrir /start. Integrado con moss-image-personal-v1: webhook, estado SQLite en moss-telegram-state, sin cron, sin proxy HTTPS a la propia API. Owner 7112930476, chat privado, firma de Mini App y secret de webhook. Secret tg-token-image / TELEGRAM_MOSS_BOT_TOKEN; configuración separada moss-telegram-config. API HTTPS sigue disponible.

Ejemplos:
```
/generar retrato !miestilo:0.7 --turbo --size 3:4 --n 4
/generar retrato --turbo12 --size 16:9
/generar retrato --superturbo --profile base --size 1:1
/generar retrato --model mi-checkpoint.safetensors --profile sft --superturbo
/descargar hf://autor/repo/estilo.safetensors mi-estilo
/descargar_modelo hf://autor/repo/checkpoint.safetensors mi-checkpoint
```

--turbo: DMD2 de data-archetype, 8 pasos, CFG1, LCM/simple, shift 4.4817. --turbo12: anyMODE, 12 pasos, CFG1, Euler/simple. --superturbo: LoRA Akhaliq de 4 pasos a peso1, CFG1, Euler/simple. Los tres modos son mutuamente excluyentes y no admiten negative prompt; las LoRAs de estilo y sus pesos se conservan; máximo8 incluyendo aceleración. Checkpoints nuevos usan la misma infraestructura y snapshots por versiones de archivos. Selección por nombre exacto mediante --model, sin editar presets. Descargas CPU e invalidación de catálogo TTL300s. HF/Civitai .com y .red, nombres personalizados, sin GPU para descargar.

Tamaños idénticos a Krea: 1:1 1328×1328;16:9 1664×928;9:16 928×1664;4:3 1472×1104;3:4 1104×1472;3:2 1584×1056;2:3 1056×1584. -w/-h permiten múltiplos16 dentro del contrato. n1..8 con semillas distintas, álbum si cada PNG<5MB, caption solo semillas; originales con metadatos Volume/R2. Repetir/variar/reenviar sin GPU, entrega incierta no se repite automáticamente. Estado conserva nombre real GPU.

La Mini App reutiliza la biblioteca móvil: búsqueda, filtros, páginas de12, alias, pesos, copia !alias:peso, selección, galería y recetas. Táctiles48px, separación8px, foco visible y alternativa al clipboard.

## Arranque y T4

Selección final del usuario: [L4,L40S]. T4 retirada después de la prueba por su lentitud. T4 generó realmente a256×256: Turbo8 tardó55,5s en la primera ejecución y27,6s en la siguiente; SuperTurbo4 ~22–23s. Pico CUDA en SuperTurbo:13.99GB. No son resultados a resolución completa. T4 no tiene Tensor Cores FP8 como Ada; la carga reducida no significa la misma velocidad que L4.

Optimización: encoder solo texto sin construir torre visual ausente; carga en inference_mode; snapshot compactado; se omite el ID nominal del perfil de la identidad cuando los archivos efectivos son iguales; fuentes GPU permitidas por lista, excluyendo backend/API/storage/UI. La exclusión se verificó en un Sandbox CPU sobre la imagen GPU real: nueve archivos, sin api.py. Editar UI/CPU no debe cambiar la imagen GPU por sus archivos. Cero GPU mínima y uso único, sin aumentar warm window.

Dos llamadas SuperTurbo4 antes del último empaquetado: RPC85.25s /51.76s, método22.37s /22.95s. La diferencia incluye asignación, arranque/restauración y transporte; no es una medición aislada de restore ni tiempo GPU facturable. 8 segundos no están demostrados. La primera carga de un modelo o tipo de GPU crea snapshots; no confundirla con restauración posterior. Ver documentación de Modal: https://modal.com/docs/guide/memory-snapshots.

Validación CPU:242 pruebas combinadas (54 MOSS). Webhook registrado, cero pendientes y sin error Telegram en comprobación; menú Biblioteca confirmado. No se enviaron mensajes de prueba al chat.

Prueba final: T4 completó1328×1328 con SuperTurbo4, pico13,92GB CUDA y149,89s dentro del métodoGPU; por indicación del usuario se retiró T4. La selección final solo permite L4 y L40S. Esta modificación de recursos puede requerir snapshot nuevo. No se generaron más imágenes después de retirar T4.


## JSON layout y negativo
El bot conserva íntegro un objeto JSON de prompt antes de procesar flags/alias: comillas, saltos de línea, números bbox y texto con !/@. Puede ir antes o después de las opciones. Enviar JSON literal sin fences de Markdown. --negative "texto" (alias --neg) añade negativo en modo normal. Con Turbo/SuperTurbo CFG1 se rechaza esa combinación en el bot con explicación, para no descartarlo silenciosamente. La API ya acepta positive_prompt como cadena JSON y negative_prompt como cadena; en modos acelerados su contrato continúa vaciando el negativo.

Formato nativo experimental MOSS: summary.caption e items con label/caption/text/bbox; bbox_format=yxyx, bbox_coord_space=abs_centralized. Coordenadas [y_min,x_min,y_max,x_max], píxeles relativos al centro de la imagen. Para1328², rango±664. No son coordenadas normalizadas0..1 ni un recorte determinista. Fuente: https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE.md#layout-prompts-experimental.


## Alias de checkpoints y semillas (18-11-2026)

La Mini App tiene una pestaña **Modelos**, separada de LoRAs y Galería. Ahí se puede buscar cada checkpoint de `diffusion_models`, asignar/cambiar/quitar un alias y copiar `--model alias`; el ID exacto sigue aceptado. Los alias se guardan en el estado del bot y resuelven al ID del volumen antes de generar.

En esa pestaña también se puede convertir a FP8 escalado o mixto desde CPU y eliminar checkpoints no usados por perfiles. Comandos: `/convertir_modelo --model alias --fp8-scaled` o `/convertir_modelo --model ID --fp8-mixed`. Se usa la referencia FP8 oficial de MOSS Base o SFT según el checkpoint. FP8 mixto conserva en BF16 las capas con más de5% de error, en el formato escalado por capa de ComfyUI. INT8/FP8 Comfy reconocido se dequantiza temporalmente y no se borra el origen cuantizado. BF16/FP16 se elimina solo después de validar y guardar la salida; perfiles que lo referencian y alias se actualizan. No se sobrescriben archivos. Si una conversión o commit falla, el original permanece.

Para lotes `n>1`, cada imagen recibe una semilla independiente generada con `secrets`, sin incremento de una semilla base. Todas son únicas dentro del lote. `--seed` explícita conserva ese valor para la primera imagen y el resto se sortean sin colisión. Una lista `seeds` enviada a la API debe tener exactamente n valores únicos. Repetir conserva la lista del lote original; Nueva variante crea una nueva lista.


## Progreso de descargas en Telegram (MOSS)

Durante /descargar y /descargar_modelo el bot edita el mensaje temporal cada vez que avanza la descarga. Muestra porcentaje, cantidad descargada, tamaño total y velocidad en MB/GB; al terminar, el mensaje de progreso se limpia y queda la confirmación habitual. Los logs estructurados conservan también los bytes exactos.
