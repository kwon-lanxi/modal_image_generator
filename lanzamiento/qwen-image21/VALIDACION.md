# Validación Qwen Image2.1 personal ·16/11/2026

## Comprobado en CPU y Modal

- Volumen real qwen-image21-vol leído, con NREDQW21-UNLOCKED-v1-BF16, encoder qwen3vl_8b_bf16_heretic, VAE específico y LoRAs existentes. No se cambiaron los BF16 originales.
- Conversión CPU real de encoder a FP8 scaled:17.534.334.584→10.588.695.748bytes. SHA256009d947fa373a41ced21152fa3732386e512106e634bab66bb70c33f81c89b10.
- Primera conversión DiT por coincidencia parcial dejó10.472.265.600bytes. El reporte detectó32capas de la referencia que no coincidían con el fine-tune. Se investigó la diferencia; no se adoptó ese artefacto como preset final.
- Fusión sin pérdida de32bloques SwiGLU BF16: gate_layer seguido de proj→gate_up. Copia streaming por bloques, orden y mapeo LoRA contrastados con códigoComfy. Test verifica bytes exactos, formas y original intacto.
- Segunda conversiónCPU real desde layout fusionado:14.230.284.552→7.251.027.176bytes. SHA256880d6aaf38ed6c26a4d3310b74b0bec1e70aef19a9eefc89a7a67f26b7ac7a3c. Encoder ya convertido se reutilizó. Temporales de fusión/conversión eliminados al terminar. Copias finales:17.839.722.924bytes entre DiT y encoder.
- Referencias HF fijadas: Qwen officialaacf31a6d48e707f352ecba714cee0680da3479b y Boogu8606e7df2f5389282617b8b534db290e1f5d7811. Converter2.3.0. Pin Comfy258f880d; nodosQwen contrastados con master por investigación.
- Build GPU/CPU construido y despliegue HTTPS completado; se reutilizaron capas compatibles del build anterior. Health autenticado200 y servicecpu-api sin consultar GPU.
- Subida CPU real de dos PNG:1024×1024 y512×512. Se verificaron manifiestos privados con hashes en Volume. Hubo un500 transitorio de polling durante preemption de infraestructura; ambos inputs quedaron guardados correctamente. Se añadieron reintentos GET limitados y reanudación de operaciones CPU seguras; no se reenvían POST automáticamente.
- 20 pruebas Qwen y 59 pruebas Krea aprobadas juntas (79 en total): contrato base25pasos/CFG1, límites, entradas inválidas, edición con referencias en orden, fallo antes de GPU ante archivo faltante, RGBA y alpha, metadatos/fechas/URLs/nombres, fallbackR2, idempotencia, conversión mock conservando original y pins, fusión streaming, autenticación, progreso, estimaciónL4, reintentoCUDA acotado y reanudaciónCPU sin duplicarGPU.
- JavaScript del HTML comprobado con node --check. Navegador automatizado no respondió en dos intentos; no se declara QA visual de esta interfaz. La base visual es la misma del playground Krea validado anteriormente.

## Prueba GPU

Job inicial0feef42077e678e29242f79c166ea7b4,25pasos,512×512,seed42. Modal confirmó: Function QwenGPU waiting to be scheduled on a GPU_L4 worker, acquiring more capacity. En ese momento backlog1/runners0. La espera no prueba inferencia ni calidad ni tiempo facturadoGPU.

Pendientes hasta asignación de GPU: warmup, inferencia FP8DiT+encoder, salidaRGB/RGBA real, edición con2referencias, LoRA real, restauración del snapshot, persistenciaVolume/R2 de resultados reales y apagado single-use. No afirmar velocidad superior aKrea2 sin la medición. Cuantización reduce pesos; la comparación visual contraBF16 no está validada.

## Límites prácticos

Hasta10referencias y6MP nominales agregados; ese límite CPU reduce solicitudes desproporcionadas pero no garantiza VRAM para cualquier prompt/ratio/configuración. El caso10referencias aún no está medido. Cacheauto/int8 es elección de memoria paraL4; oficialauto/default. No se probaron Sage, cu130, torch.compile ni un encoderW4A8 en este fine-tune.


## Estado al cerrar la validación de lanzamiento

Dos intentos de asignación L4 permanecieron en cola más de veinte minutos acumulados, con backlog1 y runners0. Modal confirmó explícitamente falta de capacidad GPU_L4. Ambos fueron cancelados antes de ejecutar inferencia, registrados como cancelled y sin trabajos GPU pendientes. La app quedó desplegada con API/manager en min_containers0; no se dejó un coordinador esperando por tiempo indefinido.

Versión final: reserva writer0,25CPU/512MiB y límite32GiB para abaratar cola; límite de referencias6MP nominales agregados, overlap tiled<=tile/4 validado antes de GPU y workaround de inicializaciónCUDA deL4 acotado. Los dos PNG de entrada y el preflight multiimagen real quedaron verificados. HTML/health autenticación se comprobaron sobre el endpoint final. El artefacto intermedio10,47GB se eliminó tras verificar el nuevo7,25GB; originales BF16 y copiasFP8 finales se conservaron.

Inferencia, LoRA real, edición GPU, calidad visual, snapshotrestore, tiempos reales y uploadR2 de una imagen Qwen **siguen sin validar por disponibilidad de L4**. No confundir los testsCPU ni la cuantización exitosa con una generación GPU certificada. No se usó A10 ni otra GPU sin la preferencia del usuario. Ver pregunta pendiente sobre alternativa A10.


## Generaciones posteriores del usuario · 16/11/2026

Se leyeron resultados persistidos de tres generaciones reales NVIDIA L4 a1024x1024, todas con R2 uploaded. CFG1/25pasos: sampling28.6869s, metodo31.088s, RPC319.7007s. CFG3/25pasos: sampling55.3095s, metodo57.517s, RPC1044.4052s. CFG3/30pasos: sampling70.9296s, metodo73.7822s, RPC109.0337s; preparacion+RPC112.8379s. Pico torch19908251136bytes (~18.54GiB). No son pruebas controladas del mismo prompt/seed; no atribuir toda diferencia a CFG ni RPC extra solamente a cola. Se confirma generacion FP8 en L4 y uploadR2. Edicion multiimagen, LoRAs aplicadas, calidad visual y factura exacta permanecen sin comprobar en esta revision de logs.
