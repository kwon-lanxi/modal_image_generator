import hashlib
import hmac
import json
import math
import re
import shlex
import time
from urllib.parse import parse_qsl

OWNER = 7112930476
from moss_service.resolution import RATIOS, dimensions, extract_size
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


def protect_json_prompt(text):
    # Decode before shlex so JSON quotes, newlines and !aliases inside it survive.
    quote=None;escaped=False
    for index,char in enumerate(text):
        if escaped:escaped=False;continue
        if char=="\\":escaped=True;continue
        if quote:
            if char==quote:quote=None
            continue
        if char in {"'",'"'}:quote=char;continue
        if char=="{" and text[index+1:].lstrip().startswith(('"',"}")):
            decoder=json.JSONDecoder(parse_constant=lambda value:(_ for _ in ()).throw(ValueError("JSON no finito")))
            try:obj,end=decoder.raw_decode(text[index:])
            except json.JSONDecodeError as exc:raise ValueError("JSON inválido: "+exc.msg) from None
            if not isinstance(obj,dict):raise ValueError("El layout debe ser un objeto JSON")
            raw=text[index:index+end]
            # Shell-quote this entire block as ONE token, without changing JSON.
            return text[:index]+shlex.quote(raw)+text[index+end:]
    return text


def parse_prompt(text, aliases, model_aliases=None):
    try:
        tokens = shlex.split(protect_json_prompt(text))
    except ValueError as exc:
        if str(exc).startswith("JSON inválido:"):
            raise
        raise ValueError('Hay comillas sin cerrar. Usá comillas dobles para valores con espacios, por ejemplo --neg "texto negativo".') from None
    tokens,size_mp=extract_size(tokens)
    prompt, loras, options = [], {}, {}
    names = {"-w":"width", "-h":"height", "--seed":"seed", "--steps":"steps", "--cfg":"cfg", "--n":"n", "--vae":"vae_alias", "--size":"size", "--model":"unet_name", "--profile":"profile", "--negative":"negative_prompt", "--neg":"negative_prompt"}
    from moss_service.acceleration import FLAGS,PRESETS
    names.update({"--upscarler":"upscale","--upscaler":"upscale","--sampler":"sampler_name","--sample":"sampler_name","--scheduler":"scheduler","--screluder":"scheduler"})
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in FLAGS:
            if "acceleration" in options:raise ValueError("Elegí un solo modo de aceleración.")
            options["acceleration"]=FLAGS[t]
        elif t in {"--turbo","--turbo12","--superturbo"}:
            options[t[2:]]=True
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
    model_name=options.get("unet_name")
    if model_name and model_aliases:
        found=next((value for name,value in model_aliases.items() if name.casefold()==model_name.casefold()),None)
        if found:options["unet_name"]=found["id"]
    size = options.pop("size", "1:1")
    if size not in SIZES: raise ValueError(f"--size {size!r} no es válido. Elegí: " + ", ".join(ASPECT_RATIOS))
    w,h = dimensions(size,size_mp if size_mp is not None else 1.5,alignment=16)
    recipe = {"positive_prompt":" ".join(prompt), "profile":"sft", "width":w, "height":h, "lora":loras, "return_base64":False}
    recipe.update(resolution_mp=size_mp if size_mp is not None else 1.5,aspect_ratio=size)
    vae_alias=options.pop("vae_alias",None)
    if vae_alias:
        from moss_service.config import VAE_ALIASES
        if vae_alias.lower() not in VAE_ALIASES:raise ValueError(f"VAE {vae_alias!r} no reconocido. Consultá /help para ver los VAE disponibles.")
        recipe["vae_name"]=VAE_ALIASES[vae_alias.lower()]
    for key,value in options.items():
        if key in {"profile","unet_name","turbo","turbo12","superturbo","negative_prompt","acceleration","sampler_name","scheduler"}:
            recipe[key]=value
        else:
            flag=next((name for name,field in names.items() if field==key),key)
            try:recipe[key]=int(value.lower().removeprefix("x")) if key=="upscale" else float(value) if key=="cfg" else int(value)
            except (TypeError,ValueError):
                expected="un número" if key=="cfg" else "un entero"
                raise ValueError(f"{flag} debe ser {expected}; recibí {value[:48]!r}.") from None
    if recipe.get("acceleration") and "profile" not in options:recipe.pop("profile",None)
    from moss_service.contracts import normalize
    from pydantic import ValidationError
    try:
        normalized=normalize(recipe,"generate")
    except ValidationError as exc:
        messages=[]
        for error in exc.errors(include_input=False,include_context=False)[:3]:
            messages.append(".".join(map(str,error["loc"]))+": "+error["msg"].removeprefix("Value error, "))
        raise ValueError("\n".join(messages)) from None
    if normalized.get("negative_prompt","").strip() and normalized["cfg"]<=1:raise ValueError("--neg requiere CFG mayor que1.")
    for key in ("profile","steps","cfg","lora","sampler_name","scheduler","negative_prompt","mu","upscale","acceleration","unet_name","weight_dtype"):
        if normalized.get(key) is not None:recipe[key]=normalized[key]
    if recipe.get("profile") not in {"base", "sft"}:
        raise ValueError("--profile debe ser base o sft.")
    if recipe["width"]%16 or recipe["height"]%16 or not 256<=recipe["width"]<=4096 or not 256<=recipe["height"]<=4096 or recipe["width"]*recipe["height"]>4194304:
        raise ValueError(f"Resolución -w {recipe['width']} -h {recipe['height']} inválida: ambos valores deben ser múltiplos de 16, estar entre 256 y 4096 y no superar 4 MP.")
    if len(loras)>8: raise ValueError("Máximo8 LoRAs")
    if not 1<=recipe.get("steps",8)<=80: raise ValueError("--steps debe estar entre 1 y 80.")
    if not 1<=recipe.get("cfg",1)<=12: raise ValueError("--cfg debe estar entre 1 y 12.")
    if not 0<=recipe.get("seed",0)<2**63: raise ValueError("--seed debe estar entre 0 y 2^63-1.")
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
    if event.get("event")=="upscale.progress":
        return f"Flow Upscaler x{event['scale']} · imagen {event['image']}/{event['total']} · pasada {event['pass_index']}"

    if event.get("event")=="sampling.progress":
        step,total = event["step"],event["total"]
        n = min(10, int(step*10/total))
        return "MOSS · Generando\n" + "█"*n + "░"*(10-n) + f" {step}/{total} pasos"
    return {"prepare":"Comprobando archivos", "gpu_call":"Esperando GPU / arranque / restauración", "lora":"Aplicando LoRAs", "encode":"Codificando prompt", "sampling":"Esperando primer paso", "decode":"Decodificando", "transfer_cpu":"Transfiriendo a CPU", "persist_and_upload":"Guardando en Volume y R2"}.get(event.get("stage"), "Procesando trabajo")
