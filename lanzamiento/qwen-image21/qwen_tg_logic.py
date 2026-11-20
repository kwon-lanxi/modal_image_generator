import hashlib
import hmac
import json
import math
import re
import shlex
import time
from urllib.parse import parse_qsl

OWNER = 7112930476
ASPECT_RATIOS = {
    "1:1": (1328, 1328), "16:9": (1664, 928), "9:16": (928, 1664),
    "4:3": (1472, 1104), "3:4": (1104, 1472),
    "3:2": (1584, 1056), "2:3": (1056, 1584),
}
ASPECT_RATIOS = {k: tuple((v+16)//32*32 for v in dims) for k,dims in ASPECT_RATIOS.items()}
SIZES = {**ASPECT_RATIOS, "square": ASPECT_RATIOS["1:1"],
         "cuadrado": ASPECT_RATIOS["1:1"], "vertical": ASPECT_RATIOS["9:16"],
         "horizontal": ASPECT_RATIOS["16:9"]}


def validate_init(raw, token, now=None):
    pairs = parse_qsl(raw, keep_blank_values=True)
    if len(dict(pairs)) != len(pairs): raise ValueError("Duplicate fields")
    data = dict(pairs); signature = data.pop("hash", "")
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, "\n".join(k+"="+v for k,v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected): raise ValueError("Invalid Telegram signature")
    age = (now or time.time()) - int(data.get("auth_date", 0))
    if not -30 <= age <= 3600: raise ValueError("Expired Telegram session; reopen Mini App")
    user = json.loads(data.get("user", "{}"))
    if user.get("id") != OWNER: raise ValueError("Unauthorized user")
    return user


def parse_prompt(text, aliases, model_aliases=None):
    try:
        tokens = shlex.split(text)
    except ValueError:
        raise ValueError('Hay comillas sin cerrar. Usá comillas dobles para valores con espacios, por ejemplo --neg "texto negativo".') from None
    prompt, loras, options = [], {}, {}
    names = {"-w":"width", "-h":"height", "--seed":"seed", "--steps":"steps", "--cfg":"cfg", "--n":"n", "--vae":"vae_alias", "--size":"size", "--ref":"reference_resolution", "--model":"unet_name", "--profile":"profile", "--neg":"negative_prompt"}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--turbo":
            options["turbo"] = True
        elif t in names:
            i += 1
            if i == len(tokens) or tokens[i] in names or tokens[i].startswith("--"):
                raise ValueError(f"Falta el valor de {t}.")
            if names[t] in options: raise ValueError(f"El parámetro {t} está repetido; dejalo una sola vez.")
            options[names[t]] = tokens[i]
        elif t.startswith("--") or t in {"-w", "-h"}: raise ValueError(f"Opción desconocida: {t}. Consultá /help para ver los flags válidos.")
        elif t.startswith(("!", "@")):
            parts = t[1:].split(":")
            if len(parts) > 2 or parts[0] not in aliases:
                raise ValueError(f"No encontré la LoRA {parts[0]!r}. Revisá /loras y usá !alias o !alias:0.8.")
            entry = aliases[parts[0]]
            if not isinstance(entry,dict) or not entry.get("id"):
                raise ValueError(f"El alias !{parts[0]} no tiene una LoRA asignada. Corregilo desde /loras.")
            try: weight = float(parts[1]) if len(parts)==2 else float(entry.get("weight", .8))
            except (TypeError, ValueError): raise ValueError(f"Peso inválido en !{parts[0]}; usá un número, por ejemplo !{parts[0]}:0.8.") from None
            if not math.isfinite(weight) or abs(weight)>2: raise ValueError(f"Peso inválido para !{parts[0]}: debe estar entre -2 y 2.")
            if entry["id"] in loras: raise ValueError("LoRA repetida")
            loras[entry["id"]] = weight
        else: prompt.append(t)
        i += 1
    if not prompt: raise ValueError("Escribí un prompt")
    size = options.pop("size", "1:1")
    if size not in SIZES: raise ValueError(f"--size {size!r} no es válido. Elegí: " + ", ".join(ASPECT_RATIOS))
    w,h = SIZES[size]
    recipe = {"positive_prompt":" ".join(prompt), "profile":"base", "width":w, "height":h, "lora":loras, "return_base64":False}
    model_name=options.get("unet_name")
    if model_name and model_aliases:
        found=next((value for alias,value in model_aliases.items() if alias.casefold()==model_name.casefold()),None)
        if found:options["unet_name"]=found["id"]
    vae_alias=options.pop("vae_alias",None)
    if vae_alias:
        from qwen_service.config import VAE_ALIASES
        if vae_alias.lower() not in VAE_ALIASES:raise ValueError(f"VAE {vae_alias!r} no reconocido. Consultá /help para ver los VAE disponibles.")
        recipe["vae_name"]=VAE_ALIASES[vae_alias.lower()]
    for key,value in options.items():
        if key in {"unet_name", "profile", "negative_prompt"}:
            recipe[key] = value
        elif key == "turbo":
            recipe[key] = bool(value)
        elif key == "cfg":
            try: recipe[key] = float(value)
            except (TypeError, ValueError): raise ValueError(f"--cfg debe ser un número; recibí {value[:48]!r}.") from None
        else:
            try: recipe[key] = int(value)
            except (TypeError, ValueError):
                flag = next((name for name,field in names.items() if field==key),key)
                raise ValueError(f"{flag} debe ser un entero; recibí {value[:48]!r}.") from None
    if options.get("turbo") and recipe.get("negative_prompt", "").strip():
        raise ValueError("--neg requiere CFG mayor que 1; no se puede combinar con --turbo")
    if options.get("turbo"):
        from qwen_service.contracts import turbo_settings
        recipe=turbo_settings(recipe)
    if recipe.get("profile") not in {"base", "base-bf16"}:
        raise ValueError("--profile debe ser base o base-bf16.")
    if recipe["width"]%32 or recipe["height"]%32 or not 256<=recipe["width"]<=2048 or not 256<=recipe["height"]<=2048 or recipe["width"]*recipe["height"]>2097152:
        raise ValueError(f"Resolución -w {recipe['width']} -h {recipe['height']} inválida: ambos valores deben ser múltiplos de 32, estar entre 256 y 2048 y no superar 2 MP.")
    if len(loras)>8: raise ValueError("Máximo8 LoRAs")
    if not 1<=recipe.get("steps",25)<=80: raise ValueError("--steps debe estar entre 1 y 80.")
    if not 1<=recipe.get("cfg",1)<=12: raise ValueError("--cfg debe estar entre 1 y 12.")
    if not 0<=recipe.get("seed",0)<2**63: raise ValueError("--seed debe estar entre 0 y 2^63-1.")
    if recipe.get("negative_prompt", "").strip() and recipe.get("cfg", 1) <= 1:
        raise ValueError("--neg requiere CFG mayor que 1; no se puede combinar con --turbo")
    if recipe.get('reference_resolution',768)%32 or not 256<=recipe.get('reference_resolution',768)<=1408:
        raise ValueError(f"--ref {recipe.get('reference_resolution')} inválido: usá un múltiplo de 32 entre 256 y 1408.")
    if not 1<=recipe.get("n",1)<=8:raise ValueError("--n admite entre 1 y 8 imágenes.")
    return recipe


def progress_text(event):
    if event.get("event") in {"download.start", "download.progress"}:
        if event["event"] == "download.start":
            return "Descargando · " + str(event.get("file", "archivo")) + "\nConectando…"
        downloaded, total = int(event.get("downloaded_bytes", event.get("bytes", 0))), int(event.get("total_bytes", 0))
        pct = min(100, int(downloaded * 100 / total)) if total else 0
        filled = min(10, int(pct / 10))
        current = event.get("downloaded", event.get("downloaded_human", "0 B"))
        expected = event.get("total", event.get("total_human", "tamaño desconocido"))
        speed = event.get("speed", event.get("speed_human", "—"))
        filename = str(event.get("file", "archivo"))[:80]
        return f"Descargando · {filename}\n{'█'*filled}{'░'*(10-filled)} {pct}% · {current} / {expected}\n{speed}"
    text = _progress_stage(event)
    gpu = event.get("gpu")
    return text + "\nGPU · " + (str(gpu) if gpu else "pendiente de asignación")


def _progress_stage(event):
    if event.get("event")=="sampling.progress":
        step,total = event["step"],event["total"]
        n = min(10, int(step*10/total))
        return "Qwen 2.1 · Generando\n" + "█"*n + "░"*(10-n) + f" {step}/{total} pasos"
    return {"prepare":"Comprobando archivos", "gpu_call":"Esperando GPU / arranque / restauración", "lora":"Aplicando LoRAs", "encode":"Codificando prompt", "sampling":"Esperando primer paso", "decode":"Decodificando", "transfer_cpu":"Transfiriendo a CPU", "persist_and_upload":"Guardando en Volume y R2"}.get(event.get("stage"), "Procesando trabajo")
