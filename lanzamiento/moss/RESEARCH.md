# MOSS Base / SFT: preparación y precisión

Fuentes consultadas el 18 de noviembre de 2026:
- https://huggingface.co/Comfy-Org/MOSS-Image (revisión 2929efe3cdd506c0a430bcfca70a9a8d805cc095).
- https://huggingface.co/pottokao/MOSS-Image-Text-Encoder-Heretic (revisión 9f4a2b4034a87b04a9d51904fd8084edfa11cf29).
- https://huggingface.co/unsloth/MOSS-Image-Base-FP8 : FP8 TorchAO con escalas por fila; el autor mide mayor desviación que INT8 ConvRot. Reempaquetarlo no recupera precisión. Se decidió convertir desde BF16 oficial para evitar doble cuantización.
- https://github.com/Comfy-Org/ComfyUI/blob/0263e48806e5be43fa926d3961caac29cd268d84/comfy/ops.py : formato nativo por capa `.comfy_quant`, formato float8_e4m3fn y `.weight_scale`.
- https://huggingface.co/akhaliq/MOSS-Image-SFT-4-steps : LoRA Euler de cuatro pasos/CFG1; compatibilidad Base y SFT declarada por autor, pendiente de validar localmente.
- https://huggingface.co/lodestones/MOSS-Image-SFT-8-steps-dmd : alternativa comunitaria de ocho pasos; se descarga como opción, no se activa por defecto.

La búsqueda no identificó FP8 Scaled nativo para ambos checkpoints exactos y Heretic. No se afirma que no existan otros modelos. Coffmeteor/MOSS-Image-Turbo ofrece un modelo fusionado FP8, pero cambia los pesos solicitados; no sustituye a Base/SFT.

`prepare.py` usa CPU y volumen moss-image-vol. Convierte las proyecciones DiT desde BF16 a E4M3FN con escala tensorial maxabs/448. Conserva en BF16 embeddings, normalizaciones, modulaciones y capas no seleccionadas. Para Heretic conserva atención lineal/estado recurrente en BF16 y convierte MLP y proyecciones de atención completa. No es un cast uniforme. El encoder es para embeddings, no incluye vision tower, lm_head ni MTP. No ofrece prompt enhancer ni edición por imágenes todavía.

VAE: flux2-vae_small, el archivo del enlace real enviado por el usuario. Carga explícita FP32. Ambos perfiles comparten encoder y VAE. Precisión interna BF16; FP16 no es una opción adecuada para esta arquitectura.

Separación CPU/GPU: descargas, conversión, PNG, metadatos, historial, Volume y R2 en CPU. Un worker GPU de un solo uso por batch, sin mínimos, sin cron. Configuración inicial L40S / RTX-PRO-6000 / H100, sin B200. Snapshot por perfil y versiones de archivos. Sin compile/CUDA graphs inicialmente: prioriza arranque y carga de LoRAs dinámicas. No hay promesa de latencia hasta medir.

API: moss-image-personal-v1, usa temporalmente el mismo token personal KREA2_API_TOKEN del Secret krea2-api. Perfiles sft (predeterminado) y base. `turbo:true` selecciona DMD2 de data-archetype (8 pasos, CFG 1, LCM); `turbo12:true` selecciona anyMODE (12 pasos, CFG 1, Euler). `superturbo:true` conserva el adaptador Akhaliq de 4 pasos. n=1..8 y semillas independientes, LoRAs con pesos, modelos seleccionables, metadatos PNG, persistencia Volume/R2. Las aceleraciones son excluyentes y sus LoRAs de estilo se conservan. Los dos nuevos archivos se fijan a revisiones inmutables de HF en `prepare.py`.

Validación inicial: cinco pruebas CPU de contratos/conversión sintética aprobadas. Una conversión sintética no valida calidad de imagen ni compatibilidad completa del checkpoint; esas comprobaciones deben realizarse tras terminar las descargas.


Validación final: 7 pruebas MOSS; generación real Turbo de cuatro pasos a 256×256 en ambos perfiles. SFT: NVIDIA L40S, 7,79 segundos dentro del método GPU. Base: H100 80GB, 6,86 segundos. Estas cifras no representan 1024² ni latencia completa; los primeros arranques crean snapshots. Ambas imágenes coherentes, PNG con metadatos, R2 uploaded. No hubo publicación Telegram. Una conversión inicial había eliminado el prefijo necesario para detectar Qwen3.5 y fue retirada; v2 conserva model.language_model y una regresión CPU verifica las claves de detección. El encoder final pesa 10,57GB y cada DiT 5,38GB; cuantiza 128 y 171 módulos respectivamente. Se retiran intermediarios tras validar, manteniendo revisiones y hashes en _release/assets.json y fp8-verification.json. Para reconstruir solo un archivo ausente, ejecutar prepare.py; no descarga de nuevo los modelos ya convertidos.

API y playground desplegados: https://kwon-lanxi--moss-image-personal-v1-api.us-east.modal.run/playground. API protegida con el token personal de Krea; sin token /health devuelve 401. No se configura bot de Telegram para MOSS en esta revisión.
