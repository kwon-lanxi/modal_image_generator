from __future__ import annotations

import math
import secrets
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import profile

TURBO_LORA = "loras/Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r256.safetensors"


def turbo_settings(payload):
    """One canonical recipe for API, Telegram and repeats; never double-stack distillers."""
    if not payload.get("turbo"):
        return dict(payload)
    loras = dict(payload.get("lora", {}))
    if any("viggle-turbo" in name.lower() and name != TURBO_LORA for name in loras):
        raise ValueError("--turbo no se combina con otra versión de Viggle")
    loras[TURBO_LORA] = 1.0
    if len(loras)>8:
        raise ValueError("Turbo ocupa una de las8LoRAs disponibles")
    return {**payload, "steps":6, "cfg":1.0, "negative_prompt":"",
            "sampler_name":"euler", "scheduler":"viggle_turbo", "lora":loras}


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    profile: Literal["base", "base-bf16"] = "base"
    turbo: bool = False
    unet_name: str | None = Field(default=None, min_length=1, max_length=512)
    clip_name: str | None = Field(default=None, min_length=1, max_length=512)
    vae_name: str | None = Field(default=None, min_length=1, max_length=512)
    weight_dtype: Literal["default", "fp8_e4m3fn_fast", "fp8_e4m3fn", "fp8_e5m2"] | None = None
    input_images: list[str] = Field(default_factory=list, max_length=10)
    reference_resolution: int = Field(default=768, ge=256, le=1408, multiple_of=32)
    kv_cache_device: Literal["auto", "cpu", "gpu", "off"] = "auto"
    kv_cache_dtype: Literal["default", "int8", "int4"] = "int8"
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
    scheduler: Literal["simple", "beta", "normal", "viggle_turbo"] = "simple"
    use_tiled_vae: bool = False
    vae_tile_size: int = Field(default=512, ge=128, le=1024, multiple_of=32)
    vae_tile_overlap: int = Field(default=64, ge=0, le=256, multiple_of=8)
    return_base64: bool = True

    @field_validator("width", "height")
    @classmethod
    def aligned(cls, value: int) -> int:
        if value % 32:
            raise ValueError("La resolución debe ser múltiplo de 32")
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
        if self.use_tiled_vae and self.vae_tile_overlap > self.vae_tile_size // 4:
            raise ValueError("En Qwen tiled, overlap no puede superar un cuarto del tile")
        if len(self.input_images) * self.reference_resolution**2 > 6_291_456:
            raise ValueError("Máximo 6 MP nominales de referencias en L4; reducir reference_resolution al usar muchas imágenes")
        return self


def normalize(payload: dict, mode: str) -> dict:
    if mode not in {"generate", "edit"}:
        raise ValueError("Modo no válido")
    # Validate user types before applying fixed Turbo overrides (e.g. "false"
    # must not be interpreted as a truthy string by turbo_settings).
    initial = GenerateRequest.model_validate(payload).model_dump()
    data = GenerateRequest.model_validate(turbo_settings(initial)).model_dump()
    if data["scheduler"] == "viggle_turbo" and not data["turbo"]:
        raise ValueError("Usar turbo=true para el muestreo Viggle")
    preset = effective_profile(data)
    if mode == "edit" and not data["input_images"]:
        raise ValueError("La edición requiere al menos una imagen subida a /inputs")
    if mode == "generate" and data["input_images"]:
        raise ValueError("Usar operation=edit para imágenes de referencia")
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
    if data.get("weight_dtype"):
        preset["weight_dtype"] = data["weight_dtype"]
    elif data.get("unet_name"):
        # A selected quantized checkpoint carries its own layout/scales.
        name = data["unet_name"].lower()
        if any(marker in name for marker in ("fp8", "int8", "convrot", "nvfp4")):
            preset["weight_dtype"] = "default"
    return preset
