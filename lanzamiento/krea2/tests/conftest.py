import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from krea_service import config


def tensor_file(path, dtype="BF16", payload=b"\0\0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = json.dumps({"__metadata__": {"author": "test"},
                         "weight": {"dtype": dtype, "shape": [len(payload) // (2 if dtype == "BF16" else 1)],
                                    "data_offsets": [0, len(payload)]}}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + payload)
    return path


@pytest.fixture
def volumes(tmp_path, monkeypatch):
    main, loras = tmp_path / "main", tmp_path / "loras"
    main.mkdir()
    loras.mkdir()
    monkeypatch.setattr(config, "MAIN", main)
    monkeypatch.setattr(config, "LORAS", loras)
    return main, loras


@pytest.fixture
def model_set(volumes):
    main, loras = volumes
    for folder, name in (("diffusion_models", "HomoFidelis_Krea2_Turbo_BF16_v1"),
                         ("text_encoders", "qwen_3vl_4b"), ("vae", "Wan2_1_VAE_fp32")):
        tensor_file(main / folder / (name + ".safetensors"))
    tensor_file(loras / "person" / "person-step0002250.safetensors")
    tensor_file(main / "vae" / "Wan2.1_VAE_upscale2x_imageonly_real_v1.safetensors")
    return main, loras
