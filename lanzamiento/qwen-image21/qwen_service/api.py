from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from . import VERSION, config
from .contracts import normalize
from .logging_utils import logger
from .pricing import RATES


def create_api(submit, poll, read, progress=None):
    """Transport only. All GPU invocation is delegated to the serialized CPU writer."""
    token = os.environ.get("QWEN21_API_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError("Configurar QWEN21_API_TOKEN con al menos 24 caracteres")
    app = FastAPI(title="Qwen Image 2.1 personal", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    origins = [s.strip() for s in os.getenv("QWEN21_CORS_ORIGINS", "").split(",") if s.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                           allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def authenticate(request, call_next):
        if request.method == "GET" and request.url.path in {"/", "/playground"}:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        expected = "Bearer " + token
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            return JSONResponse({"success": False, "error": "Unauthorized"}, status_code=401)
        # Query parameters, auth headers and request bodies are never written to HTTP logs.
        logger("http").info("http.request", extra={"fields": {"method": request.method, "path": request.url.path}})
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        return JSONResponse({"success": False, "error": str(exc)}, status_code=404)

    async def body(request):
        data = await request.body()
        if len(data) > 256 * 1024:
            raise HTTPException(413, "Solicitud demasiado grande")
        try:
            payload = json.loads(data or b"{}")
        except (ValueError, UnicodeError):
            raise HTTPException(400, "JSON inválido")
        if not isinstance(payload, dict):
            raise HTTPException(400, "El cuerpo debe ser un objeto JSON")
        return payload

    async def enqueue(request, operation, payload):
        if operation in {"generate", "edit"}:
            try:
                normalize(payload, operation)  # Shape/limits only; file checks happen on CPU writer.
            except ValidationError as exc:
                raise HTTPException(422, exc.errors(include_context=False, include_input=False))
        key = request.headers.get("Idempotency-Key", "")
        if len(key) > 128:
            raise HTTPException(400, "Idempotency-Key demasiado larga")
        request_id = hashlib.sha256(key.encode()).hexdigest()[:32] if key else uuid.uuid4().hex
        call_id = await submit(operation, payload, request_id)
        return {"success": True, "status": "accepted", "request_id": request_id, "call_id": call_id,
                "status_url": f"/jobs/{call_id}", "record_url": f"/records/{request_id}"}

    def absolute_urls(result: dict, request: Request):
        # Keep protected relative URLs valid when R2 has no public domain.
        for image in result.get("images", []):
            for key in ("url", "volume_url", "metadata_url"):
                if image.get(key, "").startswith("/"):
                    image[key] = str(request.base_url).rstrip("/") + image[key]
        return result

    @app.get("/healthz")
    @app.get("/health")
    async def health():
        return {"ok": True, "status": "ok", "service": "cpu-api", "version": VERSION,
                "gpu_probed": False}

    @app.get("/profiles")
    async def profiles():
        return {"profiles": config.profiles()}

    @app.get("/")
    @app.get("/playground")
    async def playground():
        from pathlib import Path
        return Response((Path(__file__).parent / "playground.html").read_text(encoding="utf-8"),
                        media_type="text/html", headers={"Cache-Control": "no-store",
                        "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})

    @app.get("/pricing")
    async def pricing():
        return {"rates": RATES, "is_estimate": True, "billing_verified": False}

    @app.get("/preflight")
    async def preflight(profile: str = "base"):
        return await read("preflight", {"profile": profile})

    @app.post("/preflight")
    async def selected_preflight(request: Request):
        return await read("preflight", await body(request))

    @app.post("/generate")
    @app.post("/edit")
    async def generate(request: Request):
        operation = request.url.path.strip("/")
        accepted = await enqueue(request, operation, await body(request))
        if request.query_params.get("async", "false").lower() == "true":
            return JSONResponse(accepted, status_code=202)
        # The durable Modal call continues if the HTTP client goes away. Use /jobs for long jobs.
        result = await poll(accepted["call_id"], None)
        return JSONResponse(absolute_urls(result, request), status_code=200 if result.get("success") else 500)

    @app.post("/inputs")
    async def upload_input(request: Request):
        import base64
        from .inputs import MAX_INPUT_BYTES
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_INPUT_BYTES:
                raise HTTPException(413, "Máximo 16 MiB por imagen")
        accepted = await enqueue(request, "upload_input", {"base64": base64.b64encode(raw).decode("ascii")})
        return JSONResponse(accepted, status_code=202)

    @app.post("/jobs")
    async def jobs(request: Request):
        payload = await body(request)
        operation = payload.pop("operation", "generate")
        if operation not in {"generate", "edit"}:
            raise HTTPException(400, "operation debe ser generate o edit")
        return JSONResponse(await enqueue(request, operation, payload), status_code=202)

    @app.get("/jobs/{call_id}")
    async def job(call_id: str, request: Request):
        if not call_id.startswith("fc-") or len(call_id) > 100:
            raise HTTPException(400, "call_id inválido")
        result = await poll(call_id, 0)
        if result is None:
            return JSONResponse({"status": "pending", "call_id": call_id}, status_code=202)
        return absolute_urls(result, request)

    @app.get("/records/{request_id}")
    async def record(request_id: str):
        return await read("job", {"job_id": request_id})

    @app.get("/records/{request_id}/logs")
    async def logs(request_id: str, request: Request, tail: int = 200):
        data = await read("log", {"job_id": request_id, "tail": tail})
        for event in data.get("logs", []):
            for key in ("image_url", "volume_url", "metadata_url"):
                if str(event.get(key, "")).startswith("/"):
                    event[key] = str(request.base_url).rstrip("/") + event[key]
        return data

    @app.get("/records/{request_id}/progress")
    async def live_progress(request_id: str):
        if len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise HTTPException(400, "request_id inválido")
        try:
            data = await progress(request_id) if progress else None
        except Exception:
            return {"request_id": request_id, "progress": None, "temporarily_unavailable": True}
        return {"request_id": request_id, "progress": data}

    @app.get("/records/{request_id}/conversion")
    async def conversion_log(request_id: str):
        data = await read("conversion_report", {"job_id": request_id})
        return Response(data["text"], media_type="text/plain")

    @app.get("/list_models")
    async def list_models(type: str = "lora", version: str | None = None):
        return await read("list_models", {"type": type})

    @app.get("/list_all")
    async def list_all(refresh: bool = False):
        return await read("list_all", {})

    @app.get("/resolve")
    async def resolve(name: str, type: str = "lora"):
        return await read("resolve", {"name": name, "type": type})

    @app.post("/rescan-loras")
    async def rescan():
        return await read("list_models", {"type": "lora"})

    @app.get("/outputs")
    async def outputs(limit: int = 100):
        return await read("outputs", {"limit": limit})

    @app.get("/serve_image")
    async def serve_image(filename: str):
        data = await read("file", {"filename": filename})
        media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".webp": "image/webp", ".json": "application/json"}[data["suffix"]]
        return Response(data["bytes"], media_type=media)

    @app.get("/history")
    async def history():
        return await read("history", {})

    @app.post("/download")
    @app.post("/download/batch")
    @app.post("/models/delete")
    @app.post("/outputs/retry-upload")
    @app.post("/assets/prepare")
    @app.post("/models/convert-fp8")
    async def mutate(request: Request):
        operations = {"/download": "download", "/download/batch": "download_batch",
                      "/models/delete": "delete_models", "/outputs/retry-upload": "retry_upload",
                      "/assets/prepare": "bootstrap", "/models/convert-fp8": "convert_fp8"}
        return JSONResponse(await enqueue(request, operations[request.url.path], await body(request)), status_code=202)

    return app
