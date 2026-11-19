from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).parent
MAIN = Path("/moss-image-vol")
LORAS = MAIN / "loras"
COMFY = Path("/root/ComfyUI")
COMFY_COMMIT = "0263e48806e5be43fa926d3961caac29cd268d84"
VAE_UTILS_COMMIT = "4c62ea005897fafbc593d69bedb8308ec9f932fd"


def profiles() -> dict:
    result = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))
    override = MAIN / "_release" / "profile_overrides.json"
    if override.is_file():
        changes = json.loads(override.read_text(encoding="utf-8"))
        for name, fields in changes.items():
            if name in result and isinstance(fields, dict):
                result[name].update({key: value for key, value in fields.items()
                                     if key in {"model", "weight_dtype"}})
    return result


def save_profile_overrides(changes: dict) -> None:
    path = MAIN / "_release" / "profile_overrides.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    for name, fields in changes.items():
        if name not in profiles() or not isinstance(fields, dict):
            raise ValueError("Perfil inválido para actualizar")
        current.setdefault(name, {}).update({key: value for key, value in fields.items()
                                             if key in {"model", "weight_dtype"}})
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".writing")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def profile(name: str) -> dict:
    items = profiles()
    if name not in items:
        raise ValueError(f"Perfil desconocido: {name}")
    return {"id": name, **items[name]}


def r2_settings() -> dict:
    return {
        "bucket": os.getenv("R2_BUCKET", "krea2-bucket-img-modal"),
        "public_base": os.getenv("R2_PUBLIC_BASE", "").rstrip("/"),
    }

VAE_ALIASES={"base":"flux2-vae_small"}
