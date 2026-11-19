import base64
import io
import json
import subprocess
from pathlib import Path
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from pydantic import ValidationError
from .conftest import tensor_file
from qwen_service.api import create_api
from qwen_service.backend import Backend, prepare_generation, read_operation
from qwen_service.contracts import normalize
from qwen_service.inputs import save_input, describe_input
from qwen_service.image_metadata import extract
from qwen_service.storage import save_result, read_output
from qwen_service.logging_utils import JobLog
from qwen_service.manager import convert_fp8
from qwen_service.pricing import estimate


def image_data(mode="RGBA", size=(96, 64)):
    out = io.BytesIO()
    Image.new(mode, size, (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)).save(out, "PNG")
    return out.getvalue()


def uploaded():
    return save_input({"base64": base64.b64encode(image_data()).decode()}, lambda: None)["input"]


def test_base_defaults_no_distillation():
    data = normalize({"positive_prompt": "red apple", "seed": 42}, "generate")
    assert (data["steps"], data["cfg"], data["sampler_name"], data["scheduler"]) == (25, 1, "euler", "simple")
    assert "preview_lora" not in data["effective_profile"]
    assert data["seed_string"] == "42"
    with pytest.raises(ValueError): normalize({"positive_prompt": "x"}, "preview")


@pytest.mark.parametrize("extra", [{"input_images": ["x"] * 11}, {"width": 1025},
    {"width": 2048, "height": 2048}, {"reference_resolution": 777}, {"kv_cache_device": "invalid"},
    {"use_tiled_vae": True, "vae_tile_size": 128, "vae_tile_overlap": 64},
    {"input_images": ["x"] * 10, "reference_resolution": 1024}])
def test_bounds(extra):
    with pytest.raises(ValidationError): normalize({"positive_prompt": "x", **extra}, "generate")


def test_edit_needs_references_and_generation_rejects_them():
    with pytest.raises(ValueError): normalize({"positive_prompt": "x"}, "edit")
    with pytest.raises(ValueError): normalize({"positive_prompt": "x", "input_images": ["x"]}, "generate")


def test_cpu_input_rgba_hash_dedup_and_paths(models):
    a, b = uploaded(), uploaded()
    assert a == b and len(a["sha256"]) == 64 and a["channels"] == 4
    assert describe_input(a["id"]) == a
    with pytest.raises(ValueError): describe_input("../x.png")
    with pytest.raises(ValueError): save_input({"base64": "not-image"}, lambda: None)


def test_prepare_edit_preserves_input_order_without_snapshot_per_input(models):
    a = uploaded()
    request, assets, loras = prepare_generation({"positive_prompt": "combine", "input_images": [a["id"], a["id"]]}, "edit")
    assert len(request["input_assets"]) == 2 and loras == []
    assert set(assets) == {"model", "clip", "vae"}
    assert "input_images" not in request["effective_profile"]


def test_missing_input_never_calls_gpu(models):
    calls = []
    result = Backend(lambda: None, lambda *args: calls.append(args)).execute(
        "edit", {"positive_prompt": "x", "input_images": ["a" * 64 + ".png"]}, "a" * 32)
    assert not result["success"] and calls == []


class FailedR2:
    def upload(self, *args): return {"status": "failed", "error": "test"}


def generated(channels=4):
    return {"pixels": bytes([255, 0, 0, 128] * 4), "width": 2, "height": 2, "channels": channels,
            "timing": {"sampling": .2, "gpu_method_total": 1},
            "metadata": {"generation_finished_at_utc": "2026-11-16T14:00:00+00:00", "loras": []}}


def test_original_rgba_metadata_date_filename_and_fallback(models):
    request = normalize({"positive_prompt": "test", "seed": 42}, "generate")
    log = JobLog("a" * 32)
    result = save_result(generated(), request, "a" * 32, log, lambda: None, FailedR2())
    im = result["images"][0]
    assert im["filename"].startswith("20261116_110000_qwen21_42_batch0_")
    raw = read_output(im["filename"])[0]
    with Image.open(io.BytesIO(raw)) as image:
        assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 128
    metadata = extract(raw)
    assert metadata["timing"] == result["timing"] and metadata["created_at"] == result["created_at"]
    assert result["warnings"] and any(e["event"] == "output.ready" for e in log.records)


def test_idempotency_does_not_regenerate(models):
    calls = []
    def render(*args): calls.append(args); return generated()
    backend = Backend(lambda: None, render); backend.r2 = FailedR2()
    payload = {"positive_prompt": "x", "seed": 42, "return_base64": False}
    a = backend.execute("generate", payload, "a" * 32)
    b = backend.execute("generate", payload, "a" * 32)
    assert a == b and len(calls) == 1


