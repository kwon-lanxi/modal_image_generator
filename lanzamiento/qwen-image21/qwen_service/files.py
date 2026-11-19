from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import config

MODEL_TYPES = {
    "unet": "diffusion_models", "clip": "text_encoders", "vae": "vae",
    "lora": "loras",
}
EXCLUDED = {"_backups", "save_lora", "cache", "caches", "logs", ".git"}


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".writing")
    try:
        with temp.open("wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path: Path, data: dict):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode())


def safe_relative(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative or ":" in relative:
        raise ValueError("Usar una ruta relativa con separadores /")
    parts = Path(relative).parts
    if Path(relative).is_absolute() or any(p in {"..", "."} for p in relative.split("/")):
        raise ValueError("Ruta fuera del volumen")
    if any(not p for p in relative.split("/")) or not parts:
        raise ValueError("Ruta vacía o inválida")
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Symlink fuera del volumen")
    if target.resolve() == root.resolve():
        raise ValueError("No se permite operar sobre la raíz")
    return target


def volume_path(relative: str) -> Path:
    prefix = "qwen-image21-vol/"
    if relative.startswith(prefix):
        relative = relative[len(prefix):]
    return safe_relative(config.MAIN, relative)


def file_id(path: Path) -> str:
    return path.resolve().relative_to(config.MAIN.resolve()).as_posix()


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def safetensors_header(path: Path, validate=False) -> dict:
    size = path.stat().st_size
    with path.open("rb") as file:
        raw = file.read(8)
        if len(raw) != 8:
            raise ValueError("Archivo safetensors incompleto")
        length = struct.unpack("<Q", raw)[0]
        if length < 2 or length > min(64 * 1024 * 1024, size - 8):
            raise ValueError("Cabecera safetensors inválida")
        header = json.loads(file.read(length))
    if not isinstance(header, dict):
        raise ValueError("Cabecera safetensors inválida")
    if validate:
        tensors = [(key, value) for key, value in header.items() if key != "__metadata__"]
        if not tensors:
            raise ValueError("El archivo no contiene tensores")
        spans = []
        for _, tensor in tensors:
            if not isinstance(tensor, dict):
                raise ValueError("Tensor inválido")
            start, end = tensor["data_offsets"]
            if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= size - length - 8):
                raise ValueError("Offsets safetensors inválidos")
            widths = {"BOOL": 1, "U8": 1, "I8": 1, "F8_E4M3": 1, "F8_E4M3FN": 1,
                      "F8_E5M2": 1, "I16": 2, "U16": 2, "BF16": 2, "F16": 2,
                      "I32": 4, "U32": 4, "F32": 4, "I64": 8, "U64": 8, "F64": 8}
            shape = tensor.get("shape")
            if not isinstance(shape, list) or not all(isinstance(n, int) and n >= 0 for n in shape):
                raise ValueError("Forma de tensor inválida")
            width = widths.get(tensor.get("dtype"))
            if width is None or math.prod(shape) * width != end - start:
                raise ValueError("Tamaño/dtype de tensor inválido")
            spans.append((start, end))
        spans.sort()
        if spans[0][0] != 0 or spans[-1][1] != size - length - 8:
            raise ValueError("Datos safetensors incompletos")
        if any(left[1] != right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError("Datos safetensors superpuestos o con huecos")
    return header


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe(path: Path, include_metadata=False) -> dict:
    stat = path.stat()
    result = {
        "id": file_id(path), "name": path.stem, "filename": path.name,
        "relative_path": file_id(path), "absolute_path": str(path),
        "size_bytes": stat.st_size, "size_human": human_size(stat.st_size),
        "mtime_ns": stat.st_mtime_ns, "version": f"{stat.st_size}:{stat.st_mtime_ns}",
    }
    if include_metadata and path.suffix == ".safetensors":
        header = safetensors_header(path)
        result["safetensors_metadata"] = header.get("__metadata__", {})
        if any(key.endswith(".weight.scale") for key in header):
            result["compatibility_error"] = ("FP8 con escalas .weight.scale no compatibles con el loader Comfy fijado; "
                                               "usar el BF16 con fp8_e4m3fn_fast o convertir una copia a FP8 scaled compatible")
        result["tensor_count"] = len(header) - int("__metadata__" in header)
    sidecar = path.with_suffix(".source.json")
    if sidecar.is_file() and sidecar.stat().st_size < 1024 * 1024:
        origin = json.loads(sidecar.read_text(encoding="utf-8"))
        allowed = {"source", "repo_id", "filename", "revision", "sha256", "model_id", "model_version_id",
                   "file_id", "base_model", "trained_words", "model_name", "model_type", "version_name", "converter", "source_version",
                   "format", "max_relative_error", "source_provenance", "profile_updates"}
        provenance = {key: value for key, value in origin.items() if key in allowed}
        for key in ("source_url", "reference"):
            if origin.get(key):
                parsed = urlsplit(origin[key])
                if parsed.scheme == "https" and parsed.hostname and not parsed.username:
                    provenance[key] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        result["provenance"] = provenance
        if origin.get("sha256"):
            result["sha256"] = origin["sha256"]
    return result


def model_files(kind: str) -> list[Path]:
    if kind not in MODEL_TYPES:
        raise ValueError("Tipo de modelo desconocido")
    roots = [config.MAIN / MODEL_TYPES[kind]]
    if kind == "lora":
        roots.append(config.LORAS)
    seen, found = set(), []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.safetensors"):
            if any(part in EXCLUDED or part.startswith(".") for part in path.relative_to(root).parts):
                continue
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()) or resolved in seen:
                continue
            seen.add(resolved)
            found.append(path)
    return sorted(found, key=lambda p: str(p).casefold())


def resolve(name: str, kind: str) -> Path:
    # Exact relative IDs or exact filename/stem only. Never substring matching.
    paths = model_files(kind)
    matches = [p for p in paths if name in {file_id(p), p.name, p.stem}]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"No se encontró {kind}: {name}")
    raise ValueError(f"Nombre ambiguo: {name}. Usar uno de: {[file_id(p) for p in matches]}")


def list_models(kind: str) -> dict:
    entries = [describe(path) for path in model_files(kind)]
    result = {"count": len(entries), "models": entries}
    if kind != "lora":
        return result
    groups, downloaded = {}, []
    for entry in entries:
        if not entry["id"].startswith("qwen-image21-vol/loras/"):
            downloaded.append(entry)
            continue
        relative = entry["id"].split("/", 1)[1]
        character = relative.split("/", 1)[0] if "/" in relative else "root"
        match = re.search(r"(?:[-_]step0*|[-_])(\d{3,8})$", entry["name"], re.I)
        variant = {**entry, "steps": int(match.group(1)) if match else None}
        groups.setdefault(character, {"name": character, "character": character, "variants": []})["variants"].append(variant)
    for group in groups.values():
        group["variants"].sort(key=lambda v: (v["steps"] is not None, -(v["steps"] or 0)))
        group["default"] = group["variants"][0]["name"]
    result["characters"] = {"count": len(groups), "models": list(groups.values())}
    result["downloaded"] = {"count": len(downloaded), "models": downloaded}
    return result
