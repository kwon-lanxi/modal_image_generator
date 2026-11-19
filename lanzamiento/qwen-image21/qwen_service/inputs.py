"""CPU-only, immutable image inputs. No remote URLs or GPU file uploads."""
import base64
import hashlib
import io
import json
from pathlib import Path
from PIL import Image, ImageOps
from . import config
from .files import safe_relative, atomic_write, write_json

MAX_INPUT_BYTES = 16 * 1024 * 1024


def save_input(payload, commit):
    try:
        raw = base64.b64decode(payload["base64"], validate=True)
    except Exception:
        raise ValueError("Imagen base64 inválida")
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Máximo 16 MiB por imagen")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "is_animated", False):
                raise ValueError("Usar PNG, JPEG o WebP sin animación")
            if source.width * source.height > 16_777_216:
                raise ValueError("La entrada supera 16 megapíxeles")
            original_size = source.size
            im = ImageOps.exif_transpose(source).convert("RGBA" if "A" in source.getbands() or "transparency" in source.info else "RGB")
            im.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            out = io.BytesIO(); im.save(out, "PNG", compress_level=4)
            encoded = out.getvalue()
            digest = hashlib.sha256(encoded).hexdigest()
            metadata = {"id": digest + ".png", "sha256": digest, "width": im.width, "height": im.height,
                        "original_width": original_size[0], "original_height": original_size[1],
                        "size_bytes": len(encoded), "channels": len(im.getbands())}
    except (Image.DecompressionBombError, OSError, SyntaxError):
        raise ValueError("Imagen inválida o dañada")
    image = safe_relative(config.MAIN / "inputs", metadata["id"])
    if not image.exists():
        atomic_write(image, encoded)
        write_json(image.with_suffix(".json"), metadata)
    commit()
    return {"success": True, "input": metadata}


def describe_input(name):
    path = safe_relative(config.MAIN / "inputs", name)
    if path.suffix != ".png" or len(path.stem) != 64 or any(c not in "0123456789abcdef" for c in path.stem):
        raise ValueError("Usar el id devuelto por /inputs")
    if not path.is_file():
        raise FileNotFoundError("Imagen de entrada no encontrada: " + name)
    return json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
