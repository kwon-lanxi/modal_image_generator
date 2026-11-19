"""Local UI fixture. Synthetic image labelled QA; never contacts Modal or R2.

Run: python tests/playground_server.py . Token: local-ui-test-only-not-a-real-token
"""
import asyncio
import json
import os
from pathlib import Path
import struct
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from PIL import Image, ImageDraw
from krea_service import config
from krea_service.api import create_api
from krea_service.backend import Backend, read_operation

config.MAIN = Path(__file__).resolve().parents[1] / ".playground-qa" / "main"
config.LORAS = config.MAIN.parent / "loras"
os.environ["KREA2_API_TOKEN"] = "local-ui-test-only-not-a-real-token"
tasks = {}
lock = threading.Lock()


def small_file(path):
    if path.exists(): return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = json.dumps({"weight": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 2]}}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\0\0")


for preset in config.profiles().values():
    for key, directory in (("model", "diffusion_models"), ("clip", "text_encoders"), ("vae", "vae")):
        small_file(config.MAIN / directory / (preset[key] + ".safetensors"))
    for key in ("preview_lora", "tae"):
        if preset.get(key): small_file(config.MAIN / preset[key])
small_file(config.LORAS / "example" / "example-step0002250.safetensors")


def synthetic(*args):
    request = args[1]
    image = Image.new("RGB", (512, 512), "#233d32")
    draw = ImageDraw.Draw(image)
    draw.ellipse((90, 80, 420, 410), fill="#b9ed94")
    draw.text((145, 245), "QA - SIN GPU", fill="#142317", stroke_width=1)
    now = datetime.now(timezone.utc).isoformat()
    return {"pixels": image.tobytes(), "width": 512, "height": 512,
            "timing": {"sampling": 0.0, "gpu_method_total": 0.0},
            "metadata": {"software": "QA local - imagen sintética, sin GPU",
                         "generation_started_at_utc": now, "generation_finished_at_utc": now, "loras": args[4]}}


class NoR2:
    def upload(self, *args): return {"status": "failed", "error": "QA_NO_R2"}


backend = Backend(lambda: None, synthetic)
backend.r2 = NoR2()


async def submit(operation, payload, request_id):
    def execute():
        with lock:
            if operation not in {"generate", "preview"}:
                return {"success": True, "qa": True, "operation": operation, "payload": payload}
            return backend.execute(operation, payload, request_id)
    call = "fc-" + request_id
    tasks[call] = asyncio.create_task(asyncio.to_thread(execute))
    return call


async def poll(call_id, timeout):
    task = tasks[call_id]
    if timeout == 0 and not task.done(): return None
    return await task


async def read(operation, payload):
    return read_operation(operation, payload)


if __name__ == "__main__":
    uvicorn.run(create_api(submit, poll, read), host="127.0.0.1", port=8764, access_log=False)
