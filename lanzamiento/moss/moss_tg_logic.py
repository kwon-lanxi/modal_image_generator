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
    tokens = shlex.split(protect_json_prompt(text))
    prompt, loras, options = [], {}, {}
    names = {"-w":"width", "-h":"height", "--seed":"seed", "--steps":"steps", "--cfg":"cfg", "--n":"n", "--vae":"vae_alias", "--size":"size", "--model":"unet_name", "--profile":"profile", "--negative":"negative_prompt", "--neg":"negative_prompt"}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in {"--turbo","--turbo12","--superturbo"}:
            options[t[2:]]=True
        elif t in names:
            i += 1
            if i == len(tokens): raise ValueError("Falta valor para " + t)
            options[names[t]] = tokens[i]
        elif t.startswith("--") or t in {"-w", "-h"}: raise ValueError("Opción desconocida: " + t)
        elif t.startswith(("!", "@")):
            parts = t[1:].split(":")
            if len(parts) > 2 or parts[0] not in aliases: raise ValueError("Alias desconocido: " + t)
            entry = aliases[parts[0]]
            weight = float(parts[1]) if len(parts)==2 else float(entry.get("weight", .8))
            if not math.isfinite(weight) or abs(weight)>2: raise ValueError("Peso entre -2 y 2")
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
    if size not in SIZES: raise ValueError("Proporciones: " + ", ".join(ASPECT_RATIOS))
    w,h = SIZES[size]
    recipe = {"positive_prompt":" ".join(prompt), "profile":"sft", "width":w, "height":h, "lora":loras, "return_base64":False}
    vae_alias=options.pop("vae_alias",None)
    if vae_alias:
        from moss_service.config import VAE_ALIASES
        if vae_alias.lower() not in VAE_ALIASES:raise ValueError("Alias VAE desconocido")
        recipe["vae_name"]=VAE_ALIASES[vae_alias.lower()]
    for key,value in options.items(): recipe[key] = value if key in {"profile","unet_name","turbo","turbo12","superturbo","negative_prompt"} else float(value) if key=="cfg" else int(value)
    if recipe.get("negative_prompt", "").strip() and recipe.get("cfg", 3.0) <= 1:
        raise ValueError("--neg requiere CFG mayor que 1")
    if (recipe.get("turbo") or recipe.get("turbo12") or recipe.get("superturbo")) and recipe.get("negative_prompt", "").strip():
        raise ValueError("El negative prompt requiere el modo normal con --cfg mayor que 1; omitir los flags turbo")
    if recipe.get("turbo") or recipe.get("turbo12") or recipe.get("superturbo"):
        from moss_service.contracts import normalize
        normalized=normalize(recipe,"generate")
        for key in ("steps","cfg","lora","sampler_name","scheduler","negative_prompt"):recipe[key]=normalized[key]
    if recipe["width"]%16 or recipe["height"]%16 or not 256<=recipe["width"]<=2048 or not 256<=recipe["height"]<=2048 or recipe["width"]*recipe["height"]>2097152: raise ValueError("Resolución inválida: múltiplos16, máximo2MP")
    if len(loras)>8: raise ValueError("Máximo8 LoRAs")
    if not 1<=recipe.get("steps",8)<=80 or not 1<=recipe.get("cfg",1)<=12 or not 0<=recipe.get("seed",0)<2**63: raise ValueError("Pasos/CFG/seed fuera de rango")
    if not 1<=recipe.get("n",1)<=8:raise ValueError("--n admite entre1 y8")
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
        return "MOSS · Generando\n" + "█"*n + "░"*(10-n) + f" {step}/{total} pasos"
    return {"prepare":"Comprobando archivos", "gpu_call":"Esperando GPU / arranque / restauración", "lora":"Aplicando LoRAs", "encode":"Codificando prompt", "sampling":"Esperando primer paso", "decode":"Decodificando", "transfer_cpu":"Transfiriendo a CPU", "persist_and_upload":"Guardando en Volume y R2"}.get(event.get("stage"), "Procesando trabajo")
