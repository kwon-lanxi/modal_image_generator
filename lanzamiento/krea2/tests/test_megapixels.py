import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from krea_service.resolution import dimensions,parse_mp
from krea_service.contracts import normalize
from tg_logic import parse_prompt
@pytest.mark.parametrize("mp",["0.5","0.5mp","1.5mp","2","2mp","4mp"])
def test_mp_token(mp):
    d=parse_prompt("portrait --size 1:1 "+mp,{})
    assert d["positive_prompt"]=="portrait"
    assert abs(d["width"]*d["height"]/1_000_000-parse_mp(mp))/parse_mp(mp)<.05
    assert d["width"]==d["height"] and d["resolution_mp"]==parse_mp(mp)
def test_defaults_and_overrides():
    d=parse_prompt("portrait",{});a=normalize({"positive_prompt":"portrait"},"generate")
    assert abs(d["width"]*d["height"]/1_000_000-1.5)<.05
    assert (a["width"],a["height"])==(d["width"],d["height"])
    d=parse_prompt("portrait --size 16:9 2mp -w 896 -h 1152",{})
    assert (d["width"],d["height"])==(896,1152)
    assert parse_prompt("2 people in a room",{})["positive_prompt"]=="2 people in a room"
@pytest.mark.parametrize("mp",["0mp","-1mp","5mp","nanmp","oopsmp"])
def test_bad_mp(mp):
    with pytest.raises(ValueError):parse_prompt("portrait --size 1:1 "+mp,{})
