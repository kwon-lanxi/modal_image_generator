"""MOSS Image native Comfy engine; imported only on the GPU."""
from __future__ import annotations
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from . import VERSION, config
from .files import describe, resolve, safe_relative
from .logging_utils import JobLog


def batch_noise(latent,seeds,prepare_noise):
    import torch
    batch=latent.repeat(len(seeds),*([1]*(latent.ndim-1)))
    noise=torch.cat([prepare_noise(batch[i:i+1],seed) for i,seed in enumerate(seeds)],dim=0)
    return batch,noise


class Engine:
    def __init__(self, configuration=""):
        self.preset = json.loads(configuration) if configuration else config.profile("base")
        import torch
        with torch.inference_mode():
            self.initialize()

    def initialize(self):
        import torch
        torch.cuda.init()
        sys.path.insert(0, str(config.COMFY))
        saved = sys.argv
        try:
            sys.argv = ["moss-engine"]
            from comfy.cli_args import args
        finally:
            sys.argv = saved
        args.use_pytorch_cross_attention = True
        args.disable_dynamic_vram = True
        args.disable_mmap = True
        args.disable_comfy_compiler = True
        args.disable_cuda_graphs = True
        import torch
        import comfy.sd
        import comfy.utils
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        self.paths = {k: resolve(self.preset[k], kind) for k, kind in
                      (("model", "unet"), ("clip", "clip"), ("vae", "vae"))}
        self.versions = {k: describe(p) for k, p in self.paths.items()}
        for kind, version in self.preset.get("asset_versions", {}).items():
            if self.versions[kind]["version"] != version:
                raise RuntimeError("Los pesos cambiaron antes de crear el snapshot")
        options = {}
        dtype = self.preset["weight_dtype"]
        if dtype != "default":
            options["dtype"] = torch.float8_e5m2 if dtype == "fp8_e5m2" else torch.float8_e4m3fn
            options["fp8_optimizations"] = dtype == "fp8_e4m3fn_fast"
        self.base = comfy.sd.load_diffusion_model(str(self.paths["model"]), model_options=options)
        # Quantized encoder files carry mixed-precision layer metadata. A dtype cast
        # alone does not override Qwen3.5's detected per-component precision.
        # This service accepts text only. Native Qwen3.5 otherwise constructs a
        # vision tower whose weights are absent from our TE-only checkpoint.
        # Exclude it before allocation, not after snapshotting unused tensors.
        from unittest.mock import patch
        import comfy.text_encoders.qwen35 as qwen35
        class TextOnlyVision(torch.nn.Module):
            def __init__(self, *args, **kwargs):super().__init__()
            def forward(self, *args, **kwargs):
                raise ValueError("Este encoder MOSS solo admite texto")
        with patch.object(qwen35, "Qwen35VisionModel", TextOnlyVision):
            self.clip = comfy.sd.load_clip([str(self.paths["clip"])], clip_type=comfy.sd.CLIPType.MOSS_IMAGE)
        self.vae = comfy.sd.VAE(sd=comfy.utils.load_torch_file(str(self.paths["vae"]), safe_load=True),dtype=torch.float32)

    def run(self, request, job_id, assets, loras, progress_sink=None):
        import io
        import numpy as np
        import torch
        import comfy.sample
        import comfy.samplers
        import comfy.sd
        import comfy.utils
        from PIL import Image
        for kind, expected in assets.items():
            if self.versions[kind]["version"] != expected["version"]:
                raise RuntimeError("El snapshot no corresponde a la versión solicitada")
        log = JobLog(job_id, component="gpu", sink=progress_sink)
        log.event("gpu.ready", gpu=torch.cuda.get_device_name())
        timings = {}
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        torch.cuda.reset_peak_memory_stats()
        model, clip = self.base, self.clip
        with torch.inference_mode():
            if request.get("mu") is not None:
                from comfy_extras.nodes_model_advanced import ModelSamplingAuraFlow
                model, = ModelSamplingAuraFlow().patch_aura(model, shift=request["mu"])
            with log.stage("lora", timings):
                for item in loras:
                    path = resolve(item["id"], "lora")
                    if describe(path)["version"] != item["version"]:
                        raise RuntimeError("Una LoRA cambió mientras estaba en cola")
                    data = comfy.utils.load_torch_file(str(path), safe_load=True)
                    before = sum(len(v) for v in model.patches.values()) + sum(len(v) for v in clip.patcher.patches.values())
                    model, clip = comfy.sd.load_lora_for_models(model, clip, data, item["strength"], item["strength"])
                    count = sum(len(v) for v in model.patches.values()) + sum(len(v) for v in clip.patcher.patches.values()) - before
                    if count <= 0:
                        raise ValueError("LoRA incompatible con MOSS Image: " + item["id"])
                    log.event("lora.applied", file=item["id"], patched_keys=count, strength=item["strength"])
                    del data
                torch.cuda.synchronize()
            with log.stage("encode", timings):
                positive=clip.encode_from_tokens_scheduled(clip.tokenize(request["positive_prompt"]))
                negative=clip.encode_from_tokens_scheduled(clip.tokenize(request["negative_prompt"]))
                latent=torch.zeros([1,128,request["height"]//16,request["width"]//16])
                torch.cuda.synchronize()
            latent,noise=batch_noise(latent,request.get("seeds",[request["seed"]]),comfy.sample.prepare_noise)
            def progress(step, x0, x, total):
                log.event("sampling.progress", stage="sampling", step=step + 1, total=total,
                          percent=round(100 * (step + 1) / total, 1))
            with log.stage("sampling", timings):
                samples = comfy.sample.sample(model, noise, request["steps"], request["cfg"],
                          request["sampler_name"], request["scheduler"], positive, negative, latent,
                          denoise=1.0, callback=progress, disable_pbar=True, seed=request["seed"])
                torch.cuda.synchronize()
            with log.stage("decode", timings):
                if request["use_tiled_vae"]:
                    # MOSS latent downscale=16; native VAE converts tile pixels to latent tiles.
                    image = self.vae.decode_tiled(samples, tile_x=request["vae_tile_size"] // 16,
                            tile_y=request["vae_tile_size"] // 16, overlap=request["vae_tile_overlap"] // 16)
                else:
                    image = self.vae.decode(samples)
                torch.cuda.synchronize()
            if image.ndim != 4 or image.shape[0] != request.get("n",1) or image.shape[-1] not in (3, 4):
                raise RuntimeError("Forma de imagen MOSS inesperada: " + str(tuple(image.shape)))
            if not torch.isfinite(image).all():
                raise RuntimeError("La imagen contiene NaN/Inf; revisar precisión/LoRAs")
            with log.stage("transfer_cpu", timings):
                pixels = (image.clamp(0, 1) * 255).round().to(torch.uint8).cpu().contiguous()
                raw = pixels[0].numpy().tobytes()
            timings["gpu_method_total"] = round(time.perf_counter() - started, 4)
        return {"batch_pixels":[p.numpy().tobytes() for p in pixels] if request.get("n",1)>1 else [], "pixels": raw, "width": int(pixels.shape[2]), "height": int(pixels.shape[1]),
                "channels": int(pixels.shape[-1]), "timing": timings, "events": log.records,
                "metadata": {"generation_started_at_utc": started_at,
                    "generation_finished_at_utc": datetime.now(timezone.utc).isoformat(),
                    "software": f"MOSS Image personal {VERSION}", "profile": self.preset,
                    "assets": self.versions, "loras": loras, "inputs": request.get("input_assets", []),
                    "comfy_commit": config.COMFY_COMMIT, "attention_backend": "pytorch_sdpa", "text_encoder_vision_tower":False,
                    "weight_dtype": self.preset["weight_dtype"], "compute_dtype": str(self.base.model.get_dtype()),
                    "torch": str(torch.__version__), "gpu": torch.cuda.get_device_name(),
                    
                    "turbo":request.get("turbo",False), "turbo12":request.get("turbo12",False),
                    "superturbo":request.get("superturbo",False),
                    "turbo_implementation":"data-archetype/moss_image_base_dmd2@8step" if request.get("turbo") else "anyMODE/moss-image-12step-turbo-lora@12step" if request.get("turbo12") else "akhaliq-2ded95c37a029360245926fe72e475e30039bbfb" if request.get("superturbo") else None,
                    "peak_vram_bytes": torch.cuda.max_memory_allocated(),
                    "rss_bytes": __import__("psutil").Process().memory_info().rss}}

    def warmup(self):
        from .contracts import normalize
        request=normalize({"positive_prompt":"warmup","profile":self.preset["family"],"width":256,"height":256,"steps":1,"cfg":1,"seed":42},"generate")
        return self.run(request,"0"*32,self.versions,[])
