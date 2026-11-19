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
from krea_service import config

app = modal.App("krea2-personal-v2")
main_volume = modal.Volume.from_name("krea2-volume")
lora_volume = modal.Volume.from_name("krea2-lora")
rw = {str(config.MAIN): main_volume, str(config.LORAS): lora_volume}
ro = {str(config.MAIN): main_volume.with_mount_options(read_only=True),
      str(config.LORAS): lora_volume.with_mount_options(read_only=True)}
api_secret = modal.Secret.from_name("krea2-api")
r2_secret = modal.Secret.from_name("r2-secrets")
hf_secret = modal.Secret.from_name("huggingface-secret")
civitai_secret = modal.Secret.from_name("civitai-apikey")
progress_store = modal.Dict.from_name("krea2-personal-v2-progress", create_if_missing=True)

CPU_BASE = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(HERE / "requirements-cpu.txt"))
)


def with_app_sources(image):
    """CPU containers hydrate modal_app; the included GPU sub-app is independent."""
    return (image
        .add_local_dir(str(HERE / "krea_service"), "/root/krea_service", copy=True,
                       ignore=["__pycache__", "*.pyc"])
        .add_local_file(str(HERE / "krea_gpu.py"), "/root/krea_gpu.py", copy=True)
        .add_local_file(str(HERE / "telegram_bot.py"), "/root/telegram_bot.py", copy=True)
        .add_local_file(str(HERE / "tg_logic.py"), "/root/tg_logic.py", copy=True)
        .add_local_file(str(HERE.parent / "shared_model_conversion.py"), "/root/shared_model_conversion.py", copy=True))


CPU_IMAGE = with_app_sources(CPU_BASE)
MANAGER_IMAGE = with_app_sources(
    CPU_BASE
    .pip_install("torch==2.10.0", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install("pytorch-precision-converter==2.3.0")
)
from krea_gpu import gpu_app, GPU_IMAGE, KreaGPU
app.include(gpu_app)


def commit_volumes():
    main_volume.commit()
    lora_volume.commit()


@app.cls(image=MANAGER_IMAGE, region="eu-west", cpu=0.25, memory=(512, 65536), volumes=rw,
         secrets=[r2_secret, hf_secret, civitai_secret, modal.Secret.from_name("tg-token-image")], timeout=3600,
         min_containers=0, max_containers=1, buffer_containers=0, scaledown_window=2,
         retries=0)
@modal.concurrent(max_inputs=1)
class CPUWriter:
    @modal.enter()
    def initialize(self):
        from krea_service.backend import Backend

        self.telegram_context = None

        def generate(settings, request, job_id, assets, loras):
            configuration = json.dumps(settings, sort_keys=True, separators=(",", ":"))
            if self.telegram_context is None:
                return KreaGPU(configuration=configuration).generate.remote(request, job_id, assets, loras)
            from telegram_bot import notify_progress
            call = KreaGPU(configuration=configuration).generate.spawn(request, job_id, assets, loras)
            while True:
                try:
                    return call.get(timeout=2)
                except TimeoutError:
                    try:
                        event = progress_store.get(job_id, None) or {"stage":"gpu_call"}
                        notify_progress(self.telegram_context, event)
                    except Exception as exc:
                        print(json.dumps({"event":"telegram.progress.failed","error":type(exc).__name__}))

        from krea_service.progress import publisher
        self.backend = Backend(commit_volumes, generate, progress_sink=publisher(progress_store))

    @modal.method()
    def execute(self, operation: str, payload: dict, request_id: str):
        # A single input owns all writer FS access, including reload and commit.
        # Model deletes/conversion/download cannot race a generation's file reads.
        main_volume.reload()
        lora_volume.reload()
        result = self.backend.execute(operation, payload, request_id)
        self.invalidate_catalog(operation)
        self.sync_model_aliases(operation, payload, result)
        return result

    def invalidate_catalog(self, operation):
        if operation in {"download", "download_batch", "delete_models", "bootstrap", "convert_fp8"}:
            from telegram_bot import invalidate_catalog
            try:
                invalidate_catalog()
            except Exception:
                print(json.dumps({"event":"telegram.catalog.invalidate_failed"}))

    def sync_model_aliases(self, operation, payload, result):
        if not result.get("success"):
            return
        update = {"replacements": {}, "deleted": []}
        if operation == "convert_fp8" and result.get("source_removed"):
            update["replacements"][result["source"]] = result["file"]["id"]
        elif operation == "delete_models":
            update["deleted"] = payload.get("paths", [])
        else:
            return
        try:
            modal.Cls.from_name("krea2-personal-v2", "TelegramState")().call.remote("model_alias_sync", "", update)
        except Exception as exc:
            print(json.dumps({"event":"telegram.model_alias.sync_failed","error":type(exc).__name__}))

    def execute_with_download_progress(self, operation, payload, request_id):
        if self.telegram_context is None or operation not in {"download", "download_batch"}:
            return self.backend.execute(operation, payload, request_id)
        from concurrent.futures import ThreadPoolExecutor
        from telegram_bot import notify_progress
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
                    try:
                        notify_progress(self.telegram_context, event)
                    except Exception as exc:
                        print(json.dumps({"event":"telegram.download_progress.failed","error":type(exc).__name__}))
            return future.result()


    @modal.method()
    def execute_bot(self, operation: str, payload: dict, request_id: str, bot_key: str):
        from telegram_bot import state, notify_complete, should_upload
        self.telegram_context = state("get", "job:" + bot_key)
        try:
            main_volume.reload()
            lora_volume.reload()
            result = self.execute_with_download_progress(operation, payload, request_id)
            self.invalidate_catalog(operation)
            self.sync_model_aliases(operation, payload, result)
            try:
                raw = None
                if len(result.get("images",[]))==1 and should_upload(result,self.telegram_context):
                    from krea_service.storage import read_output
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
        from krea_service.backend import read_operation
        main_volume.reload()
        lora_volume.reload()
        # All file handles close before method return or next reload.
        # Bytes are transferred to API, never served lazily from a changing mount.
        return read_operation(operation, payload)


@app.function(image=CPU_IMAGE, secrets=[api_secret], cpu=0.25, memory=512,
              min_containers=0, max_containers=1, scaledown_window=2, timeout=600)
@modal.concurrent(max_inputs=32)
@modal.asgi_app()
def api():
    from krea_service.api import create_api

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
def main(action: str = "preflight", profile: str = "turbo"):
    """Explicit CPU-only preparation; never starts GPU from this entrypoint."""
    import uuid
    # Address the deployed writer/reader: a preparation command must not create
    # another independent writer against the same Volumes.
    reader = modal.Cls.from_name("krea2-personal-v2", "CPUReader")()
    writer = modal.Cls.from_name("krea2-personal-v2", "CPUWriter")()
    if action == "preflight":
        result = reader.read.remote("preflight", {"profile": profile})
    elif action == "prepare-assets":
        result = writer.execute.remote("bootstrap", {}, uuid.uuid4().hex)
    elif action == "convert-fp8":
        result = writer.execute.remote("convert_fp8", {}, uuid.uuid4().hex)
    else:
        raise ValueError("action: preflight, prepare-assets o convert-fp8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


# CPU-only Telegram interface, registered in this same App. No scheduled functions.
BOT_IMAGE = (CPU_IMAGE
    .add_local_file(str(HERE / "tg_mini.html"), "/root/tg_mini.html", copy=True))
import telegram_bot
telegram_bot.register(app, BOT_IMAGE)
