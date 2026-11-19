# Comprobaciones de implementación Modal: release CPU + GPU

Fuentes primarias consultadas el 15 de noviembre de 2026; SDK instalado localmente: **Modal 1.6.4**. Se inspeccionaron firmas/código del SDK sin invocar GPU ni desplegar. Este documento complementa `modal-serverless.md` y distingue contratos documentados de decisiones propuestas.

## Decisiones prácticas prioritarias

1. Una App puede contener API/manager CPU y clase GPU. Solo el método GPU genera imágenes; polling, catálogo, history, logger y archivos permanecen en CPU.
2. El worker GPU debe devolver un resultado durable (JSON con archivos/rutas y metadata), después de cerrar archivos y completar commits requeridos. No retornar éxito si el guardado obligatorio falló.
3. En CPU, no mantener un lock del Volume mientras se espera una generación remota o se sube a R2. Hacer transacciones locales cortas y publicar estado por job.
4. Escribir por job con UUID (`jobs/<id>/request.json`, `result.json`, `events.jsonl`) evita que CPU y GPU actualicen el mismo JSON agregado. Un único escritor CPU administra history agregado. Un `threading.RLock` no coordina contenedores separados.
5. No conservar el esquema actual de múltiples objetos con locks independientes si todos tocan el mismo Volume: un reload debe quedar excluido de cualquier lectura/escritura en ese mount.

Estos cinco puntos son recomendaciones aplicadas al proyecto, no servicios automáticos de Modal.

## Volumes: contratos y API

