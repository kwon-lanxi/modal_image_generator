"""Lossless BF16 SwiGLU layout normalization, streamed on CPU before quantizing."""
import json
import os
import struct
from .files import safetensors_header


def fuse_swiglu(source, destination):
    header = safetensors_header(source, validate=True)
    with source.open("rb") as f:
        base = 8 + struct.unpack("<Q", f.read(8))[0]
    plans = []
    output = {"__metadata__": {**header.get("__metadata__", {}), "qwen21_swiglu_layout": "gate_then_up"}}
    offset, fused = 0, 0
    for key, spec in sorted(((k,v) for k,v in header.items() if k != "__metadata__"), key=lambda pair: pair[1]["data_offsets"][0]):
        if key.endswith(".img_mlp.proj.weight"):
            if key.replace(".proj.weight", ".gate_layer.weight") in header:
                continue
        spans = [spec["data_offsets"]]
        shape = list(spec["shape"])
        name = key
        if key.endswith(".img_mlp.gate_layer.weight"):
            other = header.get(key.replace(".gate_layer.weight", ".proj.weight"))
            if (other is None or spec["dtype"] not in {"BF16", "F16", "F32"}
                    or other["dtype"] != spec["dtype"] or other["shape"] != shape or len(shape) != 2):
                raise ValueError("SwiGLU requiere gate/proj flotantes de la misma forma y precisión")
            spans.append(other["data_offsets"])
            shape[0] *= 2
            name = key.replace(".gate_layer.weight", ".gate_up.weight")
            fused += 1
        size = sum(end-start for start,end in spans)
        output[name] = {"dtype": spec["dtype"], "shape": shape, "data_offsets": [offset, offset+size]}
        plans.append(spans)
        offset += size
    if not fused:
        return 0
    encoded = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 8)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        dst.write(struct.pack("<Q", len(encoded))); dst.write(encoded)
        for spans in plans:
            for start, end in spans:
                src.seek(base + start)
                remaining = end - start
                while remaining:
                    chunk = src.read(min(8 * 1024 * 1024, remaining))
                    if not chunk: raise ValueError("Entrada truncada durante fusión")
                    dst.write(chunk); remaining -= len(chunk)
        dst.flush(); os.fsync(dst.fileno())
    safetensors_header(destination, validate=True)
    return fused
