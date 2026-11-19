# MOSS Image: samplers y schedulers recomendados

Investigación de fuentes primarias consultadas el **19 de noviembre de 2026**. El alcance es la generación de MOSS Image Base/SFT en ComfyUI y las tres LoRAs turbo que contempla este proyecto. No se modificó código ni se hicieron pruebas de GPU.

## Resumen ejecutivo

No hay una pareja universal que convenga para todos los modos. Para el modelo normal, la referencia oficial es **Euler, 50 pasos, CFG 3 y shift 5**; el workflow nativo de ComfyUI usa **Euler + `sgm_uniform`**. Para cada LoRA destilada conviene respetar su trayectoria entrenada: cambiar el sampler, scheduler, shift, número de pasos o CFG puede degradar el resultado aunque la combinación sea válida en ComfyUI.

| Modo | Sampler | Scheduler | Pasos | CFG | Shift | Lectura práctica |
|---|---|---|---:|---:|---:|---|
| Base/SFT normal | `euler` | `sgm_uniform` como referencia del workflow nativo | 50+ | 3 (3–6 recomendado) | 5 | Punto de partida con respaldo oficial; más lento que las LoRAs pocas-pasos. |
| `--turbo` DMD2 data-archetype | `lcm` | `simple` | 8 | 1 | 4.4817 | Receta específica: LCM re-ruido fresco entre pasos. No intercambiar por Euler/otro schedule. |
| `--turbo12` anyMODE | `euler` (la tarjeta también indica `flow`) | `simple` | 12 | 1 | La tarjeta no pide un cambio | La LoRA se entrenó contra el schedule discreto `simple`; preferir `euler` para esta API. |
| `--superturbo` Akhaliq Euler 4-step | `euler` | `simple` | 4 | 1 | Usar el shift base si no hay instrucción versionada distinta | Es Euler-native y usa el sampler estándar. La tarjeta actual aconseja 6–8 pasos por encima de 1024²; verificar esa sugerencia contra el commit exacto instalado. |

La tabla distingue settings publicados de decisiones prácticas. No es un benchmark propio del worker ni promete calidad/latencia equivalente en L4 o L40S.

## Modo normal Base/SFT

