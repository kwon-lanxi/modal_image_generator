"""PNG text and JPEG EXIF + lossless metadata comments, entirely on CPU."""
from __future__ import annotations

import base64
import io
import json
import struct
import zlib
from datetime import datetime

from PIL import Image, PngImagePlugin


def parameters(metadata: dict) -> str:
    request = metadata["request"]
    preset = request.get("effective_profile", {})
    loras = [{key: item[key] for key in ("id", "strength", "sha256") if key in item}
             for item in metadata.get("generation", {}).get("loras", [])]
    return (f"{request['positive_prompt']}\nNegative prompt: {request['negative_prompt']}\n"
            f"Steps: {request['steps']}, Sampler: {request['sampler_name']}, Scheduler: {request['scheduler']}, "
            f"CFG scale: {request['cfg']}, Seed: {request['seed']}, "
            f"Size: {metadata['width']}x{metadata['height']}, Model: {preset.get('model', '')}, "
            f"VAE: {preset.get('vae', '')}, Precision: {preset.get('weight_dtype', '')}, "
            f"LoRAs: {json.dumps(loras, ensure_ascii=False)}, Created at: {metadata['created_at']}, "
            f"Generation seconds: {metadata['timing'].get('gpu_method_total')}, "
            f"Estimated USD: {metadata.get('pricing', {}).get('total')}")


def encode(result: dict, metadata: dict) -> bytes:
    channels = result.get("channels", 3)
    expected = result["width"] * result["height"] * channels
    if len(result["pixels"]) != expected:
        raise ValueError("Tamaño de píxeles inesperado")
    image = Image.frombytes("RGBA" if channels == 4 else "RGB", (result["width"], result["height"]), result["pixels"])
    output = io.BytesIO()
    text = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if metadata["request"]["mode"] in {"generate", "edit"}:
        info = PngImagePlugin.PngInfo()
        if len(text.encode("utf-8")) <= 524288:
            info.add_text("qwen21", text, zip=True)
        else:
            chunks = [text[i:i + 131072] for i in range(0, len(text), 131072)]
            info.add_text("qwen21", json.dumps({"encoding": "qwen21-text-chunks", "parts": len(chunks)}))
            for index, chunk in enumerate(chunks):
                info.add_text(f"qwen21.part{index + 1:04d}", chunk, zip=True)
        info.add_text("parameters", parameters(metadata), zip=True)
        info.add_text("Creation Time", metadata["created_at_utc"])
        image.save(output, "PNG", pnginfo=info, compress_level=4, optimize=False)
        return output.getvalue()



def extract(raw: bytes) -> dict:
    """Round-trip helper for tests, metadata inspection and downloaded originals."""
    if raw.startswith(b"\x89PNG"):
        with Image.open(io.BytesIO(raw)) as image:
            result = json.loads(image.info["qwen21"])
            if result.get("encoding") == "qwen21-text-chunks":
                return json.loads("".join(image.info[f"qwen21.part{i:04d}"] for i in range(1, result["parts"] + 1)))
            return result
    raise ValueError("Se esperaba un PNG original Qwen Image 2.1")
