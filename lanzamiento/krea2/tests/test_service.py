import base64
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from conftest import tensor_file
from krea_service.api import create_api
from krea_service.backend import Backend, prepare_generation, read_operation
from krea_service.contracts import normalize
from krea_service.files import resolve, safe_relative, safetensors_header, describe
from krea_service.logging_utils import JobLog
from krea_service.manager import download, target_path
from krea_service.storage import save_result, read_output, R2Store


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:/secret", "a\\b", "a/../b", "a//b", ".", ""])
def test_rejects_unsafe_paths(volumes, path):
    with pytest.raises(ValueError):
        safe_relative(volumes[0], path)


def test_exact_lora_resolution_and_ambiguity(model_set):
    main, loras = model_set
    selected = resolve("person-step0002250", "lora")
    assert selected.parent.name == "person"
    with pytest.raises(FileNotFoundError):
        resolve("person-step000", "lora")
    tensor_file(main / "loras" / selected.name)
    with pytest.raises(ValueError, match="ambiguo"):
        resolve(selected.name, "lora")
    assert resolve("krea2-loras-renew/person/" + selected.name, "lora") == selected


def test_header_detects_truncated_weights(volumes):
    path = tensor_file(volumes[0] / "weights.safetensors")
    assert safetensors_header(path, validate=True)["__metadata__"]["author"] == "test"
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError):
        safetensors_header(path, validate=True)


def test_preview_contract_enforces_distilled_recipe():
    result = normalize({"positive_prompt": "portrait", "steps": 50, "cfg": 7,
                        "sampler_name": "er_sde", "seed": 123}, "preview")
    assert (result["steps"], result["cfg"], result["sampler_name"], result["seed"]) == (2, 1, "euler", 123)
    with pytest.raises(ValueError, match="Turbo"):
        normalize({"positive_prompt": "portrait", "profile": "raw"}, "preview")


@pytest.mark.parametrize("extra", [{"width": 1025}, {"width": 2048, "height": 2048},
                                    {"lora": {"x": float("nan")}}, {"image": "unwanted"},
                                    {"vae_tile_overlap": 256, "vae_tile_size": 128}])
def test_bounded_generation(extra):
    with pytest.raises(ValidationError):
        normalize({"positive_prompt": "test", **extra}, "generate")


def test_missing_preview_assets_fail_before_gpu(model_set):
    with pytest.raises(FileNotFoundError):
        prepare_generation({"positive_prompt": "test"}, "preview")


class FailedR2:
    def upload(self, *args):
        return {"status": "failed", "error": "ExampleUploadError"}


def generated():
    return {"pixels": bytes([128, 64, 32] * 4), "width": 2, "height": 2,
            "metadata": {"loras": [], "attention_backend": "pytorch_sdpa"},
            "timing": {"sampling": 0.1}}


def test_volume_copy_metadata_and_real_fallback_survive_r2_failure(volumes):
    request = normalize({"positive_prompt": "private prompt", "seed": 123}, "generate")
    commits = []
    log = JobLog("a" * 32)
    result = save_result(generated(), request, "a" * 32, log, lambda: commits.append(True), FailedR2())
    entry = result["images"][0]
    assert entry["url"].startswith("/serve_image?")
    assert result["warnings"]
    assert len(commits) == 2
    raw, _ = read_output(entry["filename"])
    assert base64.b64decode(entry["base64"]) == raw
    with Image.open(io.BytesIO(raw)) as image:
        assert json.loads(image.info["krea2"])["request"]["positive_prompt"] == "private prompt"
    meta = json.loads((volumes[0] / "outputs" / Path(entry["filename"]).with_suffix(".json")).read_text())
    assert meta["r2"]["status"] == "failed" and meta["sha256"] == entry["sha256"]


def test_backend_idempotency_prevents_second_gpu_call(model_set):
    calls = []
    def fake_gpu(*args):
        calls.append(args)
        return generated()
    backend = Backend(lambda: None, fake_gpu)
    backend.r2 = FailedR2()
    payload = {"positive_prompt": "portrait", "seed": 42, "return_base64": False}
    first = backend.execute("generate", payload, "b" * 32)
    second = backend.execute("generate", payload, "b" * 32)
    assert first["success"] and second["success"] and len(calls) == 1
    with pytest.raises(ValueError, match="otra solicitud"):
        backend.execute("generate", {**payload, "seed": 43}, "b" * 32)
    assert read_operation("job", {"job_id": "b" * 32})["status"] == "done"


