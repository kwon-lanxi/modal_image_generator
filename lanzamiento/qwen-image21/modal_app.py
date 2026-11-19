"""One Modal App: authenticated CPU HTTP API, CPU manager and on-demand GPU.

Deploy from this folder: python -X utf8 -m modal deploy modal_app.py
No remote action occurs merely by importing this file.
"""
import json
from pathlib import Path
import sys

import modal

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from qwen_service import config

app = modal.App("qwen-image21-personal-v1")
main_volume = modal.Volume.from_name("qwen-image21-vol")
rw = {str(config.MAIN): main_volume}
ro = {str(config.MAIN): main_volume.with_mount_options(read_only=True)}
api_secret = modal.Secret.from_name("qwen21-api")
r2_secret = modal.Secret.from_name("r2-secrets")
hf_secret = modal.Secret.from_name("huggingface-secret")
civitai_secret = modal.Secret.from_name("civitai-apikey")
progress_store = modal.Dict.from_name("qwen-image21-personal-v1-progress", create_if_missing=True)

CPU_BASE = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(HERE / "requirements-cpu.txt"))
)
def with_app_sources(image):
    return (image.add_local_dir(str(HERE / "qwen_service"), "/root/qwen_service", copy=True,
                               ignore=["__pycache__", "*.pyc"])
            .add_local_file(str(HERE / "qwen_gpu.py"), "/root/qwen_gpu.py", copy=True)
        .add_local_file(str(HERE / "qwen_telegram.py"), "/root/qwen_telegram.py", copy=True)
            .add_local_file(str(HERE / "qwen_tg_logic.py"), "/root/qwen_tg_logic.py", copy=True)
            .add_local_file(str(HERE.parent / "shared_model_conversion.py"), "/root/shared_model_conversion.py", copy=True))

CPU_IMAGE = with_app_sources(CPU_BASE)
MANAGER_IMAGE = with_app_sources(
    CPU_BASE
    .pip_install("torch==2.10.0", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install("pytorch-precision-converter==2.3.0")
)
from qwen_gpu import gpu_app, GPU_IMAGE, QwenGPU
app.include(gpu_app)


def commit_volumes():
    main_volume.commit()


@app.cls(image=MANAGER_IMAGE, region="eu-west", cpu=0.25, memory=(512, 32768), volumes=rw,
         secrets=[r2_secret, hf_secret, civitai_secret, modal.Secret.from_name("tg-token-image")], timeout=3600,
         min_containers=0, max_containers=1, buffer_containers=0, scaledown_window=2,
         retries=0)
@modal.concurrent(max_inputs=1)
class CPUWriter:
    @modal.enter()
    def initialize(self):
        from qwen_service.backend import Backend

        self.telegram_context = None

        def generate(settings, request, job_id, assets, loras):
            configuration = json.dumps(settings, sort_keys=True, separators=(",", ":"))
            if self.telegram_context is None:
                return QwenGPU(configuration=configuration).generate.remote(request, job_id, assets, loras)
            from qwen_telegram import notify_progress
            call = QwenGPU(configuration=configuration).generate.spawn(request, job_id, assets, loras)
            while True:
                try:
                    return call.get(timeout=2)
                except TimeoutError:
                    try:
                        notify_progress(self.telegram_context, progress_store.get(job_id,None) or {"stage":"gpu_call"})
                    except Exception as exc:
                        print(json.dumps({"event":"telegram.progress.failed","error":type(exc).__name__}))

        from qwen_service.progress import publisher
        self.backend = Backend(commit_volumes, generate, progress_sink=publisher(progress_store))

    @modal.method()
    def execute(self, operation: str, payload: dict, request_id: str):
        # A single input owns all writer FS access, including reload and commit.
        # Model deletes/conversion/download cannot race a generation's file reads.
        main_volume.reload()
        result = self.backend.execute(operation, payload, request_id)
        self.invalidate_catalog(operation)
        self.sync_model_aliases(operation, payload, result)
        return result

    def invalidate_catalog(self, operation):
        if operation in {"download", "download_batch", "delete_models", "bootstrap", "convert_fp8"}:
            from qwen_telegram import invalidate_catalog
            try: invalidate_catalog()
            except Exception: print(json.dumps({"event":"telegram.catalog.invalidate_failed"}))

    def sync_model_aliases(self, operation, payload, result):
        if not result.get("success"):
            return
        update = {"replacements": {}, "deleted": []}
        if operation == "convert_fp8" and result.get("source_removed"):
            update["replacements"][result["source"]] = result["file"]["id"]
        elif operation == "bootstrap":
            for converted in result.get("assets", []):
                if converted.get("source_removed"):
                    update["replacements"][converted["source"]] = converted["file"]["id"]
        elif operation == "delete_models":
            update["deleted"] = payload.get("paths", [])
        else:
            return
        if not update["replacements"] and not update["deleted"]:
            return
        try:
            modal.Cls.from_name("qwen-image21-personal-v1", "TelegramState")().call.remote("model_alias_sync", "", update)
        except Exception as exc:
            print(json.dumps({"event":"telegram.model_alias.sync_failed","error":type(exc).__name__}))

    def execute_with_download_progress(self, operation, payload, request_id):
        if self.telegram_context is None or operation not in {"download", "download_batch"}:
            return self.backend.execute(operation, payload, request_id)
        from concurrent.futures import ThreadPoolExecutor
        from qwen_telegram import notify_progress
        file_name = payload.get("filename") or payload.get("name") or "archivo"
        if operation == "download_batch":
            items = payload.get("items") or [{}]
            file_name = str(items[0].get("filename") or "lote de archivos")
        try:
            notify_progress(self.telegram_context, {"event":"download.start", "file":file_name})
        except Exception:
            pass
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="asset-download") as pool:
            future = pool.submit(self.backend.execute, operation, payload, request_id)
            while not future.done():
                import time
                time.sleep(2)
                event = progress_store.get(request_id, None)
                if event and event.get("event") == "download.progress":
                    try: notify_progress(self.telegram_context, event)
                    except Exception as exc: print(json.dumps({"event":"telegram.download_progress.failed","error":type(exc).__name__}))
            return future.result()

    @modal.method()
    def execute_bot(self, operation: str, payload: dict, request_id: str, bot_key: str):
        from qwen_telegram import state, notify_complete, should_upload, prepare_inputs
        self.telegram_context = state("get", "job:"+bot_key)
        try:
            main_volume.reload()
            try:
                payload, operation = prepare_inputs(self.telegram_context, payload, operation, self.backend)
                state("merge", "job:"+bot_key, {"recipe":payload,"operation":operation})
                result = self.execute_with_download_progress(operation, payload, request_id)
                self.invalidate_catalog(operation)
                self.sync_model_aliases(operation, payload, result)
            except Exception as exc:
                result = {"success":False,"error":type(exc).__name__,"detail":"No se pudieron preparar las imágenes de entrada"}
            try:
                raw = None
                if len(result.get("images",[]))==1 and should_upload(result,self.telegram_context):
                    from qwen_service.storage import read_output
                    raw = read_output(result["images"][0]["filename"])[0]
                notify_complete(bot_key, result, raw)
            except Exception as exc:
                print(json.dumps({"event":"telegram.delivery.failed","job_id":request_id,"error":type(exc).__name__,"error_kind":getattr(exc,"kind",None),"telegram_code":getattr(exc,"code",None),"ambiguous":getattr(exc,"ambiguous",None)}))
            return result
        finally:
            self.telegram_context = None


