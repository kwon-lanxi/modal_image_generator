from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import VERSION, config
from .contracts import normalize
from .files import (MODEL_TYPES, describe, list_models, resolve, safe_relative,
                    volume_path, write_json, atomic_write)
from .logging_utils import JobLog, redact
from .manager import bootstrap, convert_fp8, delete_models, download
from .storage import R2Store, outputs, read_output, response, save_result


def prepare_generation(payload: dict, mode: str) -> tuple[dict, dict, list[dict]]:
    request = normalize(payload, mode)
    preset = request["effective_profile"]
    assets = {key: describe(resolve(preset[key], kind), include_metadata=True)
              for key, kind in (("model", "unet"), ("clip", "clip"), ("vae", "vae"))}
    if assets["model"].get("compatibility_error"):
        raise ValueError(assets["model"]["compatibility_error"])
    if preset.get("tae") and (config.MAIN / preset["tae"]).is_file():
        assets["tae"] = describe(resolve(preset["tae"], "tae"))
    # The actual selection + file versions form the Modal snapshot identity.
    for key, asset in assets.items():
        preset[key] = asset["id"]
    preset["asset_versions"] = {key: asset["version"] for key, asset in assets.items()}
    # Sampling settings belong to each request, not the weight snapshot key.
    preset.pop("steps", None)
    preset.pop("cfg", None)
    loras = []
    for name, weight in request["lora"].items():
        if weight != 0:
            loras.append({**describe(resolve(name, "lora"), include_metadata=True), "strength": weight})
    if mode == "preview":
        # Fail in CPU before starting GPU if optional preview assets are absent.
        resolve(preset["tae"], "tae")
        distill = describe(resolve(preset["preview_lora"], "lora"))
        if any(item["id"] == distill["id"] for item in loras):
            raise ValueError("El adaptador de dos pasos lo administra el preset preview")
        loras.append({**distill, "strength": 1.0, "role": "distillation"})
    elif preset.get("preview_lora"):
        if any(item["id"] == preset["preview_lora"] for item in loras):
            raise ValueError("Usar /preview para el adaptador de dos pasos")
    return request, assets, loras


