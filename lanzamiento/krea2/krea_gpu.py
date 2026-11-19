"""Stable GPU sub-app. Included in the main App; never deployed separately.

Keep Telegram, HTTP, manager and HTML dependencies out of this module/image.
"""
import json
from pathlib import Path
import sys
import time
import modal
HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from krea_service import config

gpu_app = modal.App()
main_volume = modal.Volume.from_name("krea2-volume")
lora_volume = modal.Volume.from_name("krea2-lora")
ro = {str(config.MAIN): main_volume.with_mount_options(read_only=True), str(config.LORAS): lora_volume.with_mount_options(read_only=True)}
progress_store = modal.Dict.from_name("krea2-personal-v2-progress",create_if_missing=True)

GPU_IMAGE = (
    modal.Image.from_registry("nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04", add_python="3.12")
    .apt_install("git", "libgl1", "libglib2.0-0", "libgomp1")
    .add_local_file(str(HERE / "constraints-gpu.txt"), "/opt/constraints-gpu.txt", copy=True)
    .pip_install("torch==2.10.0", "torchvision==0.25.0",
                 index_url="https://download.pytorch.org/whl/cu128")
    .run_commands(
        "git clone --depth 1 https://github.com/Comfy-Org/ComfyUI.git /root/ComfyUI",
        f"git -C /root/ComfyUI fetch --depth 1 origin {config.COMFY_COMMIT}",
        f"git -C /root/ComfyUI checkout {config.COMFY_COMMIT}",
        "python -m pip install -c /opt/constraints-gpu.txt -r /root/ComfyUI/requirements.txt",
        "git clone --depth 1 https://github.com/spacepxl/ComfyUI-VAE-Utils.git /root/ComfyUI/custom_nodes/ComfyUI-VAE-Utils",
        f"git -C /root/ComfyUI/custom_nodes/ComfyUI-VAE-Utils fetch --depth 1 origin {config.VAE_UTILS_COMMIT}",
        f"git -C /root/ComfyUI/custom_nodes/ComfyUI-VAE-Utils checkout {config.VAE_UTILS_COMMIT}",
        "python -m pip check",
        "python -m pip freeze > /opt/build-packages.txt",
    )
    .env({"PYTHONUNBUFFERED": "1", "PYTORCH_ALLOC_CONF": "expandable_segments:True",
          "TORCHINDUCTOR_COMPILE_THREADS": "1"})
)
GPU_IMAGE = GPU_IMAGE.add_local_file(str(HERE / "krea_gpu.py"), "/root/krea_gpu.py", copy=True)
GPU_SOURCE_FILES = ('__init__.py', 'config.py', 'profiles.json', 'contracts.py', 'files.py', 'logging_utils.py', 'progress.py', 'engine.py', 'snapshot.py')
for name in GPU_SOURCE_FILES:
    GPU_IMAGE = GPU_IMAGE.add_local_file(str(HERE / "krea_service" / name), "/root/krea_service/"+name, copy=True)



@gpu_app.cls(image=GPU_IMAGE, include_source=False,gpu=["L40S", "RTX-PRO-6000", "H100"], cpu=4, memory=(32768, 65536), volumes=ro,
         min_containers=0, max_containers=1, buffer_containers=0,
         single_use_containers=True, timeout=600, startup_timeout=1200,
         retries=0, enable_memory_snapshot=True,
         experimental_options={"enable_gpu_snapshot": True})
class KreaGPU:
    configuration: str = modal.parameter(default="")

    @modal.enter(snap=True)
    def load_snapshot(self):
        from krea_service.engine import Engine
        from krea_service.snapshot import timed, compact_snapshot
        with timed("snapshot.model_initialize"):
            self.engine = Engine(self.configuration)
        with timed("snapshot.warmup"):
            self.engine.warmup()
        compact_snapshot()

    @modal.enter(snap=False)
    def restore(self):
        # No open mmap files: engine disables disk-backed models/mmap before imports.
        from krea_service.snapshot import timed
        with timed("snapshot.reload.main_volume"):
            main_volume.reload()
        with timed("snapshot.reload.lora_volume"):
            lora_volume.reload()

    @modal.method()
    def generate(self, request: dict, job_id: str, assets: dict, loras: list[dict]):
        from krea_service.progress import publisher
        return self.engine.run(request, job_id, assets, loras, progress_sink=publisher(progress_store))


