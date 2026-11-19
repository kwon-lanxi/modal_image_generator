"""Check built container artifacts on CPU, including the GPU image.

Run: python -X utf8 lanzamiento/qwen-image21/scripts/check_image_imports.py
Creates short-lived CPU Sandboxes; does not allocate a GPU or generate images.
"""
import ast
import sys
from pathlib import Path

import modal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modal_app


PROBE = """
import ast
import importlib.util
from pathlib import Path
for name in ('qwen_telegram', 'qwen_tg_logic', 'qwen_service.config'):
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise ModuleNotFoundError("No module named '" + name + "'")
    ast.parse(Path(spec.origin).read_text())
# CPU images include the SDK in their requirements. Function containers supply
# it at runtime on GPU; the GPU-image Sandbox has no Function runtime mounts.
if importlib.util.find_spec('modal') is not None:
    import qwen_telegram
    import qwen_tg_logic
    import qwen_service.config
print('SHARED_IMPORTS_OK')
"""

GPU_PROBE = """
import ast
import importlib.util
from pathlib import Path
for name in ("qwen_gpu", "qwen_service.config", "qwen_service.engine", "qwen_service.snapshot"):
    spec=importlib.util.find_spec(name)
    assert spec is not None, name
    ast.parse(Path(spec.origin).read_text())
import qwen_gpu
assert qwen_gpu.gpu_app is not None
for name in ("qwen_telegram", "qwen_service.backend", "qwen_service.storage", "qwen_service.api"):
    assert importlib.util.find_spec(name) is None, "CPU dependency leaked into GPU: "+name
print('SHARED_IMPORTS_OK')
"""


def main():
    app = modal.App.lookup("qwen-image21-personal-v1")
    for name in ("CPU_IMAGE", "MANAGER_IMAGE", "GPU_IMAGE", "BOT_IMAGE"):
        image=getattr(modal_app,name)
        if name=="GPU_IMAGE":
            # Function containers supply Modal at runtime. Supply the same SDK
            # to this CPU-only Sandbox to exercise actual module hydration.
            image=image.pip_install("modal==1.6.4")
        sandbox = modal.Sandbox.create(
            "python", "-c", GPU_PROBE if name=="GPU_IMAGE" else PROBE, image=image,
            app=app, cpu=.25, memory=512, timeout=90,
        )
        try:
            sandbox.wait()
            stdout, stderr = sandbox.stdout.read(), sandbox.stderr.read()
            if sandbox.returncode or "SHARED_IMPORTS_OK" not in stdout:
                raise RuntimeError(f"{name}: {stdout}\n{stderr}")
            print(name + ": SHARED_IMPORTS_OK")
        finally:
            sandbox.terminate()


if __name__ == "__main__":
    main()
