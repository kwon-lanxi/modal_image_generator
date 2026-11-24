# MOSS: investigación de LoRAs turbo — 2026-11-24

Fuentes primarias: cards, archivos y workflows de los autores. Selección para probar; no es un benchmark propio. No se descargaron pesos ni se modificó MOSS. [Manifiesto de 12 archivos verificados, con revisión fijada](moss-turbo-candidates-2026-11-24.json).

| Uso | Candidato prioritario | Configuración / límite |
|---|---|---|
| SFT, detalle a4 pasos | lvladikov chk30000, export ComfyUI | Euler con sigmas manuales; CFG1 oCFG2 experimental |
| SFT, diversidad a4 pasos | HaDeS Turbo Diverse v0.6 | Euler/simple, shift5, CFG1 |
| SFT, texto a4 pasos | HaDeS Turbo v0.6 / akhaliq v2.0 | Comparar sobre prompts propios |
| Base,8 pasos | lodestones/Kromoss DMD2 | Euler/simple, shift5, CFG1 |
| Base/SFT,8 pasos y poco peso | HaDeS v1.0 de cada familia | Base permiteCFG1.5 medido; SFT recomiendaCFG1 |
| Base/SFT, turbo actual | data-archetype v2 | LCM/simple, shift4.4817, CFG1 |
| SFT,12 pasos | anyMODE weightsx2 | Euler/simple, CFG1 |
| Draft rápido | akhaliq3step | Mejor documentado que los experimentos2step |

## Evidencia

### lvladikov

[Card](https://huggingface.co/lvladikov/MOSS-SFT-Distill-4step-LoRA), [detalle de CFG/negativo](https://huggingface.co/lvladikov/MOSS-SFT-Distill-4step-LoRA/blob/main/DETAILED-README.md).

SFT, moss_sft_4step_rank_64_lora_comfyui.safetensors a fuerza1; Euler con sigmas 1,0.91,0.717,0.024,0. No usar simple: cambia los puntos del schedule. La exportación ComfyUI compensa redondeo mediante alpha. Sus comparaciones de detalle/texto son del autor. Entrenado paraCFG1; CFG2 con negativo es experimental, con control parcial de atributos, sin garantizar eliminar elementos centrales. CFG4 sobresatura. La referencia usaBF16: requiere comparación específica con nuestroFP8.

### HaDeS

[Card y pruebas](https://huggingface.co/DeVilDoNia/HaDeS-MOSS-Turbo).

Rank2 y aproximadamente3.5MB por archivo. SFT: Turbo v0.6 para texto y Diverse v0.6 para variedad a4 pasos; SFT v1.0 a8. Base:8steps v1.0 aCFG1 o1.5. Euler/simple, shift5, fuerza1. SFT recomiendaCFG1; subirlo oscurece en sus pruebas. En Base, el negativo a1.5 tuvo poco efecto. Sus comparaciones son pequeñas y no prueban superioridad universal. Separar resultados de las versiones. Poco tamaño reduce lectura de la LoRA, no el modelo base ni garantiza menor cold start.

### lodestones/Kromoss

[Card](https://huggingface.co/lodestones/Kromoss).

8-steps-dmd2-lora.safetensors se destiló deBase y se transfiere aKromoss:8 pasos,CFG1, Euler/simple, shift5, fuerza1; también reporta4 pasos. Sus mediciones favorecenDMD2 sobre suDMD anterior. Rank128 FP32, unos1.2GB; el autor advierte contra fusionar con redondeoBF16 por el pequeño delta. No garantizar el mismo resultado en cualquierSFT/fine-tune.

### data-archetype v2

[Card](https://huggingface.co/data-archetype/moss_image_base_dmd2).

Es nuestroturbo8 actual. Archivo loras/moss_image_base_dmd2_v2_bf16.safetensors:8 pasos,CFG1, LCM/simple, shift4.4817, fuerza1. OrigenBase, transferencia aSFT/fine-tunes y combinación conLoRAs reportada por su autor. V2 usa rank256 y combina estudiantes para equilibrar detalle/diversidad. No cambiarlo aEuler copiando otroDMD2: depende de su renoise. Su recetaCFG1 no utiliza negativo.

### akhaliq

[Card](https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps).

El archivo4step v2.0 usa SFT, fuerza1, Euler/simple yCFG1; el autor también reportaBase y más pasos por encima de1024². Draft3 tiene archivo propio entrenado exactamente para3 pasos. El archivo2step permanece como experimento: describe duplicación/borrosidad de sujetos. Los repos separados2step-v2/v3 carecen de card; el nombre no demuestra calidad. Revisiones archivadas y archivos de main no son intercambiables sin revisar su workflow.

### anyMODE12

[README primario leído por HTTP](https://huggingface.co/anyMODE/moss-image-12step-turbo-lora/raw/main/README.md).

SFT,12 pasos,CFG1, Euler/simple, fuerza1. Recomienda 12_step_turbo_moss_1024_distill_v8_r64_weightsx2.safetensors para no depender del tratamiento dealpha. El proyecto usa el export estándar: comprobar escala antes de sustituirlo. Guidance/negativo delteacher durante entrenamiento no habilita negativo independiente aCFG1. No hay comparación que justifique declararlo ganador universal por tener12 pasos.

## Otros y límites

- [lodestonesSFT8](https://huggingface.co/lodestones/MOSS-Image-SFT-8-steps-dmd): distinguir LoRA estándar/latest y sus fuerzas. Su card separa ruta actual y data-space obsoleto; verificar revisión/workflow.
- [MFD](https://huggingface.co/congruency/MOSS-Image-Turbo-MFD): variantesBase/SFT,4–6/4–8 pasos, exportsComfyUI. Card insuficiente sobreCFG/sampler y comparaciones: candidato experimental.
- [JuggernautDMD2Beta](https://huggingface.co/Kandoo/Juggernaut-Moss-DMD2-LoRA-Beta/raw/main/README.md): especializado enJuggernautINT8,6 pasos recomendados,4–8 posibles, Euler ancestral/simple,CFG1. El autor advierte menor variedad; no lo priorizaría para preservar creatividad deMOSS estándar.
- darask0 rapid-moss devolvióHTTP401. No pude verificar el estado actual.
- [Serenity](https://huggingface.co/malcolmrey/moss-serenity) publica receta12 pasos/CFG4 con su propio ajuste yDMD. No demuestraCFG4 general paraBase/SFT.

CFG1 omite la rama negativa, pero el prompt positivo sigue activo. CFG>1 suele evaluar dos ramas por paso: mayor coste de muestreo, sin duplicar necesariamente el trabajo total. Distinguir recetas recomendadas de experimentos. Para negativo explícitamente documentado priorizaría lvladikovCFG2 experimental; HaDeSBase1.5 tiene negativo débil según sus mediciones.

Los turbos actuales del worker fuerzanCFG1 y rechazan/vacían negativo. Lvladikov exige preset nuevo con sigmas manuales y políticaCFG experimental; descargarlo y activar elturbo actual no basta. Cada preset debe declarar familia, archivo/revisión, fuerza, sampler, shift/sigmas yCFG. Un acelerador por receta; comparar lasLoRAs de estilo/identidad aparte. NuestroencoderHeretic y checkpointsFP8 no están automáticamente cubiertos por losbenchmarks externos.

Comparación propuesta: mismas6 semillas, retratos/manos/múltiples sujetos/texto/layout/escenas oscuras y resoluciones reales1328×1328. Medir lectura/parcheo, muestreo,VRAM y entrega por separado, junto a diversidad y errores. Las recomendaciones de este documento son inferencias de documentación y adecuación alworker; falta benchmark local.
