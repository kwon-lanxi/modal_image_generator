from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from . import config
from .downloads import download_source
from .files import (MODEL_TYPES, atomic_write, describe, file_id, resolve, safe_relative,
                    safetensors_header, sha256, volume_path, write_json)


def infer_target(origin: dict) -> str:
    name = origin.get("filename", "").lower()
    kind = str(origin.get("model_type", "")).lower()
    for folder, target in (("vae_approx/", "tae"), ("diffusion_models/", "unet"),
                           ("text_encoders/", "clip"), ("vae/", "vae"), ("loras/", "lora")):
        if name.startswith(folder) or "/" + folder in name:
            return target
    base = Path(name).name
    if base.startswith(("tae_", "taesd", "lighttae")):
        return "tae"
    if kind == "vae" or re.search(r"(?:^|[_-])vae(?:[_.-]|$)", base):
        return "vae"
    if kind == "checkpoint":
        return "unet"
    return "lora"


def target_path(item: dict) -> Path:
    kind = item.get("target", "lora")
    if kind not in MODEL_TYPES:
        raise ValueError("Target inválido; usar lora, unet, clip, vae o tae")
    name = item.get("name") or Path(item.get("filename", "")).name
    if not name:
        raise ValueError("Se requiere name o filename")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("name debe ser un nombre de archivo")
    if not name.endswith(".safetensors"):
        name += ".safetensors"
    subfolder = item.get("subfolder", "").strip("/")
    if item.get("volume", "main") == "loras":
        if kind != "lora":
            raise ValueError("El volumen de personajes solo admite LoRAs")
        root = config.LORAS
    elif item.get("volume", "main") == "main":
        root = config.MAIN / MODEL_TYPES[kind]
    else:
        raise ValueError("volume debe ser main o loras")
    return safe_relative(root, f"{subfolder}/{name}" if subfolder else name)



