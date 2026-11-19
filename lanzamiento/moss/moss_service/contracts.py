from __future__ import annotations

import math
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import profile


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    profile: Literal["base", "sft"] = "sft"
    unet_name: str | None = Field(default=None, min_length=1, max_length=512)
    clip_name: str | None = Field(default=None, min_length=1, max_length=512)
    vae_name: str | None = Field(default=None, min_length=1, max_length=512)
    model_type: Literal["base", "sft"] | None = None
    weight_dtype: Literal["default", "fp8_e4m3fn_fast", "fp8_e4m3fn", "fp8_e5m2"] | None = None
    turbo: bool = False
    turbo12: bool = False
    superturbo: bool = False
    mu: float | None = Field(default=None, ge=0.0, le=5.0)
    seeds: list[int] = Field(default_factory=list, max_length=8)
    n: int = Field(default=1, ge=1, le=8)
    positive_prompt: str = Field(min_length=1, max_length=16000)
    negative_prompt: str = Field(default="", max_length=16000)
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    seed: int = Field(default=0, ge=0, le=2**63 - 1)
    lora: dict[str, float] = Field(default_factory=dict, max_length=8)
    steps: int | None = Field(default=None, ge=1, le=80)
    cfg: float | None = Field(default=None, ge=1, le=12)
    sampler_name: Literal["euler", "er_sde", "lcm"] = "euler"
    scheduler: Literal["simple", "beta", "normal"] = "simple"
    use_tiled_vae: bool = False
    vae_tile_size: int = Field(default=512, ge=128, le=1024, multiple_of=32)
    vae_tile_overlap: int = Field(default=64, ge=0, le=256, multiple_of=8)
    return_base64: bool = True

    @field_validator("width", "height")
    @classmethod
    def aligned(cls, value: int) -> int:
        if value % 16:
            raise ValueError("La resolución debe ser múltiplo de 16")
        return value

    @field_validator("lora")
    @classmethod
    def valid_weights(cls, values: dict[str, float]) -> dict[str, float]:
        for name, weight in values.items():
            if not name.strip() or len(name) > 512 or not math.isfinite(weight) or abs(weight) > 2:
                raise ValueError("LoRA inválida: usar un ID exacto y peso entre -2 y 2")
        return values

    @model_validator(mode="after")
    def bounded(self):
        if self.width * self.height > 2097152:
            raise ValueError("Máximo 2 megapíxeles latentes por trabajo")
        if self.vae_tile_overlap >= self.vae_tile_size:
            raise ValueError("overlap debe ser menor al tile")
        return self


def normalize(payload: dict, mode: str) -> dict:
    if mode not in {"generate", "preview"}:
        raise ValueError("Modo no válido")
    data = GenerateRequest.model_validate(payload).model_dump()
    preset = effective_profile(data)
    if mode == "preview":
        raise ValueError("Usar turbo=true para MOSS; no hay TAE de Krea en este modelo")
    data["steps"] = data["steps"] or preset["steps"]
    data["cfg"] = data["cfg"] if data["cfg"] is not None else preset["cfg"]
    turbo_modes = [name for name in ("turbo", "turbo12", "superturbo") if data[name]]
    if len(turbo_modes) > 1:
        raise ValueError("Elegir solo un modo: --turbo, --turbo12 o --superturbo")
    if turbo_modes:
        mode = turbo_modes[0]
        if mode == "turbo":
            data.update(steps=8, cfg=1.0, sampler_name="lcm", scheduler="simple",
                        negative_prompt="", mu=4.4817)
            adapter = "loras/moss_image_base_dmd2_v2_bf16.safetensors"
        elif mode == "turbo12":
            data.update(steps=12, cfg=1.0, sampler_name="euler", scheduler="simple",
                        negative_prompt="", mu=None)
            adapter = "loras/12_step_turbo_moss_1024_distill_v8_r64.safetensors"
        else:
            data.update(steps=4, cfg=1.0, sampler_name="euler", scheduler="simple",
                        negative_prompt="", mu=None)
            adapter = "loras/moss-turbo-4step.safetensors"
        other_adapters = {
            "loras/moss_image_base_dmd2_v2_bf16.safetensors",
            "loras/12_step_turbo_moss_1024_distill_v8_r64.safetensors",
            "loras/moss-dmd-8step.safetensors",
            "loras/moss-turbo-4step.safetensors",
        } - {adapter}
        if other_adapters.intersection(data["lora"]):
            raise ValueError("No combinar adaptadores de aceleración")
        data["lora"][adapter]=1.0
        if len(data["lora"])>8:raise ValueError("Máximo 8 LoRAs incluyendo aceleración")
    data["seeds"] = independent_seeds(data["n"], data["seed"], data["seeds"])
    data["seed"] = data["seeds"][0]
    data["seed_string"] = str(data["seed"])
    data["mode"] = mode
    data["effective_profile"] = preset
    return data


def independent_seeds(count: int, first_seed: int = 0, supplied: list[int] | None = None) -> list[int]:
    """Return unique per-image seeds; a nonzero explicit seed pins only image one."""
    maximum = 2**63 - 1
    if supplied:
        if len(supplied) != count or any(not 1 <= value <= maximum for value in supplied):
            raise ValueError("Enviar exactamente n semillas entre 1 y 2^63-1")
        if len(set(supplied)) != len(supplied):
            raise ValueError("Las semillas del lote deben ser únicas")
        return list(supplied)
    seeds: list[int] = []
    if first_seed:
        seeds.append(first_seed)
    seen = set(seeds)
    while len(seeds) < count:
        candidate = secrets.randbelow(maximum) + 1
        if candidate not in seen:
            seen.add(candidate)
            seeds.append(candidate)
    return seeds


def effective_profile(data: dict) -> dict:
    preset = profile(data["profile"])
    for field, target in (("unet_name", "model"), ("clip_name", "clip"), ("vae_name", "vae")):
        if data.get(field):
            preset[target] = data[field]
    if data.get("model_type") and data["model_type"]!=preset["family"]:
        raise ValueError("Seleccionar el perfil base o sft correspondiente al checkpoint")
    if data.get("weight_dtype"):preset["weight_dtype"]=data["weight_dtype"]
    return preset