def test_running_job_is_not_blindly_regenerated(volumes):
    backend = Backend(lambda: None, lambda *a: pytest.fail("GPU should not run"))
    from krea_service.files import write_json
    import hashlib
    payload = {"positive_prompt": "x"}
    digest = hashlib.sha256(json.dumps({"operation": "generate", "payload": payload}, sort_keys=True).encode()).hexdigest()
    write_json(volumes[0] / "_release/jobs" / ("c" * 32) / "status.json",
               {"status": "running", "input_hash": digest})
    with pytest.raises(RuntimeError, match="registrado"):
        backend.execute("generate", payload, "c" * 32)


def test_download_failure_never_publishes_model(volumes, monkeypatch):
    path = tensor_file(volumes[0] / "reference.safetensors")
    data = path.read_bytes()
    class Download:
        status_code = 200
        headers = {"Content-Length": str(len(data) + 100)}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, **kwargs): yield data
    monkeypatch.setattr("krea_service.manager.requests.get", lambda *a, **k: Download())
    item = {"repo_id": "owner/model", "filename": "example.safetensors", "target": "lora"}
    with pytest.raises(ValueError, match="incompleta"):
        download(item, "d" * 32, JobLog("d" * 32), lambda: None)
    assert not (volumes[0] / "loras/example.safetensors").exists()
    assert not list((volumes[0] / "loras").glob("*.part"))


def test_structured_logger_omits_prompt_and_token(volumes):
    path = volumes[0] / "events.jsonl"
    log = JobLog("e" * 32, path)
    log.event("test", prompt="sensitive", token="secret", stage="decode")
    data = json.loads(path.read_text())
    assert "prompt" not in data and "token" not in data and data["stage"] == "decode"


def test_exception_details_redact_credentials_and_signed_urls(monkeypatch):
    from krea_service.logging_utils import redact
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "very-private-secret")
    text = redact("Failed very-private-secret https://example.com/model?token=private Bearer hello")
    assert "very-private-secret" not in text and "token=private" not in text and "Bearer hello" not in text
    assert "Failed" in text


def test_r2_client_failure_still_returns_volume_result(volumes, monkeypatch):
    for key in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "fake-credentials")
    def fail(*args, **kwargs):
        raise RuntimeError("provider failed")
    monkeypatch.setattr("boto3.client", fail)
    request = normalize({"positive_prompt": "x", "seed": 123}, "generate")
    result = save_result(generated(), request, "1" * 32, JobLog("1" * 32), lambda: None, R2Store())
    assert result["success"] and result["images"][0]["r2"]["status"] == "failed"
    assert read_output(result["images"][0]["filename"])[0]


def test_incompatible_lora_request_does_not_start_gpu(model_set):
    calls = []
    backend = Backend(lambda: None, lambda *a: calls.append(a))
    result = backend.execute("generate", {"positive_prompt": "x", "lora": {"not-present": 1}}, "2" * 32)
    assert result["success"] is False and calls == []


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setenv("KREA2_API_TOKEN", "a-test-secret-at-least-24-chars")
    calls = []
    async def submit(*args): calls.append(args); return "fc-test"
    async def poll(*args): return None
    async def read(operation, payload): calls.append((operation, payload)); return {"count": 0}
    app = create_api(submit, poll, read)
    return TestClient(app), calls, {"Authorization": "Bearer a-test-secret-at-least-24-chars"}


def test_health_and_catalog_do_not_submit_gpu(api_client):
    client, calls, auth = api_client
    assert client.get("/healthz").status_code == 401
    assert client.get("/healthz", headers=auth).json()["gpu_probed"] is False
    assert calls == []
    assert client.get("/list_models?type=lora", headers=auth).status_code == 200
    assert calls == [("list_models", {"type": "lora"})]


def test_async_jobs_and_idempotency_key(api_client):
    client, calls, auth = api_client
    payload = {"positive_prompt": "portrait", "seed": 42}
    a = client.post("/jobs", headers={**auth, "Idempotency-Key": "one"}, json=payload)
    b = client.post("/jobs", headers={**auth, "Idempotency-Key": "one"}, json=payload)
    assert a.status_code == 202 and a.json()["request_id"] == b.json()["request_id"]
    assert client.get("/jobs/fc-test", headers=auth).status_code == 202
    assert client.post("/jobs", headers=auth, json={"positive_prompt": "x", "width": 1025}).status_code == 422


def test_playground_is_static_public_but_operations_stay_authenticated(api_client):
    client, calls, auth = api_client
    page = client.get("/playground")
    assert page.status_code == 200 and "hf://AcademiaSD" in page.text
    assert "a-test-secret-at-least-24-chars" not in page.text
    assert calls == []
    assert client.get("/pricing").status_code == 401
    assert client.get("/pricing", headers=auth).json()["rates"]["l40s_per_second"] == .000542


