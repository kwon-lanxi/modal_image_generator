"""
Krea2 Programmatic API on Modal — v1.1.0
==========================================
★ v1.1.0:
  - LoraRegistry.resolve() acepta el formato `{character}-{N}` (sin extensión),
    que es el que emite el manager v1.1.0 al listar LoRAs colapsados por personaje.
  - También acepta `{character}` (variante final / step más alto por defecto)
    y los formatos heredados `{character}-step0000N` / `{character}.safetensors`.
  - Cache en memoria: <1 ms por resolución.
  - Ignora _backups y save_lora; prioriza steps altos.
  - L40S + GPU snapshots + warmup en @modal.enter(snap=True).
  - Modelo por defecto: HomoFidelis_Krea2_Turbo_BF16_v1.
"""
from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
import io
import json
import datetime
import os
import queue as _queue
import random
import re
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════════════════
# ENV VARS CRÍTICAS
# ════════════════════════════════════════════════════════════════════════
os.environ.setdefault("COMFYUI_ATTENTION_BACKEND", "sage")
os.environ.setdefault("COMFYUI_HEADLESS", "1")
os.environ.setdefault("COMFY_DISABLE_SERVER", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault(
    "PYTORCH_CUDA_ALLOC_CONF",
    "max_split_size_mb:512,expandable_segments:True,garbage_collection_threshold:0.6",
)
os.environ.setdefault("SAFETENSORS_FAST_OPEN", "1")
os.environ.setdefault("TORCH_CUDNN_V8_API_ENABLED", "1")
os.environ.setdefault("TORCH_ALLOW_TF32_CUBLAS_OVERRIDE", "1")
os.environ.setdefault("TORCHINDUCTOR_CACHE_DIR", "/tmp/inductor_cache")
os.environ.setdefault("TORCHDYNAMO_VERBOSE", "0")

import modal

# ════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE MODAL
# ════════════════════════════════════════════════════════════════════════
APP_NAME = "krea2-api-v1-1-0"
SERVICE_VERSION = "1.1.0"

app = modal.App(APP_NAME)


def _build_image() -> modal.Image:
    return (
        modal.Image.from_registry(
            "nvidia/cuda:12.8.0-cudnn-devel-ubuntu22.04",
            add_python="3.12",
        )
        .apt_install(
            "libgl1", "libglib2.0-0", "git", "wget", "curl", "build-essential",
        )
        .pip_install(
            "torch==2.7.0", "torchvision==0.22.0", "torchaudio==2.7.0",
            extra_index_url="https://download.pytorch.org/whl/cu128",
        )
        .pip_install(
            "safetensors", "Pillow", "numpy", "omegaconf", "einops",
            "transformers", "tokenizers", "accelerate", "scipy",
            "tqdm", "psutil", "huggingface_hub",
            "fastapi", "uvicorn[standard]", "python-multipart",
            "sageattention", "triton", "requests", "timm",
            "boto3", "opencv-python-headless", "cycler", "matplotlib",
        )
        .run_commands(
            "git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git /root/ComfyUI",
            "cd /root/ComfyUI && pip install --no-deps -r requirements.txt",
            "cd /root/ComfyUI && pip install -r requirements.txt",
            "cd /root/ComfyUI/custom_nodes && "
            "git clone --depth 1 https://github.com/spacepxl/ComfyUI-VAE-Utils.git",
            "cd /root/ComfyUI/custom_nodes && "
            "git clone --depth 1 https://github.com/lbouaraba/comfyui-krea2edit.git",
            "cd /root/ComfyUI/custom_nodes && "
            "git clone --depth 1 https://github.com/facok/comfyui-krea2-controlnet.git",
            "cd /root/ComfyUI/custom_nodes && "
            "git clone --depth 1 https://github.com/Fannovel16/comfyui_controlnet_aux.git",
            "cd /root/ComfyUI/custom_nodes/comfyui_controlnet_aux && "
            "pip install --no-deps -r requirements.txt || true",
            "pip install --no-cache-dir triton comfy-kitchen || true",
            "pip install --no-cache-dir sageattention || true",
            "python3.12 -m compileall -q /root/ComfyUI /root/ComfyUI/custom_nodes "
            "2>&1 | tail -5 || true",
        )
        .env({
            "PYTHONUNBUFFERED": "1",
            "COMFYUI_HEADLESS": "1",
            "COMFY_DISABLE_SERVER": "1",
            "COMFYUI_ATTENTION_BACKEND": "sage",
            "PYTORCH_CUDA_ALLOC_CONF": (
                "max_split_size_mb:512,expandable_segments:True,"
                "garbage_collection_threshold:0.6"
            ),
            "TORCH_CUDNN_V8_API_ENABLED": "1",
            "TORCH_ALLOW_TF32_CUBLAS_OVERRIDE": "1",
            "TORCHINDUCTOR_CACHE_DIR": "/tmp/inductor_cache",
        })
    )


IMAGE = _build_image()

# ── Volúmenes ───────────────────────────────────────────────────────────
VOL_MAIN = modal.Volume.from_name("krea2-volume")
VOL_LORAS = modal.Volume.from_name("krea2-lora")

VOLUMES = {
    "/krea2-beam-volume": VOL_MAIN,
    "/krea2-loras-renew": VOL_LORAS,
}

# ── Secrets existentes en el workspace ─────────────────────────────────
R2_SECRET = modal.Secret.from_name("r2-secrets")
CIVITAI_SECRET = modal.Secret.from_name("civitai-apikey")
HF_SECRET = modal.Secret.from_name("huggingface-secret")

# ════════════════════════════════════════════════════════════════════════
# PRICING CONFIG
# ════════════════════════════════════════════════════════════════════════
GPU_PRICE_PER_SEC = float(os.environ.get("BEAM_GPU_PRICE_PER_SEC", "0.000191667"))
CPU_PRICE_PER_SEC_PER_CORE = float(os.environ.get("BEAM_CPU_PRICE_PER_SEC", "0.0000125"))
RAM_PRICE_PER_SEC_PER_GIB = float(os.environ.get("BEAM_RAM_PRICE_PER_SEC", "0.0000021"))
CPU_CORES = 4
RAM_GIB = 32

CPU_TOTAL_PRICE_PER_SEC = CPU_PRICE_PER_SEC_PER_CORE * CPU_CORES
RAM_TOTAL_PRICE_PER_SEC = RAM_PRICE_PER_SEC_PER_GIB * RAM_GIB
TOTAL_PRICE_PER_SEC = GPU_PRICE_PER_SEC + CPU_TOTAL_PRICE_PER_SEC + RAM_TOTAL_PRICE_PER_SEC

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

# ════════════════════════════════════════════════════════════════════════
# RUTAS Y VOLÚMENES
# ════════════════════════════════════════════════════════════════════════
VOL_MAIN_PATH = Path("/krea2-beam-volume")
VOL_LORAS_PATH = Path("/krea2-loras-renew")

DIR_DIFFUSION = VOL_MAIN_PATH / "diffusion_models"
DIR_LORAS_MAIN = VOL_MAIN_PATH / "loras"
DIR_TEXT_ENCODERS = VOL_MAIN_PATH / "text_encoders"
DIR_UPSCALE = VOL_MAIN_PATH / "upscale"
DIR_VAE = VOL_MAIN_PATH / "vae"
DIR_UTILS = VOL_MAIN_PATH / "utils"
DIR_OUTPUTS = VOL_MAIN_PATH / "outputs"

COMFY_ROOT = Path("/root/ComfyUI")

# Optimizado para 48 GB VRAM (L40S) y 32 GB RAM
LRU_UNET = 2
LRU_CLIP = 1
LRU_VAE = 2
LRU_UPSCALE = 2
LRU_LORA = 5
LRU_CONDITIONING = 32
LRU_LATENT = 16

DEFAULT_UNET = "HomoFidelis_Krea2_Turbo_BF16_v1"
DEFAULT_CLIP = "qwen_3vl_4b"
DEFAULT_VAE = "wan_2.1_vae"
DEFAULT_LORAS: Dict[str, float] = {}
DEFAULT_KREA_MU = 1.15

DEFAULT_VAE_TILE_SIZE = 512
DEFAULT_VAE_TILE_OVERLAP = 64

# ════════════════════════════════════════════════════════════════════════
# CLOUDFLARE R2 UPLOAD
# ════════════════════════════════════════════════════════════════════════
_R2_BUCKET = os.environ.get("R2_BUCKET", "krea2-bucket-img-modal").strip()
_R2_CLIENT = None
_R2_LOCK = threading.Lock()


def _get_r2_client():
    global _R2_CLIENT
    if _R2_CLIENT is not None:
        return _R2_CLIENT
    with _R2_LOCK:
        if _R2_CLIENT is not None:
            return _R2_CLIENT
        account = os.environ.get("R2_ACCOUNT_ID", "").strip()
        key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
        secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
        if not (account and key and secret):
            raise RuntimeError("Secrets R2 no configurados")
        import boto3
        from botocore.config import Config as BotoConfig
        _R2_CLIENT = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name="auto",
            config=BotoConfig(
                connect_timeout=60,
                read_timeout=60,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
        print(f"[R2] ✓ cliente inicializado (bucket={_R2_BUCKET})", flush=True)
        return _R2_CLIENT


def _upload_to_r2(raw_bytes: bytes, fname: str) -> Optional[str]:
    public_base = os.environ.get("R2_PUBLIC_BASE", "").strip().rstrip("/")
    if not public_base:
        return None
    try:
        client = _get_r2_client()
    except Exception as e:
        print(f"[R2] ✗ no se pudo inicializar cliente: {e}", flush=True)
        return None
    now = datetime.datetime.now()
    key = f"krea2/{now.year:04d}/{now.month:02d}/{now.day:02d}/{fname}"
    t0 = time.time()
    try:
        client.put_object(
            Bucket=_R2_BUCKET, Key=key, Body=raw_bytes, ContentType="image/png",
        )
    except Exception as e:
        print(f"[R2] ✗ ERROR {type(e).__name__}: {e}", flush=True)
        return None
    url = f"{public_base}/{key}"
    print(f"[R2] ✓ subido en {time.time() - t0:.2f}s → {url}", flush=True)
    return url


# ════════════════════════════════════════════════════════════════════════
# STATE MACHINE + EVENT BUS
# ════════════════════════════════════════════════════════════════════════
class EngineState(str, Enum):
    BOOTING = "booting"
    IDLE = "idle"
    GENERATING = "generating"


class _EngineStateMachine:
    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._state = EngineState.BOOTING
        self._current_task_id: Optional[str] = None
        self._entered_at: Optional[float] = None

    def get_state(self) -> EngineState:
        with self._lock:
            return self._state

    def is_snapshot_safe(self) -> bool:
        with self._lock:
            return self._state == EngineState.IDLE

    def try_enter(self, task_id: str, timeout: float = 0.0) -> bool:
        self.check_zombie()
        deadline = time.time() + timeout if timeout > 0 else None
        with self._cond:
            while self._state != EngineState.IDLE:
                if deadline is None:
                    return False
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=remaining)
            self._state = EngineState.GENERATING
            self._current_task_id = task_id
            self._entered_at = time.time()
            return True

    def exit(self, task_id: str):
        with self._cond:
            if self._current_task_id == task_id:
                self._state = EngineState.IDLE
                self._current_task_id = None
                self._entered_at = None
                self._cond.notify_all()

    def force_idle(self, reason: str = "manual"):
        with self._cond:
            self._state = EngineState.IDLE
            self._current_task_id = None
            self._entered_at = None
            self._cond.notify_all()

    def get_info(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.value,
                "task_id": self._current_task_id,
                "entered_at": self._entered_at,
                "elapsed_in_state": (time.time() - self._entered_at) if self._entered_at else 0,
                "snapshot_safe": self._state == EngineState.IDLE,
            }

    ZOMBIE_TIMEOUT_S = 300.0

    def check_zombie(self) -> bool:
        with self._cond:
            if self._state != EngineState.GENERATING or self._entered_at is None:
                return False
            elapsed = time.time() - self._entered_at
            if elapsed < self.ZOMBIE_TIMEOUT_S:
                return False
            print(f"[state] ☠ ZOMBIE: task={self._current_task_id} ({elapsed:.0f}s) → force_idle", flush=True)
            self._state = EngineState.IDLE
            self._current_task_id = None
            self._entered_at = None
            self._cond.notify_all()
            return True


class _EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: Dict[str, List] = {}

    def subscribe(self, task_id: str, loop: asyncio.AbstractEventLoop) -> _queue.Queue:
        q = _queue.Queue(maxsize=200)
        with self._lock:
            self._subs.setdefault(task_id, []).append((q, loop))
        return q

    def unsubscribe(self, task_id: str, q: _queue.Queue):
        with self._lock:
            if task_id in self._subs:
                self._subs[task_id] = [(qq, lp) for (qq, lp) in self._subs[task_id] if qq is not q]
                if not self._subs[task_id]:
                    del self._subs[task_id]

    def emit(self, task_id: str, event: Dict[str, Any]):
        with self._lock:
            subs = list(self._subs.get(task_id, []))
        for q, loop in subs:
            try:
                loop.call_soon_threadsafe(self._safe_put, q, event)
            except Exception:
                pass

    @staticmethod
    def _safe_put(q: _queue.Queue, event: Dict):
        try:
            q.put_nowait(event)
        except _queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass

    def close_task(self, task_id: str, final_event: Optional[Dict] = None):
        with self._lock:
            subs = list(self._subs.get(task_id, []))
            self._subs.pop(task_id, None)
        for q, loop in subs:
            try:
                if final_event:
                    loop.call_soon_threadsafe(self._safe_put, q, final_event)
                loop.call_soon_threadsafe(q.put, None)
            except Exception:
                pass


def make_emit(bus: _EventBus, task_id: Optional[str]):
    if not task_id:
        return lambda event_type, **kwargs: None
    def emit(event_type: str, **kwargs):
        bus.emit(task_id, {"type": event_type, "ts": time.time(), **kwargs})
    return emit


async def _parse_payload(request: Request) -> Dict[str, Any]:
    content_type = request.headers.get("Content-Type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        payload_str = form.get("payload")
        if not payload_str:
            raise ValueError("Multipart request must include a 'payload' JSON string field")
        payload = json.loads(payload_str)
        for key in ["image", "image_b", "depth_image"]:
            if key in form:
                file_obj = form[key]
                payload[key] = await file_obj.read()
        return payload
    else:
        return await request.json()


# ════════════════════════════════════════════════════════════════════════
# LRU CACHE
# ════════════════════════════════════════════════════════════════════════
def _empty_cuda_cache():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _destroy_value(value):
    try:
        del value
    except Exception:
        pass
    gc.collect()
    _empty_cuda_cache()


class LRUCache:
    def __init__(self, capacity: int, on_evict: Optional[callable] = None):
        self.capacity = capacity
        self._data: "OrderedDict[Any, Any]" = OrderedDict()
        self._on_evict = on_evict
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self._data: return None
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key, value):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
            else:
                self._data[key] = value
                if len(self._data) > self.capacity:
                    evicted_key, evicted_val = self._data.popitem(last=False)
                    if self._on_evict:
                        try: self._on_evict(evicted_key, evicted_val)
                        except Exception: pass

    def clear(self):
        with self._lock:
            if self._on_evict:
                for k, v in list(self._data.items()):
                    try: self._on_evict(k, v)
                    except Exception: pass
            self._data.clear()

    def __contains__(self, key):
        with self._lock: return key in self._data

    def __len__(self):
        with self._lock: return len(self._data)


# ════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════
_STEP_PATTERNS = [
    re.compile(r"^(?P<base>.+?)[-_]step0*(?P<step>\d+)$", re.IGNORECASE),
    re.compile(r"^(?P<base>.+?)[-_]0*(?P<step>\d{3,5})$"),
]


def _safe_filename(name: str) -> str:
    name = os.path.basename(name)
    return re.sub(r"[^A-Za-z0-9._\-+]", "", name)


def _parse_step_name(stem: str) -> Tuple[str, int]:
    """
    Extrae (base, step) de un nombre.
    Ejemplos:
      'seungcheol_BXL'                -> ('seungcheol_BXL', -1)
      'seungcheol_BXL-3000'           -> ('seungcheol_BXL', 3000)
      'seungcheol_BXL-step00003000'   -> ('seungcheol_BXL', 3000)
      'mingyu_MVT-step00002250'       -> ('mingyu_MVT', 2250)
    """
    for pat in _STEP_PATTERNS:
        m = pat.match(stem)
        if m and m.group("step"):
            try:
                return m.group("base"), int(m.group("step"))
            except ValueError:
                continue
    return stem, -1


# ════════════════════════════════════════════════════════════════════════
# ★ LORA REGISTRY — v1.1.0
#   Acepta: {character}, {character}-{N}, {character}-step0000N,
#           y variantes con extensión .safetensors.
# ════════════════════════════════════════════════════════════════════════
class LoraRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Dict[str, str] = {}
        self._entries: List[Dict[str, Any]] = []
        self._last_scan = 0.0

    def scan(self, force: bool = False):
        with self._lock:
            now = time.time()
            if not force and self._entries and (now - self._last_scan < 60):
                return
            self._cache.clear()
            self._entries.clear()

            roots = [DIR_LORAS_MAIN, VOL_LORAS_PATH]
            seen_paths = set()
            model_exts = {".safetensors", ".ckpt", ".pt", ".bin"}

            for root in roots:
                if not root.exists():
                    continue
                try:
                    for p in root.rglob("*"):
                        if not p.is_file() or p.suffix.lower() not in model_exts:
                            continue
                        real_p = str(p.resolve())
                        if real_p in seen_paths:
                            continue
                        seen_paths.add(real_p)

                        path_str = str(p).replace("\\", "/")
                        path_lower = path_str.lower()
                        stem_lower = p.stem.lower()

                        is_v1 = "/v1_" in path_lower or "/v1/" in path_lower or "_v1" in stem_lower
                        is_backup = "/_backups" in path_lower or "/save_lora" in path_lower

                        _, step = _parse_step_name(p.stem)

                        self._entries.append({
                            "path": real_p,
                            "filename": p.name,
                            "stem": p.stem,
                            "stem_lower": stem_lower,
                            "is_v1": is_v1,
                            "is_backup": is_backup,
                            "step": step,
                        })
                except Exception as e:
                    print(f"[LoraRegistry] WARN {root}: {e}", flush=True)

            self._last_scan = now
            print(f"[LoraRegistry] ✓ {len(self._entries)} LoRAs indexados en memoria", flush=True)

    def resolve(self, query: str) -> str:
        if not query:
            raise FileNotFoundError("Nombre de LoRA vacío")

        with self._lock:
            if query in self._cache:
                return self._cache[query]

        if not self._entries:
            self.scan()

        # Normalizar query
        q_clean = _safe_filename(query)               # quita directorios y caracteres raros
        q_stem = Path(q_clean).stem                    # quita extensión si la tiene
        q_lower = q_clean.lower()
        q_stem_low = q_stem.lower()

        # Parsear (base, step) del query
        q_base, q_step = _parse_step_name(q_stem)
        q_base_low = q_base.lower()

        wants_v1 = "v1" in q_lower or "v1_loras" in q_lower
        wants_v2 = "v2" in q_lower

        candidates: List[Tuple[int, int, str]] = []

        for e in self._entries:
            e_base, e_step = _parse_step_name(e["stem"])
            e_base_low = e_base.lower()
            e_stem_low = e["stem_lower"]
            e_fname_low = e["filename"].lower()

            # ── Match score ────────────────────────────────────────────
            if q_lower == e_fname_low or q_stem_low == e_stem_low:
                # Coincidencia exacta de filename o stem
                score = 1000
            elif q_base_low == e_base_low:
                # Mismo base (character sin step)
                score = 800
            elif q_base_low and (q_base_low in e_base_low or e_base_low in q_base_low):
                # Substring match sobre el base
                score = 400
            elif q_stem_low in e_stem_low or e_stem_low in q_stem_low:
                # Substring match sobre el stem completo (fallback)
                score = 300
            else:
                continue

            # ── Step handling ──────────────────────────────────────────
            if q_step > 0:
                # El query pide un step específico
                if e_step == q_step:
                    score += 500
                else:
                    score -= 400
            else:
                # El query no especifica step: preferir el archivo final
                if e_step == -1:
                    score += 300
                elif e_step > 0:
                    # Preferencia leve por steps más altos (max +100)
                    score += min(e_step // 1000, 100)

            # ── v1/v2 preference ───────────────────────────────────────
            if wants_v1:
                score += 200 if e["is_v1"] else -100
            elif wants_v2:
                score += 200 if not e["is_v1"] else -100
            else:
                score += 50 if not e["is_v1"] else 0

            # ── Backup penalty ─────────────────────────────────────────
            if e["is_backup"]:
                score -= 400
            else:
                score += 100

            candidates.append((score, e_step, e["path"]))

        if not candidates:
            # Refrescar una vez y buscar paths típicos
            self.scan(force=True)
            for cand_base in (DIR_LORAS_MAIN, VOL_LORAS_PATH):
                for suffix in (".safetensors", ".ckpt", ".pt", ".bin", ""):
                    cand = cand_base / (q_clean + suffix)
                    if cand.exists() and cand.is_file():
                        with self._lock:
                            self._cache[query] = str(cand)
                        return str(cand)
            raise FileNotFoundError(
                f"No se encontró el LoRA '{query}' en los volúmenes montados."
            )

        # Orden: score desc, step desc (para preferir steps altos en empates)
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        chosen = candidates[0][2]

        with self._lock:
            self._cache[query] = chosen

        return chosen


_LORA_REGISTRY = LoraRegistry()


def resolve_model_path(name: str, type_folder: str) -> str:
    if not name:
        raise FileNotFoundError("Nombre vacío")
    if type_folder == "lora":
        return _LORA_REGISTRY.resolve(name)

    roots = {
        "unet": [DIR_DIFFUSION],
        "clip": [DIR_TEXT_ENCODERS],
        "vae": [DIR_VAE],
        "upscale": [DIR_UPSCALE],
    }.get(type_folder)

    if not roots:
        raise ValueError(f"type_folder desconocido: {type_folder}")

    safe_query = _safe_filename(name)
    query_stem = Path(safe_query).stem
    extensions = [".safetensors", ".ckpt", ".pt", ".bin", ""]

    existing_roots = [r for r in roots if r.exists()]
    for root in existing_roots:
        for ext in extensions:
            cand = root / (safe_query + ext)
            if cand.is_file():
                return str(cand.resolve())

    for root in existing_roots:
        for path in root.rglob("*"):
            if path.is_file() and query_stem.lower() in path.name.lower():
                return str(path.resolve())

    raise FileNotFoundError(f"No se encontró '{name}' ({type_folder}) en {existing_roots}")


# ════════════════════════════════════════════════════════════════════════
# COMFYUI ENGINE
# ════════════════════════════════════════════════════════════════════════
class ComfyEngine:
    def __init__(self):
        self.cache_unet = LRUCache(LRU_UNET, on_evict=_destroy_value)
        self.cache_clip = LRUCache(LRU_CLIP, on_evict=_destroy_value)
        self.cache_vae = LRUCache(LRU_VAE, on_evict=_destroy_value)
        self.cache_upscale = LRUCache(LRU_UPSCALE, on_evict=_destroy_value)
        self.cache_lora = LRUCache(LRU_LORA, on_evict=_destroy_value)
        self.cache_conditioning = LRUCache(LRU_CONDITIONING)
        self.cache_vae_latent = LRUCache(LRU_LATENT)
        self.nodes: Dict[str, Any] = {}
        self.NODE_CLASS_MAPPINGS: Dict[str, Any] = {}
        self._warmed_up = False
        self.warmup_lock = threading.Lock()
        self._sage_patched = False
        self._state_machine = _EngineStateMachine()
        self._event_bus = _EventBus()
        self._vae_optimized_ids: set = set()

    def get_engine_state(self) -> Dict:
        return self._state_machine.get_info()

    def _cleanup_transient_state(self):
        _empty_cuda_cache()
        self.cache_vae_latent.clear()

    def initialize_comfy(self):
        if not COMFY_ROOT.exists():
            raise FileNotFoundError(f"ComfyUI no encontrado en {COMFY_ROOT}")

        comfy_str = str(COMFY_ROOT.resolve())
        if comfy_str not in sys.path:
            sys.path.insert(0, comfy_str)
        os.chdir(comfy_str)

        try:
            import folder_paths
            folder_paths.add_model_folder_path("loras", str(VOL_LORAS_PATH))
            folder_paths.add_model_folder_path("loras", str(DIR_LORAS_MAIN))
        except Exception as e:
            print(f"[init] WARN registrando carpetas loras: {e}", flush=True)

        t_nodes = time.time()
        from nodes import init_extra_nodes
        res = init_extra_nodes()
        if asyncio.iscoroutine(res):
            try:
                asyncio.run(res)
            except RuntimeError:
                asyncio.get_event_loop().run_until_complete(res)
        print(f"[init] init_extra_nodes: {time.time()-t_nodes:.2f}s", flush=True)

        self._apply_sage_attention()
        self._apply_tf32_and_cudnn()

        try:
            import comfy.model_management as _mm
            if hasattr(_mm, "text_encoder_offload_enabled"):
                _mm.text_encoder_offload_enabled = False
            if hasattr(_mm, "model_offload_enabled"):
                _mm.model_offload_enabled = False
        except Exception:
            pass

        from nodes import NODE_CLASS_MAPPINGS
        self.NODE_CLASS_MAPPINGS = NODE_CLASS_MAPPINGS
        DIR_OUTPUTS.mkdir(parents=True, exist_ok=True)

    def _apply_tf32_and_cudnn(self):
        try:
            import torch
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
            print("[TF32/cuDNN] ✓ matmul.tf32 + cudnn.benchmark ON", flush=True)
        except Exception:
            pass

    def _apply_sage_attention(self):
        if self._sage_patched:
            return
        try:
            import sageattention
            import torch.nn.functional as F
            _orig_sdpa = F.scaled_dot_product_attention

            def _sage_sdpa(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False, scale=None, *args, **kwargs):
                if q.dim() >= 4 and q.size(-3) != k.size(-3):
                    rep = q.size(-3) // k.size(-3)
                    k = k.repeat_interleave(rep, dim=-3)
                    v = v.repeat_interleave(rep, dim=-3)
                if attn_mask is not None or dropout_p > 0.0 or is_causal:
                    return _orig_sdpa(q, k, v, attn_mask=attn_mask, dropout_p=dropout_p, is_causal=is_causal, scale=scale)
                try:
                    return sageattention.sageattn(q, k, v, scale=scale)
                except Exception:
                    return _orig_sdpa(q, k, v, scale=scale)

            F.scaled_dot_product_attention = _sage_sdpa
            self._sage_patched = True
            print("[SageAttention] ✓ SDPA reemplazada por SageAttention (con GQA)", flush=True)
        except Exception:
            print("[SageAttention] ⚠ sageattention no disponible, usando SDPA", flush=True)

    def get_node(self, name: str):
        if name in self.nodes:
            return self.nodes[name]
        cls = self.NODE_CLASS_MAPPINGS.get(name)
        if cls is None:
            raise KeyError(f"Nodo '{name}' no registrado")
        inst = cls()
        self.nodes[name] = inst
        return inst

    def resolve(self, name, kind):
        return resolve_model_path(name, kind)

    def load_unet(self, name):
        path = self.resolve(name, "unet")
        cached = self.cache_unet.get(path)
        if cached is not None:
            return cached

        print(f"[load_unet] Purgando cachés antes de cargar {name}...", flush=True)
        self.cache_lora.clear()
        self.cache_unet.clear()
        try:
            import comfy.model_management as mm
            mm.unload_all_models()
            mm.soft_empty_cache()
        except Exception:
            pass
        _empty_cuda_cache()
        gc.collect()

        loader = self.get_node("UNETLoader") if "UNETLoader" in self.NODE_CLASS_MAPPINGS else self.get_node("CheckpointLoaderSimple")
        fname = os.path.basename(path)
        print(f"[load_unet] Cargando: {fname}", flush=True)

        for dt in ["fp8_e4m3fn", "fp8_e5m2", "default", "bf16"]:
            try:
                try:
                    out = loader.load_unet(unet_name=fname, weight_dtype=dt)[0]
                except TypeError:
                    out = loader.load_unet(unet_name=fname)[0]
                break
            except Exception:
                continue
        else:
            raise RuntimeError(f"No se pudo cargar el UNET {name}")

        print(f"[load_unet] OK: {fname}", flush=True)
        _empty_cuda_cache()
        self.cache_unet.put(path, out)
        return out

    def load_clip(self, name):
        path = self.resolve(name, "clip")
        cached = self.cache_clip.get(path)
        if cached is not None:
            return cached
        loader = self.get_node("CLIPLoader")
        fname = os.path.basename(path)
        print(f"[load_clip] Cargando: {fname}", flush=True)
        try:
            out = loader.load_clip(clip_name=fname, type="krea2")[0]
        except TypeError:
            out = loader.load_clip(clip_name=fname)[0]
        self.cache_clip.put(path, out)
        return out

    def load_vae(self, name):
        path = self.resolve(name, "vae")
        cached = self.cache_vae.get(path)
        if cached is not None:
            return cached
        fname = os.path.basename(path)
        is_wan = "wan" in fname.lower()
        if is_wan and "VAEUtils_CustomVAELoader" in self.NODE_CLASS_MAPPINGS:
            out = self.get_node("VAEUtils_CustomVAELoader").load_vae(fname, disable_offload=True)[0]
        else:
            out = self.get_node("VAELoader").load_vae(vae_name=fname)[0]

        self._optimize_vae(out, fname)
        self.cache_vae.put(path, out)
        return out

    def _patch_with_lora(self, unet_model, clip, lpath, strength):
        from pathlib import Path
        p = Path(lpath)
        if not p.is_file():
            raise FileNotFoundError(
                f"[LoRA ERROR] El archivo no existe o es un symlink roto: {lpath}"
            )
        file_size = p.stat().st_size
        if file_size < 1024 * 10:
            raise ValueError(
                f"[LoRA ERROR] El archivo {p.name} está dañado o vacío ({file_size} bytes)."
            )
        import comfy.utils
        import comfy.sd
        try:
            lora_data = comfy.utils.load_torch_file(str(p.resolve()), safe_load=True)
        except Exception as e:
            raise RuntimeError(
                f"[LoRA ERROR] No se pudo leer el archivo safetensors {p.name}: {e}"
            ) from e
        try:
            patched_unet, patched_clip = comfy.sd.load_lora_for_models(
                unet_model, clip, lora_data, strength, strength
            )
            return patched_unet, patched_clip
        except Exception as e:
            raise RuntimeError(
                f"[LoRA ERROR] Incompatibilidad al aplicar {p.name} en el modelo base. "
                f"¿Fue entrenado para la misma arquitectura? Error: {e}"
            ) from e

    def apply_loras(self, unet, clip, lora_dict, unet_path=None, clip_path=None, variant_key=None):
        info = {"combo_key": None, "lora_hits": [], "lora_loaded": []}
        if not lora_dict:
            return unet, clip, info

        t0 = time.time()
        items = []
        for name, w in lora_dict.items():
            p = self.resolve(name, "lora")
            items.append((p, round(float(w), 2)))
        items = sorted(items)

        combo_key = ("lora_combo", unet_path or id(unet), clip_path or id(clip), variant_key, tuple(items))
        cached = self.cache_lora.get(combo_key)
        if cached is not None:
            print(f"[apply_loras] ✓ CACHE HIT en {time.time()-t0:.3f}s", flush=True)
            info["lora_hits"] = [os.path.basename(p) for p, _ in items]
            return cached[0], cached[1], info

        current_unet, current_clip = unet, clip
        for lpath, lweight in items:
            current_unet, current_clip = self._patch_with_lora(current_unet, current_clip, lpath, lweight)
            info["lora_loaded"].append(os.path.basename(lpath))

        print(f"[apply_loras] {len(items)} LoRAs aplicados en {time.time()-t0:.2f}s", flush=True)
        self.cache_lora.put(combo_key, (current_unet, current_clip))
        return current_unet, current_clip, info

    def load_upscale(self, name):
        path = self.resolve(name, "upscale")
        cached = self.cache_upscale.get(path)
        if cached is not None:
            return cached
        out = self.get_node("UpscaleModelLoader").load_model(model_name=os.path.basename(path))[0]
        self.cache_upscale.put(path, out)
        return out

    def encode_prompt(self, clip, prompt, negative=""):
        key = (id(clip), prompt, negative)
        cached = self.cache_conditioning.get(key)
        if cached is not None:
            return cached, True
        encoder = self.get_node("CLIPTextEncode")

        import torch
        with torch.inference_mode():
            pos = encoder.encode(clip, prompt)[0]
            neg = encoder.encode(clip, negative or " ")[0]

        self.cache_conditioning.put(key, (pos, neg))
        return (pos, neg), False

    def make_empty_latent(self, w, h, batch=1):
        return self.get_node("EmptyLatentImage").generate(width=w, height=h, batch_size=batch)[0]

    def _optimize_vae(self, vae, vae_name: str):
        if id(vae) in self._vae_optimized_ids:
            return
        try:
            import torch
            target_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            inner = getattr(vae, "first_stage_model", getattr(vae, "model", vae))
            if isinstance(inner, torch.nn.Module):
                inner.to("cuda").to(target_dtype)
                for m in inner.modules():
                    if isinstance(m, torch.nn.Conv2d):
                        m.weight.data = m.weight.data.to(memory_format=torch.channels_last)
            self._patch_vae_forward_cast(vae, target_dtype)
            self._vae_optimized_ids.add(id(vae))
            print(f"[VAE-OPT] ✓ {vae_name} optimizado (bf16+channels_last)", flush=True)
        except Exception as e:
            print(f"[VAE-OPT] WARN: {e}", flush=True)

    def _patch_vae_forward_cast(self, vae, target_dtype):
        if getattr(vae, "_cast_patched", False):
            return
        _orig_decode = vae.decode

        def _decode_cast(samples_in, *args, **kwargs):
            try:
                import torch
                if isinstance(samples_in, dict) and "samples" in samples_in:
                    if isinstance(samples_in["samples"], torch.Tensor) and samples_in["samples"].dtype != target_dtype:
                        samples_in = dict(samples_in)
                        samples_in["samples"] = samples_in["samples"].to(target_dtype)
                elif isinstance(samples_in, torch.Tensor) and samples_in.dtype != target_dtype:
                    samples_in = samples_in.to(target_dtype)
            except Exception:
                pass
            return _orig_decode(samples_in, *args, **kwargs)

        vae.decode = _decode_cast
        vae._cast_patched = True

    def _warmup_text_encoder(self, clip, clip_name: str):
        try:
            import torch
            if not torch.cuda.is_available():
                return
            encoder = self.get_node("CLIPTextEncode")
            prompts = [
                "a portrait of a person",
                "a highly detailed cinematic landscape with beautiful lighting, photorealistic 8k",
            ]
            t0 = time.time()
            with torch.inference_mode():
                for p in prompts:
                    _ = encoder.encode(clip, p)[0]
                _ = encoder.encode(clip, " ")[0]
            print(f"[warmup-encoder] {clip_name} OK en {time.time()-t0:.2f}s", flush=True)
            _empty_cuda_cache()
        except Exception as e:
            print(f"[warmup-encoder] WARN: {e}", flush=True)

    def _warmup_vae(self, vae, target_h: int, target_w: int, vae_name: str):
        try:
            import torch
            if not torch.cuda.is_available():
                return
            latent_h = min(target_h, 1024) // 8
            latent_w = min(target_w, 1024) // 8
            target_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            is_wan = "wan" in vae_name.lower()

            if is_wan:
                dummy = torch.randn(1, 16, 1, latent_h, latent_w, device="cuda", dtype=target_dtype)
            else:
                dummy = torch.randn(1, 16, latent_h, latent_w, device="cuda", dtype=target_dtype)

            t = time.time()
            with torch.inference_mode():
                _ = self.decode(vae, {"samples": dummy}, use_tiled=False)
            torch.cuda.synchronize()
            print(f"[warmup-vae] {vae_name} dummy decode en {time.time()-t:.2f}s", flush=True)
            del dummy
            _empty_cuda_cache()
        except Exception as e:
            print(f"[warmup-vae] WARN: {e}", flush=True)

    def _warmup_unet_dummy(self, unet_model, unet_name: str):
        try:
            import torch
            import comfy.sample as _cs
            if not torch.cuda.is_available():
                return

            dummy_latent = torch.randn(1, 16, 1, 104, 156, device="cuda", dtype=torch.float16)
            cond_dummy = [[torch.zeros(1, 256, 30720, device="cuda", dtype=torch.float16), {
                "pooled_output": torch.zeros(1, 768, device="cuda", dtype=torch.float16)
            }]]
            cond_empty = [[torch.zeros(1, 256, 30720, device="cuda", dtype=torch.float16), {
                "pooled_output": torch.zeros(1, 768, device="cuda", dtype=torch.float16)
            }]]

            t = time.time()
            _cs.sample(
                unet_model, dummy_latent, 1, 1.0, "euler", "simple",
                cond_dummy, cond_empty,
                latent_image=dummy_latent, denoise=1.0,
                disable_pbar=True, force_full_denoise=False,
            )
            torch.cuda.synchronize()
            print(f"[warmup] UNET dummy forward OK en {time.time()-t:.2f}s", flush=True)
            del dummy_latent, cond_dummy, cond_empty
            _empty_cuda_cache()
        except Exception as e:
            print(f"[warmup] WARN UNET warmup: {e}", flush=True)

    def apply_krea_time_shift(self, model, mu=1.15, width=1024, height=1024):
        node_name = "ModelSamplingFlux"
        if node_name not in self.NODE_CLASS_MAPPINGS:
            return model
        try:
            patcher = self.get_node(node_name)
            try:
                return patcher.patch(model=model, width=width, height=height, base_shift=0.5, max_shift=mu)[0]
            except TypeError:
                return patcher.patch(model=model, max_shift=mu)[0]
        except Exception:
            return model

    def sample(self, model, seed, steps, cfg, sampler, scheduler, positive, negative, latent, denoise=1.0, task_id=None, emit=None):
        if seed == 0:
            seed = random.randint(1, 2**31 - 1)
        out = self.get_node("KSampler").sample(
            model=model, positive=positive, negative=negative,
            latent_image=latent, seed=seed, steps=steps, cfg=cfg,
            sampler_name=sampler, scheduler=scheduler, denoise=denoise,
        )[0]
        return out, seed

    def decode(self, vae, latent, tile_size=None, overlap=None, use_tiled=False):
        if tile_size is None:
            tile_size = DEFAULT_VAE_TILE_SIZE
        if overlap is None:
            overlap = DEFAULT_VAE_TILE_OVERLAP

        try:
            import comfy.model_management as mm
            mm.soft_empty_cache()
        except Exception:
            pass
        _empty_cuda_cache()

        tiled = self.nodes.get("VAEUtils_VAEDecodeTiled")
        if tiled is None and "VAEUtils_VAEDecodeTiled" in self.NODE_CLASS_MAPPINGS:
            tiled = self.get_node("VAEUtils_VAEDecodeTiled")

        import torch
        if use_tiled and tiled is not None:
            with torch.inference_mode():
                return tiled.decode(
                    samples=latent, vae=vae, upscale=-1,
                    tile=True, tile_size=tile_size, overlap=overlap,
                    temporal_size=64, temporal_overlap=8,
                )[0]

        try:
            with torch.inference_mode():
                if isinstance(latent, dict) and "samples" in latent:
                    return vae.decode(latent["samples"])
                return vae.decode(latent)
        except RuntimeError as e:
            if "out of memory" in str(e).lower() and tiled is not None:
                print("[decode] OOM en decode estándar → reintentando tiled", flush=True)
                _empty_cuda_cache()
                with torch.inference_mode():
                    return tiled.decode(samples=latent, vae=vae, upscale=-1, tile=True, tile_size=tile_size, overlap=overlap, temporal_size=64, temporal_overlap=8)[0]
            raise

    def upscale_image(self, image, upscale_model_name, scale):
        up_model = self.load_upscale(upscale_model_name)
        upscaled = self.get_node("ImageUpscaleWithModel").upscale(upscale_model=up_model, image=image)[0]
        scaler = self.get_node("ImageScale")
        return scaler.upscale(image=upscaled, upscale_method="lanczos", width=int(upscaled.shape[2] * scale), height=int(upscaled.shape[1] * scale), crop="disabled")[0]

    def save_image(self, image_tensor, seed: int, batch_index: int = 0):
        import numpy as np
        from PIL import Image

        if hasattr(image_tensor, "detach"):
            t = image_tensor.detach().cpu()
        else:
            t = image_tensor

        if t.dim() == 5:
            t = t[batch_index][0]
        elif t.dim() == 4:
            t = t[batch_index]
            if t.shape[0] in (1, 3, 4) and t.shape[-1] not in (1, 3, 4):
                t = t.permute(1, 2, 0)
        elif t.dim() == 3:
            if t.shape[0] in (1, 3, 4) and t.shape[-1] not in (1, 3, 4):
                t = t.permute(1, 2, 0)

        arr = t.float().numpy()

        if np.isnan(arr).any() or np.isinf(arr).any():
            print("[save_image] ⚠ Detectados NaNs/Infs en imagen (saturación por LoRAs), limpiando...", flush=True)
            arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=0.0)

        if arr.max() <= 1.05 and arr.min() >= -0.05:
            arr = (arr * 255.0).clip(0, 255).astype("uint8")
        else:
            arr = arr.clip(0, 255).astype("uint8")

        if arr.ndim > 3:
            arr = np.squeeze(arr)

        if arr.ndim == 2:
            h, w = arr.shape
            img = Image.fromarray(arr, mode="L")
        elif arr.ndim == 3:
            h, w, c = arr.shape
            if c == 1:
                img = Image.fromarray(arr[:, :, 0], mode="L")
            elif c == 4:
                img = Image.fromarray(arr, mode="RGBA")
            else:
                img = Image.fromarray(arr[:, :, :3], mode="RGB")
        else:
            raise ValueError(f"Dimensión inesperada para imagen final: {arr.shape}")

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False, compress_level=4)
        raw = buf.getvalue()
        sha = hashlib.sha256(raw).hexdigest()

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{timestamp}_krea2_{seed}_batch{batch_index}.png"

        try:
            (DIR_OUTPUTS / fname).write_bytes(raw)
        except OSError:
            pass

        r2_url = _upload_to_r2(raw, fname)
        url = r2_url if r2_url else f"/serve_image?filename={fname}"

        return {
            "filename": fname,
            "sha256": sha,
            "size_bytes": len(raw),
            "width": w,
            "height": h,
            "_bytes_b64": base64.b64encode(raw).decode("ascii"),
            "url": url,
            "r2_url": r2_url,
        }

    def _resolve_image(self, image_data: Any):
        from PIL import Image
        if image_data is None:
            raise ValueError("Se requiere imagen")
        if isinstance(image_data, bytes):
            return Image.open(io.BytesIO(image_data)).convert("RGB")
        if isinstance(image_data, str):
            if image_data.startswith("http://") or image_data.startswith("https://"):
                import requests
                resp = requests.get(image_data, timeout=15)
                resp.raise_for_status()
                return Image.open(io.BytesIO(resp.content)).convert("RGB")
            encoded = image_data.strip()
            if encoded.startswith("data:") and "," in encoded:
                encoded = encoded.split(",", 1)[1]
            raw = base64.b64decode(encoded, validate=True)
            return Image.open(io.BytesIO(raw)).convert("RGB")
        raise ValueError("Formato de imagen inválido")

    def pil_to_tensor(self, pil_image):
        import numpy as np
        import torch
        arr = np.array(pil_image).astype(np.float32) / 255.0
        return torch.from_numpy(arr)[None, ...]

    def blend_latents(self, base_latent, ref_image, vae, strength):
        ref_tensor = self.pil_to_tensor(ref_image)
        ref_latent = self.get_node("VAEEncode").encode(pixels=ref_tensor, vae=vae)[0]
        from comfy_extras.nodes_latent import BlendLatents
        return BlendLatents().blend_latents(samples1=base_latent, samples2=ref_latent, blend_mode="lerp", blend_factor=strength)[0]

    @staticmethod
    def calculate_pricing(timings: Dict[str, float]) -> Dict[str, Any]:
        pricing = {}
        total_cost = 0.0
        for key, val in timings.items():
            cost = val * TOTAL_PRICE_PER_SEC
            pricing[key] = round(cost, 6)
            if key != "total":
                total_cost += cost
        pricing["total"] = round(total_cost, 6)
        pricing["config"] = {
            "gpu_per_sec": GPU_PRICE_PER_SEC,
            "cpu_per_sec": CPU_TOTAL_PRICE_PER_SEC,
            "ram_per_sec": RAM_TOTAL_PRICE_PER_SEC,
            "total_per_sec": TOTAL_PRICE_PER_SEC,
        }
        return pricing

    def generate(self, payload):
        task_id = payload.get("task_id") or str(uuid.uuid4())
        payload["task_id"] = task_id

        if not self._state_machine.try_enter(task_id, timeout=0):
            state = self._state_machine.get_info()
            raise RuntimeError(f"Engine busy (state={state['state']}, task={state['task_id']}).")

        try:
            return self._generate_inner(payload, task_id=task_id)
        finally:
            self._cleanup_transient_state()
            self._state_machine.exit(task_id)
            self._event_bus.close_task(task_id, final_event={"type": "done", "ts": time.time()})

    def _generate_inner(self, payload, task_id: Optional[str] = None):
        emit = make_emit(self._event_bus, task_id)
        emit("start", stage="init")

        timings = {}
        cache_hits = {"unet_hit": False, "clip_hit": False, "vae_hit": False, "lora_hits": [], "conditioning_hit": False}
        cache_loads = {"unet_loaded": None, "clip_loaded": None, "vae_loaded": None, "loras_loaded": []}

        positive_prompt = payload.get("positive_prompt", "")
        negative_prompt = payload.get("negative_prompt", "")
        width = int(payload.get("width", 1024))
        height = int(payload.get("height", 1024))
        steps = int(payload.get("steps", 28))
        cfg = float(payload.get("cfg", 1.0))
        seed = int(payload.get("seed", 0))
        sampler_name = payload.get("sampler_name", "euler")
        scheduler = payload.get("scheduler", "beta")
        unet_name = payload.get("unet_name", DEFAULT_UNET)
        clip_name = payload.get("clip_name", DEFAULT_CLIP)
        vae_name = payload.get("vae_name", DEFAULT_VAE)
        mu_value = float(payload.get("mu", DEFAULT_KREA_MU))
        lora_dict = payload.get("lora", {}) or {}
        denoise = float(payload.get("denoise", 1.0))
        ref_images = payload.get("reference_images", []) or []
        upscale_model = payload.get("upscale_model")
        upscale_scale = float(payload.get("upscale_scale", 1.0))
        return_base64 = bool(payload.get("return_base64", True))
        use_tiled_vae = bool(payload.get("use_tiled_vae", False))
        vae_tile_size = int(payload.get("vae_tile_size", DEFAULT_VAE_TILE_SIZE))
        vae_tile_overlap = int(payload.get("vae_tile_overlap", DEFAULT_VAE_TILE_OVERLAP))

        t0 = time.time()
        unet_path = self.resolve(unet_name, "unet")
        clip_path = self.resolve(clip_name, "clip")

        t_load = time.time()
        unet = self.load_unet(unet_name)
        clip = self.load_clip(clip_name)
        vae = self.load_vae(vae_name)
        timings["model_load"] = time.time() - t_load

        if id(vae) not in self._vae_optimized_ids:
            self._warmup_vae(vae, height, width, vae_name)

        if mu_value > 0:
            t_shift = time.time()
            unet = self.apply_krea_time_shift(unet, mu=mu_value, width=width, height=height)
            timings["time_shift"] = time.time() - t_shift

        unet, clip, lora_info = self.apply_loras(
            unet, clip, lora_dict, unet_path=unet_path, clip_path=clip_path,
            variant_key=(round(mu_value, 4), width, height),
        )
        cache_hits["lora_hits"] = lora_info["lora_hits"]
        cache_loads["loras_loaded"] = lora_info["lora_loaded"]

        t_enc = time.time()
        (pos, neg), hit = self.encode_prompt(clip, positive_prompt, negative_prompt)
        cache_hits["conditioning_hit"] = hit
        timings["encode"] = time.time() - t_enc

        latent = self.make_empty_latent(width, height, batch=1)
        for ref in ref_images:
            try:
                pil = self._resolve_image(ref.get("image"))
                latent = self.blend_latents(latent, pil, vae, float(ref.get("strength", 0.5)))
            except Exception as e:
                print(f"[generate] WARN ref_image: {e}", flush=True)

        t_samp = time.time()
        latent, used_seed = self.sample(
            unet, seed, steps, cfg, sampler_name, scheduler,
            pos, neg, latent, denoise=denoise,
            task_id=task_id, emit=emit,
        )
        timings["sampling"] = time.time() - t_samp
        _empty_cuda_cache()

        t_dec = time.time()
        img_tensor = self.decode(vae, latent, tile_size=vae_tile_size, overlap=vae_tile_overlap, use_tiled=use_tiled_vae)
        timings["decode"] = time.time() - t_dec

        t_up = time.time()
        if upscale_model:
            img_tensor = self.upscale_image(img_tensor, upscale_model, upscale_scale)
        timings["upscale"] = time.time() - t_up

        meta = self.save_image(img_tensor, used_seed, 0)
        timings["total"] = time.time() - t0
        pricing = ComfyEngine.calculate_pricing(timings)

        return {
            "success": True,
            "seed": used_seed,
            "images": [
                {
                    "filename": meta["filename"],
                    "sha256": meta["sha256"],
                    "size_bytes": meta["size_bytes"],
                    "width": meta["width"],
                    "height": meta["height"],
                    "url": meta["url"],
                    **({"base64": meta["_bytes_b64"]} if return_base64 else {}),
                }
            ],
            "timing": timings,
            "pricing": pricing,
            "task_id": task_id,
            "metadata": {
                "software": f"Krea2 Programmatic API v{SERVICE_VERSION}",
                "cache": cache_hits,
                "prompt": positive_prompt,
            },
        }

    def variation(self, payload):
        task_id = payload.get("task_id") or str(uuid.uuid4())
        payload["task_id"] = task_id
        if not self._state_machine.try_enter(task_id, timeout=0):
            state = self._state_machine.get_info()
            raise RuntimeError(f"Engine busy ({state['state']}).")
        try:
            image_data = payload.get("image")
            if not image_data:
                raise ValueError("Requiere 'image'")
            return self._generate_inner({
                **payload,
                "reference_images": [{
                    "image": image_data,
                    "strength": float(payload.get("strength", 0.5)),
                }],
                "denoise": float(payload.get("denoise", 0.65)),
            }, task_id=task_id)
        finally:
            self._cleanup_transient_state()
            self._state_machine.exit(task_id)
            self._event_bus.close_task(task_id, final_event={"type": "done", "ts": time.time()})

    def edit(self, payload):
        task_id = payload.get("task_id") or str(uuid.uuid4())
        payload["task_id"] = task_id
        if not self._state_machine.try_enter(task_id, timeout=0):
            raise RuntimeError("Engine busy")
        try:
            image_data = payload.get("image")
            if not image_data:
                raise ValueError("/edit requiere 'image'")
            edit_prompt = payload.get("edit_prompt", "")
            negative_prompt = payload.get("negative_prompt", "")
            lora_dict = dict(payload.get("lora", {}) or {})
            lora_dict["krea2_identity_edit_v1_1"] = 1.0

            unet = self.load_unet(payload.get("unet_name", DEFAULT_UNET))
            clip = self.load_clip(payload.get("clip_name", DEFAULT_CLIP))
            vae = self.load_vae(payload.get("vae_name", DEFAULT_VAE))

            unet, clip, _ = self.apply_loras(unet, clip, lora_dict)
            (pos, neg), _ = self.encode_prompt(clip, edit_prompt, negative_prompt)

            pil = self._resolve_image(image_data)
            latent = self.get_node("VAEEncode").encode(pixels=self.pil_to_tensor(pil), vae=vae)[0]

            latent, used_seed = self.sample(
                unet, int(payload.get("seed", 0)), int(payload.get("steps", 28)),
                float(payload.get("cfg", 5.0)), "euler", "normal", pos, neg, latent,
                denoise=float(payload.get("denoise", 0.7)),
            )
            img_tensor = self.decode(vae, latent, use_tiled=bool(payload.get("use_tiled_vae", False)))
            meta = self.save_image(img_tensor, used_seed, 0)
            return {"success": True, "seed": used_seed, "images": [{"url": meta["url"], "filename": meta["filename"]}]}
        finally:
            self._cleanup_transient_state()
            self._state_machine.exit(task_id)

    def depth_generate(self, payload):
        task_id = payload.get("task_id") or str(uuid.uuid4())
        payload["task_id"] = task_id
        if not self._state_machine.try_enter(task_id, timeout=0):
            raise RuntimeError("Engine busy")
        try:
            import torch
            image_data = payload.get("image") or payload.get("depth_image")
            if not image_data:
                raise ValueError("Requiere 'image'")

            width = int(payload.get("width", 1024))
            height = int(payload.get("height", 1024))
            steps = int(payload.get("steps", 28))
            cfg = float(payload.get("cfg", 1.0))
            seed = int(payload.get("seed", 0))

            unet = self.load_unet(payload.get("unet_name", DEFAULT_UNET))
            clip = self.load_clip(payload.get("clip_name", DEFAULT_CLIP))
            vae = self.load_vae(payload.get("vae_name", DEFAULT_VAE))
            unet, clip, _ = self.apply_loras(unet, clip, payload.get("lora", {}) or {})

            pil_image = self._resolve_image(image_data)
            depth_tensor = self.pil_to_tensor(pil_image)

            depth_latent = self.get_node("VAEEncode").encode(pixels=depth_tensor, vae=vae)[0]
            latent = self.make_empty_latent(width, height, batch=1)

            if depth_latent.dim() == 5 and latent.dim() == 4:
                latent = latent.unsqueeze(2)
            elif depth_latent.dim() == 4 and latent.dim() == 5:
                depth_latent = depth_latent.unsqueeze(2)

            latent = torch.cat([latent, depth_latent], dim=1)
            (pos, neg), _ = self.encode_prompt(clip, payload.get("positive_prompt", ""), payload.get("negative_prompt", ""))

            latent, used_seed = self.sample(unet, seed, steps, cfg, "euler", "beta", pos, neg, latent, denoise=float(payload.get("denoise", 1.0)))
            img_tensor = self.decode(vae, latent, use_tiled=bool(payload.get("use_tiled_vae", False)))
            meta = self.save_image(img_tensor, used_seed, 0)
            return {"success": True, "seed": used_seed, "images": [{"url": meta["url"], "filename": meta["filename"]}]}
        finally:
            self._cleanup_transient_state()
            self._state_machine.exit(task_id)

    def warmup(self):
        with self.warmup_lock:
            t0 = time.time()
            unet = self.load_unet(DEFAULT_UNET)
            clip = self.load_clip(DEFAULT_CLIP)
            vae = self.load_vae(DEFAULT_VAE)
            print(f"[warmup] Modelos cargados en {time.time()-t0:.1f}s", flush=True)

            self._warmup_text_encoder(clip, DEFAULT_CLIP)
            self._warmup_unet_dummy(unet, DEFAULT_UNET)
            self._warmup_vae(vae, 1024, 1024, DEFAULT_VAE)
            self._warmed_up = True
        return {"warmed_up": True, "ts": time.time()}


