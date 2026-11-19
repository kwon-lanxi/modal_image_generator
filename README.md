# Modal Image Generator

Workers personales de generación de imágenes con Modal para Krea 2, Qwen Image 2.1 y MOSS Image. Cada modelo mantiene su API HTTPS, worker GPU, gestor CPU y bot de Telegram en su propia app. Los bots son una interfaz adicional: no sustituyen las APIs.

## Componentes

| Proyecto | Carpeta | App Modal | GPU | Volumen principal |
|---|---|---|---|---|
| Krea 2 | `lanzamiento/krea2/` | `krea2-personal-v2` | L40S | `krea2-volume` + `krea2-lora` |
| Qwen Image 2.1 | `lanzamiento/qwen-image21/` | `qwen-image21-personal-v1` | L4 | `qwen-image21-vol` |
| MOSS Image | `lanzamiento/moss/` | `moss-image-personal-v1` | L4 o L40S | `moss-image-vol` |
| Web UI / galería | `krea2-webui/` | Cloudflare Workers/Pages | — | D1/R2 de Cloudflare |

Las carpetas de Krea, Qwen y MOSS contienen su propia app Modal, API, código GPU/CPU, servicios, Mini App/Telegram y documentación. `lanzamiento/shared_model_conversion.py` comparte utilidades de conversión.

## Requisitos para continuar desde otra computadora

- Git y Python 3.12.
- Una cuenta Modal con acceso al workspace y entorno donde ya existen las apps, volúmenes y secretos.
- Node.js y Wrangler solo para desarrollar o desplegar `krea2-webui/`.
- Acceso a los Secrets y a las cuentas de Hugging Face, Civitai, Cloudflare y R2 que correspondan.

Cloná el repositorio y configurá el CLI de Modal con tu propia sesión:

```bash
git clone --branch codex/initial-project-import https://github.com/kwon-lanxi/modal_image_generator.git
cd modal_image_generator
python -m pip install modal
modal setup
modal app list
```

No copies tokens desde la computadora anterior. Los tokens locales, los archivos `.local-api-token`, estado de Wrangler y archivos de entorno están excluidos del repositorio. La autenticación del CLI se configura de nuevo en cada dispositivo.

## Secrets y recursos de Modal

Las apps esperan que existan los recursos administrados por Modal. Los nombres que usa el código incluyen:

- Secrets comunes: `krea2-api`, `r2-secrets`, `huggingface-secret`, `civitai-apikey`, `tg-token-image`.
- Telegram: `krea2-telegram-config` y `moss-telegram-config`; los tokens de los tres bots están en `tg-token-image` como `TELEGRAM_KREA2_BOT_TOKEN`, `TELEGRAM_QWEN21_BOT_TOKEN` y `TELEGRAM_MOSS_BOT_TOKEN`.
- Volúmenes de pesos: `krea2-volume`, `krea2-lora`, `qwen-image21-vol` y `moss-image-vol`.
- Estado de bots: volúmenes separados, por ejemplo `krea2-telegram-state`, `qwen21-telegram-state` y `moss-telegram-state`.

Las descargas y administración de modelos corren en CPU y guardan los pesos en esos volúmenes. No subas checkpoints/LoRAs al repo: `*.safetensors`, `*.ckpt`, `*.pt`, `*.pth`, `*.bin` y `*.gguf` están ignorados por Git.

## Desplegar las apps de Modal

Desde la raíz del repo, desplegá cada app desde su propia carpeta:

```bash
cd lanzamiento/krea2
python -X utf8 -m modal deploy modal_app.py

cd ../qwen-image21
python -X utf8 -m modal deploy modal_app.py

cd ../moss
python -X utf8 -m modal deploy modal_app.py
```

Cada app sirve API HTTPS y Telegram en el mismo despliegue de ese modelo. Revisá el README y `TELEGRAM.md` de la carpeta correspondiente antes de cambiar sus contratos, secretos o configuración del webhook. El despliegue no debe descargar modelos ni iniciar una GPU por sí solo; las tareas de preparación/descarga están documentadas en cada proyecto.

## API y pruebas manuales

Los playgrounds están en:

- Krea 2: `lanzamiento/krea2/krea_service/playground.html` y su [README](lanzamiento/krea2/README.md).
- Qwen: `lanzamiento/qwen-image21/qwen_service/playground.html` y su [README](lanzamiento/qwen-image21/README.md).
- MOSS: `lanzamiento/moss/moss_service/playground.html` y su [README](lanzamiento/moss/RESEARCH.md).

Los endpoints publicados y tokens de API son propios de cada app. No fijes valores secretos ni tokens en ejemplos de código o HTML. Los workers tienen escala a cero y la GPU se solicita únicamente para generar.

## Telegram

Cada bot es independiente y complementa la API HTTPS correspondiente. Los comandos, flags, LoRAs, parámetros, descargas y URL pública del webhook se explican en:

- [Krea 2 Telegram](lanzamiento/krea2/TELEGRAM.md)
- [Qwen Telegram](lanzamiento/qwen-image21/TELEGRAM.md)
- [MOSS Telegram](lanzamiento/moss/TELEGRAM.md)

Los webhooks se configuran mediante las funciones de configuración de cada app. No se utiliza polling permanente ni un cron dedicado.

## Cloudflare UI

`krea2-webui/` contiene la galería web y su worker auxiliar. Entrá a esa carpeta para ver su [README](krea2-webui/README.md), configuración `wrangler.toml`, esquema D1 y funciones. Configurá los bindings y secretos de Wrangler desde el dashboard/CLI en el nuevo entorno; las credenciales y el estado local de `.wrangler/` no viajan en Git.

## Investigación y decisiones

`docs/research/` reúne referencias y decisiones sobre Modal, cold starts, costos, Krea 2, Qwen y MOSS. El informe de samplers de MOSS está en [moss-samplers-schedulers.md](docs/research/moss-samplers-schedulers.md). `lanzamiento/INVESTIGACION_COLD_STARTS.md` registra el análisis de arranque de contenedores.

## Convenciones de mantenimiento

- Mantener las apps de Krea, Qwen y MOSS separadas; los cambios de un modelo no deben arrancar o importar la GPU de los otros.
- En cambios de GPU, revisar la lista de archivos fuente permitidos de la imagen y mantener dependencias de API/UI fuera de la imagen GPU.
- Mantener API HTTPS y Telegram como interfaces complementarias.
- Mantener descargas, conversiones, archivos, metadatos, R2 y gestión en CPU; reservar GPU para inferencia.
- No borrar pesos de volúmenes ni cambiar Secrets durante un despliegue normal.
- No incluir tokens, `.local-api-token`, directorios de caché ni pesos en commits.
