import sys,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from moss_service.contracts import normalize
from moss_service.sampling import SAMPLERS,SCHEDULERS
from moss_tg_logic import parse_prompt

@pytest.mark.parametrize("sampler",SAMPLERS)
def test_all_samplers(sampler):
    r=parse_prompt("test --sample "+sampler,{})
    assert normalize(r,"generate")["sampler_name"]==sampler

@pytest.mark.parametrize("scheduler",SCHEDULERS)
def test_all_schedulers(scheduler):
    r=parse_prompt("test --screluder "+scheduler,{})
    assert normalize(r,"generate")["scheduler"]==scheduler

def test_aliases_and_invalid_values():
    r=parse_prompt("test --sampler heun --scheduler beta",{})
    assert r["sampler_name"]=="heun" and r["scheduler"]=="beta"
    for text in ("test --sample typo","test --scheduler typo","test --sample","test --scheduler","test --sample heun --sampler euler"):
        with pytest.raises(ValueError):parse_prompt(text,{})

def test_catalog_matches_krea():
    expected=json.loads((Path(__file__).resolve().parents[2]/"krea2/krea_service/variance_options.json").read_text(encoding="utf8"))
    assert SAMPLERS==expected["samplers"] and SCHEDULERS==expected["schedulers"]

@pytest.mark.parametrize("flag",["--turbo","--turbo12","--superturbo","--turbo-sft-hades","--turbo-kromoss-base","--turbo-juggernaut"])
def test_turbo_overrides_are_not_silently_lost(flag):
    r=parse_prompt("test "+flag+" --sample heun --scheduler beta",{})
    out=normalize(r,"generate")
    assert out["sampler_name"]=="heun" and out["scheduler"]=="beta"

@pytest.mark.parametrize("flag",["--hiperturbo-base","--hiperturbo-sft","--superturbo-sft"])
def test_fixed_sampler_conflict_is_explicit(flag):
    with pytest.raises(ValueError,match="muestreo fijo"):
        parse_prompt("test "+flag+" --sample heun",{})
    with pytest.raises(ValueError,match="muestreo fijo"):
        normalize({"positive_prompt":"test","acceleration":flag[2:],"scheduler":"beta"},"generate")
