import json
import struct
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qwen_service import config


def tensor_file(path, quantized=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"weight": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 2]}}
    raw = b"00"
    if quantized:
        header = {"layer.weight": {"dtype": "F8_E4M3", "shape": [1], "data_offsets": [0, 1]},
                  "layer.comfy_quant": {"dtype": "U8", "shape": [1], "data_offsets": [1, 2]}}
    encoded = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + raw)
    return path


@pytest.fixture
def models(tmp_path, monkeypatch):
    main = tmp_path / "volume"
    monkeypatch.setattr(config, "MAIN", main)
    monkeypatch.setattr(config, "LORAS", main / "loras")
    for preset in config.profiles().values():
        for key, folder in (("model", "diffusion_models"), ("clip", "text_encoders"), ("vae", "vae")):
            tensor_file(main / folder / (preset[key] + ".safetensors"))
    return main
