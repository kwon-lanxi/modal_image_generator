from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import copy
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, PngImagePlugin

from . import config
from .files import atomic_write, safe_relative, write_json
from .image_metadata import encode as encode_pixels
from .pricing import estimate


def output_paths(job_id: str, mode: str, created: datetime, seed: int) -> tuple[Path, Path]:
    suffix = ".jpg" if mode == "preview" else ".png"
    label = "moss_preview" if mode == "preview" else "moss"
    stem = f"{created:%Y%m%d_%H%M%S}_{label}_{seed}_batch0_{job_id}"
    return (config.MAIN / "outputs" / (stem + suffix),
            config.MAIN / "outputs" / (stem + ".json"))


class R2Store:
    def __init__(self):
        self.client = None

    def upload(self, raw: bytes, image: Path, metadata: dict) -> dict:
        import boto3
        from botocore.config import Config

        account = os.environ.get("R2_ACCOUNT_ID", "")
        access = os.environ.get("R2_ACCESS_KEY_ID", "")
        secret = os.environ.get("R2_SECRET_ACCESS_KEY", "")
        if not all((account, access, secret)):
            return {"status": "failed", "error": "R2_CREDENTIALS_MISSING"}
        settings = config.r2_settings()
        key = metadata["r2"]["key"]
        try:
            if self.client is None:
                self.client = boto3.client(
                    "s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
                    aws_access_key_id=access, aws_secret_access_key=secret, region_name="auto",
                    config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}),
                )
            self.client.put_object(
                Bucket=settings["bucket"], Key=key, Body=raw,
                ContentType="image/jpeg" if image.suffix == ".jpg" else "image/png",
                Metadata={"job-id": metadata["job_id"], "sha256": metadata["sha256"]},
            )
            # Full recipe in a sidecar; S3 user metadata is too small for prompts/LoRAs.
            self.client.put_object(
                Bucket=settings["bucket"], Key=key + ".json",
                Body=json.dumps({**metadata, "r2": {**metadata["r2"], "status": "uploaded"}},
                                ensure_ascii=False).encode(), ContentType="application/json",
            )
        except Exception as exc:
            return {"status": "failed", "error": type(exc).__name__}
        public = settings["public_base"]
        return {"status": "uploaded", "url": f"{public}/{key}" if public else None}


def save_result(result: dict, request: dict, job_id: str, log, commit, r2: R2Store) -> dict:
    finished = datetime.fromisoformat(result["metadata"].get(
        "generation_finished_at_utc", datetime.now(timezone.utc).isoformat()))
    zone = os.getenv("MOSS_TIMEZONE", "America/Argentina/Buenos_Aires")
    local = finished.astimezone(ZoneInfo(zone))
    image, sidecar = output_paths(job_id, request["mode"], local, request["seed"])
    batch_index=result["metadata"].get("batch_index",0)
    if batch_index:
        image=image.with_name(image.name.replace("_batch0_",f"_batch{batch_index}_"))
        sidecar=image.with_suffix(".json")
    timing = copy.deepcopy(result["timing"])
    pricing = estimate(timing, result["metadata"], timing.get("render_total_observed", 0))
    preset = request.get("effective_profile", {})
    metadata = {
        "schema_version": "2.1", "job_id": job_id, "filename": image.name,
        "created_at": local.isoformat(), "created_at_utc": finished.astimezone(timezone.utc).isoformat(),
        "timezone": zone,
        "generation_started_at_utc": result["metadata"].get("generation_started_at_utc"),
        "generation_finished_at_utc": finished.astimezone(timezone.utc).isoformat(),
        "request": copy.deepcopy(request), "generation": copy.deepcopy(result["metadata"]),
        "timing": timing, "pricing": pricing,
        "seed": request["seed"], "seed_string": str(request["seed"]), "prompt": request["positive_prompt"],
        "negative_prompt": request["negative_prompt"], "steps": request["steps"], "cfg": request["cfg"],
        "sampler_name": request["sampler_name"], "scheduler": request["scheduler"],
        "model": preset.get("model"), "clip": preset.get("clip"), "vae": preset.get("vae"),
        "loras": result["metadata"].get("loras", []),
        "width": result["width"], "height": result["height"],
    }
    started = time.perf_counter()
    raw = encode_pixels(result, metadata)
    encoding_seconds = round(time.perf_counter() - started, 4)
    digest = hashlib.sha256(raw).hexdigest()
    day = metadata["created_at_utc"][:10].replace("-", "/")
    metadata.update(filename=image.name, sha256=digest, size_bytes=len(raw),
                    r2={"status": "pending", "key": f"moss/{day}/{image.name}"})
    metadata["delivery"] = {"encoding_seconds": encoding_seconds,
                            "note": "Las métricas de entrega y hash se añaden al manifiesto después de codificar"}
    atomic_write(image, raw)
    write_json(sidecar, metadata)
    # A recoverable Volume copy is committed BEFORE attempting the external upload.
    commit()
    log.event("output.volume.saved", filename=image.name, bytes=len(raw), sha256=digest)
    upload = r2.upload(raw, image, metadata)
    metadata["r2"].update(upload)
    write_json(sidecar, metadata)
    commit()
    log.event("output.r2." + upload["status"], filename=image.name, error=upload.get("error"))
    ready = response(metadata)
    log.event("output.ready", filename=image.name, image_url=ready["images"][0]["url"],
              volume_url=ready["images"][0]["volume_url"],
              metadata_url=ready["images"][0]["metadata_url"])
    return response(metadata, raw if request["return_base64"] else None)


