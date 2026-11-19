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
    expected = result["width"] * result["height"] * 3
    if len(result["pixels"]) != expected:
        raise ValueError("Tamaño de píxeles inesperado")
    image = Image.frombytes("RGB", (result["width"], result["height"]), result["pixels"])
    output = io.BytesIO()
    text = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if metadata["request"]["mode"] != "preview":
        info = PngImagePlugin.PngInfo()
        if len(text.encode("utf-8")) <= 524288:
            info.add_text("krea2", text, zip=True)
        else:
            chunks = [text[i:i + 131072] for i in range(0, len(text), 131072)]
            info.add_text("krea2", json.dumps({"encoding": "krea2-text-chunks", "parts": len(chunks)}))
            for index, chunk in enumerate(chunks):
                info.add_text(f"krea2.part{index + 1:04d}", chunk, zip=True)
        info.add_text("parameters", parameters(metadata), zip=True)
        info.add_text("Creation Time", metadata["created_at_utc"])
        image.save(output, "PNG", pnginfo=info, compress_level=4, optimize=False)
        return output.getvalue()

    exif = Image.Exif()
    created = datetime.fromisoformat(metadata["created_at"])
    exif[306] = created.strftime("%Y:%m:%d %H:%M:%S")
    exif[36867] = exif[306]  # DateTimeOriginal
    offset = created.strftime("%z")
    exif[36881] = offset[:3] + ":" + offset[3:]  # OffsetTimeOriginal
    exif[305] = metadata.get("generation", {}).get("software", "Krea2 personal")
    exif[270] = f"Krea2; seed={metadata['request']['seed']}; created={metadata['created_at']}"
    ascii_json = json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode()
    comment = ascii_json if len(ascii_json) < 55000 else b"KREA2_ZLIB_BASE64:" + base64.b64encode(zlib.compress(text.encode()))
    exif[37510] = b"ASCII\0\0\0" + (comment if len(comment) < 58000 else b"Full metadata: KREA2META JPEG comment segments")
    image.save(output, "JPEG", quality=82, optimize=False, exif=exif)
    # JPEG segments have a 64 KiB limit. These numbered chunks preserve arbitrarily
    # large Unicode metadata even if it exceeds the EXIF UserComment budget.
    data = text.encode("utf-8")
    chunks = [data[i:i + 60000] for i in range(0, len(data), 60000)]
    segments = b""
    for number, chunk in enumerate(chunks):
        body = f"KREA2META {number + 1}/{len(chunks)} ".encode() + chunk
        segments += b"\xff\xfe" + struct.pack(">H", len(body) + 2) + body
    raw = output.getvalue()
    return raw[:2] + segments + raw[2:]


def extract(raw: bytes) -> dict:
    """Round-trip helper for tests, metadata inspection and downloaded originals."""
    if raw.startswith(b"\x89PNG"):
        with Image.open(io.BytesIO(raw)) as image:
            result = json.loads(image.info["krea2"])
            if result.get("encoding") == "krea2-text-chunks":
                return json.loads("".join(image.info[f"krea2.part{i:04d}"] for i in range(1, result["parts"] + 1)))
            return result
    if not raw.startswith(b"\xff\xd8"):
        raise ValueError("Se esperaba PNG o JPEG original")
    chunks, offset = {}, 2
    total = None
    while offset + 4 <= len(raw):
        marker = raw[offset:offset + 2]
        if marker in {b"\xff\xda", b"\xff\xd9"}:
            break
        length = struct.unpack(">H", raw[offset + 2:offset + 4])[0]
        if length < 2 or offset + 2 + length > len(raw):
            raise ValueError("Segmento JPEG inválido")
        body = raw[offset + 4:offset + 2 + length]
        if marker == b"\xff\xfe" and body.startswith(b"KREA2META "):
            _, numbering, content = body.split(b" ", 2)
            index, total = map(int, numbering.split(b"/"))
            chunks[index] = content
        offset += length + 2
    if total is None or set(chunks) != set(range(1, total + 1)):
        raise ValueError("Metadatos Krea2 incompletos")
    return json.loads(b"".join(chunks[i] for i in range(1, total + 1)))