def test_conversion_keeps_original_and_uses_pinned_reference(model_set, monkeypatch):
    from krea_service.manager import convert_fp8
    source = resolve("HomoFidelis_Krea2_Turbo_BF16_v1", "unet")
    original = source.read_bytes()
    commands = []
    monkeypatch.setattr("shutil.which", lambda name: "precision-convert")
    def run(command, **kwargs):
        commands.append(command)
        destination = Path(command[command.index("-o") + 1])
        tensor_file(destination, dtype="F8_E4M3", payload=b"\0")
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout=b"conversion report")
    monkeypatch.setattr("krea_service.manager.subprocess.run", run)
    result = convert_fp8({}, "f" * 32, JobLog("f" * 32), lambda: None)
    assert result["success"] and source.read_bytes() == original
    assert "--report" in commands[0] and "--like" in commands[0]
    from krea_service import config
    assert config.OFFICIAL_REVISION in commands[0][commands[0].index("--like") + 1]


def test_provider_string_subclasses_never_cross_api_boundary(model_set):
    class ProviderVersion(str):
        pass
    def render(*args):
        data = generated()
        data["metadata"]["torch"] = ProviderVersion("2.10.0")
        return data
    backend = Backend(lambda: None, render)
    backend.r2 = FailedR2()
    result = backend.execute("generate", {"positive_prompt": "x", "seed": 42}, "7" * 32)
    assert result["success"]
    assert type(result["metadata"]["generation"]["torch"]) is str


def test_legacy_scaled_fp8_rejected_before_gpu(model_set, monkeypatch):
    import krea_service.files as files
    original = files.safetensors_header
    def legacy(path, validate=False):
        header = original(path, validate)
        if path.stem == "HomoFidelis_Krea2_Turbo_BF16_v1":
            header["blocks.0.attn.wq.weight.scale"] = {}
        return header
    monkeypatch.setattr(files, "safetensors_header", legacy)
    calls = []
    result = Backend(lambda: None, lambda *args: calls.append(args)).execute(
        "generate", {"positive_prompt": "x"}, "8" * 32)
    assert result["success"] is False and calls == []
    assert ".weight.scale" in result["detail"]


def test_output_names_include_local_date_seed_and_unique_job(volumes):
    from datetime import datetime
    from krea_service.storage import output_paths
    created = datetime.fromisoformat("2026-11-16T10:43:21-03:00")
    a, sidecar = output_paths("a" * 32, "generate", created, 42)
    b, _ = output_paths("b" * 32, "generate", created, 42)
    assert a.name == "20261116_104321_krea2_42_batch0_" + "a" * 32 + ".png"
    assert a != b and a.with_suffix(".json") == sidecar
    preview, _ = output_paths("a" * 32, "preview", created, 42)
    assert "krea2_preview" in preview.name and preview.suffix == ".jpg"


def test_live_progress_reads_only_store_and_requires_auth(monkeypatch):
    monkeypatch.setenv("KREA2_API_TOKEN", "a-test-secret-at-least-24-chars")
    seen = []
    async def unexpected(*args): pytest.fail("No writer/GPU/Volume call expected")
    async def progress(job):
        seen.append(job)
        return {"event": "sampling.progress", "step": 3, "total": 8, "percent": 37.5}
    client = TestClient(create_api(unexpected, unexpected, unexpected, progress=progress))
    path = "/records/" + "a" * 32 + "/progress"
    assert client.get(path).status_code == 401
    data = client.get(path, headers={"Authorization": "Bearer a-test-secret-at-least-24-chars"}).json()
    assert data["progress"]["step"] == 3 and seen == ["a" * 32]


def test_progress_publishing_preserves_final_step_without_writing_every_step(monkeypatch):
    from krea_service.progress import publisher
    class Store:
        def __init__(self): self.events = []
        def put(self, key, value): self.events.append(value)
    store = Store()
    monkeypatch.setattr("krea_service.progress.time.monotonic", lambda: 100)
    publish = publisher(store)
    for step in range(1, 9):
        publish({"job_id": "test", "event": "sampling.progress", "step": step, "total": 8})
    assert [e["step"] for e in store.events] == [1, 8]


def test_output_ready_log_preserves_result_urls(volumes):
    log = JobLog("a" * 32)
    request = normalize({"positive_prompt": "x", "seed": 42}, "generate")
    result = save_result(generated(), request, "a" * 32, log, lambda: None, FailedR2())
    ready = next(e for e in log.records if e["event"] == "output.ready")
    assert ready["image_url"] == result["images"][0]["url"]
    assert ready["metadata_url"] == result["images"][0]["metadata_url"]


def test_live_progress_failure_does_not_abort_logging(volumes):
    def failed(event): raise ConnectionError("unavailable")
    log = JobLog("a" * 32, volumes[0] / "test.jsonl", sink=failed)
    log.event("sampling.progress", step=1, total=8)
    assert json.loads((volumes[0] / "test.jsonl").read_text())["step"] == 1