# ════════════════════════════════════════════════════════════════════════
# SYMLINKS
# ════════════════════════════════════════════════════════════════════════
def _ensure_comfy_symlinks():
    comfy_models = Path("/root/ComfyUI/models")
    main_vol = VOL_MAIN_PATH

    DIR_UTILS.mkdir(parents=True, exist_ok=True)
    DIR_LORAS_MAIN.mkdir(parents=True, exist_ok=True)

    if not comfy_models.is_symlink():
        if comfy_models.exists():
            import shutil
            shutil.rmtree(comfy_models)
        comfy_models.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(str(main_vol), str(comfy_models))
        print(f"[symlinks] ✓ ComfyUI models -> {main_vol}", flush=True)

    annotators_dir = comfy_models / "annotators"
    if not annotators_dir.exists() and not annotators_dir.is_symlink():
        try:
            os.symlink(str(DIR_UTILS), str(annotators_dir))
            print(f"[symlinks] ✓ annotators -> {DIR_UTILS}", flush=True)
        except Exception:
            pass

    renew_symlink = DIR_LORAS_MAIN / "renew_vol"
    if VOL_LORAS_PATH.exists() and not renew_symlink.exists() and not renew_symlink.is_symlink():
        try:
            os.symlink(str(VOL_LORAS_PATH), str(renew_symlink))
            print(f"[symlinks] ✓ LoRAs renew montado como carpeta única en {renew_symlink}", flush=True)
        except Exception as e:
            print(f"[symlinks] WARN renew_symlink: {e}", flush=True)


