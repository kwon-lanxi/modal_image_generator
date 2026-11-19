import io
import json
from datetime import datetime

import pytest
from PIL import Image

from conftest import tensor_file
from krea_service.downloads import download_source
from krea_service.contracts import normalize
from krea_service.backend import prepare_generation, Backend, read_operation
from krea_service.image_metadata import extract
from krea_service.logging_utils import JobLog
from krea_service.manager import target_path
from krea_service.pricing import estimate, RATES
from krea_service.storage import save_result, read_output
from test_service import FailedR2, generated


@pytest.mark.parametrize("uri", [
    "hf://AcademiaSD/TAE-Krea-2/TAE_Krea2_AcademiaSD.safetensors",
    "https://huggingface.co/AcademiaSD/TAE-Krea-2/resolve/main/TAE_Krea2_AcademiaSD.safetensors",
    "https://huggingface.co/AcademiaSD/TAE-Krea-2/blob/main/TAE_Krea2_AcademiaSD.safetensors?download=true",
])
def test_one_link_hf_download_with_custom_name(volumes, uri):
    url, headers, source = download_source({"url": uri, "name": "my-tae", "target": "tae"})
    assert url == "https://huggingface.co/AcademiaSD/TAE-Krea-2/resolve/main/TAE_Krea2_AcademiaSD.safetensors"
    assert source["repo_id"] == "AcademiaSD/TAE-Krea-2"
    destination = target_path({"name": "my-tae", "filename": source["filename"], "target": "tae"})
    assert destination == volumes[0] / "vae_approx/my-tae.safetensors"


def test_hf_uri_subfolders_and_revision():
    url, _, _ = download_source({"url": "hf://Comfy-Org/Krea-2/diffusion_models/krea2_raw_fp8_scaled.safetensors?revision=abc123"})
    assert "/resolve/abc123/diffusion_models/" in url


@pytest.mark.parametrize("host", ["civitai.com", "civitai.red", "www.civitai.red"])
def test_civitai_direct_and_version_links_need_no_extra_lookup(host, monkeypatch):
    monkeypatch.setattr("krea_service.downloads.requests.get", lambda *a, **k: pytest.fail("Unexpected API lookup"))
    for path in ("/api/download/models/456?format=SafeTensor&token=do-not-keep", "/models/123/name?modelVersionId=456"):
        url, _, origin = download_source({"url": "https://" + host + path, "name": "my-lora"})
        assert url.startswith("https://" + host.removeprefix("www.") + "/api/download/models/456")
        assert "token=" not in url and "do-not-keep" not in json.dumps(origin)
        assert origin["model_version_id"] == "456"


