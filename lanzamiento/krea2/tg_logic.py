import hashlib
import hmac
import json
import math
import re
import shlex
import time
from urllib.parse import parse_qsl

OWNER = 7112930476
from krea_service.resolution import RATIOS, dimensions, extract_size
ASPECT_RATIOS = {ratio:dimensions(ratio,alignment=16) for ratio in RATIOS}
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
    tokens,size_mp=extract_size(tokens)
    prompt, loras, options = [], {}, {}
    names = {"-w":"width", "-h":"height", "--seed":"seed", "--steps":"steps", "--cfg":"cfg", "--n":"n", "--vae":"vae_alias", "--size":"size", "--model":"unet_name", "--profile":"profile", "--neg":"negative_prompt"}
    names.update({"--sampler":"sampler_name","--sample":"sampler_name","--scheduler":"scheduler","--screluder":"scheduler",
        "--variance":"variance","--variance-strength":"variance_strength","--variance-seed":"variance_seed",
        "--variance-protect":"variance_protect","--variance-regions":"variance_regions","--variance-direction":"variance_direction",
        "--variance-shift":"variance_shift","--variance-fade":"variance_fade","--variance-timing":"variance_timing",
        "--variance-schedule":"variance_schedule","--variance-cutoff":"variance_cutoff","--variance-tail":"variance_tail",
        "--variance-vibe":"variance_vibe","--variance-blend":"variance_blend"})
    switches={"--variance-freeze":"variance_freeze","--variance-normalize":"variance_normalize"}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in switches:
            if switches[t] in options:raise ValueError(f"El parámetro {t} está repetido.")
            options[switches[t]]=True
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
    w,h = dimensions(size,size_mp if size_mp is not None else 1.5,alignment=16)
    recipe = {"positive_prompt":" ".join(prompt), "profile":"turbo", "width":w, "height":h, "lora":loras, "return_base64":False}
    recipe.update(resolution_mp=size_mp if size_mp is not None else 1.5,aspect_ratio=size)
    model_name=options.get("unet_name")
    if model_name and model_aliases:
        found=next((value for alias,value in model_aliases.items() if alias.casefold()==model_name.casefold()),None)
        if found:options["unet_name"]=found["id"]
    vae_alias=options.pop("vae_alias",None)
    if vae_alias:
        from krea_service.config import VAE_ALIASES
        if vae_alias.lower() not in VAE_ALIASES:raise ValueError(f"VAE {vae_alias!r} no reconocido. Elegí base, wanx2, realvae o HDvae.")
        recipe["vae_name"]=VAE_ALIASES[vae_alias.lower()]
    for key,value in options.items():
        if key in {"unet_name", "profile", "negative_prompt", "sampler_name", "scheduler", "variance",
                   "variance_protect", "variance_regions", "variance_direction", "variance_fade", "variance_timing",
                   "variance_schedule", "variance_vibe", "variance_freeze", "variance_normalize"}:
            recipe[key] = value
        else:
            flag = next((name for name,field in names.items() if field==key),key)
            try: recipe[key] = float(value) if key in {"cfg","variance_tail","variance_blend"} else int(value)
            except (TypeError, ValueError):
                expected = "un número" if key in {"cfg","variance_tail","variance_blend"} else "un entero"
                raise ValueError(f"{flag} debe ser {expected}; recibí {value[:48]!r}.") from None
    if recipe.get("profile") not in {"turbo", "turbo-scaled", "raw"}:
        raise ValueError("--profile debe ser turbo, turbo-scaled o raw.")
    if recipe["width"]%16 or recipe["height"]%16 or not 256<=recipe["width"]<=4096 or not 256<=recipe["height"]<=4096 or recipe["width"]*recipe["height"]>4194304:
        raise ValueError(f"Resolución -w {recipe['width']} -h {recipe['height']} inválida: ambos valores deben ser múltiplos de 16, estar entre 256 y 4096 y no superar 4 MP.")
    if len(loras)>8: raise ValueError("Máximo8 LoRAs")
    default_cfg = 3.5 if recipe.get("profile") == "raw" else 1.0
    if recipe.get("negative_prompt", "").strip() and recipe.get("cfg", default_cfg) <= 1:
        raise ValueError("--neg requiere CFG mayor que 1; usa --profile raw o indica --cfg > 1")
    if not 1<=recipe.get("steps",8)<=80: raise ValueError("--steps debe estar entre 1 y 80.")
    if not 1<=recipe.get("cfg",1)<=12: raise ValueError("--cfg debe estar entre 1 y 12.")
    if not 0<=recipe.get("seed",0)<2**63: raise ValueError("--seed debe estar entre 0 y 2^63-1.")
    if not 1<=recipe.get("n",1)<=8:raise ValueError("--n admite entre 1 y 8 imágenes.")
    from krea_service.contracts import GenerateRequest
    from pydantic import ValidationError
    try:GenerateRequest.model_validate(recipe)
    except ValidationError as exc:
        messages=[]
        for error in exc.errors(include_input=False,include_context=False)[:3]:
            key=str(error["loc"][0]) if error["loc"] else "variance"
            flag=next((flag for flag,field in {**names,**switches}.items() if field==key),key)
            messages.append(flag+": "+error["msg"].removeprefix("Value error, "))
        raise ValueError("\n".join(messages)) from None
    from krea_service.config import profile
    effective_steps=recipe.get("steps",profile(recipe["profile"])["steps"])
    if recipe.get("variance","off")!="off" and recipe.get("variance_schedule","constant")!="constant" and recipe.get("variance_cutoff",4)>effective_steps:
        raise ValueError("--variance-cutoff no puede superar --steps.")
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
        return "Krea2 · Generando\n" + "█"*n + "░"*(10-n) + f" {step}/{total} pasos"
    return {"prepare":"Comprobando archivos", "gpu_call":"Esperando GPU / arranque / restauración", "lora":"Aplicando LoRAs", "encode":"Codificando prompt", "sampling":"Esperando primer paso", "decode":"Decodificando", "transfer_cpu":"Transfiriendo a CPU", "persist_and_upload":"Guardando en Volume y R2"}.get(event.get("stage"), "Procesando trabajo")