# ════════════════════════════════════════════════════════════════════════
# FASTAPI HANDLER
# ════════════════════════════════════════════════════════════════════════
def create_app(engine: ComfyEngine) -> FastAPI:
    app = FastAPI(title=f"Krea2 API v{SERVICE_VERSION}")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _abs_urls(res, request):
        base_url = str(request.base_url).rstrip("/")
        for img in res.get("images", []):
            u = img.get("url")
            if u and u.startswith("/"):
                img["url"] = f"{base_url}{u}"

    @app.post("/generate")
    async def generate(request: Request):
        engine._state_machine.check_zombie()
        try:
            payload = await _parse_payload(request)
            res = await run_in_threadpool(engine.generate, payload)
            _abs_urls(res, request)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse(
                {"success": False, "error": str(e), "traceback": traceback.format_exc()},
                status_code=500,
            )

    @app.post("/variation")
    async def variation(request: Request):
        try:
            payload = await _parse_payload(request)
            res = await run_in_threadpool(engine.variation, payload)
            _abs_urls(res, request)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/edit")
    async def edit(request: Request):
        try:
            payload = await _parse_payload(request)
            res = await run_in_threadpool(engine.edit, payload)
            _abs_urls(res, request)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/depth-generate")
    async def depth_generate(request: Request):
        try:
            payload = await _parse_payload(request)
            res = await run_in_threadpool(engine.depth_generate, payload)
            _abs_urls(res, request)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @app.post("/rescan-loras")
    def rescan_loras():
        _LORA_REGISTRY.scan(force=True)
        return {"success": True, "loras_count": len(_LORA_REGISTRY._entries)}

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "version": SERVICE_VERSION, **engine.get_engine_state()}

    return app