def test_civitai_page_selects_version_file_hash_and_name(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def json(self): return {"modelVersions": [{"id": 456, "name": "v2", "trainedWords": ["trigger"],
            "files": [{"id": 12, "name": "readme.zip", "type": "Training Data"},
                      {"id": 13, "name": "real.safetensors", "type": "Model", "primary": True,
                       "hashes": {"SHA256": "abc"}}]}]}
    def get(url, **kwargs): calls.append(url); return Response()
    monkeypatch.setattr("krea_service.downloads.requests.get", get)
    url, _, origin = download_source({"url": "https://civitai.red/models/123/title"})
    assert calls == ["https://civitai.red/api/v1/models/123"]
    assert url == "https://civitai.red/api/download/models/456?fileId=13"
    assert origin["filename"] == "real.safetensors" and origin["sha256"] == "abc"
    assert origin["trained_words"] == ["trigger"]


@pytest.mark.parametrize("uri", ["https://civitai.red.evil.com/models/123", "http://civitai.red/models/123",
                                  "https://huggingface.co.evil.com/x/y/resolve/main/file", "hf://owner/repo/../file"])
def test_source_resolver_rejects_untrusted_host_or_path(uri):
    with pytest.raises(ValueError):
        download_source({"url": uri, "source": "civitai" if "civitai" in uri else "huggingface"})


def test_per_request_model_encoder_vae_precision_and_snapshot_identity(model_set):
    main, _ = model_set
    for kind, name in [("diffusion_models", "other_fp8_scaled"), ("text_encoders", "another_encoder"), ("vae", "another_vae")]:
        tensor_file(main / kind / (name + ".safetensors"))
    request, assets, _ = prepare_generation({"positive_prompt": "x", "unet_name": "other_fp8_scaled",
         "clip_name": "another_encoder", "vae_name": "another_vae"}, "generate")
    effective = request["effective_profile"]
    assert effective["model"] == "diffusion_models/other_fp8_scaled.safetensors"
    assert effective["clip"] == "text_encoders/another_encoder.safetensors"
    assert effective["vae"] == "vae/another_vae.safetensors"
    assert effective["weight_dtype"] == "default"
    assert effective["asset_versions"]["model"] == assets["model"]["version"]
    check = read_operation("preflight", {"unet_name": "other_fp8_scaled", "clip_name": "another_encoder", "vae_name": "another_vae"})
    assert check["success"] and check["files"]["model"]["id"] == effective["model"]


def test_raw_override_updates_recipe_and_rejects_turbo_preview():
    result = normalize({"positive_prompt": "x", "profile": "turbo", "model_type": "raw", "unet_name": "raw_fp8_scaled"}, "generate")
    assert result["steps"] == 52 and result["cfg"] == 3.5
    with pytest.raises(ValueError, match="Turbo"):
        normalize({"positive_prompt": "x", "model_type": "raw"}, "preview")


@pytest.mark.parametrize("mode", ["generate", "preview"])
def test_originals_embed_generation_settings_time_zone_and_pricing(volumes, mode):
    request = normalize({"positive_prompt": "retrato, luz cálida", "seed": 123,
                         "lora": {"character": .8}}, mode)
    result = generated()
    result["metadata"].update(generation_started_at_utc="2026-11-15T15:00:00+00:00",
                              generation_finished_at_utc="2026-11-15T15:00:10+00:00")
    saved = save_result(result, request, "3" * 32, JobLog("3" * 32), lambda: None, FailedR2())
    raw, _ = read_output(saved["images"][0]["filename"])
    embedded = extract(raw)
    assert embedded["created_at"] == "2026-11-15T12:00:10-03:00"
    assert embedded["created_at_utc"] == "2026-11-15T15:00:10+00:00"
    assert embedded["timezone"] == "America/Argentina/Buenos_Aires"
    assert embedded["request"]["lora"] == {"character": .8}
    assert embedded["pricing"] == saved["pricing"]
    assert embedded["timing"] == saved["timing"]
    assert embedded["generation_started_at_utc"] == "2026-11-15T15:00:00+00:00"
    if mode == "preview":
        with Image.open(io.BytesIO(raw)) as image:
            assert image.getexif()[36867] == "2026:11:15 12:00:10"
            assert image.getexif()[36881] == "-03:00"


@pytest.mark.parametrize("mode", ["preview", "generate"])
def test_large_metadata_round_trips_without_truncation(volumes, mode):
    request = normalize({"positive_prompt": "x", "seed": 123}, mode)
    result = generated()
    result["metadata"]["extra"] = "漢字" * 200000
    saved = save_result(result, request, "4" * 32, JobLog("4" * 32), lambda: None, FailedR2())
    raw, _ = read_output(saved["images"][0]["filename"])
    assert extract(raw)["generation"]["extra"] == result["metadata"]["extra"]
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        assert image.size == (2, 2)


def test_actual_published_rates_estimate_is_labelled_not_billed():
    cost = estimate({"gpu_method_total": 20, "gpu_call": 60}, {}, 0)
    assert cost["total"] == pytest.approx(.0399264)
    assert cost["rates"]["l40s_per_second"] == .000542
    assert cost["billing_verified"] is False and cost["is_estimate"] is True
    assert "upload R2" in cost["excluded"]
    slow_queue = estimate({"gpu_method_total": 20, "gpu_call": 600}, {}, 0)
    assert slow_queue["method_only_total"] == cost["method_only_total"]
    assert slow_queue["rpc_scenario_total"] > cost["rpc_scenario_total"]
    assert cost["total_basis"] == "rpc_wall_scenario_not_billed_time"


def test_embedded_timings_do_not_change_after_persistence(model_set):
    backend = Backend(lambda: None, lambda *a: generated())
    backend.r2 = FailedR2()
    result = backend.execute("generate", {"positive_prompt": "x", "seed": 42, "return_base64": False}, "5" * 32)
    assert result["success"]
    raw, _ = read_output(result["images"][0]["filename"])
    assert extract(raw)["timing"] == result["timing"]
    assert result["delivery_timing"]["persist_and_upload"] >= 0


def test_provenance_and_lora_training_metadata_are_preserved_without_tokens(model_set):
    from krea_service.files import describe, write_json
    main, loras = model_set
    path = loras / "person/person-step0002250.safetensors"
    write_json(path.with_suffix(".source.json"), {"source": "civitai", "model_version_id": "456",
               "sha256": "a" * 64, "trained_words": ["trigger"], "token": "must-not-copy",
               "source_url": "https://civitai.red/api/download/models/456?token=must-not-copy"})
    item = describe(path, include_metadata=True)
    assert item["sha256"] == "a" * 64 and item["safetensors_metadata"]["author"] == "test"
    assert "must-not-copy" not in json.dumps(item)
    assert item["provenance"]["trained_words"] == ["trigger"]


def test_seed_above_javascript_precision_is_preserved_as_decimal_string():
    seed = "9223372036854775806"
    request = normalize({"positive_prompt": "x", "seed": seed}, "generate")
    assert request["seed"] == int(seed) and request["seed_string"] == seed


def test_simple_download_infers_tae_or_model_folder():
    from krea_service.manager import infer_target
    assert infer_target({"filename": "TAE_Krea2_AcademiaSD.safetensors"}) == "tae"
    assert infer_target({"filename": "diffusion_models/custom.safetensors"}) == "unet"
    assert infer_target({"filename": "vae/Wan2.1_VAE_upscale2x.safetensors"}) == "vae"
    assert infer_target({"filename": "character.safetensors"}) == "lora"
