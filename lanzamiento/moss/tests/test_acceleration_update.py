import sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from types import ModuleType
import pytest
from moss_service.acceleration import FLAGS,PRESETS,downloads
from moss_service.contracts import normalize
from moss_tg_logic import parse_prompt
@pytest.mark.parametrize("flag,key",list(FLAGS.items()))
def test_flags(flag,key):
    d=parse_prompt("portrait !style:0.6 "+flag,{"style":{"id":"loras/style.safetensors"}})
    assert d["acceleration"]==key and d["steps"]==PRESETS[key]["steps"] and d["profile"]==PRESETS[key]["family"]
    assert d["lora"]["loras/style.safetensors"]==.6 and d["lora"][PRESETS[key]["file"]]==1
    a=normalize(d,"generate");assert a["mode"]=="generate" and a["lora"]==d["lora"]
@pytest.mark.parametrize("flags",["--turbo-base-hades --cfg 2","--turbo-sft-hades --neg bad","--turbo-sft-hades --profile base","--turbo-sft --turbo","--hiperturbo-base --turbo-sft","--upscarler x3"])
def test_bad_flags(flags):
    with pytest.raises(ValueError):parse_prompt("portrait "+flags,{})
def test_negative_and_upscale():
    d=parse_prompt('portrait --superturbo-sft --cfg 2 --neg "red clothes" --upscarler x4 --n 6 --size 1:1 1.5mp',{})
    assert d["cfg"]==2 and d["negative_prompt"]=="red clothes" and d["upscale"]==4 and d["n"]==6
    assert PRESETS[d["acceleration"]]["sigmas"]==[1,.91,.717,.024,0]
    assert len(downloads())<20
    assert normalize({"positive_prompt":"x","turbo":True},"generate")["mode"]=="generate"
def test_flow_batch(monkeypatch):
    torch=pytest.importorskip("torch")
    from moss_service.flow_upscale import upscale_batch
    comfy=ModuleType("comfy");mm=ModuleType("comfy.model_management")
    mm.get_torch_device=lambda:torch.device("cpu");mm.unload_all_models=lambda:None;comfy.model_management=mm
    monkeypatch.setitem(sys.modules,"comfy",comfy);monkeypatch.setitem(sys.modules,"comfy.model_management",mm)
    class Tiny(torch.nn.Module):
        def forward(self,sample,timestep,latents_small):return torch.zeros_like(sample)
    x=torch.randn(2,128,5,7)
    out,meta=upscale_batch(x,[42,89],4,None,model=Tiny());again,_=upscale_batch(x,[42,89],4,None,model=Tiny());single,_=upscale_batch(x[1:],[89],4,None,model=Tiny())
    assert out.shape==(2,128,20,28) and torch.equal(out,again) and torch.equal(out[1:],single)
    assert not torch.equal(out[0],out[1]) and len({s for row in meta["seeds"] for s in row})==4


def test_idm_hook_cleanup_and_unmerged_weights():
    torch=pytest.importorskip("torch")
    from torch import nn
    from moss_service.idm import patched_network
    class Block(nn.Module):
        def __init__(self):super().__init__();self.proj=nn.ModuleDict({"qkv":nn.Linear(8,24,bias=False)})
    class Net(nn.Module):
        def __init__(self):
            super().__init__();self.layers=nn.ModuleList([Block()]);self.time_embedding=nn.Sequential(nn.Linear(256,8),nn.SiLU(),nn.Linear(8,8));self.shared_block_modulation=nn.Linear(8,48);self.video_head_modulation=nn.Linear(8,16)
    net=Net().to(torch.bfloat16);saved=copy.deepcopy(net.state_dict())
    time={k:v.clone() for k,v in saved.items() if k.startswith("time_embedding.") or k in {"shared_block_modulation.bias","video_head_modulation.bias"}}
    key="diffusion_model.layers.0.proj.q"
    lora={key+".lora_down.weight":torch.ones(2,8),key+".lora_up.weight":torch.ones(8,2),key+".alpha":torch.tensor(2.)}
    x=torch.ones(1,1,8);base=net.layers[0].proj["qkv"](x.to(torch.bfloat16))
    with pytest.raises(RuntimeError,match="cleanup"):
        with patched_network(net,lora,time,torch.device("cpu")) as clock:
            clock["code"]=torch.tensor([.25]);out=net.layers[0].proj["qkv"](x)
            assert not torch.equal(out[...,:8],base[...,:8]) and torch.equal(out[...,8:],base[...,8:])
            assert torch.isfinite(net.time_embedding(torch.ones(1,256))).all()
            raise RuntimeError("cleanup")
    assert not net.layers[0].proj["qkv"]._forward_hooks
    assert not net.layers[0].proj["qkv"]._forward_pre_hooks
    assert all(torch.equal(v,saved[k]) for k,v in net.state_dict().items())
