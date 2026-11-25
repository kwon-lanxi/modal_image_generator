# MOSS: aceleración, nodos y resolución

## Uso implementado

Los tres bots aceptan `--size 16:9 2mp` o `--size 16:9 2`: megapíxeles decimales reales, con alineación al modelo (16 px Krea/MOSS; 32 Qwen). Predeterminado: 1,5 MP. Rango 0,25–4 MP. `-w`/`-h` permiten dimensiones explícitas. El redondeo cambia ligeramente área y proporción; 2 MP cuadrados son 1408×1408. La API acepta `aspect_ratio` y `resolution_mp` cuando no se envían dimensiones.

MOSS conserva los flags previos. Nuevas recetas:

| Flag | Familia | Pasos | CFG admitido | Implementación |
| --- | --- | --- | --- | --- |
| --turbo-sft-hades | SFT | 8 | 1 | HaDeS SFT v1 |
| --turbo-base-hades | Base | 8 | 1–1,5 | HaDeS Base v1 |
| --turbo-kromoss-base | Base | 8 | 1 | Kromoss DMD2 |
| --turbo-kromoss-sft / --turbo-sft | SFT | 8 | 1 | lodestones SFT DMD8 |
| --turbo-juggernaut | Base | 6 | 1 | Juggernaut checkpoint + LoRA propios |
| --superturbo-sft | SFT | 4 | 1–2 | lvladikov ComfyUI; sigmas 1,.91,.717,.024,0 |
| --hiperturbo-sft | SFT | 2 | 1 | IDM v1 export750, BF16 + adaptador FP32 separado |
| --hiperturbo-base | Base | 2 | 1 | transferencia experimental del adaptador SFT |

`--hyperturbo-*` también funciona. Solo un acelerador por solicitud; conserva LoRAs de estilo. Las familias incompatibles y checkpoints especiales incorrectos producen errores antes de solicitar GPU. CFG superior a 1 en HaDeS Base y lvladikov es experimental, no la receta de destilación de referencia. `--neg` necesita CFG >1.

Ejemplo: `Un jardín bajo la lluvia !estilo:0.7 --turbo-sft --size 3:4 1.5mp --n 6 --upscarler x2`.

La API usa `acceleration: "turbo-sft"`, `upscale: 2`, `n: 6`. El comando `/turbo` muestra las recetas. Para preparar otro volumen luego de desplegar: `cd lanzamiento/moss` y `python prepare_acceleration.py turbo-sft --upscale` (solo CPU; IDM y Juggernaut incluyen sus checkpoints obligatorios). Los pesos y revisiones exactas están en `lanzamiento/moss/moss_service/acceleration_sources.json`.

## Escalado

