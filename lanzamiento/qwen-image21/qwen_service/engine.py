"""Qwen Image 2.1 native Comfy engine; imported only on the GPU."""
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
        self.initialize()

    def initialize(self):
        from .cuda_init import ensure_cuda
        ensure_cuda()
        sys.path.insert(0, str(config.COMFY))
        saved = sys.argv
        try:
            sys.argv = ["qwen21-engine"]
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
        # alone does not override Qwen3-VL's detected per-component precision.
        self.clip = comfy.sd.load_clip([str(self.paths["clip"])], clip_type=comfy.sd.CLIPType.QWEN_IMAGE)
        self.vae = comfy.sd.VAE(sd=comfy.utils.load_torch_file(str(self.paths["vae"]), safe_load=True))

    def run(self, request, job_id, assets, loras, progress_sink=None):
        import io
        import numpy as np
        import torch
        import comfy.sample
        import comfy.samplers
        import comfy.sd
        import comfy.utils
        from PIL import Image
        from comfy_extras.nodes_qwen import TextEncodeQwenImage21, QwenImage21Cache
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
            with log.stage("lora", timings):
                for item in loras:
                    path = resolve(item["id"], "lora")
                    if describe(path)["version"] != item["version"]:
                        raise RuntimeError("Una LoRA cambió mientras estaba en cola")
                    from .contracts import TURBO_LORA
                    if request.get("turbo") and item["id"] == TURBO_LORA:
                        import folder_paths
                        from .vendor.viggle_turbo import ViggleTurboLora
                        folder_paths.add_model_folder_path("loras", str(config.LORAS))
                        model = ViggleTurboLora().load(model, path.relative_to(config.LORAS).as_posix(), 1.0)[0]
                        log.event("lora.applied", file=item["id"], strength=1.0, application="viggle_runtime_unmerged")
                        continue
                    data = comfy.utils.load_torch_file(str(path), safe_load=True)
                    before = sum(len(v) for v in model.patches.values()) + sum(len(v) for v in clip.patcher.patches.values())
                    model, clip = comfy.sd.load_lora_for_models(model, clip, data, item["strength"], item["strength"])
                    count = sum(len(v) for v in model.patches.values()) + sum(len(v) for v in clip.patcher.patches.values()) - before
                    if count <= 0:
                        raise ValueError("LoRA incompatible con Qwen Image 2.1: " + item["id"])
                    log.event("lora.applied", file=item["id"], patched_keys=count, strength=item["strength"])
                    del data
                model = QwenImage21Cache.execute(model, request["kv_cache_device"], request["kv_cache_dtype"])[0]
                torch.cuda.synchronize()
            with log.stage("encode", timings):
                images = {}
                for index, asset in enumerate(request.get("input_assets", []), 1):
                    path = safe_relative(config.MAIN / "inputs", asset["id"])
                    raw = path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != asset["sha256"]:
                        raise RuntimeError("Una imagen de entrada cambió")
                    with Image.open(io.BytesIO(raw)) as im:
                        array = np.array(im.convert("RGBA" if im.mode == "RGBA" else "RGB"), dtype=np.float32) / 255.0
                    images["image_" + str(index)] = torch.from_numpy(array).unsqueeze(0)
                encoded = TextEncodeQwenImage21.execute(clip, request["positive_prompt"], request["negative_prompt"],
                           vae=self.vae if images else None, resolution=request["reference_resolution"], images=images)
                positive, negative, edit_latent = encoded[0], encoded[1], encoded[2]
                if images:
                    latent = edit_latent["samples"]
                else:
                    latent = torch.zeros([1, 64, request["height"] // 16, request["width"] // 16])
                del images, encoded
                torch.cuda.synchronize()
            latent,noise=batch_noise(latent,request.get("seeds",[request["seed"]]),comfy.sample.prepare_noise)
            def progress(step, x0, x, total):
                log.event("sampling.progress", stage="sampling", step=step + 1, total=total,
                          percent=round(100 * (step + 1) / total, 1))
            with log.stage("sampling", timings):
                if request.get("turbo"):
                    from .vendor.viggle_turbo import ViggleTurboSigmas
                    sigmas = ViggleTurboSigmas().get_sigmas(
                        {"samples":latent}, "1.0, 0.9375, 0.875, 0.75, 0.5, 0.25")[0]
                    log.event("sampling.schedule", scheduler="viggle_turbo", steps=6,
                              sigmas=sigmas.tolist(), cfg=1.0, sampler="euler")
                    samples = comfy.sample.sample_custom(model, noise, 1.0,
                              comfy.samplers.sampler_object("euler"), sigmas, positive, negative, latent,
                              callback=progress, disable_pbar=True, seed=request["seed"])
                else:
                    samples = comfy.sample.sample(model, noise, request["steps"], request["cfg"],
                              request["sampler_name"], request["scheduler"], positive, negative, latent,
                              denoise=1.0, callback=progress, disable_pbar=True, seed=request["seed"])
                torch.cuda.synchronize()
            with log.stage("decode", timings):
                if request["use_tiled_vae"]:
                    # Qwen latent downscale=16; native VAE converts tile pixels to latent tiles.
                    image = self.vae.decode_tiled(samples, tile_x=request["vae_tile_size"] // 16,
                            tile_y=request["vae_tile_size"] // 16, overlap=request["vae_tile_overlap"] // 16)
                else:
                    image = self.vae.decode(samples)
                torch.cuda.synchronize()
            if image.ndim != 4 or image.shape[0] != request.get("n",1) or image.shape[-1] not in (3, 4):
                raise RuntimeError("Forma de imagen Qwen inesperada: " + str(tuple(image.shape)))
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
                    "software": f"Qwen Image 2.1 personal {VERSION}", "profile": self.preset,
                    "assets": self.versions, "loras": loras, "inputs": request.get("input_assets", []),
                    "comfy_commit": config.COMFY_COMMIT, "attention_backend": "pytorch_sdpa",
                    "weight_dtype": self.preset["weight_dtype"], "compute_dtype": str(self.base.model.get_dtype()),
                    "torch": str(torch.__version__), "gpu": torch.cuda.get_device_name(),
                    "kv_cache_device": request["kv_cache_device"], "kv_cache_dtype": request["kv_cache_dtype"],
                    "turbo":request.get("turbo",False),
                    "turbo_implementation":"viggle-official-009a44a895ef85f7e643c80fdca9543795248867" if request.get("turbo") else None,
                    "peak_vram_bytes": torch.cuda.max_memory_allocated(),
                    "rss_bytes": __import__("psutil").Process().memory_info().rss}}

    def warmup(self):
        from .contracts import normalize
        request = normalize({"profile": self.preset["id"], "unet_name": self.preset["model"],
                            "clip_name": self.preset["clip"], "vae_name": self.preset["vae"],
                            "weight_dtype": self.preset["weight_dtype"],
                            "positive_prompt": "a red apple on a table", "seed": 42,
                            "width": 256, "height": 256, "steps": 1}, "generate")
        result = self.run(request, "snapshot-warmup", self.versions, [])
        if not result["pixels"]:
            raise RuntimeError("Warmup sin imagen")
