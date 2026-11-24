"""CPU-safe settings and lazy GPU adapter for pinned SmartSeedVariance."""
from __future__ import annotations
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, model_validator

OPTIONS=json.loads(Path(__file__).with_name("variance_options.json").read_text(encoding="utf-8"))
PRESETS=OPTIONS["presets"]
DIRECTIONS=OPTIONS["directions"]
FADES=OPTIONS["fades"]
SAMPLERS=OPTIONS["samplers"]
SCHEDULERS=OPTIONS["schedulers"]
PROTECTION={"none":"🚫 None","first-quarter":"First Quarter","first-half":"First Half",
            "last-quarter":"Last Quarter","last-half":"Last Half","custom":"⚙️ Custom Regions","random":"🎲 Random Regions"}
TIMING={"all":"All Steps","beginning":"Beginning Steps","ending":"Ending Steps"}

class VarianceRequest(BaseModel):
    variance: Literal[tuple(PRESETS)] = "off"
    variance_strength: int = Field(default=50,ge=0,le=100)
    variance_seed: int | None = Field(default=None,ge=0,le=2**63-1)
    variance_protect: Literal[tuple(PROTECTION)] = "none"
    variance_regions: str = Field(default="",max_length=512)
    variance_direction: Literal[tuple(DIRECTIONS)] = "none"
    variance_shift: int = Field(default=100,ge=0,le=200)
    variance_fade: Literal[tuple(FADES)] = "instant"
    variance_timing: Literal[tuple(TIMING)] = "all"
    variance_schedule: Literal["constant","decreasing","step_cutoff","tiered_release","hard_lock"] = "constant"
    variance_cutoff: int = Field(default=4,ge=0,le=80)
    variance_tail: float = Field(default=0,ge=0,le=1)
    variance_freeze: bool = False
    variance_normalize: bool = False
    variance_vibe: str = Field(default="",max_length=16000)
    variance_blend: float = Field(default=.5,ge=0,le=1)
    @model_validator(mode="after")
    def regions_valid(self):
        if self.variance!="off" and self.variance_protect=="custom":
            if not re.fullmatch(r"\s*\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*\s*",self.variance_regions):
                raise ValueError('--variance-regions requiere índices o rangos, por ejemplo "0-5,15-20".')
            for part in self.variance_regions.split(','):
                bounds=[int(x) for x in part.split('-')]
                if len(bounds)==2 and bounds[0]>bounds[1]:raise ValueError("--variance-regions contiene un rango invertido.")
        return self


def seed_for_variance(seed,override=None):
    if override is None:return seed
    raw=hashlib.sha256(f"krea2-variance:{override}:{seed}".encode()).digest()
    return int.from_bytes(raw[:8],"big") & (2**63-1)


def apply_batch(conditioning,request,target_vibe=None):
    # Keep the disabled path identical, with no tensor copies or vendor import.
    if request.get("variance","off")=="off":return conditioning,None
    import torch
    from .vendor.smart_seed_variance import RBG_Smart_Seed_Variance
    node=RBG_Smart_Seed_Variance()
    settings=VarianceRequest.model_validate({k:v for k,v in request.items() if k=="variance" or k.startswith("variance_")}).model_dump()
    seeds=[seed_for_variance(seed,settings["variance_seed"]) for seed in request.get("seeds",[request["seed"]])]
    variants=[]
    for seed in seeds:
        # Upstream receives its own metadata dicts; the encoder cache stays pristine.
        source=[[tensor,copy.deepcopy(meta)] for tensor,meta in conditioning]
        result=node.apply_variance(source,variance_preset=PRESETS[settings["variance"]],
            fine_tune_variance=settings["variance_strength"],model_type="📸 Krea2 (SingleStream)",
            fade_curve=FADES[settings["variance_fade"]],noise_injection=TIMING[settings["variance_timing"]],
            protect_mode=PROTECTION[settings["variance_protect"]],protect_regions=settings["variance_regions"],
            direction_shift=DIRECTIONS[settings["variance_direction"]],shift_strength=settings["variance_shift"],seed=seed,
            variance_schedule=settings["variance_schedule"],cutoff_step=settings["variance_cutoff"],total_steps=request["steps"],
            cutoff_strength=settings["variance_tail"],structure_freeze=settings["variance_freeze"],
            normalize_strength=settings["variance_normalize"],target_vibe=target_vibe,vibe_blend=settings["variance_blend"],
            prompt_text=request["positive_prompt"])
        variants.append(result["result"][0])
    combined=[]
    for index in range(len(variants[0])):
        tensors=[variant[index][0] for variant in variants]
        metadata=copy.deepcopy(variants[0][index][1])
        # Pooled embeddings are batch-sensitive too, when present.
        if isinstance(metadata.get("pooled_output"),torch.Tensor):
            metadata["pooled_output"]=torch.cat([v[index][1]["pooled_output"] for v in variants],dim=0)
        combined.append([torch.cat(tensors,dim=0),metadata])
    return combined,{"revision":OPTIONS["revision"],"model_type":"krea2","settings":settings,"seeds":seeds}
