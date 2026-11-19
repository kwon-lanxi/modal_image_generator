"""CPU-only, pinned downloads and conversion. modal run prepare.py"""
from pathlib import Path
import modal

app=modal.App("moss-image-prepare")
volume=modal.Volume.from_name("moss-image-vol",create_if_missing=True)
image=(modal.Image.debian_slim(python_version="3.12")
       .pip_install("huggingface_hub==1.4.1","safetensors==0.7.0","numpy==2.2.6")
       .pip_install("torch==2.10.0",index_url="https://download.pytorch.org/whl/cpu"))
ROOT=Path("/moss-image-vol")
ASSETS=[
 ("Comfy-Org/MOSS-Image","2929efe3cdd506c0a430bcfca70a9a8d805cc095","diffusion_models/moss_image_base_bf16.safetensors","sources/base-bf16.safetensors"),
 ("Comfy-Org/MOSS-Image","2929efe3cdd506c0a430bcfca70a9a8d805cc095","diffusion_models/moss_image_sft_bf16.safetensors","sources/sft-bf16.safetensors"),
 ("Comfy-Org/MOSS-Image","2929efe3cdd506c0a430bcfca70a9a8d805cc095","vae/flux2-vae_small.safetensors","vae/flux2-vae_small.safetensors"),
 ("akhaliq/MOSS-Image-SFT-4-steps","2ded95c37a029360245926fe72e475e30039bbfb","lora.safetensors","loras/moss-turbo-4step.safetensors"),
 ("lodestones/MOSS-Image-SFT-8-steps-dmd","f95df83b5e90ae91ea412434b3f9b7f1c8c6314f","lora.safetensors","loras/moss-dmd-8step.safetensors"),
 ("data-archetype/moss_image_base_dmd2","f36d91fc11a3135cf499504e57f2bc9a17a0c1dc","loras/moss_image_base_dmd2_v2_bf16.safetensors","loras/moss_image_base_dmd2_v2_bf16.safetensors"),
 ("anyMODE/moss-image-12step-turbo-lora","5045cdb8366d6993282a716613227d79108e8274","12_step_turbo_moss_1024_distill_v8_r64.safetensors","loras/12_step_turbo_moss_1024_distill_v8_r64.safetensors"),
]

def quantize_tensor(weight):
    import torch
    value=weight.float()
    if not torch.isfinite(value).all():raise ValueError("Non-finite weights")
    scale=(value.abs().max()/448).clamp_min(1e-12)
    return (value/scale).clamp(-448,448).to(torch.float8_e4m3fn),scale

def convert(source,destination,encoder=False):
    """Repack TorchAO row scales into native Comfy tensorwise FP8, safely."""
    import json,torch
    torch.set_num_threads(4)
    from safetensors import safe_open
    from safetensors.torch import save_file
    if destination.exists():return
    paths=source if isinstance(source,list) else [source]
    output={};quantized=0
    for path in paths:
        with safe_open(str(path),framework="pt",device="cpu") as src:
            keys=set(src.keys())
            for key in sorted(keys):
                if key.endswith("._weight_scale"):continue
                name=key
                if encoder:
                    if not key.startswith("model.language_model."):continue
                    name=key
                tensor=src.get_tensor(key)
                scale_key=key.removesuffix(".weight")+"._weight_scale"
                if scale_key in keys:
                    tensor=tensor.float()*src.get_tensor(scale_key).float()
                # Keep embeddings, norms and state-space/linear-attention sensitive
                # tensors in BF16. Quantize only proven Linear module names.
                linear=(".proj." in key if not encoder else any(x in key for x in
                        (".mlp.gate_proj.",".mlp.up_proj.",".mlp.down_proj.",
                         ".self_attn.q_proj.",".self_attn.k_proj.",".self_attn.v_proj.",".self_attn.o_proj.")))
                if linear and key.endswith(".weight") and tensor.ndim==2:
                    tensor,scale=quantize_tensor(tensor)
                    layer=name.removesuffix(".weight")
                    output[layer+".weight_scale"]=scale
                    output[layer+".comfy_quant"]=torch.tensor(list(json.dumps({"format":"float8_e4m3fn"}).encode()),dtype=torch.uint8)
                    quantized+=1
                elif tensor.is_floating_point():tensor=tensor.to(torch.bfloat16)
                if name in output:raise ValueError("Duplicate tensor: "+name)
                output[name]=tensor.contiguous()
    if not quantized:raise ValueError("No quantizable Linear weights found")
    destination.parent.mkdir(parents=True,exist_ok=True)
    partial=destination.with_suffix(".part")
    save_file(output,str(partial),metadata={"conversion":"native Comfy tensorwise e4m3fn; sensitive layers BF16"})
    partial.replace(destination)
    print({"event":"conversion.complete","path":str(destination),"quantized_linears":quantized},flush=True)

