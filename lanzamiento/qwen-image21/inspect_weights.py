"""CPU-only weight header inspection; never downloads weight tensors locally."""
from pathlib import Path
import json
import struct
import modal
app = modal.App("qwen21-inspect")

@app.function(image=modal.Image.debian_slim(python_version="3.12"), cpu=0.25, memory=512,
    volumes={"/models": modal.Volume.from_name("qwen-image21-vol").with_mount_options(read_only=True)})
def inspect_header(path: str):
    target = (Path("/models") / path).resolve()
    if not target.is_relative_to(Path("/models").resolve()):
        raise ValueError("Path outside volume")
    with target.open("rb") as f:
        length = struct.unpack("<Q", f.read(8))[0]
        if length > 64 * 1024 * 1024: raise ValueError("Invalid header")
        header = json.loads(f.read(length))
    return {"path": path, "size_bytes": target.stat().st_size,
            "metadata": header.pop("__metadata__", {}), "tensors": header}

@app.local_entrypoint()
def main(path: str = "diffusion_models/NREDQW21-UNLOCKED-v1-BF16.safetensors"):
    result = inspect_header.remote(path)
    root = Path(__file__).resolve().parents[2] / "extras/qwen21-source"
    root.mkdir(parents=True, exist_ok=True)
    (root / (Path(path).stem + "-header.json")).write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps({"size_bytes": result["size_bytes"], "block0": {k: v for k,v in result["tensors"].items()
                if k.startswith("transformer_blocks.0.")}}, indent=2))
