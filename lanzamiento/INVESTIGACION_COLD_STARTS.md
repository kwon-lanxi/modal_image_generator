# Arranque GPU: objetivo8s sin mantener contenedores calientes

Fecha local:17noviembre2026 (America/Buenos_Aires). Investigación de lectura de código, logs y metadatos existentes; no se reservó GPU ni se redesplegó.

## Evidencia

Captura1:0,953s de cola;23,282s desde asignación hasta ejecución;19,722s de ejecución. Captura2:34m56s de cola;28,504s de arranque;27,963s de ejecución. Los números no identifican por sí solos si se creó o restauró un snapshot.

Los logs Krea2 confirman Restoring Function from memory snapshot a17nov10:10:57 GMT-3; gpu.ready a10:11:19,166. Por tanto el caso23s YA usa restauración, no se corrige simplemente activando snapshots. Otra restauración a10:42:17 da gpu.ready a10:42:27,571: unos10–11s entre marcadores, que no son exactamente el intervalo completo de la UI. Hubo creaciones de snapshot a09:02:46 y09:55:57. El log también registra un fallo de snapshot a07:48:09; no se atribuye causa con el fragmento recuperado.

Metadatos del job814611b92a9d17af5654ae06731bd430: RSS17,73GiB al terminar, picoVRAM25,75GiB; ejecuciónGPU19,5664s; LoRA2,2975s; encode0,3941s; sampling15,3739s; decode1,3871s; transferenciaCPU0,0673s. Estos valores NO son el tamaño real del snapshot ni su huella en el instante de captura. Archivo UNet en Volume26.280.491.512bytes BF16; encoder5.242.481.504bytes; VAE507.684.560bytes. El UNet se carga con fp8_e4m3fn_fast. Tener archivo BF16 no demuestra que se recargue BF16 en cada restauración.

Ambas apps ya usan GPU Memory Snapshots, enter(snap=True) con inicialización y warmup, single_use_containers=True y sin min/buffer containers. Krea carga snapshot y luego recarga dos Volumes en enter(snap=False). Qwen recarga uno. El warmup actual no limpia explícitamente GC/caché CUDA antes de la captura.

## Costos

Tarifa pública L40S0,000542USD/s. Solo GPU:23,28s≈0,01262USD;28,50s≈0,01545USD;8s≈0,00434USD. Ahorro hipotético de15,28–20,5s:0,00828–0,01111USD por solicitud (8,28–11,11USD por1000solicitudes), más CPU/RAM evitadas. No es factura ni sustituye los recursos medidos. Con ejecución constante, los ejemplos reducen GPU total alrededor35–36% al bajar arranque a8s. La carga de aplicación es facturable; la espera sin GPU asignada no representa ese mismo tiempo GPU. CPUWriter en espera sigue vivo y puede facturar CPU/RAM.

## Orden propuesto

1. Separar el módulo e imagen GPU del bot/API, dentro de la misma app (App.include). Actualmente las imágenes GPU incorporan telegram_bot/qwen_telegram y todo el paquete de servicio, incluso fuentes CPU/UI. Cambios de esas fuentes cambian la imagen GPU y pueden invalidar sus snapshots. Reducir esas dependencias evita reconstrucciones evitables; no demuestra por sí solo bajar un restore23s a8s.
2. Instrumentar antes/después de Engine.initialize, warmup y reloads, y medir RSS, CUDA allocated/reserved JUSTO antes de capturar. La duración de restauración de la plataforma se obtiene de Containers; un timer después del restore no la mide.
3. Tras warmup, ejecutar GC y liberar CUDA no ocupada, conservando los pesos residentes. Revisar copias CPU/GPU, caches y buffers descartables. Probar compatibilidad/calidad antes de sustituir producción. No descargar los modelos de GPU a CPU indiscriminadamente: traslada carga al primer pedido y puede ser peor.
4. Comparar restore limpio en varios arranques de la MISMA revisión/configuración: mediana/p90 y costo total. Modal depende del cache local del worker;8s es objetivo experimental, no un SLA. Evitar prewarming artificial como mecanismo permanente.
5. Solo con esas mediciones, comparar GPU snapshot completo contra CPU snapshot+transferencia al restaurar, y/o encoder cuantizado compatible. Ambas opciones pueden empeorar el arranque; no asumir mejoras. Reducir tamaño del checkpoint en disco beneficia lectura/construcción, pero no necesariamente restauración ya materializada.
6. Evaluar reserva RAM32GiB de Krea respecto a consumo real y máximos observados (la medición17,73GiB es un solo punto). Reducir reserva puede ahorrar aunque no reduzca latencia; no hacerlo a ciegas con una sola muestra. Más CPU puede acelerar inicialización pero necesita prueba de costo total.

## Fuentes oficiales

- https://modal.com/docs/guide/memory-snapshots : tamaño de estado/caché local, invalidación al cambiar código/configuración,2–3snapshots por familiaGPU, limitaciones si domina lectura de pesos.
- https://modal.com/docs/guide/cold-start : componentes del arranque y costo de mantener recursos ociosos.
- https://modal.com/pricing : tarifas y facturación durante carga, ejecución e inactividad configurada.
- https://modal.com/docs/guide/project-structure : modularidad y App.include.
- https://docs.pytorch.org/docs/stable/generated/torch.cuda.memory.empty_cache.html : liberar caché desocupada no libera tensores activos.


Implementación posterior: separación GPU en krea_gpu.py/qwen_gpu.py con App.include, fuentes GPU explícitas e include_source=False; compactación y mediciones de memoria, warmup y reloads. Ambas apps desplegadas.168pruebas aprobadas e imports aislados verificados en CPU. La latencia nueva todavía no se midió con GPU: no afirmar8s. Los cambios posteriores de transporte Telegram se desplegaron sin cambiar las fuentes/imagen GPU aisladas. Ver TELEGRAM.md de cada release para recuperación R2.