def test_conversion_keeps_original_and_uses_pinned_reference(models, monkeypatch):
    source = models / "diffusion_models/NREDQW21-UNLOCKED-v1-BF16.safetensors"
    original = source.read_bytes(); commands = []
    monkeypatch.setattr("shutil.which", lambda name: "precision-convert")
    def run(cmd, **kwargs):
        commands.append(cmd); tensor_file(Path(cmd[cmd.index("-o") + 1]), quantized=True)
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr("qwen_service.manager.subprocess.run", run)
    folder = models / "_release/jobs" / ("b" * 32); folder.mkdir(parents=True)
    result = convert_fp8({"output": "test_fp8.safetensors"}, "b" * 32, JobLog("b" * 32), lambda: None)
    assert result["success"] and source.read_bytes() == original
    assert "--like" in commands[0] and "aacf31a6" in commands[0][commands[0].index("--like") + 1]
    assert not any(p.name.startswith(".") for p in (models / "diffusion_models").glob("*.safetensors"))


def test_api_inputs_progress_and_auth_are_cpu_only(monkeypatch):
    monkeypatch.setenv("QWEN21_API_TOKEN", "private-test-key-longer-than-24-characters")
    calls = []
    async def submit(*args): calls.append(args); return "fc-test"
    async def poll(*args): return None
    async def read(*args): return {}
    async def progress(*args): return {"event": "sampling.progress", "step": 2, "total": 25}
    client = TestClient(create_api(submit, poll, read, progress))
    auth = {"Authorization": "Bearer private-test-key-longer-than-24-characters"}
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers=auth).json()["gpu_probed"] is False
    assert calls == []
    response = client.post("/inputs", content=image_data(), headers=auth)
    assert response.status_code == 202 and calls[0][0] == "upload_input"
    assert client.get("/records/" + "a" * 32 + "/progress", headers=auth).json()["progress"]["step"] == 2
    assert client.post("/edit", json={"positive_prompt": "x"}, headers=auth).status_code == 400


def test_pricing_is_l4_reference_not_billing():
    cost = estimate({"gpu_method_total": 10, "gpu_call": 100}, {}, 100)
    assert cost["rates"]["l4_per_second"] == .000222
    assert cost["rpc_scenario_total"] > cost["method_only_total"] and not cost["billing_verified"]


def test_swiglu_fusion_streams_gate_then_up_without_changing_other_weights(tmp_path):
    import struct
    from qwen_service.quantization import fuse_swiglu
    from qwen_service.files import safetensors_header
    source, target = tmp_path / "original.safetensors", tmp_path / "native.safetensors"
    entries = {"transformer_blocks.0.img_mlp.gate_layer.weight": {"dtype": "BF16", "shape": [1, 2], "data_offsets": [0, 4]},
               "transformer_blocks.0.img_mlp.proj.weight": {"dtype": "BF16", "shape": [1, 2], "data_offsets": [4, 8]},
               "other": {"dtype": "BF16", "shape": [1], "data_offsets": [8, 10]}}
    raw_header = json.dumps(entries).encode()
    original = struct.pack("<Q", len(raw_header)) + raw_header + b"GATEUP__XY"
    source.write_bytes(original)
    assert fuse_swiglu(source, target) == 1 and source.read_bytes() == original
    header = safetensors_header(target, validate=True)
    key = "transformer_blocks.0.img_mlp.gate_up.weight"
    assert header[key]["shape"] == [2, 2]
    raw = target.read_bytes(); base = 8 + struct.unpack("<Q", raw[:8])[0]
    assert raw[base:] == b"GATEUP__XY"


def test_cuda_init_retry_is_bounded(monkeypatch):
    from qwen_service.cuda_init import ensure_cuda
    from types import SimpleNamespace
    attempts, sleeps = [], []
    def init(_): attempts.append(1); return 999 if len(attempts) < 3 else 0
    monkeypatch.setattr("qwen_service.cuda_init.ctypes.CDLL", lambda _: SimpleNamespace(cuInit=init))
    monkeypatch.setattr("qwen_service.cuda_init.time.sleep", lambda x: sleeps.append(x))
    ensure_cuda()
    assert len(attempts) == 3 and sleeps == [.5, .5]
    monkeypatch.setattr("qwen_service.cuda_init.ctypes.CDLL", lambda _: SimpleNamespace(cuInit=lambda _: 999))
    with pytest.raises(RuntimeError): ensure_cuda()
    assert len(sleeps) == 11


def test_preempted_cpu_upload_can_resume_but_gpu_cannot(models):
    import hashlib
    payload = {"base64": base64.b64encode(image_data()).decode()}
    job = "c" * 32
    folder = models / "_release/jobs" / job; folder.mkdir(parents=True)
    digest = hashlib.sha256(json.dumps({"operation": "upload_input", "payload": payload}, sort_keys=True).encode()).hexdigest()
    (folder / "status.json").write_text(json.dumps({"status": "running", "input_hash": digest}), encoding="utf-8")
    result = Backend(lambda: None, lambda *a: pytest.fail("No GPU expected")).execute("upload_input", payload, job)
    assert result["success"]
    payload = {"positive_prompt": "x"}; job = "d" * 32
    folder = models / "_release/jobs" / job; folder.mkdir(parents=True)
    digest = hashlib.sha256(json.dumps({"operation": "generate", "payload": payload}, sort_keys=True).encode()).hexdigest()
    (folder / "status.json").write_text(json.dumps({"status": "running", "input_hash": digest}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        Backend(lambda: None, lambda *a: pytest.fail("No duplicate GPU")).execute("generate", payload, job)