`VOL.with_mount_options(read_only=True)` devuelve un handle para montaje de solo lectura; las opciones se aplican por asociación contenedor/Volume. Puede combinarse con `sub_path=...`. El SDK local confirma la firma. `VOL.commit()` publica cambios; `VOL.reload()` trae commits ajenos. Reload exige cero archivos abiertos; los Volumes no ofrecen locking distribuido y escrituras simultáneas al mismo archivo usan last-write-wins. [Referencia Volume](https://modal.com/docs/sdk/py/latest/Volume).

Mientras se ejecuta `reload`, el mount aparece vacío al propio contenedor y no debe leerse/escribirse. Hay commits periódicos y al terminar, pero un commit explícito permite completar la persistencia antes de responder. Nuevos Volumes pueden usar `version=2`; especificarlo en un Volume existente exige que coincida. No migrar versiones implícitamente en esta release. [Guía Volumes](https://modal.com/docs/guide/volumes).

### Aplicación al release

- Pesos/LoRAs GPU: montar solo lectura si ningún inicializador hace mkdir, write, symlink dentro de ellos. Crear symlinks bajo `/root/ComfyUI`, no en el Volume read-only. Si modelos y outputs comparten Volume, usar `sub_path` o separar claramente permisos; no montar todo read-only si generación escribe ahí.
- Estado CPU: un máximo de contenedor más lock local ayuda al uso personal, pero no debe presentarse como exclusión distribuida perfecta frente a reemplazos de contenedor/redeploy. Archivos únicos por job y escrituras atómicas reducen el área de conflicto.
- Atomicidad local propuesta: escribir temporal en el mismo directorio, `os.replace` al destino, cerrar handles, después commit. Un único archivo compartido de history no debe tener escritor CPU y GPU.
- Lectura de imágenes: no retornar `FileResponse` cuyo archivo se abre después de liberar el lock si otra petición puede ejecutar reload. Leer bytes bajo lock y retornar `Response`, o copiar el archivo a `/tmp` bajo lock y servir desde ahí. Para imágenes personales pequeñas, bytes es simple.
- Logger: mantener log persistente Volume y metadata R2, pero archivos separados por job; logger de CPU separado del logger GPU. Un append a un JSONL común desde dos contenedores no se vuelve seguro con append mode.
- No ejecutar reload mientras se conserva un mmap de safetensors o un archivo abierto del mismo Volume. Tener pesos read-only no elimina esta restricción. Revisar ciclo real de loaders.

## Trabajos asíncronos: submit y polling

El patrón oficial es `spawn`, conservar `call.object_id` y recuperar luego con `FunctionCall.from_id(id)` y `get(timeout=0)`. En handlers async, `await worker.generate.spawn.aio(payload)` y `await call.get.aio(timeout=0)`. `from_id` es síncrono/lazy: no usar `await` ni `.aio` ahí. `TimeoutError` de Python significa que todavía no llegó salida; `modal.exception.OutputExpiredError` se maneja por separado. Las salidas de spawn se conservan hasta siete días después de finalizar. [Job processing](https://modal.com/docs/guide/job-queue).

```python
call = await GPUWorker().generate.spawn.aio(payload)
call_id = call.object_id

# En una petición posterior al API CPU:
call = modal.FunctionCall.from_id(call_id)
try:
    result = await call.get.aio(timeout=0)
except modal.exception.OutputExpiredError:
    # Buscar resultado propio persistido; si no existe, informar expirado.
    ...
except TimeoutError:
    # 202 pending; no arrancar otra generación.
    ...
```

`get` espera indefinidamente si no se le pasa timeout y su coroutine no es cancellation-safe. Evitar envolverlo en `asyncio.wait_for`; usar su timeout propio. La firma local es `get(self, timeout=None, *, index=0)`. Inspección de SDK 1.6.4: polling usa `clear_on_success=False`, por lo que no consume el resultado. No confiar en resultados Modal como archivo histórico permanente. [FunctionCall](https://modal.com/docs/sdk/py/latest/FunctionCall).

La creación del job debe guardar una ficha durable con payload normalizado y luego enlazar `call_id`. Existe una ventana entre spawn y persistir el ID: no afirmar exactamente-once ni reintentar automáticamente submit tras un error de red sin una clave de idempotencia. Recomendación simple para uso personal: UUID proporcionado por CPU, marcar submitting antes de spawn, y no repetir automáticamente un job ambiguo. Una generación fallida no es lo mismo que polling pending; no convertir cualquier excepción en 202.

## API CPU y locks

Modal admite FastAPI vía `@modal.asgi_app()` y ASGI lifespan. El lifespan comienza al arrancar el contenedor, normalmente con la primera petición; Modal recomienda `@modal.enter` para hooks de contenedor. El ejemplo usa `@modal.concurrent(max_inputs=100)` para servir solicitudes concurrentes en ASGI. [Web Functions](https://modal.com/docs/guide/webhooks).

Diseño recomendado:

- API CPU reutilizable, `max_containers=1`; concurrencia moderada permite polling mientras hay submit o descarga. No usar single-use en el manager que mantiene caches y locks.
- Crear locks dentro del contenedor (enter/lifespan o creación de app), no como sustituto de un lock distribuido.
- Si se usa `asyncio.Lock`, compartirlo entre todos los handlers que acceden al mismo Volume; mantener escrituras bloqueantes fuera del event loop mediante threadpool. No adquirir un `threading.Lock` bloqueante en el event loop mientras otra coroutine necesita liberarlo.
- Alternativa simple: función síncrona `volume_transaction` con un `threading.RLock`, ejecutada completa mediante `run_in_threadpool`; todas las operaciones filesystem de ese Volume pasan por ahí. Que el mismo thread adquiera y libere el RLock.
- No devolver un iterador/stream ligado al Volume después de terminar la transacción.
- Usar `.aio` para I/O Modal desde async; no ejecutar `.remote()` bloqueante en un handler async. [Async Modal](https://modal.com/docs/guide/async).

## Worker GPU y snapshots

La referencia actual admite conjuntamente `single_use_containers`, `enable_memory_snapshot` y `experimental_options` en la firma de `App.cls`; SDK local 1.6.4 lo confirma. `single_use_containers=True` termina el contenedor tras una entrada. No equivale a `@modal.concurrent(max_inputs=1)`. [App](https://modal.com/docs/sdk/py/latest/App).

Mantener un solo método GPU de generación. No añadir consultas health/status al worker: cualquier invocación consume su única entrada o exige inicializarlo. Dejar concurrencia GPU por defecto (una entrada); `max_containers=1`, `min_containers=0`, `buffer_containers=0`. Una llamada puede producir varios previews si el trabajo lo pide, sin transformarlos en varias llamadas GPU.

La base se prepara en `enter(snap=True)`; clientes R2/sesiones HTTP, reloj de arranque, estado de petición y recursos renovables se crean en `enter(snap=False)`. Cambios de archivos en Volume no invalidan snapshots. No asumir que catálogo/LoRAs en RAM reflejan lo cargado desde el manager. [Snapshots](https://modal.com/docs/guide/memory-snapshots).

**Riesgo específico**: `reload` de pesos/LoRAs después de restaurar puede fallar por handles/mmap capturados. No resolver ocultando el error y usando un catálogo viejo. Opciones: pesos base inmutables y LoRAs en Volume separado recargable con handles cerrados; o versión explícita de artefactos que cambie configuración del worker y fuerce snapshot nuevo. La compatibilidad real requiere una GPU y una restauración probada.

**Costo del postproceso**: si el worker codifica PNG/sube R2/hace logging antes de devolver, la reserva GPU continúa durante esas operaciones. Para minimizar costo, transferir bytes/metadata al CPU y realizar allí R2/metadata/publicación. El tamaño de retorno y duración del transporte también se deben medir; no quitar persistencia obligatoria para aparentar apagado más rápido. Si hay escritura Volume de GPU, completar commit y devolver descriptor antes de teardown.

## Validación mínima de la release

Sin GPU: importación/AST, rutas CPU y payloads, persistencia ante reinicio, polling pending/done/failed/expired, consistencia entre metadata PNG/JSON/R2, protección de paths, no import de torch/ComfyUI en Image CPU, ningún await de GPU bajo lock Volume.

Con GPU cuando se autorice ejecutar: primera captura y restauración reales, generar LoRA A y después B sin contaminación, subir una nueva LoRA desde CPU y verla en worker siguiente, preview→final tras escalar a cero, comprobar que health/poll/list/history no crean contenedor GPU, y confirmar resultado persistente aunque se cierre worker/API.
