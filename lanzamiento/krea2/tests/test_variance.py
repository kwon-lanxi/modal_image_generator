import copy
import sys
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from krea_service.contracts import normalize, GenerateRequest
from krea_service.variance import apply_batch, seed_for_variance, OPTIONS
from tg_logic import parse_prompt


def test_disabled_is_identity_and_does_not_import_vendor():
    cond=[[object(),{"pooled_output":object()}]]
    before="krea_service.vendor.smart_seed_variance" in sys.modules
    out,metadata=apply_batch(cond,{"variance":"off"})
    assert out is cond and metadata is None
    assert ("krea_service.vendor.smart_seed_variance" in sys.modules)==before


def test_api_default_and_all_flags_keep_loras_and_aliases():
    original=normalize({"positive_prompt":"portrait","seed":42},"generate")
    assert original["variance"]=="off" and original["sampler_name"]=="euler" and original["scheduler"]=="simple"
    aliases={"style":{"id":"loras/style.safetensors","weight":.7}}
    recipe=parse_prompt('portrait !style --n 3 --seed 42 --variance custom --variance-strength 25 --variance-protect custom --variance-regions "0-5,15-20" --variance-direction cinematic-framing --variance-shift 80 --variance-fade ease-out --variance-timing beginning --variance-schedule step_cutoff --variance-cutoff 3 --variance-tail 0.1 --variance-freeze --variance-normalize --variance-vibe "film still" --variance-blend 0.3 --sample heun --screluder beta',aliases)
    assert recipe["lora"]=={"loras/style.safetensors":.7}
    data=normalize(recipe,"generate")
    assert data["n"]==3 and len(set(data["seeds"]))==3
    assert data["sampler_name"]=="heun" and data["scheduler"]=="beta"
    assert data["variance_freeze"] and data["variance_normalize"]
    assert data["variance_vibe"]=="film still"


@pytest.mark.parametrize("flags",["--variance bogus","--sample unknown","--scheduler unknown","--variance-strength 101","--variance-shift -1","--variance-tail nan","--variance custom --variance-protect custom --variance-regions 5-2","--variance balanced --variance-schedule step_cutoff --variance-cutoff 9 --steps 8"])
def test_bad_flags_fail_before_gpu(flags):
    with pytest.raises(ValueError):parse_prompt("portrait "+flags,{})


def test_preview_uses_its_effective_steps_and_sampling():
    data=normalize({"positive_prompt":"portrait","variance":"subtle","sampler_name":"heun","scheduler":"beta"},"preview")
    assert data["steps"]==2 and data["sampler_name"]=="euler" and data["scheduler"]=="simple"
    with pytest.raises(ValueError,match="cutoff"):
        normalize({"positive_prompt":"portrait","variance":"subtle","variance_schedule":"step_cutoff","variance_cutoff":4},"preview")


def test_real_vendor_independent_reproducible_batch_without_cache_mutation():
    torch=pytest.importorskip("torch")
    generator=torch.Generator().manual_seed(123)
    tensor=torch.randn((1,32,48),generator=generator)
    pooled=torch.randn((1,8),generator=generator)
    cond=[[tensor,{"pooled_output":pooled}]]
    saved=copy.deepcopy(cond)
    request=normalize({"positive_prompt":"portrait","variance":"balanced","seed":42,"seeds":[42,98,12],"n":3},"generate")
    out,meta=apply_batch(cond,request)
    again,meta2=apply_batch(cond,request)
    assert out[0][0].shape==(3,32,48) and out[0][1]["pooled_output"].shape==(3,8)
    assert torch.equal(out[0][0],again[0][0])
    assert not torch.equal(out[0][0][0],out[0][0][1])
    assert torch.equal(cond[0][0],saved[0][0]) and torch.equal(cond[0][1]["pooled_output"],saved[0][1]["pooled_output"])
    assert "rbg_variance_applied" not in cond[0][1]
    single,_=apply_batch(cond,{**request,"n":1,"seed":98,"seeds":[98]})
    assert torch.equal(single[0][0][0],out[0][0][1])
    assert meta["seeds"]==[42,98,12] and meta["revision"]==OPTIONS["revision"]


def test_override_seed_stays_independent_and_repeatable():
    assert seed_for_variance(42,100)==seed_for_variance(42,100)
    assert seed_for_variance(42,100)!=seed_for_variance(43,100)


def test_schedule_batches_matching_windows(monkeypatch):
    torch=pytest.importorskip("torch")
    import krea_service.vendor.smart_seed_variance as vendor
    # Only Comfy's metadata helper is substituted; noise is the real upstream implementation.
    def values(cond,values):return [[t,{**m,**values}] for t,m in cond]
    monkeypatch.setattr(vendor,"node_helpers",SimpleNamespace(conditioning_set_values=values),raising=False)
    monkeypatch.setattr(vendor,"_NODE_HELPERS_AVAILABLE",True)
    cond=[[torch.randn(1,32,48),{}]]
    request=normalize({"positive_prompt":"portrait","variance":"subtle","variance_schedule":"step_cutoff","variance_cutoff":3,"seed":42,"seeds":[42,43],"n":2},"generate")
    out,meta=apply_batch(cond,request)
    assert len(out)==2 and out[0][0].shape[0]==2 and out[1][0].shape[0]==2
    assert out[0][1]["end_percent"]==3/8 and out[1][1]["start_percent"]==3/8


def test_sampling_choices_match_pinned_catalog():
    for name in OPTIONS["samplers"]:
        assert GenerateRequest.model_validate({"positive_prompt":"x","sampler_name":name}).sampler_name==name
    for name in OPTIONS["schedulers"]:
        assert GenerateRequest.model_validate({"positive_prompt":"x","scheduler":name}).scheduler==name


@pytest.mark.parametrize("direction",list(OPTIONS["directions"]))
def test_directions_produce_finite_conditioning(direction):
    torch=pytest.importorskip("torch")
    cond=[[torch.randn(1,24,48),{}]]
    request=normalize({"positive_prompt":"portrait","variance":"subtle","variance_direction":direction,"seed":42},"generate")
    out,_=apply_batch(cond,request)
    assert torch.isfinite(out[0][0]).all()
