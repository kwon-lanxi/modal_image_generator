from __future__ import annotations

import math
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import profile


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    profile: Literal["turbo", "turbo-scaled", "raw"] = "turbo"
    unet_name: str | None = Field(default=None, min_length=1, max_length=512)
    clip_name: str | None = Field(default=None, min_length=1, max_length=512)
    vae_name: str | None = Field(default=None, min_length=1, max_length=512)
    model_type: Literal["turbo", "raw"] | None = None
    weight_dtype: Literal["default", "fp8_e4m3fn_fast", "fp8_e4m3fn", "fp8_e5m2"] | None = None
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
    sampler_name: Literal["euler", "er_sde"] = "euler"
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
        if preset["family"] != "turbo":
            raise ValueError("La preview de dos pasos requiere un perfil Turbo")
        data.update(steps=2, cfg=1.0, sampler_name="euler", scheduler="simple")
    else:
        data["steps"] = data["steps"] or preset["steps"]
        data["cfg"] = data["cfg"] if data["cfg"] is not None else preset["cfg"]
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
    family = data.get("model_type") or preset["family"]
    if family != preset["family"]:
        preset.update(family=family, steps=52 if family == "raw" else 8,
                      cfg=3.5 if family == "raw" else 1.0, shift=None if family == "raw" else 1.15)
    if data.get("weight_dtype"):
        preset["weight_dtype"] = data["weight_dtype"]
    elif data.get("unet_name"):
        # A selected quantized checkpoint carries its own layout/scales.
        name = data["unet_name"].lower()
        if any(marker in name for marker in ("fp8", "int8", "convrot", "nvfp4")):
            preset["weight_dtype"] = "default"
    if data.get("mu") is not None:
        if family == "raw":
            raise ValueError("RAW usa shift dinámico; omitir mu")
        preset["shift"] = data["mu"]
    if family == "turbo" and not preset.get("preview_lora"):
        turbo = profile("turbo")
        preset.update(preview_lora=turbo["preview_lora"], tae=turbo["tae"])
    if family == "raw":
        preset.pop("tae", None)
        preset.pop("preview_lora", None)
    return preset
