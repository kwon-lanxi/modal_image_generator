"""GPU-only module. Imported inside Modal lifecycle, never by the CPU API."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from collections import OrderedDict

from . import VERSION, config
from .files import describe, resolve
from .logging_utils import JobLog


def batch_noise(latent,seeds,prepare_noise):
    import torch
    batch=latent.repeat(len(seeds),*([1]*(latent.ndim-1)))
    noise=torch.cat([prepare_noise(batch[i:i+1],seed) for i,seed in enumerate(seeds)],dim=0)
    return batch,noise


class Engine:
    def __init__(self, configuration: str = ""):
        self.preset = json.loads(configuration) if configuration else config.profile("turbo")
        self.paths = {}
        self.conditioning = OrderedDict()
        self.tae = None
        self.initialize()

    def initialize(self):
        sys.path.insert(0, str(config.COMFY))
        # Configure supported Comfy args BEFORE importing nodes/model_management.
        saved_args = sys.argv
        try:
            sys.argv = ["krea2-engine"]
            from comfy.cli_args import args
        finally:
            sys.argv = saved_args
        args.use_pytorch_cross_attention = True
        args.disable_dynamic_vram = True
        args.disable_mmap = True
        args.disable_comfy_compiler = True
        args.disable_cuda_graphs = True

        import folder_paths
        import torch
        import comfy.sd
        from nodes import CLIPLoader

        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
        folder_paths.add_model_folder_path("text_encoders", str(config.MAIN / "text_encoders"))
        folder_paths.add_model_folder_path("vae", str(config.MAIN / "vae"))
        self.paths = {
            "model": resolve(self.preset["model"], "unet"),
            "clip": resolve(self.preset["clip"], "clip"),
            "vae": resolve(self.preset["vae"], "vae"),
        }
        self.versions = {kind: describe(path) for kind, path in self.paths.items()}
        dtype = self.preset["weight_dtype"]
        if dtype not in {"default", "fp8_e4m3fn_fast", "fp8_e4m3fn", "fp8_e5m2"}:
            raise ValueError("Precisión no soportada en profiles.json")
        options = {}
        if dtype != "default":
            options["dtype"] = torch.float8_e5m2 if dtype == "fp8_e5m2" else torch.float8_e4m3fn
            if dtype == "fp8_e4m3fn_fast":
                options["fp8_optimizations"] = True
        self.base = comfy.sd.load_diffusion_model(str(self.paths["model"]), model_options=options)
        clip_name = self.paths["clip"].relative_to(config.MAIN / "text_encoders").as_posix()
        self.clip = CLIPLoader().load_clip(clip_name=clip_name, type="krea2")[0]

        # Load only this necessary custom package. No init_extra_nodes() scan.
        directory = config.COMFY / "custom_nodes" / "ComfyUI-VAE-Utils"
        spec = importlib.util.spec_from_file_location(
            "krea_vae_utils", directory / "__init__.py", submodule_search_locations=[str(directory)])
        if spec is None or spec.loader is None:
            raise RuntimeError("No se pudo cargar VAE-Utils")
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        self.vae_nodes = package.NODE_CLASS_MAPPINGS
        vae_name = self.paths["vae"].relative_to(config.MAIN / "vae").as_posix()
        self.vae = self.vae_nodes["VAEUtils_CustomVAELoader"]().load_vae(
            vae_name, disable_offload=False,
            precision="fp32" if self.paths["vae"].stem == "Wan2_1_VAE_fp32" else "auto")[0]
        self.vae_decoder = self.vae_nodes["VAEUtils_VAEDecodeTiled"]()

        tae_name = self.preset.get("tae")
        if tae_name and (config.MAIN / tae_name).is_file():
            self.load_tae()
        for kind, version in self.preset.get("asset_versions", {}).items():
            if self.versions[kind]["version"] != version:
                raise RuntimeError("Un archivo cambió antes de crear el snapshot; volver a enviar la solicitud")

    def load_tae(self):
        import comfy.utils
        import torch
        from comfy.taesd.taesd import Decoder

        path = config.MAIN / self.preset["tae"]
        state = comfy.utils.load_torch_file(str(path), safe_load=True)
        decoder = Decoder(latent_channels=16)
        decoder.load_state_dict(state, strict=True)
        self.tae = decoder.to(device="cuda", dtype=torch.float16).eval()
        self.tae_version = describe(path)
        self.versions["tae"] = self.tae_version

    def verify_versions(self, assets: dict):
        # Immutable files + size/mtime fingerprint detect ordinary Volume changes.
        # Never overwrite a model while preserving its timestamp; version its path.
        for kind, expected in assets.items():
            if self.versions[kind]["version"] != expected["version"] or self.versions[kind]["id"] != expected["id"]:
                raise RuntimeError(f"Snapshot de {kind} desactualizado: versionar el perfil y redesplegar")

    def prompt(self, clip, request: dict, lora_versions: tuple):
        import torch
        from nodes import CLIPTextEncode, ConditioningZeroOut

        key = (lora_versions, request["positive_prompt"],
               request["negative_prompt"] if request["cfg"] != 1.0 else None)
        if key in self.conditioning:
            self.conditioning.move_to_end(key)
            return self.conditioning[key], True
        with torch.inference_mode():
            positive = CLIPTextEncode().encode(clip, request["positive_prompt"])[0]
            if request["cfg"] == 1.0:
                negative = ConditioningZeroOut().zero_out(positive)[0]
            else:
                negative = CLIPTextEncode().encode(clip, request["negative_prompt"])[0]
        self.conditioning[key] = (positive, negative)
        if len(self.conditioning) > 4:
            self.conditioning.popitem(last=False)
        return (positive, negative), False

    def sampling_model(self, model, width: int, height: int):
        from comfy_extras.nodes_model_advanced import ModelSamplingFlux

        if self.preset["family"] == "turbo":
            shift = self.preset["shift"]
        else:
            # Krea RAW: dynamic shift from 256 to 6400 image tokens, not pixels.
            tokens = (width // 16) * (height // 16)
            shift = 0.5 + (tokens - 256) * (1.15 - 0.5) / (6400 - 256)
        return ModelSamplingFlux().patch(model, max_shift=shift, base_shift=shift,
                                         width=width, height=height)[0]

    def run(self, request: dict, job_id: str, assets: dict, loras: list[dict], progress_sink=None) -> dict:
        import torch
        import comfy.sample
        import comfy.sd
        import comfy.utils
        from nodes import EmptyLatentImage

        self.verify_versions(assets)
        log, timing = JobLog(job_id, component="gpu", sink=progress_sink), {}
        log.event("gpu.ready", gpu=torch.cuda.get_device_name())
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        torch.cuda.reset_peak_memory_stats()
        model, clip = self.base, self.clip
        with torch.inference_mode():
            with log.stage("lora", timing):
                for item in loras:
                    path = resolve(item["id"], "lora")
                    if describe(path)["version"] != item["version"]:
                        raise RuntimeError("Una LoRA cambió mientras el trabajo estaba en cola")
                    data = comfy.utils.load_torch_file(str(path), safe_load=True)
                    def patch_count(m, c):
                        return sum(len(v) for v in m.patches.values()) + sum(
                            len(v) for v in c.patcher.patches.values())
                    before = patch_count(model, clip)
                    model, clip = comfy.sd.load_lora_for_models(model, clip, data, item["strength"], item["strength"])
                    applied = patch_count(model, clip) - before
                    if applied <= 0:
                        raise ValueError(f"La LoRA no aplica ningún peso a Krea2: {item['id']}")
                    log.event("lora.applied", file=item["id"], patched_keys=applied, strength=item["strength"])
                torch.cuda.synchronize()
            model = self.sampling_model(model, request["width"], request["height"])
            combo = tuple((item["id"], item["version"], item["strength"]) for item in loras)
            with log.stage("encode", timing):
                (positive, negative), cache_hit = self.prompt(clip, request, combo)
                torch.cuda.synchronize()
            from .variance import apply_batch
            variance_metadata = None
            if request.get("variance", "off") != "off":
                with log.stage("smart_seed_variance", timing):
                    target = None
                    if request.get("variance_vibe"):
                        (target, _), _ = self.prompt(clip, {**request,"positive_prompt":request["variance_vibe"],"negative_prompt":"","cfg":1.0}, combo)
                    positive, variance_metadata = apply_batch(positive, request, target)
                    torch.cuda.synchronize()
                    log.event("variance.applied", preset=request["variance"], count=len(request["seeds"]),
                              revision=variance_metadata["revision"])
            latent = EmptyLatentImage().generate(request["width"], request["height"], 1)[0]["samples"]
            latent = comfy.sample.fix_empty_latent_channels(model, latent)
            latent,noise=batch_noise(latent,request.get("seeds",[request["seed"]]),comfy.sample.prepare_noise)

            def progress(step, x0, x, total):
                log.event("sampling.progress", stage="sampling", step=step + 1, total=total,
                          percent=round(100 * (step + 1) / total, 1))

            with log.stage("sampling", timing):
                samples = comfy.sample.sample(
                    model, noise, request["steps"], request["cfg"], request["sampler_name"],
                    request["scheduler"], positive, negative, latent, denoise=1.0,
                    callback=progress, disable_pbar=True, seed=request["seed"])
                torch.cuda.synchronize()
            with log.stage("decode", timing):
                if request["mode"] == "preview":
                    if self.tae is None:
                        self.load_tae()
                    # Final sampler output is denormalized. AcademiaSD expects normalized x0.
                    normalized = model.get_model_object("latent_format").process_in(samples)
                    if normalized.ndim == 5:
                        if normalized.shape[2] != 1:
                            raise ValueError("Este worker solo decodifica una imagen")
                        normalized = normalized[:, :, 0]
                    image = self.tae(normalized.to(device="cuda", dtype=torch.float16)).movedim(1, -1)
                else:
                    # This path performs the necessary pixel shuffle for Wan upscale2x.
                    image = self.vae_decoder.decode(
                        samples={"samples": samples}, vae=self.vae, upscale=-1,
                        tile=request["use_tiled_vae"], tile_size=request["vae_tile_size"],
                        overlap=request["vae_tile_overlap"], temporal_size=64, temporal_overlap=8)[0]
                torch.cuda.synchronize()
            if image.ndim != 4 or image.shape[0] != request.get("n",1) or image.shape[-1] != 3:
                raise ValueError(f"Forma de imagen inesperada: {tuple(image.shape)}")
            if not torch.isfinite(image).all():
                raise RuntimeError("La imagen contiene NaN/Inf; revisar precisión y LoRAs")
            with log.stage("transfer_cpu", timing):
                pixels = (image.clamp(0, 1) * 255).round().to(torch.uint8).cpu().contiguous()
                raw = pixels[0].numpy().tobytes()
            timing["gpu_method_total"] = round(time.perf_counter() - started, 4)
        return {
            "batch_pixels":[p.numpy().tobytes() for p in pixels] if request.get("n",1)>1 else [], "pixels": raw, "height": pixels.shape[1], "width": pixels.shape[2], "timing": timing,
            "events": log.records,
            "metadata": {
                "generation_started_at_utc": started_at, "smart_seed_variance": variance_metadata,
                "generation_finished_at_utc": datetime.now(timezone.utc).isoformat(),
                "software": f"Krea2 personal {VERSION}", "profile": self.preset,
                "assets": self.versions, "loras": loras,
                "comfy_commit": config.COMFY_COMMIT, "vae_utils_commit": config.VAE_UTILS_COMMIT,
                "attention_backend": "pytorch_sdpa", "weight_dtype": self.preset["weight_dtype"],
                "compute_dtype": str(self.base.model.get_dtype()),
                "conditioning_cache_hit": cache_hit, "torch": str(torch.__version__),
                "gpu": torch.cuda.get_device_name(),
                "peak_vram_bytes": torch.cuda.max_memory_allocated(),
                "rss_bytes": __import__("psutil").Process().memory_info().rss,
                "tae": getattr(self, "tae_version", None) if request["mode"] == "preview" else None,
            },
        }

    def warmup(self):
        # Real encoder -> sampler -> final VAE. Errors abort snapshot creation.
        from .contracts import normalize

        request = normalize({"profile": self.preset["id"], "unet_name": self.preset["model"],
                             "clip_name": self.preset["clip"], "vae_name": self.preset["vae"],
                             "model_type": self.preset["family"], "weight_dtype": self.preset["weight_dtype"],
                             "positive_prompt": "a red apple on a table",
                             "seed": 42, "width": 512, "height": 512, "steps": 1, "cfg": 1.0}, "generate")
        result = self.run(request, "snapshot-warmup", self.versions, [])
        if not result["pixels"]:
            raise RuntimeError("Warmup no produjo una imagen")
        self.conditioning.clear()
