"""FlowUpscaler x2/x4 with independent noise per image and pass, GPU-only."""
import hashlib

def pass_seed(seed,index):
    return int.from_bytes(hashlib.sha256(f"moss-flow:{seed}:{index}".encode()).digest()[:8],"big") & (2**63-1)

def unpack(x):
    b,c,h,w=x.shape
    return x.reshape(b,c//4,2,2,h,w).permute(0,1,4,2,5,3).reshape(b,c//4,h*2,w*2)

def pack(x):
    b,c,h,w=x.shape
    return x.reshape(b,c,h//2,2,w//2,2).permute(0,1,3,5,2,4).reshape(b,c*4,h//2,w//2)

def upscale_batch(samples,seeds,scale,weight_path,progress=None,model=None):
    import torch
    import torch.nn.functional as F
    import comfy.model_management as mm
    if scale==1:return samples,None
    if scale not in (2,4) or len(samples)!=len(seeds):raise ValueError("Upscaler: escala o lote inválido.")
    from .vendor.flow_upscaler_unet import UpscalerUNet
    from safetensors.torch import load_file
    device=mm.get_torch_device()
    mm.unload_all_models()
    if model is None:
        model=UpscalerUNet();model.load_state_dict(load_file(str(weight_path)),strict=True)
    model=model.to(device=device,dtype=torch.float32).eval()
    outputs=[];record=[]
    try:
        for i,seed in enumerate(seeds):
            x=samples[i:i+1].to(device=device,dtype=torch.float32)
            passes=[]
            for stage in range(1 if scale==2 else 2):
                low=unpack(x);h,w=low.shape[-2:]
                # UNet has two reductions; pad edge pixels and crop after the pass.
                low=F.pad(low,(0,(-w)%4,0,(-h)%4),mode="replicate")
                current=pass_seed(seed,stage);passes.append(current)
                noise=torch.randn((1,32,low.shape[-2]*2,low.shape[-1]*2),device=device,dtype=torch.float32,generator=torch.Generator(device=device).manual_seed(current))
                # Exact default FlowMatchEuler one-step: sigma1 -> 0, t1000.
                high=noise-model(sample=noise,timestep=torch.tensor([1000.],device=device),latents_small=low)
                x=pack(high[...,:h*2,:w*2].contiguous())
                if progress:progress(i,stage)
            outputs.append(x.cpu());record.append(passes)
        return torch.cat(outputs),{"scale":scale,"seeds":record,"implementation":"TensorForger/FlowUpscaler","revision":"ae334296526769d7fb5065465e364049f6c45c2b"}
    finally:model.to("cpu")