def read_operation(operation: str, payload: dict):
    if operation == "list_models":
        return list_models(payload.get("type", "lora"))
    if operation == "list_all":
        return {"success": True, "models": {kind: list_models(kind) for kind in MODEL_TYPES}}
    if operation == "resolve":
        return {"success": True, **describe(resolve(payload["name"], payload.get("type", "lora")), True)}
    if operation == "outputs":
        return {"success": True, "outputs": outputs(min(max(int(payload.get("limit", 100)), 1), 500))}
    if operation == "file":
        raw, suffix = read_output(payload["filename"])
        return {"bytes": raw, "suffix": suffix}
    if operation == "history":
        root = config.MAIN / "_release" / "jobs"
        records = sorted(root.glob("*/status.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        return {"entries": [json.loads(p.read_text(encoding="utf-8")) for p in records[:100]]}
    if operation in {"log", "job", "conversion_report"}:
        job_id = payload["job_id"]
        if not job_id or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("request_id inválido")
        directory = config.MAIN / "_release" / "jobs" / job_id
        if operation == "log":
            tail = min(max(int(payload.get("tail", 200)), 1), 1000)
            records = []
            for name in ("cpu.jsonl", "gpu.jsonl"):
                path = directory / name
                if path.exists():
                    records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
            records.sort(key=lambda item: item["ts"])
            return {"job_id": job_id, "logs": records[-tail:]}
        if operation == "conversion_report":
            return {"text": (directory / "conversion.txt").read_text(encoding="utf-8", errors="replace")}
        return json.loads((directory / "status.json").read_text(encoding="utf-8"))
    if operation == "preflight":
        checked = normalize({"positive_prompt": "preflight", **payload}, "generate")
        preset = checked["effective_profile"]
        result, errors = {}, []
        for key, kind in (("model", "unet"), ("clip", "clip"), ("vae", "vae")):
            try:
                result[key] = describe(resolve(preset[key], kind), include_metadata=True)
                if result[key].get("compatibility_error"):
                    errors.append(result[key]["compatibility_error"])
            except (ValueError, FileNotFoundError) as exc:
                errors.append(str(exc))
        for key, kind in (("tae", "tae"), ("preview_lora", "lora")):
            if preset.get(key):
                try:
                    result[key] = describe(resolve(preset[key], kind))
                except (ValueError, FileNotFoundError) as exc:
                    result[key] = {"available": False, "error": str(exc)}
        return {"success": not errors, "profile": preset, "files": result, "errors": errors,
                "gpu_tested": False, "note": "Solo valida archivos/metadatos; no prueba inferencia ni VRAM"}
    raise ValueError("Operación de lectura desconocida")


class Backend:
    """One serialized CPU writer. GPU requests and model mutations cannot race."""

    def __init__(self, commit, generate, progress_sink=None):
        self.commit, self.generate, self.r2 = commit, generate, R2Store()
        self.progress_sink = progress_sink

    def execute(self, operation: str, payload: dict, request_id: str):
        started = time.perf_counter()
        if len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
            raise ValueError("request_id inválido")
        directory = config.MAIN / "_release" / "jobs" / request_id
        digest = hashlib.sha256(json.dumps({"operation": operation, "payload": payload},
                                            sort_keys=True, allow_nan=False).encode()).hexdigest()
        status_path, result_path = directory / "status.json", directory / "result.json"
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status["input_hash"] != digest:
                raise ValueError("Idempotency-Key ya utilizada con otra solicitud")
            if status["status"] == "done" and result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if operation in {"generate", "preview"} and payload.get("return_base64", True):
                    import base64
                    for image in result["images"]:
                        image["base64"] = base64.b64encode(read_output(image["filename"])[0]).decode()
                return result
            raise RuntimeError("Trabajo ya registrado; consultar su estado antes de repetir inferencia")
        log = JobLog(request_id, directory / "cpu.jsonl", sink=self.progress_sink)
        status = {"request_id": request_id, "operation": operation, "status": "running", "input_hash": digest,
                  "started_at_utc": datetime.now(timezone.utc).isoformat()}
        write_json(status_path, status)
        self.commit()
        log.event("job.start", operation=operation, software=VERSION)
        timing = {}
        try:
            if operation in {"generate", "preview"}:
                with log.stage("prepare", timing):
                    request, assets, loras = prepare_generation(payload, operation)
                    write_json(directory / "request.json", {"request": request, "assets": assets, "loras": loras})
                    self.commit()
                with log.stage("gpu_call", timing):
                    generated = self.generate(request["effective_profile"], request, request_id, assets, loras)
                gpu_events = generated.pop("events", [])
                if gpu_events:
                    atomic_write(directory / "gpu.jsonl", "".join(json.dumps(e) + "\n" for e in gpu_events).encode())
                generated["timing"].update(timing)
                generated["timing"]["render_total_observed"] = round(time.perf_counter() - started, 4)
                generated["timing"]["total"] = generated["timing"]["render_total_observed"]
                generated["metadata"]["orchestrator_started_at_utc"] = status["started_at_utc"]
                with log.stage("persist_and_upload", timing):
                    batch_pixels = generated.pop("batch_pixels", [])
                    if batch_pixels:
                        images=[]
                        batch_results=[]
                        for index,(pixels,seed) in enumerate(zip(batch_pixels,request["seeds"])):
                            child_request={**request,"n":1,"seed":seed,"seed_string":str(seed),"seeds":[seed]}
                            child_generated={**generated,"pixels":pixels,"timing":{k:v/request["n"] for k,v in generated["timing"].items()},"metadata":{**generated["metadata"],"batch_timing":generated["timing"],"batch_index":index,"batch_count":request["n"],"parent_job_id":request_id}}
                            child_id=hashlib.sha256((request_id+":"+str(index)).encode()).hexdigest()[:32]
                            child=save_result(child_generated,child_request,child_id,log,self.commit,self.r2)
                            images.extend(child["images"])
                            batch_results.append({k:v for k,v in child.items() if k!="images"})
                            write_json(directory / "batch_partial.json", {"images":images,"batch":batch_results})
                            self.commit()
                        result={**batch_results[0],"task_id":request_id,"images":images,"batch":batch_results,"seeds":request["seeds"],"timing":generated["timing"],"pricing":{**batch_results[0]["pricing"],"total":sum(item["pricing"]["total"] for item in batch_results)}}
                        for cost_key in ("method_only_total","rpc_scenario_total"):
                            result["pricing"][cost_key]=sum(item["pricing"][cost_key] for item in batch_results)
                        result["pricing"]["components"]={k:sum(item["pricing"]["components"][k] for item in batch_results) for k in batch_results[0]["pricing"]["components"]}
                    else:
                        result = save_result(generated, request, request_id, log, self.commit, self.r2)
                result["delivery_timing"] = {"persist_and_upload": timing["persist_and_upload"]}
            elif operation == "download":
                result = download(payload, request_id, log, self.commit)
            elif operation == "download_batch":
                items = payload.get("items", [])
                if not isinstance(items, list) or not 1 <= len(items) <= 20:
                    raise ValueError("El lote debe tener entre 1 y 20 archivos")
                result = {"success": True, "results": [download(item, request_id, log, self.commit) for item in items]}
            elif operation == "bootstrap":
                result = bootstrap(request_id, log, self.commit)
            elif operation == "convert_fp8":
                result = convert_fp8(payload, request_id, log, self.commit)
            elif operation == "delete_models":
                result = delete_models(payload.get("paths", []), log)
            elif operation == "retry_upload":
                image_path = safe_relative(config.MAIN / "outputs", payload["filename"])
                if image_path.suffix not in {".png", ".jpg"}:
                    raise ValueError("Formato no válido")
                sidecar = image_path.with_suffix(".json")
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                raw = image_path.read_bytes()
                metadata["r2"].update(self.r2.upload(raw, image_path, metadata))
                write_json(sidecar, metadata)
                result = response(metadata)
            else:
                raise ValueError("Operación desconocida")
            # Keep the CPU/API boundary free of TorchVersion and other provider subclasses.
            result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
            stored = {**result}
            if "images" in stored:
                stored["images"] = [{k: v for k, v in item.items() if k != "base64"} for item in stored["images"]]
            write_json(result_path, stored)
            status.update(status="done", success=True)
            log.event("job.complete", operation=operation)
            return result
        except Exception as exc:
            # Never expose provider exception messages (they can contain signed URLs/credentials).
            code = type(exc).__name__
            detail = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else "Consultar logs del trabajo y de Modal"
            status.update(status="failed", success=False, error=code, detail=detail)
            log.log.exception("job.failed", extra={"fields": {"job_id": request_id, "operation": operation}})
            log.event("job.failed", level=logging.ERROR, error=code, detail=redact(exc))
            return {"success": False, "task_id": request_id, "error": code, "detail": detail}
        finally:
            status["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
            status["elapsed_seconds"] = round(time.perf_counter() - started, 4)
            write_json(status_path, status)
            self.commit()