def download(item: dict, job_id: str, log, commit) -> dict:
    # No overwrite: content used in a deployed snapshot must be versioned.
    if item.get("overwrite"):
        raise ValueError("Guardar una versión nueva con otro nombre; overwrite está deshabilitado")
    # Parse HF URLs before choosing the destination filename.
    url, headers, origin = download_source(item)
    item = {**item, "filename": item.get("filename") or origin.get("filename", "")}
    if not item.get("target") or item["target"] == "auto":
        item["target"] = infer_target(origin)
    destination = target_path(item)
    if destination.exists():
        return {"success": True, "skipped": True, "file": describe(destination)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{job_id}.part")
    started, received, last_log = time.monotonic(), 0, 0.0
    limit = 80 * 1024**3
    try:
        with requests.get(url, headers=headers, stream=True, timeout=(15, 120)) as response:
            if response.status_code != 200:
                raise ValueError(f"Descarga rechazada: HTTP {response.status_code}")
            expected = int(response.headers.get("Content-Length", 0))
            if expected > limit:
                raise ValueError("Archivo superior al límite de 80 GiB")
            digest = __import__("hashlib").sha256()
            with partial.open("wb") as file:
                for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > limit:
                        raise ValueError("Descarga superior al límite de 80 GiB")
                    file.write(chunk)
                    digest.update(chunk)
                    now = time.monotonic()
                    if now - last_log >= 5:
                        rate = round(received / max(now - started, 0.001))
                        log.event("download.progress", file=destination.name,
                                  downloaded=human_transfer_size(received),
                                  total=human_transfer_size(expected) if expected else "unknown",
                                  speed=human_transfer_size(rate) + "/s",
                                  downloaded_bytes=received, total_bytes=expected, bytes_per_second=rate)
                        last_log = now
            if expected and received != expected:
                raise ValueError("Descarga incompleta")
        safetensors_header(partial, validate=True)
        expected_hash = item.get("sha256") or origin.get("sha256")
        if expected_hash and expected_hash.lower() != digest.hexdigest():
            raise ValueError("SHA256 no coincide")
        os.replace(partial, destination)
        write_json(destination.with_suffix(".source.json"), {
            **origin, "sha256": digest.hexdigest(), "size_bytes": received,
        })
        commit()
        log.event("download.complete", file=file_id(destination), size=human_transfer_size(received),
                  bytes_exact=received, sha256=digest.hexdigest())
        return {"success": True, "file": describe(destination), "sha256": digest.hexdigest()}
    finally:
        partial.unlink(missing_ok=True)


def human_transfer_size(value: int) -> str:
    """Use decimal MB/GB units to keep transfer progress readable in Modal logs."""
    amount = max(0, float(value))
    unit = "B"
    for candidate in ("KB", "MB", "GB", "TB"):
        amount /= 1000
        unit = candidate
        if amount < 1000 or candidate == "TB":
            break
    return f"{amount:.2f} {unit}"


def bootstrap(job_id: str, log, commit) -> dict:
    results = []
    for target, name, output in [
        ("unet", "NREDQW21-UNLOCKED-v1-BF16", "NREDQW21-UNLOCKED-v1-Comfy-FP8_scaled.safetensors"),
        ("clip", "qwen3vl_8b_bf16_heretic", "qwen3vl_8b_heretic_fp8_scaled.safetensors"),
    ]:
        results.append(convert_fp8({"target": target, "name": name, "output": output}, job_id, log, commit))
    return {"success": True, "assets": results}


def convert_fp8(payload: dict, job_id: str, log, commit) -> dict:
    target = payload.get("target", "unet")
    if target not in {"unet", "clip"}:
        raise ValueError("Solo diffusion model o text encoder admiten conversión FP8")
    name = payload.get("name") or ("NREDQW21-UNLOCKED-v1-BF16" if target == "unet" else "qwen3vl_8b_bf16_heretic")
    source = resolve(name, target)
    precision = payload.get("precision", "fp8_scaled")
    if precision not in {"fp8_scaled", "fp8_mixed"}:
        raise ValueError("Usar fp8_scaled o fp8_mixed")
    suffix = "fp8_mixed" if precision == "fp8_mixed" else "fp8_scaled"
    output = payload.get("output") or (source.stem + "_" + suffix + ".safetensors")
    destination = safe_relative(config.MAIN / MODEL_TYPES[target], output)
    header = safetensors_header(source, validate=True)
    if target == "unet":
        reference = "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/" + config.OFFICIAL_REVISION + "/diffusion_models/qwen_image_2.1_int8_convrot.safetensors"
    else:
        reference = "https://huggingface.co/Comfy-Org/Boogu-Image/resolve/" + config.ENCODER_REFERENCE_REVISION + "/text_encoders/qwen3vl_8b_fp8_scaled.safetensors"
    import shutil
    executable = shutil.which("precision-convert")
    if not executable:
        raise RuntimeError("Falta precision-convert en CPU")
    report = config.MAIN / "_release" / "jobs" / job_id / "conversion.txt"
    native = report.parent / (".native." + target + "." + job_id + ".safetensors")
    conversion_input = source
    try:
        native.unlink(missing_ok=True)
        if target == "unet" and any(
                k.endswith(".img_mlp.gate_layer.weight") and spec.get("dtype") in {"BF16", "F16", "F32"}
                for k, spec in header.items()):
            from .quantization import fuse_swiglu
            count = fuse_swiglu(source, native)
            if count:
                conversion_input = native
                log.event("conversion.layout_fused", blocks=count, order="gate_then_up")
        from shared_model_conversion import convert_comfy_fp8
        return convert_comfy_fp8(
            source=source, destination=destination, reference=reference, precision=precision,
            job_id=job_id, report_path=report, log=log, commit=commit, describe=describe,
            file_id=file_id, safetensors_header=safetensors_header, sha256=sha256,
            write_json=write_json, source_for_conversion=conversion_input, profile_config=config,
            profile_field="clip" if target == "clip" else "model")
    finally:
        native.unlink(missing_ok=True)
        try:
            commit()
        except Exception as exc:
            log.event("conversion.native_cleanup_commit_failed", error=type(exc).__name__)


def delete_models(paths: list[str], log) -> dict:
    if not paths or len(paths) > 100:
        raise ValueError("Indicar entre 1 y 100 archivos")
    resolved = []
    for name in paths:
        path = volume_path(name)
        if not path.is_file() or path.suffix != ".safetensors":
            raise ValueError("Solo se pueden borrar archivos .safetensors, no carpetas")
        # Restrict deletion to model folders, never outputs/history/logs.
        if path.is_relative_to(config.MAIN) and path.relative_to(config.MAIN).parts[0] not in MODEL_TYPES.values():
            raise ValueError("Ruta fuera de las carpetas de modelos")
        if path.is_relative_to(config.MAIN) and path.relative_to(config.MAIN).parts[0] == MODEL_TYPES["unet"]:
            identifiers = {path.stem, path.name, file_id(path)}
            profiles = config.profiles()
            in_use = [name for name, profile in profiles.items() if profile.get("model") in identifiers]
            if in_use:
                raise ValueError("No se puede borrar un modelo usado por perfiles: " + ", ".join(in_use))
        resolved.append(path)
    for path in resolved:
        path.unlink()
        path.with_suffix(".source.json").unlink(missing_ok=True)
        log.event("model.deleted", file=file_id(path))
    return {"success": True, "deleted": len(resolved)}
