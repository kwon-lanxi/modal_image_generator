# Modal: GPU bajo demanda para Krea2 personal

Investigación de fuentes primarias consultadas el 15 de noviembre de 2026. No se ejecutó ninguna GPU, despliegue ni benchmark. Las cifras son tarifas publicadas, no una factura estimada con tiempos medidos del modelo.

## Decisión propuesta

Mantener Modal y ComfyUI como motor Python por ahora. Separar la API que consulta estado, lista LoRAs y entrega archivos del proceso GPU. La GPU recibe únicamente trabajos de generación; usar un solo contenedor máximo y evaluar **`single_use_containers=True`** para terminar tras cada llamada. La alternativa reutilizable tiene ventana de apagado de dos segundos. Evaluar snapshots contra arranque normal, sin prometer un cold start específico.

Esto responde al uso personal esporádico: no se paga una GPU mientras el usuario decide qué preview le gusta. La consecuencia es que confirmar una imagen más tarde puede exigir restaurar otro contenedor. Es una decisión de costo/latencia, no existe conservación gratuita de VRAM residente.

## Apagado y facturación

Modal escala a cero por defecto. `min_containers=0`, `buffer_containers=0` y `max_containers=1` expresan el perfil de un solo usuario sin reserva adicional. `scaledown_window` es el máximo tiempo inactivo del contenedor al reducir capacidad. [Autoscaling](https://modal.com/docs/guide/scale).

La documentación admite `scaledown_window` entre **2 y 1200 segundos**, con valor predeterminado de 60. Los recursos usados durante la espera, incluida la reserva GPU, **se facturan**. Por tanto, `scaledown_window=2` es el ajuste documentado más próximo al apagado inmediato; no corresponde prometer que devolver la respuesta apaga instantáneamente el dispositivo. El arranque y la inicialización también forman parte de la vida del contenedor; deben medirse junto con la inferencia. [Cold starts](https://modal.com/docs/guide/cold-start).

Existe una opción distinta y más ajustada al pedido: **`single_use_containers=True`**, admitida por `@app.cls` y `@app.function`. Hace que el contenedor se cierre después de procesar una entrada. `max_inputs=1` en esos decoradores es su nombre anterior, deprecado; el cambio de nombre llegó en SDK 1.3.4. No confundirlo con `@modal.concurrent(max_inputs=1)`, que solo limita trabajo simultáneo. [Referencia App](https://modal.com/docs/sdk/py/latest/App), [release notes](https://modal.com/docs/sdk/py/releases).

La variante de una sola llamada usaría un método GPU que completa **todo un trabajo**, incluido un lote corto de previews si se solicita. La siguiente llamada final necesita otro arranque/restauración. Guardar resultados antes de devolver; no depender de RAM para recuperar el preview. La referencia expone snapshots y single-use en la misma clase de configuración y no documenta una exclusión mutua, pero eso no constituye una prueba de restauración de este motor. Validar ambos juntos. El contrato es cierre tras una entrada, no latencia de liberación física exactamente cero. No hace falta matar el proceso manualmente.

La facturación no exige bloques mínimos de minutos. Es por cómputo usado o solicitado. CPU y memoria se cobran por el mayor entre reserva y uso; `cpu=4.0` son cuatro núcleos físicos y `memory=32768` son 32 GiB. Reducir reservas a ciegas puede hacer más lento el arranque y no evita cobrar un pico real de memoria. [Facturación](https://modal.com/docs/guide/billing), [recursos](https://modal.com/docs/guide/resources).

### Tarifas verificadas

| Recurso | Tarifa publicada |
|---|---:|
| L40S | USD 0.000542/s = USD 1.9512/h |
| H100 SXM5 | USD 0.001097/s |
| RTX PRO 6000 | USD 0.000842/s |
| CPU | USD 0.0000131/núcleo físico/s |
| RAM | USD 0.00000222/GiB/s |
| Volumes | USD 0.09/GiB/mes |

Starter publica USD 30 de crédito mensual y 1 TiB mensual de egreso incluido. Comprobar condiciones y tarifa antes de ejecutar. [Precios](https://modal.com/pricing).

Con la reserva actual de 4 CPU y 32 GiB, el subtotal teórico es `0.000542 + 4*0.0000131 + 32*0.00000222 = 0.00066544 USD/s`, antes de uso superior, almacenamiento, red u otros cargos. Ejemplos aritméticos: 60 s = USD 0.03993; 2 s = USD 0.00133. No son tiempos de generación medidos.

El archivo usa por defecto `BEAM_GPU_PRICE_PER_SEC=0.000191667`, por lo que su cálculo interno subestima aproximadamente **2.83 veces la tarifa GPU** vigente. Sus tarifas CPU y RAM también difieren. El encabezado habla de cinco minutos de keep-warm, pero el decorador real establece 60 segundos. No usar esos comentarios ni el cálculo embebido para decidir costos.

Para comparar GPUs se usa costo total por imagen: una H100 tendría que completar todo el tramo facturado en aproximadamente la mitad del tiempo GPU de una L40S para compensar su tarifa GPU. El arranque y los recursos CPU/RAM alteran ese umbral. La L40S ofrece 48 GB y Modal la recomienda como punto de partida de inferencia. [Selección GPU](https://modal.com/docs/guide/gpu).

## Qué checkpoint conviene guardar

**Pesos en Volume:** sobreviven al apagado; Modal recomienda este almacenamiento para modelos. Se descargan una vez mediante una función CPU y luego se leen al iniciar cada contenedor. Guardarlos en la Image ofrece rendimiento similar y menos flexibilidad. No descargar desde Hugging Face dentro de cada arranque GPU. [Almacenamiento de pesos](https://modal.com/docs/guide/model-weights).

**Snapshot de memoria:** GPU Memory Snapshots sigue en Alpha. Captura CPU, CUDA y VRAM mediante `enable_memory_snapshot=True` y `experimental_options={"enable_gpu_snapshot": True}`. Inicialización en `@modal.enter(snap=True)`; recursos que deban renovarse, en `snap=False`. Solo se crean para Apps desplegadas. No acelera necesariamente lectura de pesos: utiliza el mismo filesystem que Volumes y puede agregar sobrecarga. Sirve especialmente para imports, compilación y warmup. Cambiar archivos del Volume no actualiza el snapshot; cambiar código/configuración sí. Hay restricciones multi-GPU y no-CUDA; `torch.compile` puede requerir `TORCHINDUCTOR_COMPILE_THREADS=1`. La guía no garantiza un tiempo de restauración de Krea2 ni certifica este conjunto exacto L40S/ComfyUI/nodos. [Snapshots](https://modal.com/docs/guide/memory-snapshots).

No encontré una tarifa separada explícita de almacenamiento/restauración de **Function Memory Snapshots** en las páginas consultadas. No extrapolar la tarifa de Volumes o de snapshots de Sandbox ni afirmar que el proceso de creación/restauración no tiene costo. Verificar uso facturado en la cuenta y preguntar a Modal si se necesita desglosarlo.

No son snapshots del último estado después de cada imagen. La propuesta es restaurar una base inicializada, limpia, sin LoRAs del último trabajo, sin peticiones activas ni resultados de otro usuario. Cada nueva petición aplica sus adaptadores. Este es un diseño recomendado, no una capacidad automática de guardar una sesión arbitraria al terminar.

### Subprocesos y servicios

No existe una prohibición general de snapshots con subprocesos: el ejemplo oficial Ministral lanza vLLM con `subprocess.Popen`, prepara el servidor antes del snapshot y lo reactiva después. Eso demuestra el patrón, pero no valida un servidor ComfyUI particular. Mantener el motor actual en el mismo proceso simplifica la prueba. [Ejemplo Ministral](https://modal.com/docs/examples/ministral3_inference).

El mecanismo técnico enumera sesiones CUDA y sus PID antes de hacer checkpoint. Un proceso auxiliar o socket no debe suponerse compatible sin una restauración real probada. [Explicación de ingeniería](https://modal.com/blog/gpu-mem-snapshots).

## Arquitectura recomendada para este archivo

1. **API CPU**: autentica, valida, consulta catálogo LoRA, guarda IDs y estado persistente, devuelve resultados. Envío del trabajo GPU por método remoto; si la respuesta tarda demasiado, devuelve job ID y permite polling en CPU.
2. **Worker GPU**: inicializa exclusivamente ComfyUI y base Krea2; método de generación serial; aplica LoRAs, codifica texto, samplea, decodifica y devuelve bytes o ruta persistente. No contiene rutas de health, catálogo, estadísticas ni lectura de imágenes antiguas.
3. **Persistencia**: pesos/LoRAs en Volumes; resultados y estado fuera de la memoria del contenedor GPU. Una imagen ya terminada se puede recuperar sin volver a iniciar el modelo.
4. **Escala**: un máximo de worker, cero mínimos/buffer, `single_use_containers=True` si se prioriza cerrar tras cada trabajo. Alternativa: reutilizable con ventana de dos segundos. Validar que previews y finales pendientes se encolan en vez de descartarse como engine busy.

La API actual está en la misma clase GPU (`Krea2App.fastapi_app`), por lo que consultar `/healthz` inicia o utiliza esa clase y puede mantenerla activa. Devolver una imagen vieja tampoco debería necesitar una L40S. Esta separación probablemente importa más para ahorro personal que añadir un nodo de optimización.

Los conceptos de API HTTP y funciones de cómputo están soportados por Modal; la división propuesta es una inferencia de arquitectura aplicada al código. [Web Functions](https://modal.com/docs/guide/webhooks), [invocaciones](https://modal.com/docs/guide/trigger-deployed-functions).

## Plan de validación económica

- Primero medir un trabajo normal sin snapshot; luego el mismo con snapshot, registrando desde envío hasta resultado y tiempo facturado del contenedor. No comparar solo el tiempo del sampler.
- Diferenciar primera creación de snapshot, restauraciones posteriores y cambio de versión. Probar después de escalar a cero y con diferentes LoRAs. Verificar que nunca reaparece una LoRA de un trabajo anterior.
- Separar marcas: arranque/imports, lectura de pesos, movimiento GPU, aplicación LoRA, encoder, sampler, VAE, codificación imagen, upload, inactividad final.
- Probar dos pasos a la resolución de preview y una imagen final; incluir el costo de ambas restauraciones si el usuario tarda en elegir.
- Evitar multiplicar variaciones de kernels, compilación y caches antes de obtener la línea base.

El dashboard expone VRAM, utilización y potencia; 100% de GPU utilization solo significa que hubo kernels ejecutándose, no que se aprovechó el 100% de FLOPS. Modal recomienda trazar y perfilar para localizar cuellos de botella. [Métricas GPU](https://modal.com/docs/guide/gpu-metrics).

## Qué no se puede concluir sin GPU

No se verificó que el warmup actual ejecute el camino real de Krea2 ni que su snapshot restaure correctamente. No hay evidencia para afirmar cold starts inferiores a diez segundos, previews de latencia determinada, velocidad de H100 frente a L40S o ahorro exacto de SageAttention/compilación. Las páginas oficiales consultadas no ofrecen un benchmark del checkpoint HomoFidelis usado por este archivo. Esas promesas requieren un pequeño benchmark controlado en la cuenta del usuario.