@app.cls(image=CPU_IMAGE, cpu=0.25, memory=512, volumes=ro,
         min_containers=0, max_containers=1, buffer_containers=0, scaledown_window=2,
         timeout=120)
@modal.concurrent(max_inputs=1)
class CPUReader:
    @modal.method()
    def read(self, operation: str, payload: dict):
        from qwen_service.backend import read_operation
        main_volume.reload()
        # All file handles close before method return or next reload.
        # Bytes are transferred to API, never served lazily from a changing mount.
        return read_operation(operation, payload)


@app.function(image=CPU_IMAGE, secrets=[api_secret], cpu=0.25, memory=512,
              min_containers=0, max_containers=1, scaledown_window=2, timeout=600)
@modal.concurrent(max_inputs=32)
@modal.asgi_app()
def api():
    from qwen_service.api import create_api

    async def submit(operation, payload, request_id):
        call = await CPUWriter().execute.spawn.aio(operation, payload, request_id)
        return call.object_id

    async def poll(call_id, timeout):
        try:
            return await modal.FunctionCall.from_id(call_id).get.aio(timeout=timeout)
        except TimeoutError:
            return None
        except modal.exception.OutputExpiredError:
            return {"success": False, "error": "OUTPUT_EXPIRED",
                    "detail": "Consultar /records/{request_id}, /history o /outputs: la copia de Volume permanece"}

    async def read(operation, payload):
        return await CPUReader().read.remote.aio(operation, payload)

    async def progress(job_id):
        return await progress_store.get.aio(job_id, None)

    return create_api(submit, poll, read, progress=progress)


@app.local_entrypoint()
def main(action: str = "preflight", profile: str = "base"):
    """Explicit CPU-only preparation; never starts GPU from this entrypoint."""
    import uuid
    # Address the deployed writer/reader: a preparation command must not create
    # another independent writer against the same Volumes.
    reader = modal.Cls.from_name("qwen-image21-personal-v1", "CPUReader")()
    writer = modal.Cls.from_name("qwen-image21-personal-v1", "CPUWriter")()
    if action == "preflight":
        result = reader.read.remote("preflight", {"profile": profile})
    elif action == "prepare-assets":
        result = writer.execute.remote("bootstrap", {}, uuid.uuid4().hex)
    elif action == "convert-fp8":
        result = writer.execute.remote("convert_fp8", {}, uuid.uuid4().hex)
    else:
        raise ValueError("action: preflight, prepare-assets o convert-fp8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


BOT_IMAGE = CPU_IMAGE.add_local_file(str(HERE / "qwen_mini.html"), "/root/qwen_mini.html", copy=True)
import qwen_telegram
qwen_telegram.register(app, BOT_IMAGE)