# ════════════════════════════════════════════════════════════════════════
# MODAL — Clase con GPU Snapshots
# ════════════════════════════════════════════════════════════════════════
@app.cls(
    image=IMAGE,
    gpu="L40S",
    memory=32768,
    cpu=4.0,
    volumes=VOLUMES,
    secrets=[R2_SECRET, CIVITAI_SECRET, HF_SECRET],
    timeout=3600,
    scaledown_window=60,
    min_containers=0,
    max_containers=1,
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},
)
class Krea2App:

    @modal.enter(snap=True)
    def load_and_snapshot(self):
        print(f"[Krea2App v{SERVICE_VERSION}] Iniciando carga para snapshot...", flush=True)
        t0 = time.time()

        _ensure_comfy_symlinks()
        _LORA_REGISTRY.scan(force=True)

        self.engine = ComfyEngine()
        self.engine.initialize_comfy()
        self.engine._state_machine.force_idle(reason="snapshot")

        try:
            self.engine.warmup()
        except Exception as we:
            print(f"[Krea2App] WARN en warmup: {we}", flush=True)

        print(f"[Krea2App] ✓ Snapshot listo en {time.time()-t0:.2f}s", flush=True)

    @modal.asgi_app()
    def fastapi_app(self):
        return create_app(self.engine)


@app.local_entrypoint()
def main():
    print(f"App v{SERVICE_VERSION} configurada.")
    print("Despliega con: modal deploy krea2_modal-v1_1.py")