def response(metadata: dict, raw: bytes | None = None) -> dict:
    image = {
        key: metadata[key] for key in ("filename", "sha256", "size_bytes", "width", "height")
    }
    image["url"] = metadata["r2"].get("url") or f"/serve_image?filename={metadata['filename']}"
    image["volume_url"] = f"/serve_image?filename={metadata['filename']}"
    image["r2"] = metadata["r2"]
    image["mime_type"] = "image/jpeg" if metadata["filename"].endswith(".jpg") else "image/png"
    image["metadata_url"] = f"/serve_image?filename={Path(metadata['filename']).stem}.json"
    if raw is not None:
        image["base64"] = base64.b64encode(raw).decode("ascii")
    return {"success": True, "seed": metadata["request"]["seed"], "task_id": metadata["job_id"],
            "seed_string": str(metadata["request"]["seed"]),
            "images": [image], "timing": metadata["timing"], "metadata": metadata,
            "pricing": metadata.get("pricing"), "created_at": metadata["created_at"],
            "created_at_utc": metadata.get("created_at_utc"), "timezone": metadata.get("timezone"),
            "warnings": [] if metadata["r2"]["status"] == "uploaded" else ["R2 pendiente; copia guardada en Volume"]}


def read_output(filename: str) -> tuple[bytes, str]:
    path = safe_relative(config.MAIN / "outputs", filename)
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".json"}:
        raise ValueError("Formato de salida inválido")
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("Archivo demasiado grande para esta API")
    return path.read_bytes(), path.suffix.lower()


def outputs(limit=100) -> list[dict]:
    root = config.MAIN / "outputs"
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    results = []
    for file in files:
        try:
            meta = json.loads(file.read_text(encoding="utf-8"))
            if "request" in meta and "r2" in meta:
                results.append(response(meta))
        except (ValueError, KeyError):
            continue
        if len(results) >= limit:
            break
    # Existing output images remain accessible; do not invent missing legacy metadata.
    known = {item["images"][0]["filename"] for item in results}
    legacy = sorted((p for p in root.glob("*") if p.suffix.lower() in {".png", ".jpg", ".webp"}),
                    key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for path in legacy:
        if len(results) >= limit:
            break
        if path.name not in known and not path.with_suffix(".json").exists():
            results.append({"success": True, "legacy": True, "images": [{
                "filename": path.name, "size_bytes": path.stat().st_size,
                "url": f"/serve_image?filename={path.name}"}]})
    return results
