"""CPU-only acceleration registry: recipe, model family and immutable assets."""
import json
from pathlib import Path
REVISIONS=json.loads(Path(__file__).with_name("acceleration_sources.json").read_text())

def asset(repo,file,target,name,subfolder=""):
    return {"url":"https://huggingface.co/"+repo+"/resolve/"+REVISIONS[repo]+"/"+file,
            "target":target,"name":name,"subfolder":subfolder}

HADES="DeVilDoNia/HaDeS-MOSS-Turbo"
PRESETS={
 "turbo-sft-hades":dict(family="sft",steps=8,cfg=1,max_cfg=1,mu=5.,sampler="euler",file="loras/moss-hades-sft-v1.safetensors",asset=asset(HADES,"HaDeS-MOSS-Turbo-8steps-sft-v1.0.safetensors","lora","moss-hades-sft-v1.safetensors")),
 "turbo-base-hades":dict(family="base",steps=8,cfg=1,max_cfg=1.5,mu=5.,sampler="euler",file="loras/moss-hades-base-v1.safetensors",asset=asset(HADES,"HaDeS-MOSS-Turbo-8steps-v1.0.safetensors","lora","moss-hades-base-v1.safetensors")),
 "turbo-kromoss-base":dict(family="base",steps=8,cfg=1,max_cfg=1,mu=5.,sampler="euler",file="loras/moss-kromoss-base-dmd2.safetensors",asset=asset("lodestones/Kromoss","8-steps-dmd2-lora.safetensors","lora","moss-kromoss-base-dmd2.safetensors")),
 "turbo-kromoss-sft":dict(family="sft",steps=8,cfg=1,max_cfg=1,mu=5.,sampler="euler",file="loras/moss-lodestones-sft-dmd8.safetensors",asset=asset("lodestones/MOSS-Image-SFT-8-steps-dmd","lora.safetensors","lora","moss-lodestones-sft-dmd8.safetensors")),
 "superturbo-sft":dict(family="sft",steps=4,cfg=1,max_cfg=2,mu=None,sampler="euler",sigmas=[1.,.91,.717,.024,0.],file="loras/moss-sft-lvladikov-4step.safetensors",asset=asset("lvladikov/MOSS-SFT-Distill-4step-LoRA","moss_sft_4step_rank_64_lora_comfyui.safetensors","lora","moss-sft-lvladikov-4step.safetensors")),
 "turbo-juggernaut":dict(family="base",steps=6,cfg=1,max_cfg=1,mu=5.,sampler="euler_ancestral",model="juggernaut_moss_v1_int8_convrot",file="loras/moss-juggernaut-turbo.safetensors",asset=asset("Kandoo/Juggernaut-Moss-DMD2-LoRA-Beta","JuggernautMossDMD2Lora4-8Steps.safetensors","lora","moss-juggernaut-turbo.safetensors")),
}
for family in ("base","sft"):
    PRESETS["hiperturbo-"+family]=dict(family=family,steps=2,cfg=1,max_cfg=1,mu=None,sampler="idm_dataspace",file="loras/moss-idm2/lora.safetensors",model="moss_image_"+family+"_bf16",weight_dtype="default",experimental=family=="base")
# Closest SFT-trained peer of the existing Base DMD2: lodestones SFT DMD8.
ALIASES={"turbo-sft":"turbo-kromoss-sft","hyperturbo-base":"hiperturbo-base","hyperturbo-sft":"hiperturbo-sft"}
FLAGS={"--"+key:ALIASES.get(key,key) for key in list(PRESETS)+list(ALIASES)}
IDM_ASSETS=[asset("akhaliq/MOSS-Image-IDM-2-Steps","runs/idm/export/000750/"+name,"lora",name,"moss-idm2") for name in ("lora.safetensors","time_embedding.safetensors","dataspace.safetensors")]
FLOW_ASSET=asset("TensorForger/FlowUpscaler","flow_upscaler.safetensors","upscaler","flow_upscaler.safetensors")
MODEL_ASSETS=[asset("Comfy-Org/MOSS-Image","diffusion_models/moss_image_"+f+"_bf16.safetensors","unet","moss_image_"+f+"_bf16.safetensors") for f in ("base","sft")]
MODEL_ASSETS.append(asset("Kandoo/Juggernaut-Moss","juggernaut_moss_v1_int8_convrot.safetensors","unet","juggernaut_moss_v1_int8_convrot.safetensors"))

def downloads():
    return [p["asset"] for p in PRESETS.values() if p.get("asset")]+IDM_ASSETS+MODEL_ASSETS+[FLOW_ASSET]

def apply_preset(payload):
    data=dict(payload)
    name=data.get("acceleration")
    if not name:return data
    name=ALIASES.get(name,name)
    if name not in PRESETS:raise ValueError("Modo de aceleración desconocido: "+str(name))
    preset=PRESETS[name]
    if any(data.get(k) for k in ("turbo","turbo12","superturbo")):raise ValueError("Elegí un único flag de aceleración.")
    if data.get("profile") and data["profile"]!=preset["family"]:raise ValueError("--"+name+" requiere --profile "+preset["family"])
    data.update(acceleration=name,profile=preset["family"])
    if preset.get("model"):
        if data.get("unet_name") and data["unet_name"]!=preset["model"]:raise ValueError("--"+name+" requiere el checkpoint "+preset["model"])
        data["unet_name"]=preset["model"]
    if name.startswith("hiperturbo"):
        if data.get("weight_dtype") not in (None,"default"):raise ValueError("IDM requiere el modelo BF16 y adaptador FP32, sin cast FP8.")
        data["weight_dtype"]="default"
    cfg=data.get("cfg") if data.get("cfg") is not None else preset["cfg"]
    if not 1<=cfg<=preset["max_cfg"]:raise ValueError("--"+name+" admite CFG entre 1 y "+str(preset["max_cfg"]))
    if data.get("negative_prompt","").strip() and cfg<=1:raise ValueError("--neg necesita CFG > 1; este modo se está ejecutando a CFG 1.")
    data.update(steps=preset["steps"],cfg=cfg,mu=preset["mu"],sampler_name=preset["sampler"] if preset["sampler"]!="idm_dataspace" else "euler",scheduler="simple")
    loras=dict(data.get("lora",{}))
    files={p["file"] for p in PRESETS.values()}|{"loras/moss_image_base_dmd2_v2_bf16.safetensors","loras/moss-turbo-4step.safetensors","loras/moss-dmd-8step.safetensors","loras/12_step_turbo_moss_1024_distill_v8_r64.safetensors"}
    if any(k in files and k!=preset["file"] for k in loras):raise ValueError("No combinar LoRAs de aceleración.")
    loras[preset["file"]]=1.
    if len(loras)>8:raise ValueError("Máximo 8 LoRAs incluyendo aceleración.")
    data["lora"]=loras
    return data