La guía de OpenMOSS indica para Base **50 o más pasos**, CFG predeterminado 3 (rango 3–6), shift 5 y Euler como sampler por defecto. El README oficial documenta Flow-matching Euler, 50 pasos, CFG 3, shift 5 y negative-branch skip-layer 22. El workflow nativo de ComfyUI concreta Euler + `sgm_uniform` para sus 50 pasos. La recomendación práctica es usar ese workflow como baseline y comparar cualquier cambio de scheduler manteniendo prompt, seed, resolución y CFG idénticos. [OpenMOSS User Guide](https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE.md#inference-parameters), [README oficial](https://github.com/OpenMOSS/MOSS-Image/blob/main/README.md#-sampling-settings), [workflow ComfyUI oficial](https://github.com/OpenMOSS/MOSS-Image/blob/main/comfyui/README.md#workflow)

- **Euler**: recomendación explícita del equipo; es el baseline que facilita atribuir diferencias al prompt o al schedule.
- **`sgm_uniform`**: schedule usado por el workflow ComfyUI oficial para MOSS. Es una opción bien respaldada para comparar con la configuración actual del worker.
- **`simple`**: ComfyUI obtiene puntos de la lista de sigmas del modelo en intervalos discretos. Es obligatorio en las recetas publicadas de las LoRAs turbo, pero no encontré una recomendación oficial de que sea superior a `sgm_uniform` para el MOSS normal.
- **CFG**: 3 es el valor oficial inicial. La guía plantea subir hacia 3–6 cuando el prompt corto necesita más guía. CFG 1 pertenece a las destilaciones Turbo, no debe extrapolarse al modelo normal.
- **Shift y SLG**: shift 5 es el valor nativo por defecto. La guía dice que SLG puede mejorar detalle, y cita la variante de dos pasadas con salto no condicional de la capa 22; apagar SLG puede verse más realista, pero puede colapsar algunos detalles. SLG es guidance adicional, no un sampler o scheduler.

Los documentos oficiales presentan principalmente Base en la guía de parámetros. Para SFT normal, esto es un punto de partida prudente de la misma familia, no una garantía de óptimo específica para cada checkpoint SFT.

### Diferencia con el worker actual

El worker actual arranca el modo normal con `euler` + `simple`, 50 pasos y CFG 3; su contrato todavía no admite `sgm_uniform`. El workflow oficial de OpenMOSS para ComfyUI usa `euler` + `sgm_uniform` y ModelSamplingSD3 con shift 5. Esto convierte a `euler/sgm_uniform` en un candidato bien respaldado para comparar en el modo normal, pero no demuestra por sí solo que genere mejor en el worker: su integración y su variante de ComfyUI deben coincidir. No cambiar nada automáticamente a partir de esta revisión.

## LoRAs aceleradas: seguir la tarjeta de cada una

### DMD2 de data-archetype — 8 pasos

Su tarjeta dice que la LoRA fue entrenada para **un sampler concreto** y que otros samplers empeoran el resultado. La receta es `lcm` + `simple`, ocho pasos, CFG 1, fuerza 1, sin negative prompt y shift 4.4817 mediante el nodo ModelSamplingAuraFlow. `lcm` toma la estimación limpia y reinyecta ruido fresco al avanzar al próximo nivel; por eso no es simplemente Euler con menos pasos. La LoRA se entrenó para una secuencia concreta de tiempos y la escala es parte de esa receta. [Tarjeta DMD2](https://huggingface.co/data-archetype/moss_image_base_dmd2), [workflow DMD2](https://huggingface.co/data-archetype/moss_image_base_dmd2/blob/main/workflows/moss_image_base_dmd2_v2_8step.json)

**Ventajas:** ocho forwards en lugar de los 50 guiados de referencia; la guía destilada permite CFG 1; conserva compatibilidad declarada con Base, SFT y la mayoría de sus fine-tunes.

**Desventajas:** queda muy atada a sampler, schedule y shift de entrenamiento; la tarjeta dice explícitamente que otras elecciones dan peor calidad. No tiene negative prompt efectivo en su receta CFG 1. Sobre todo, la tarjeta de ComfyUI advierte que el LoRA debe aplicarse sin fusionarlo a los pesos BF16: recomienda **LoraLoaderBypassModelOnly** porque la delta puede ser menor que el redondeo BF16. Eso merece revisarse aparte antes de interpretar una imagen mala como problema del sampler. [Notas de muestreo y carga DMD2](https://huggingface.co/data-archetype/moss_image_base_dmd2#comfyui)

### anyMODE — 12 pasos

La tarjeta recomienda 12 pasos, CFG 1, LoRA fuerza 1, scheduler `simple` y sampler Euler (o `flow`). El entrenamiento declara alineamiento con la secuencia discreta nativa `simple`; por eso usar otra curva no tiene respaldo del autor. El archivo estándar instalado tiene metadata alpha 128 y la tarjeta recomienda fuerza 1 en ComfyUI. [Tarjeta anyMODE](https://huggingface.co/anyMODE/moss-image-12step-turbo-lora)

**Ventajas:** un punto medio entre la calidad/latencia del modelo normal y los modos de 4 u 8 pasos; no pide un sampler personalizado y usa Euler/Simple nativos.

**Desventajas:** 12 evaluaciones del DiT siguen siendo más que 8 o 4; CFG 1 significa que no es comparable al CFG 3 normal. La tarjeta documenta que el schedule `simple` está alineado al entrenamiento, así que no conviene reemplazarlo por Karras, normal o `sgm_uniform` sin pruebas visuales controladas.

### Akhaliq — 4 pasos Euler-native

La tarjeta actual describe la versión Euler-native con `euler` + `simple`, cuatro pasos, CFG 1 y fuerza 1. También advierte no usar la receta normal de 25–50 pasos con CFG 2.5–3 junto a esta LoRA. En la resolución mayor a 1024², la misma tarjeta sugiere probar 6–8 pasos, de modo que cuatro pasos no es automáticamente la mejor opción a más de 1 MP. Hay que confirmar que las recomendaciones de la tarjeta vigente corresponden al commit exacto que el worker instaló. [Tarjeta Akhaliq](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps), [instrucciones ComfyUI](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps#comfyui)

**Ventajas:** receta simple con sampler estándar; la tarjeta declara que funciona también sobre Base, aunque con aspecto algo más suave.

**Desventajas:** requiere CFG 1 y pasos reducidos; su recomendación de subir a 6–8 en más de 1024² sacrifica parte del ahorro de cuatro pasos. No usar `lcm` por llamarse “turbo”: es Euler-native.

## Qué no asumir de otros samplers

ComfyUI ofrece muchos algoritmos (`dpmpp_2m`, `dpmpp_3m_sde`, `heun`, `euler_ancestral`, `lcm`, entre otros) y schedules (`karras`, `normal`, `beta`, `sgm_uniform`, `simple` y otros). Que aparezcan en el selector solo significa que están disponibles. No encontré recomendaciones MOSS de primer nivel para reemplazar Euler en el modo normal o las parejas concretas de las LoRAs pocas-pasos por DPM++, Karras o ancestral. En una LoRA destilada que aprende una trayectoria, cambiar ambas dimensiones simultáneamente vuelve confuso el resultado.

Como regla de experimentación, mantener fijo el modelo, LoRA y strength, pasos, shift, CFG, resolución y semillas; variar **una sola** dimensión (primero scheduler, luego sampler) y comparar varios prompts. Esto es una propuesta de método experimental, no una receta publicada por OpenMOSS ni un benchmark hecho aquí.

## Términos

- **Sampler**: regla numérica que convierte las predicciones del DiT en la siguiente muestra latente; puede cambiar cómo progresa cada actualización.
- **Scheduler**: selecciona los niveles de ruido/sigmas y, por tanto, los tiempos de denoise que el sampler va a recorrer. ComfyUI implementa `simple` tomando puntos de la lista de sigmas del objeto `model_sampling`; la implementación nativa de MOSS también distingue el shift del modelo.
- **Shift**: transforma el mapeo de tiempos/sigmas; no es otro nombre para scheduler. El workflow base oficial de OpenMOSS usa ModelSamplingSD3 con shift 5. Para su receta DMD2, en cambio, data-archetype fija shift 4.4817 y pide ModelSamplingAuraFlow (o ModelSamplingFlux); advierte que ModelSamplingSD3 escala esos tiempos de forma distinta y puede producir ruido. Esa advertencia corresponde a la receta DMD2, no debe extrapolarse al workflow base de OpenMOSS. [Workflow base oficial](https://github.com/OpenMOSS/MOSS-Image/blob/main/comfyui/moss-image/example_workflows/moss-image-api.json), [receta DMD2](https://huggingface.co/data-archetype/moss_image_base_dmd2#comfyui), [implementación de nodos ComfyUI](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_model_advanced.py)

## Fuentes primarias

1. [OpenMOSS: USERGUIDE.md, parámetros de inferencia](https://github.com/OpenMOSS/MOSS-Image/blob/main/USERGUIDE.md#inference-parameters)
2. [OpenMOSS: README, settings de muestreo](https://github.com/OpenMOSS/MOSS-Image/blob/main/README.md#-sampling-settings)
3. [OpenMOSS: workflow e instrucciones ComfyUI](https://github.com/OpenMOSS/MOSS-Image/blob/main/comfyui/README.md)
4. [data-archetype: MOSS Image Base DMD2](https://huggingface.co/data-archetype/moss_image_base_dmd2)
5. [anyMODE: MOSS Image 12-step Turbo LoRA](https://huggingface.co/anyMODE/moss-image-12step-turbo-lora)
6. [Akhaliq: MOSS-Image-SFT 4-step](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps)
7. [Comfy-Org: implementación de `simple_scheduler` y samplers](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/samplers.py)
8. [Comfy-Org: nodo ModelSamplingAuraFlow](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_model_advanced.py)
