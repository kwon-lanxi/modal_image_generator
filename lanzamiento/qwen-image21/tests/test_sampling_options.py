import sys,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qwen_service.contracts import normalize
from qwen_service.sampling import SAMPLERS,SCHEDULERS
from qwen_tg_logic import parse_prompt

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

def test_viggle_keeps_its_fixed_schedule():
    r=parse_prompt("test --turbo --sample euler --scheduler viggle_turbo",{})
    assert normalize(r,"generate")["scheduler"]=="viggle_turbo"
    for key,value in [("sampler_name","heun"),("scheduler","beta")]:
        with pytest.raises(ValueError,match="Viggle"):
            normalize({"positive_prompt":"test","turbo":True,key:value},"generate")
    with pytest.raises(ValueError,match="Viggle"):
        parse_prompt("test --turbo --sample heun",{})
