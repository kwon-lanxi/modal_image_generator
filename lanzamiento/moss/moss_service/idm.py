"""Native MOSS data-space IDM; unmerged FP32 deltas and temporary time path.
Algorithm/source: akhaliq/MOSS-Image-IDM-2-Steps, Apache-2.0, v1 export750.
"""
from contextlib import contextmanager

@contextmanager
def patched_network(network,lora,time_state,device):
    import torch
    from torch import nn
    import torch.nn.functional as F
    handles=[];originals={};clock={"code":None}
    class TimePath(nn.Module):
        def forward(self,x):
            code=clock["code"].reshape(-1)
            half=128
            freq=10000**(-torch.arange(half,device=device,dtype=torch.float64)/half)
            phase=code.double()[:,None]*freq
            emb=torch.cat((phase.cos(),phase.sin()),-1).float()
            return F.linear(F.silu(F.linear(emb,time_state["time_embedding.0.weight"],time_state["time_embedding.0.bias"])),time_state["time_embedding.2.weight"],time_state["time_embedding.2.bias"])
    class Modulation(nn.Module):
        def __init__(self,weight,bias):
            super().__init__();self.weight=weight;self.bias=bias
        def forward(self,x):return F.linear(x.float(),self.weight,self.bias)
    try:
        required={"time_embedding.0.weight","time_embedding.0.bias","time_embedding.2.weight","time_embedding.2.bias","shared_block_modulation.bias","video_head_modulation.bias"}
        if set(time_state)!=required:raise ValueError("Formato de time_embedding IDM inesperado.")
        time_state={k:v.to(device=device,dtype=torch.float32) for k,v in time_state.items()}
        for name in ("time_embedding","shared_block_modulation","video_head_modulation"):
            originals[name]=getattr(network,name)
        network.time_embedding=TimePath()
        for name in ("shared_block_modulation","video_head_modulation"):
            old=originals[name]
            setattr(network,name,Modulation(old.weight.detach().to(device=device,dtype=torch.float32),time_state[name+".bias"]))
        # FP32 modulation promotes the residual stream. Native Comfy BF16
        # linears have no autocast in this raw-network route; cast only their
        # inputs, preserving the FP32 time path and unmerged LoRA computation.
        def base_input(module,inputs):
            if inputs and inputs[0].dtype!=module.weight.dtype:
                return (inputs[0].to(module.weight.dtype),*inputs[1:])
        for module in network.modules():
            if isinstance(module,nn.Linear):
                handles.append(module.register_forward_pre_hook(base_input))
        groups={}
        for key,down in lora.items():
            if not key.endswith(".lora_down.weight"):continue
            stem=key[:-len(".lora_down.weight")];path=stem.removeprefix("diffusion_model.")
            up=lora[stem+".lora_up.weight"];alpha=float(lora.get(stem+".alpha",down.shape[0]));scale=alpha/down.shape[0]
            section=None
            if path.endswith((".proj.q",".proj.k",".proj.v")):
                part=path[-1];parent=path.rsplit('.',1)[0];module=network.get_submodule(parent)
                if "qkv" in module:
                    path=parent+".qkv";section="qkv".index(part)
            target=network.get_submodule(path)
            groups.setdefault(target,[]).append((down.to(device=device,dtype=torch.float32),up.to(device=device,dtype=torch.float32),scale,section))
        if not groups:raise ValueError("El adaptador IDM no contiene LoRAs compatibles.")
        for module,patches in groups.items():
            def hook(mod,inputs,output,patches=patches):
                result=output.clone();x=inputs[0].float()
                for down,up,scale,section in patches:
                    delta=F.linear(F.linear(x,down),up)*scale
                    if section is None:result=result+delta.to(result.dtype)
                    else:
                        size=result.shape[-1]//3
                        result[...,section*size:(section+1)*size]+=delta.to(result.dtype)
                return result
            handles.append(module.register_forward_hook(hook))
        yield clock
    finally:
        for handle in handles:handle.remove()
        for name,original in originals.items():setattr(network,name,original)


def sample_idm(model,noise,positive,assets,progress):
    import torch
    import comfy.model_management as mm
    from safetensors.torch import load_file
    from .files import resolve
    mm.load_models_gpu([model],force_full_load=True)
    device=mm.get_torch_device();network=model.model.diffusion_model
    if len(positive)!=1:raise ValueError("IDM no admite conditioning temporal dividido.")
    context=positive[0][0].to(device=device,dtype=torch.bfloat16)
    z=noise.to(device=device,dtype=torch.float32);context=context.expand(z.shape[0],-1,-1)
    lora=load_file(str(resolve(assets["lora"],"lora")))
    time=load_file(str(resolve(assets["time"],"lora")))
    ds=load_file(str(resolve(assets["dataspace"],"lora")))
    q=ds["latent_mix.Q"].to(device=device,dtype=torch.float32);codes=ds["step_codes"].to(device=device,dtype=torch.float32)
    if len(codes)!=2 or tuple(q.shape)!=(128,128):raise ValueError("Se esperaba el adaptador IDM T2I de 2 pasos.")
    x0=torch.zeros_like(z)
    with patched_network(network,lora,time,device) as clock:
        for index,code in enumerate(codes):
            clock["code"]=code.reshape(1).expand(z.shape[0])
            x=z+torch.einsum("oc,bchw->bohw",q,x0)
            kwargs={}
            if positive[0][1].get("context_attention_bias") is not None:
                kwargs["context_attention_bias"]=positive[0][1]["context_attention_bias"].to(device)
            velocity=network(x.to(torch.bfloat16),clock["code"],context,transformer_options=model.model_options.get("transformer_options",{}),**kwargs)
            x0=z-velocity.float()
            progress(index,x0,x,2)
    if not torch.isfinite(x0).all():raise ValueError("IDM produjo NaN/Inf.")
    return x0
