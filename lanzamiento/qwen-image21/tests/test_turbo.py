import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from qwen_service.contracts import normalize, TURBO_LORA
from qwen_tg_logic import parse_prompt


def test_turbo_flag_selects_fixed_recipe_and_preserves_style():
    r=parse_prompt('portrait !style:0.6 --turbo --steps 25 --cfg 3',{'style':{'id':'loras/style.safetensors'}})
    assert r['positive_prompt']=='portrait' and r['turbo'] is True
    assert r['steps']==6 and r['cfg']==1 and r['scheduler']=='viggle_turbo'
    assert r['lora']=={'loras/style.safetensors':.6,TURBO_LORA:1.0}
    assert normalize(r,'generate')['turbo'] is True


def test_api_turbo_edit_and_repeat_are_idempotent():
    r=normalize({'positive_prompt':'blue','turbo':True,'input_images':['a.png'],'negative_prompt':'bad'},'edit')
    assert r['steps']==6 and r['cfg']==1 and r['negative_prompt']==''
    payload={k:v for k,v in r.items() if k not in {'mode','effective_profile','seed_string'}}
    assert normalize(payload,'edit')['lora']=={TURBO_LORA:1.0}
    assert normalize({'positive_prompt':'x','turbo':'false'},'generate')['turbo'] is False


def test_turbo_rejects_mixed_distillers_and_ninth_lora():
    with pytest.raises(ValueError):parse_prompt('x !old --turbo',{'old':{'id':'loras/Qwen-Image-2.1-viggle-turbo-v0.2.safetensors'}})
    with pytest.raises(ValueError):normalize({'positive_prompt':'x','turbo':True,'lora':{f'style{i}':1 for i in range(8)}},'generate')
    with pytest.raises(ValueError):normalize({'positive_prompt':'x','scheduler':'viggle_turbo'},'generate')
    assert normalize({'positive_prompt':'x'},'generate')['steps']==25


@pytest.fixture
def official_nodes(monkeypatch):
    global torch
    torch=pytest.importorskip('torch')
    # Real PyTorch hooks/sigma math; replace only unavailable Comfy infrastructure.
    comfy=ModuleType('comfy');comfy.__path__=[]
    patcher=ModuleType('comfy.patcher_extension')
    patcher.WrappersMP=SimpleNamespace(DIFFUSION_MODEL='diffusion')
    utils=ModuleType('comfy.utils')
    folders=ModuleType('folder_paths')
    comfy.patcher_extension=patcher;comfy.utils=utils
    for name,module in [('comfy',comfy),('comfy.patcher_extension',patcher),('comfy.utils',utils),('folder_paths',folders)]:
        monkeypatch.setitem(sys.modules,name,module)
    path=Path(__file__).resolve().parents[1]/'qwen_service/vendor/viggle_turbo.py'
    spec=importlib.util.spec_from_file_location('viggle_test_nodes',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module,utils,folders


def test_official_sigmas_are_shifted_six_steps_and_terminal_zero(official_nodes):
    module,_,_=official_nodes
    sigmas=module.ViggleTurboSigmas().get_sigmas({'samples':torch.zeros(1,64,16,16)},'1.0,0.9375,0.875,0.75,0.5,0.25')[0]
    assert sigmas.shape==(7,) and sigmas[0]==1 and sigmas[-1]==0
    assert torch.all(sigmas[:-1]>sigmas[1:])
    assert sigmas[4].item()==pytest.approx(.6224593312)
    large=module.ViggleTurboSigmas().get_sigmas({'samples':torch.zeros(1,64,64,64)},'1.0,0.9375,0.875,0.75,0.5,0.25')[0]
    assert large[4]>sigmas[4]


def test_official_runtime_lora_preserves_base_and_removes_hooks(official_nodes):
    module,utils,folders=official_nodes
    dm=torch.nn.Module();dm.proj=torch.nn.Linear(2,2,bias=False)
    original=dm.proj.weight.detach().clone()
    a=torch.tensor([[.25,.5]]);b=torch.tensor([[.1],[.2]])
    utils.load_torch_file=lambda *a,**k:({'transformer.proj.lora_A.weight':a_weight,'transformer.proj.lora_B.weight':b_weight},{'lora_adapter_metadata':json.dumps({'transformer.lora_alpha':1,'transformer.r':1})})
    a_weight,b_weight=a,b
    folders.get_full_path_or_raise=lambda *a:'adapter'
    wrappers=[]
    model=SimpleNamespace(clone=lambda:SimpleNamespace(add_wrapper_with_key=lambda *args:wrappers.append(args[-1])))
    module.ViggleTurboLora().load(model,'adapter',1.0)
    x=torch.tensor([[1.,2.]])
    executor=SimpleNamespace(class_obj=dm)
    class Executor:
        class_obj=dm
        def __call__(self,values):return dm.proj(values)
    base=torch.nn.functional.linear(x,original)
    assert torch.allclose(wrappers[0](Executor(),x),base+torch.nn.functional.linear(torch.nn.functional.linear(x,a),b))
    assert torch.equal(dm.proj.weight,original) and len(dm.proj._forward_hooks)==0
    class Broken(Executor):
        def __call__(self,values):raise RuntimeError('sampling error')
    with pytest.raises(RuntimeError):wrappers[0](Broken(),x)
    assert len(dm.proj._forward_hooks)==0


def test_official_fused_mlp_branch_matches_unmerged_reference(official_nodes):
    module,_,_=official_nodes
    class MLP(torch.nn.Module):
        fused=True
        def __init__(self):
            super().__init__();self.gate_up=torch.nn.Linear(2,4,bias=False);self.out=torch.nn.Linear(2,2,bias=False)
        def forward(self,x):
            g,u=self.gate_up(x).chunk(2,-1)
            return torch.nn.functional.linear(torch.nn.functional.silu(g)*u,self.out.weight)
    dm=torch.nn.Module();dm.mlp=MLP()
    x=torch.tensor([[1.,2.]])
    a=torch.tensor([[.1,.2]]);b=torch.tensor([[.3],[.4]])
    delta=torch.nn.functional.linear(torch.nn.functional.linear(x,a),b)
    g,u=dm.mlp.gate_up(x).chunk(2,-1)
    hidden=torch.nn.functional.silu(g+delta)*(u+delta)
    expected=torch.nn.functional.linear(hidden,dm.mlp.out.weight)+torch.nn.functional.linear(torch.nn.functional.linear(hidden,a),b)
    lora={f'mlp.{name}':[a,b] for name in ['gate_layer','proj','out']}
    class Executor:
        class_obj=dm
        def __call__(self,values):return dm.mlp(values)
    result=module.run_with_lora(lora,Executor(),x)
    assert torch.allclose(result,expected,atol=1e-6)
    assert not dm.mlp._forward_hooks and not dm.mlp.gate_up._forward_hooks
