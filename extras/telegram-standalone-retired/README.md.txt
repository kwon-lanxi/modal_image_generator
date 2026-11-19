# Bot Krea2 · adaptador CPU independiente

Usa la API HTTPS existente. No modifica Krea2 ni activa GPU para consultas, alias, galería o descargas. Acceso exclusivamente al usuario7112930476 en chat privado. Secret tg-token-image: TELEGRAM_KREA2_BOT_TOKEN; la variable de Qwen no se utiliza.

## Uso

/start o /help abre biblioteca y galería. /loras abre la Mini App con buscador y páginas de20archivos. Se conserva el ID completo del archivo y volumen: loras/... o krea2-loras-renew/.... Guardar alias permite asignar un peso habitual. Un alias existente se puede ajustar para el mismo archivo; cambiarlo a otro archivo se rechaza para evitar cambios silenciosos. Seleccionar y copiar genera @alias:peso. El portapapeles tiene alternativa manual si el cliente no permite copiar.

Mensaje: `Un retrato junto a la ventana @persona:0.8 @foto:0.4 --size vertical --seed 42`. También -w896 -h1152 (con espacios: -w 896 -h 1152), --steps y --cfg. Los tamaños son de muestreo; el VAE2x puede duplicarlos. Cada prompt crea un borrador con botón Generar; repetir confirmaciones no duplica el trabajo. Repetir conserva la seed del resultado; Nueva variante genera otra seed y vuelve a pedir confirmación. Reenviar original usa el archivo ya guardado, sin GPU. No se aplica automáticamente una LoRA de4pasos: falta seleccionar y validar ese archivo/preset.

/descargar ENLACE [nombre] descarga una LoRA al volumen principal por CPU. Para mover destino al volumen personalizado o gestionar modelos, usar el playground existente. /trabajos muestra los últimos10estados. Galería muestra los últimos100resultados de la API y su receta completa; las imágenes se cargan a pedido. Esta primera versión copia la selección de LoRAs al mensaje; todavía no guarda presets personales ni ofrece cambiar pesos mediante botones del chat.

## Webhook y recuperación

Telegram recibe200 solo después de guardar update_id en SQLite persistente. Se valida X-Telegram-Bot-Api-Secret-Token y usuario/chat antes de aceptar trabajo. Se solicitan solo message/callback_query, sin drop_pending_updates. No hay polling getUpdates. Las actualizaciones duplicadas se insertan una sola vez y las confirmaciones son transiciones de estado. callback_data contiene IDs cortos, nunca prompts ni rutas completas. answerCallbackQuery cierra el indicador del botón.

Webhook guarda y lanza process; process tiene un solo contenedor, escanea bandeja y trabajos, y consulta progreso cada5s durante45s. Una tarea programada cada minuto retoma trabajos activos tras terminar o perder el wakeup. Puede haber un intervalo de hasta aproximadamente un minuto en el progreso entre ciclos. State es el único escritor de su Volume, cierra SQLite antes de commit/reload y serializa llamadas cortas; no bloquea la Mini App durante inferencia.

La clave Idempotency-Key es estable por borrador. Si se pierde la respuesta del POST se consulta records; no se vuelve a lanzar automáticamente. Un estado ambiguo antes del envío exige revisión manual; no se promete exactly-once entre Telegram, Modal y HTTP. Un proceso que muere mientras maneja una actualización queda en handling para revisión; no se reproduce ciegamente. Un envío Telegram puede ser aceptado sin respuesta: delivery_unknown no se reenvía automáticamente. Los botones de entrega permiten reintentar cuando hay resultado conocido. Los429 mantienen un plazo de bloqueo según retry_after; pueden retrasar la entrega y exigir reenvío manual.

El bot entrega PNG mediante sendDocument para conservar bytes/metadatos. No inserta tokens en URLs ni registra URLs de BotAPI. Los errores del worker registran clase y job_id, sin datos secretos. No implementa cancelación de una generación en curso: Descartar solo actúa sobre borradores. Detener el bot no cancela trabajos ya aceptados por Krea.

## Mini App

El servidor verifica HMAC-SHA256 de initData con WebAppData, compara firma en tiempo constante, comprueba auth_date (una hora) y el ID autorizado. No usa initDataUnsafe como autenticación. No expone KREA2_API_TOKEN ni el token del bot al navegador. Archivos protegidos se entregan mediante proxy CPU autenticado. Al expirar la sesión, cerrar y reabrir la Mini App.

## Despliegue y activación

Secret krea2-telegram-config contiene KREA2_URL, BOT_PUBLIC_URL y TELEGRAM_WEBHOOK_SECRET aleatorio. Reutiliza krea2-api para KREA2_API_TOKEN. El volumen krea2-telegram-state conserva datos, separado de pesos/imágenes.

```
python -m pytest test_logic.py -q
python -X utf8 -m modal deploy bot_app.py
python -X utf8 -m modal run bot_app.py --url https://kwon-lanxi--krea2-telegram-web.us-east.modal.run
```

La última orden verifica getMe y configura setWebhook. No ejecutarla contra otro bot por error: se usa exclusivamente TELEGRAM_KREA2_BOT_TOKEN. Cambiar el webhook reemplaza el destino de recepción de ese bot. El despliegue por sí solo no cambia el webhook. La tarea periódica CPU permanece programada incluso cuando no hay trabajos; no reserva GPU.

## Fuentes primarias consultadas

- https://core.telegram.org/bots/api : Update, setWebhook, secret_token, callback queries, editMessageText, sendDocument y retry_after.
- https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app : validación HMAC del servidor y auth_date.
- https://modal.com/docs/guide/webhooks : ASGI y respuesta desacoplada.

Pruebas locales cubren firma/manipulación/caducidad/propietario, pesos/rutas de ambos volúmenes, parámetros inválidos, barra sin porcentaje inventado en cola, confirmación duplicada, POST ambiguo sin segunda generación y rechazo de webhooks falsos/no autorizados. Falta la prueba manual desde la cuenta Telegram para certificar interfaz, callback, envío real y rendimiento.