[FlowUpscaler](https://huggingface.co/TensorForger/FlowUpscaler) trabaja en el espacio latente Flux.2 usado por MOSS. `--upscarler x2` y `--upscaler x2` son equivalentes; x4 encadena dos pases. Se aplica a cada imagen del lote, con ruido derivado de su semilla y del pase, y VAE por tiles. Escala dimensiones: x4 multiplica el área por 16. Límite de salida: 8192 px por lado y 64 Mi píxeles por imagen. No se aplica a Krea/Qwen: sus latentes no son Flux.2. Los 8 ms anunciados por el autor son solo un paso latente 512→1024 en RTX 5090, sin VAE, transferencia ni Telegram.

## Selección de 2 pasos

[IDM](https://huggingface.co/akhaliq/MOSS-Image-IDM-2-Steps) recomienda v1 export750 para texto a imagen; v2 perjudica esa tarea. Requiere tres archivos, la ruta temporal FP32 y un sampler de punto fijo en espacio de datos; un loader LoRA normal pierde parte de la adaptación. Se implementó esta ruta nativa dentro del worker, con limpieza de hooks incluso ante fallo. BF16 obligatorio para estos modos. No encontré evidencia primaria suficiente para recomendar un adaptador exclusivo de Base a 2 pasos; el flag Base está señalado como experimental. Las referencias de Reddit sirven para descubrir candidatos, no para demostrar superioridad universal: [publicación IDM](https://www.reddit.com/r/StableDiffusion/comments/1xk1x12/mossimageidm2steps_is_out/).

El [SFT DMD8](https://huggingface.co/lodestones/MOSS-Image-SFT-8-steps-dmd) es el equivalente por familia más cercano al DMD2 Base existente; el README actual permite cargar `lora.safetensors` en el loader normal. Su sección antigua de dataspace está expresamente marcada deprecated. [lvladikov](https://huggingface.co/lvladikov/MOSS-SFT-Distill-4step-LoRA) publica una exportación ComfyUI compensada a fuerza 1, y CFG2 con negativo como experimento.

## Nodos investigados

| Proyecto | Decisión y motivo |
| --- | --- |
| [Layout Studio](https://github.com/Rexoplanets/layout-studio-moss-image) | Editor HTML; candidato para la miniapp CPU. El nodo Comfy es otro repositorio, `layout-studio-moss-image-comfy-node`. No necesita persistencia GPU. |
| [MossImageTools](https://github.com/hybskgks28275/ComfyUI-MossImageTools) | Editor visual de cajas, grilla, selección y undo. Útil para authoring CPU/navegador; no acelera inferencia. |
| [Prompt Studio](https://github.com/rabbitANDtank/comfyui-moss-prompt-studio) | Editor, compilador y traducción LLM opcional; interesa validación CPU, sin cargar otro LLM por defecto. |
| [GrabTag](https://github.com/blackll7/MOSS-image-edit-grabtag) | Herramienta CPU para regiones/layout y mapas. No asumir autocaption con un VLM sin pesos. |
| [Concept Sliders](https://github.com/quinteroac/ComfyUI-MOSS-Concept-Sliders) | Parches de conditioning/NAG y catálogo de estilos. Un catálogo `sref` puede resolverse en CPU; no confundir con un LoRA de aceleración. |
| [RS MultiCharacter](https://github.com/raspie10032/ComfyUI_RS_MultiCharacter_Sampler) | Cámara requiere su adaptador; pack amplio. Evaluar por separado para evitar dependencias en el cold start. |
| [rapid-moss](https://huggingface.co/darask0/MOSS-Image_4-step_adapter_lora) | Recomendado por IDM para tres archivos. El repo no fue accesible sin autenticación durante la revisión; se usó como fuente ejecutable el código abierto IDM, sin instalar un paquete desconocido. |
| [BSAI](https://github.com/xm6018924/BSAI-MOSS-ComfyUI) | Presets y diagnóstico interesantes. La ruta DMD revisada usa muestreo Comfy; no asumir que sustituye el bucle IDM Q + step_codes. |
| [TE-Speed](https://github.com/tl2012tl/TE-Speed-MOSS) | Distribuye `nodes.pyd` para Windows x64 y Python3.13 portable; no compatible con este worker Linux/Python3.12. |
| [MG-Cache](https://github.com/KidiXDev/ComfyUI-MG-Cache) | Evaluar después con 25–50 pasos; algunos ajustes de su propio benchmark empeoran velocidad/calidad. No activado sobre destilados de 2–8 pasos sin medir. |

No se instalaron todos estos packs: los editores pertenecen a CPU/miniapp y las cachés necesitan medición por modelo. Se mantiene la separación CPU/GPU, sin cron ni contenedores mínimos.

## Validación de esta entrega

Pruebas locales focalizadas: 137 pasan (resolución en tres servicios, ratios de los bots, registro de aceleradores, errores, hooks BF16/FP32 y limpieza, ruido independiente por imagen/pase, variación Krea y regresiones MOSS). Sintaxis JavaScript de los tres playgrounds válida.

Pruebas reales, sin mensajes de Telegram:

- HaDeS SFT, lote de 2 a 512×512, Flow x2: dos PNG de 1024×1024 en L4, semillas distintas, metadatos PNG comprobados desde Volume, subida R2 confirmada.
- IDM SFT v1, 2 pasos a 512×512 en L40S: imagen válida, 1,39 s de muestreo. Se corrigió y probó la entrada BF16 de las capas lineales cuando la modulación promueve el residual a FP32.
- lvladikov SFT, 4 pasos, CFG2 y negativo, Flow x4: salida 2048×2048 en L40S; 4,47 s de muestreo, 6,30 s de escalado, 2,88 s de VAE. Son pruebas funcionales pequeñas, no benchmarks comparables de calidad o resolución predeterminada.

Los tiempos anteriores excluyen adquisición de GPU y creación/restauración del snapshot; la primera llamada completa de cada configuración tomó aproximadamente 80–116 s. El arranque no se vuelve instantáneo por usar un LoRA turbo. Los comandos/webhooks de los tres bots se actualizaron conservando los mensajes pendientes.

- Juggernaut con su checkpoint INT8 y LoRA propios: generación completada en L40S.
- IDM Base BF16: prueba funcional de 2 pasos completada en L4; esto verifica ejecución, no equivalencia de calidad con el entrenamiento SFT.
- Diagnóstico final de los tres bots: 0 actualizaciones pendientes, sin error de webhook y estado accesible.