@app.function(image=image,volumes={str(ROOT):volume},cpu=4,memory=65536,timeout=7200,retries=0)
def prepare():
    import json,shutil
    from huggingface_hub import hf_hub_download
    for folder in ("diffusion_models","text_encoders","vae","loras","inputs","outputs","_release","sources"):
        (ROOT/folder).mkdir(parents=True,exist_ok=True)
    manifest=[]
    for repo,revision,filename,target in ASSETS:
        destination=ROOT/target
        if target.startswith("sources/"):
            variant=Path(target).name.split("-")[0]
            converted=ROOT/f"diffusion_models/moss-{variant}-comfy-fp8_scaled.safetensors"
            if converted.exists() and not destination.exists():
                manifest.append(dict(repo=repo,revision=revision,filename=filename,target=target,source_cleaned=True))
                continue
        if not destination.exists():
            cached=hf_hub_download(repo,filename,revision=revision,local_dir=str(ROOT/"sources"/repo.replace("/","--")))
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(cached,str(destination)+".part")
            Path(str(destination)+".part").replace(destination)
        manifest.append(dict(repo=repo,revision=revision,filename=filename,target=target,size=destination.stat().st_size))
        volume.commit();print({"event":"download.complete","target":target},flush=True)
    repo="pottokao/MOSS-Image-Text-Encoder-Heretic";revision="9f4a2b4034a87b04a9d51904fd8084edfa11cf29"
    shards=[]
    for i in ([] if (ROOT/"text_encoders/moss-heretic-comfy-fp8_scaled-v2.safetensors").exists() else range(1,5)):
        filename=f"model.safetensors-{i:05d}-of-00004.safetensors"
        shards.append(Path(hf_hub_download(repo,filename,revision=revision,local_dir=str(ROOT/"sources"/"heretic"))))
        volume.commit()
    for variant in ("base","sft"):
        convert(ROOT/f"sources/{variant}-bf16.safetensors",ROOT/f"diffusion_models/moss-{variant}-comfy-fp8_scaled.safetensors")
        volume.commit()
    convert(shards,ROOT/"text_encoders/moss-heretic-comfy-fp8_scaled-v2.safetensors",encoder=True)
    manifest.append(dict(repo=repo,revision=revision,output="text_encoders/moss-heretic-comfy-fp8_scaled-v2.safetensors"))
    (ROOT/"_release/assets.json").write_text(json.dumps(manifest,indent=2))
    volume.commit()
    return manifest

@app.function(image=image,volumes={str(ROOT):volume},cpu=1,memory=2048,timeout=1800,retries=0)
def verify():
    import json,hashlib,struct
    report=[]
    paths=[ROOT/f"diffusion_models/moss-{v}-comfy-fp8_scaled.safetensors" for v in ("base","sft")]
    paths.append(ROOT/"text_encoders/moss-heretic-comfy-fp8_scaled-v2.safetensors")
    for path in paths:
        with path.open("rb") as file:
            size=struct.unpack("<Q",file.read(8))[0]
            header=json.loads(file.read(size))
        quant=[k for k in header if k.endswith(".comfy_quant")]
        weights=[k for k,v in header.items() if k!="__metadata__" and v["dtype"].startswith("F8")]
        if len(quant)!=len(weights) or not weights:raise ValueError("Missing native FP8 scales/metadata")
        for key in quant:
            if key.removesuffix(".comfy_quant")+".weight_scale" not in header:raise ValueError("Missing scale")
        digest=hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda:file.read(8*1024**2),b""):digest.update(block)
        item=dict(file=str(path.relative_to(ROOT)),sha256=digest.hexdigest(),size=path.stat().st_size,quantized_linears=len(weights))
        path.with_suffix(".source.json").write_text(json.dumps({**item,"converter":"prepare.py native FP8 Scaled e4m3fn","source":"BF16; see _release/assets.json"},indent=2))
        report.append(item)
    (ROOT/"_release/fp8-verification.json").write_text(json.dumps(report,indent=2))
    volume.commit()
    return report

@app.local_entrypoint()
def main(action: str="prepare"):
    if action=="cleanup-obsolete":print(cleanup_obsolete.remote())
    else:print(verify.remote() if action=="verify" else prepare.remote())


@app.function(image=image,volumes={str(ROOT):volume},cpu=.25,memory=512,timeout=600,retries=0)
def cleanup_obsolete():
    """Remove only artifacts from the superseded conversion, never user outputs."""
    import shutil
    relative=["sources/base.safetensors","sources/sft.safetensors",
              "sources/unsloth--MOSS-Image-Base-FP8","sources/unsloth--MOSS-Image-SFT-FP8",
              "sources/base-bf16.safetensors","sources/sft-bf16.safetensors",
              "sources/Comfy-Org--MOSS-Image","sources/heretic",
              "sources/akhaliq--MOSS-Image-SFT-4-steps","sources/lodestones--MOSS-Image-SFT-8-steps-dmd",
              "text_encoders/moss-heretic-comfy-fp8_scaled.safetensors",
              "text_encoders/moss-heretic-comfy-fp8_scaled.source.json"]
    removed=[]
    for name in relative:
        target=(ROOT/name).resolve()
        if not target.is_relative_to(ROOT.resolve()) or target==ROOT.resolve():raise ValueError("Unsafe cleanup path")
        if target.is_dir():shutil.rmtree(target);removed.append(name)
        elif target.is_file():target.unlink();removed.append(name)
    volume.commit()
    return removed
