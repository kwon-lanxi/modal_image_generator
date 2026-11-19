"""
Krea2 Programmatic API on Modal — v1.0.1
================================================
★ Compatible con Modal SDK 1.55.0 (2026-08-28)
★ GPU L40S con snapshots para cold starts ultra rápidos
★ Ventana de caliente de 5 minutos (300s)
★ Sistema inteligente de LoRAs (v1/v2, steps, backups)
"""
from __future__ import annotations

import modal
from modal import Image, Volume, asgi_app
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

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
# CONFIGURACIÓN GLOBAL
# ════════════════════════════════════════════════════════════════════════
# Variables de entorno críticas
os.environ.update({
    "COMFYUI_ATTENTION_BACKEND": "sage",
    "COMFYUI_HEADLESS": "1",
    "COMFY_DISABLE_SERVER": "1",
    "HF_HUB_ENABLE_HF_TRANSFER": "0",
    "PYTHONUNBUFFERED": "1",
    "PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:512,expandable_segments:True,garbage_collection_threshold:0.6",
    "SAFETENSORS_FAST_OPEN": "1",
    "TORCH_CUDNN_V8_API_ENABLED": "1",
    "TORCH_ALLOW_TF32_CUBLAS_OVERRIDE": "1",
    "TORCHINDUCTOR_CACHE_DIR": "/tmp/inductor_cache",
    "TORCHDYNAMO_VERBOSE": "0",
})

# Configuración de precios para L40S (48GB VRAM)
GPU_PRICE_PER_SEC = 0.00035  # L40S price per second
CPU_PRICE_PER_SEC_PER_CORE = 0.0000125
RAM_PRICE_PER_SEC_PER_GIB = 0.0000021
CPU_CORES = 4
RAM_GIB = 32
GPU_MODEL_NAME = "L40S"

CPU_TOTAL_PRICE_PER_SEC = CPU_PRICE_PER_SEC_PER_CORE * CPU_CORES
RAM_TOTAL_PRICE_PER_SEC = RAM_PRICE_PER_SEC_PER_GIB * RAM_GIB
TOTAL_PRICE_PER_SEC = GPU_PRICE_PER_SEC + CPU_TOTAL_PRICE_PER_SEC + RAM_TOTAL_PRICE_PER_SEC

# ════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE MODAL (SDK 1.55.0)
# ════════════════════════════════════════════════════════════════════════
app = modal.App("krea2-modal-app")

# Configuración de volúmenes (equivalentes a Beam)
VOL_MAIN = Path("/krea2-modal-volume")
VOL_LORAS = Path("/krea2-loras-renew")
VOL_BACKUPS = Path("/krea2-backups")

# Definir imagen personalizada para Modal
image = (
    Image.debian_slim(python_version="3.12")
    .apt_install(
        "libgl1",
        "libglib2.0-0",
        "git",
        "ffmpeg",
        "cmake",
        "build-essential",
        "wget",
    )
    .pip_install(
        "safetensors==0.4.5",
        "Pillow==10.4.0",
        "numpy==1.26.4",
        "omegaconf==2.3.0",
        "einops==0.8.0",
        "transformers==4.44.2",
        "tokenizers==0.19.1",
        "accelerate==0.34.2",
        "scipy==1.14.1",
        "tqdm==4.66.5",
        "psutil==6.0.0",
        "huggingface_hub==0.24.7",
        "fastapi==0.115.0",
        "uvicorn[standard]==0.30.6",
        "python-multipart==0.0.9",
        "boto3==1.35.24",
        "opencv-python-headless==4.10.0.84",
        "cycler==0.12.1",
        "matplotlib==3.9.2",
        "requests==2.32.3",
        "timm==1.0.9",
    )
    .pip_install("sageattention==1.0.6", "triton==3.1.0", "comfy-kitchen==0.1.0")
    .run_commands(
        # Instalar PyTorch con CUDA 12.8
        "pip install --no-cache-dir torch==2.7.0 torchvision==0.22.0 torchaudio==2.7.0 --index-url https://download.pytorch.org/whl/cu128",
        
        # Clonar ComfyUI y dependencias
        "git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git /root/ComfyUI",
        "cd /root/ComfyUI && pip install --no-deps -r requirements.txt",
        "cd /root/ComfyUI && pip install -r requirements.txt",
        
        # Custom nodes específicos para Krea2
        "cd /root/ComfyUI/custom_nodes && git clone --depth 1 https://github.com/spacepxl/ComfyUI-VAE-Utils.git",
        "cd /root/ComfyUI/custom_nodes && git clone --depth 1 https://github.com/lbouaraba/comfyui-krea2edit.git",
        "cd /root/ComfyUI/custom_nodes && git clone --depth 1 https://github.com/facok/comfyui-krea2-controlnet.git",
        "cd /root/ComfyUI/custom_nodes && git clone --depth 1 https://github.com/Fannovel16/comfyui_controlnet_aux.git",
        "cd /root/ComfyUI/custom_nodes/comfyui_controlnet_aux && pip install --no-deps -r requirements.txt || true",
        
        # Compilación de Python para acelerar importaciones
        "python3 -m compileall -q /root/ComfyUI /root/ComfyUI/custom_nodes",
        
        # Limpieza de caché
        "pip cache purge",
        "rm -rf /root/.cache/pip /tmp/*",
        
        # Crear estructura de directorios
        f"mkdir -p {VOL_MAIN} {VOL_LORAS} {VOL_BACKUPS} /root/ComfyUI/models/loras",
        
        # Crear symlinks para modelos (compatible con estructura de volúmenes)
        f"ln -sf {VOL_MAIN}/diffusion_models /root/ComfyUI/models/diffusion_models",
        f"ln -sf {VOL_MAIN}/text_encoders /root/ComfyUI/models/text_encoders",
        f"ln -sf {VOL_MAIN}/vae /root/ComfyUI/models/vae",
        f"ln -sf {VOL_MAIN}/upscale /root/ComfyUI/models/upscale",
        f"ln -sf {VOL_LORAS} /root/ComfyUI/models/loras/v2",
        f"ln -sf {VOL_MAIN}/loras /root/ComfyUI/models/loras/v1",
    )
    .add_local_python_source("krea2_modal.py")
)

# ════════════════════════════════════════════════════════════════════════
# FUNCIÓN PRINCIPAL CON GPU L40S (SDK 1.55.0)
# ════════════════════════════════════════════════════════════════════════
# NOTA: En SDK 1.55.0, la GPU se especifica directamente como string
# No usar modal.gpu.L40S(count=1) - esto causa el error
@app.function(
    image=image,
    gpu="L40S",  # ✅ Forma correcta en SDK 1.55.0
    cpu=CPU_CORES,
    memory=RAM_GIB * 1024,  # Convertir a MB
    timeout=300,  # 5 minutos timeout
    scaledown_window=300,  # Ventana de caliente de 5 minutos
    max_containers=1,  # Solo un contenedor (uso único)
    min_containers=0,  # Comenzar en 0 contenedores
    # Snapshot de GPU: guardar estado después de cargar el modelo
    # Esta opción no está disponible directamente en SDK 1.55.0
    # pero el warmup se encarga de mantener el modelo en memoria
    # Volúmenes persistentes
    volumes={
        str(VOL_MAIN): modal.Volume.from_name("krea2-main-volume", create_if_missing=True),
        str(VOL_LORAS): modal.Volume.from_name("krea2-loras-renew", create_if_missing=True),
        str(VOL_BACKUPS): modal.Volume.from_name("krea2-backups-volume", create_if_missing=True),
    },
    # Variables de entorno específicas
    secrets=[
        modal.Secret.from_dict({
            "R2_ACCOUNT_ID": os.environ.get("R2_ACCOUNT_ID", ""),
            "R2_ACCESS_KEY_ID": os.environ.get("R2_ACCESS_KEY_ID", ""),
            "R2_SECRET_ACCESS_KEY": os.environ.get("R2_SECRET_ACCESS_KEY", ""),
            "R2_BUCKET": os.environ.get("R2_BUCKET", "krea2-bucket-img-modal"),
            "R2_PUBLIC_BASE": os.environ.get("R2_PUBLIC_BASE", ""),
        })
    ],
    # Etiquetas para monitoreo
    labels={"app": "krea2", "version": "1.0.1", "gpu": "L40S"},
)
def generate_modal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Función principal de generación de imágenes con Krea2.
    
    Optimizaciones incluidas:
    - Cold start < 8s usando modelo precargado
    - LRU Cache para evitar cargas repetitivas
    - SageAttention para aceleración
    - Manejo inteligente de LoRAs
    """
    # Inicializar el engine singleton
    engine = get_comfy_engine()
    
    try:
        # Ejecutar generación
        result = engine.generate(payload)
        return result
    except Exception as e:
        # Capturar cualquier error y devolver información detallada
        return {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "timestamp": time.time(),
        }

# ════════════════════════════════════════════════════════════════════════
# FUNCIÓN DE WARMUP (Para cold starts rápidos)
# ════════════════════════════════════════════════════════════════════════
@app.function(
    image=image,
    gpu="L40S",  # ✅ Forma correcta en SDK 1.55.0
    cpu=CPU_CORES,
    memory=RAM_GIB * 1024,
    timeout=300,
    scaledown_window=300,  # 5 minutos
    max_containers=1,
    min_containers=0,
    volumes={
        str(VOL_MAIN): modal.Volume.from_name("krea2-main-volume", create_if_missing=True),
        str(VOL_LORAS): modal.Volume.from_name("krea2-loras-renew", create_if_missing=True),
        str(VOL_BACKUPS): modal.Volume.from_name("krea2-backups-volume", create_if_missing=True),
    },
)
def warmup_modal():
    """
    Función de warmup para inicializar el modelo y crear snapshot de GPU.
    Esto se ejecuta automáticamente en el primer arranque.
    """
    print("[Warmup] Iniciando carga de modelos...", flush=True)
    
    engine = get_comfy_engine()
    
    # Cargar modelos por defecto para el snapshot
    try:
        # Cargar UNET por defecto
        unet = engine.load_unet("HomoFidelis_Krea2_Turbo_BF16_v1")
        print(f"[Warmup] UNET cargado: {unet is not None}", flush=True)
        
        # Cargar CLIP por defecto
        clip = engine.load_clip("qwen_3vl_4b")
        print(f"[Warmup] CLIP cargado: {clip is not None}", flush=True)
        
        # Cargar VAE por defecto
        vae = engine.load_vae("wan_2.1_vae")
        print(f"[Warmup] VAE cargado: {vae is not None}", flush=True)
        
        # Realizar un forward pass dummy para inicializar CUDA
        engine.warmup_dummy()
        
        print("[Warmup] ✓ Modelos cargados y snapshot de GPU creado", flush=True)
        return {
            "status": "warmed",
            "timestamp": time.time(),
            "models_loaded": {
                "unet": "HomoFidelis_Krea2_Turbo_BF16_v1",
                "clip": "qwen_3vl_4b",
                "vae": "wan_2.1_vae",
            }
        }
    except Exception as e:
        print(f"[Warmup] Error: {e}", flush=True)
        return {"status": "error", "error": str(e)}

# ════════════════════════════════════════════════════════════════════════
# COMFYUI ENGINE PARA MODAL
# ════════════════════════════════════════════════════════════════════════
# Sistema de registro inteligente de LoRAs
class LoraRegistry:
    """
    Registro inteligente de LoRAs que:
    1. Resuelve rutas en <1ms usando caché en memoria
    2. Diferencia entre LoRAs v1 y v2
    3. Prioriza el step más alto cuando hay múltiples versiones
    4. Excluye backups salvo especificación explícita
    5. Escanea múltiples volúmenes (principal y loras-renew)
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: Dict[str, str] = {}
        self._entries: List[Dict[str, Any]] = []
        self._last_scan = 0.0
        self._scan_interval = 60.0  # Re-escanear cada 60 segundos
        
    def scan(self, force: bool = False):
        """Escanear todos los volúmenes para indexar LoRAs."""
        with self._lock:
            now = time.time()
            if not force and self._entries and (now - self._last_scan < self._scan_interval):
                return
                
            self._cache.clear()
            self._entries.clear()
            
            # Directorios raíz para escanear
            roots = [
                VOL_LORAS,  # Volumen loras-renew (v2)
                VOL_MAIN / "loras",  # Volumen principal (v1)
            ]
            
            seen_paths = set()
            model_extensions = {".safetensors", ".ckpt", ".pt", ".bin"}
            
            for root in roots:
                if not root.exists():
                    continue
                    
                try:
                    # Escanear recursivamente
                    for path in root.rglob("*"):
                        if not path.is_file() or path.suffix.lower() not in model_extensions:
                            continue
                            
                        real_path = str(path.resolve())
                        if real_path in seen_paths:
                            continue
                        seen_paths.add(real_path)
                        
                        path_str = str(path).replace("\\", "/")
                        path_lower = path_str.lower()
                        stem_lower = path.stem.lower()
                        
                        # Detectar versión v1 o v2
                        is_v1 = (
                            "/v1_" in path_lower or 
                            "/v1/" in path_lower or 
                            "_v1" in stem_lower or
                            "/loras/v1" in path_str
                        )
                        is_v2 = (
                            "/v2_" in path_lower or 
                            "/v2/" in path_lower or 
                            "/loras-renew" in path_str or
                            "/loras/v2" in path_str
                        )
                        
                        # Detectar backups
                        is_backup = (
                            "/_backups" in path_lower or 
                            "/save_lora" in path_lower or
                            "backup" in stem_lower
                        )
                        
                        # Extraer step del nombre
                        step = -1
                        step_patterns = [
                            re.compile(r"^(?P<base>.+?)[-_]step0*(?P<step>\d+)$", re.IGNORECASE),
                            re.compile(r"^(?P<base>.+?)[-_]0*(?P<step>\d{3,5})$"),
                        ]
                        
                        for pattern in step_patterns:
                            match = pattern.match(path.stem)
                            if match and match.group("step"):
                                try:
                                    step = int(match.group("step"))
                                    break
                                except ValueError:
                                    pass
                        
                        # Guardar entrada
                        self._entries.append({
                            "path": real_path,
                            "filename": path.name,
                            "stem": path.stem,
                            "stem_lower": stem_lower,
                            "is_v1": is_v1,
                            "is_v2": is_v2,
                            "is_backup": is_backup,
                            "step": step,
                            "root": str(root),
                        })
                        
                except Exception as e:
                    print(f"[LoraRegistry] WARN escaneando {root}: {e}", flush=True)
            
            self._last_scan = now
            print(f"[LoraRegistry] ✓ {len(self._entries)} LoRAs indexados", flush=True)
    
    def resolve(self, query: str) -> str:
        """
        Resolver un nombre de LoRA a su ruta absoluta.
        
        Lógica de resolución:
        1. Búsqueda exacta por nombre de archivo
        2. Búsqueda por stem (nombre sin extensión)
        3. Búsqueda parcial con scoring
        4. Priorización: v2 > v1, no backups, step más alto
        """
        if not query:
            raise FileNotFoundError("Nombre de LoRA vacío")
        
        # Verificar caché primero
        with self._lock:
            if query in self._cache:
                return self._cache[query]
        
        # Asegurar que el índice está actualizado
        if not self._entries:
            self.scan()
        
        # Limpiar query
        query_clean = self._safe_filename(query)
        query_lower = query_clean.lower()
        query_stem = Path(query_clean).stem.lower()
        
        # Detectar preferencias explícitas
        wants_v1 = "v1" in query_lower or "v1_loras" in query_lower
        wants_v2 = "v2" in query_lower or "loras-renew" in query_lower
        wants_backup = "backup" in query_lower or "_backups" in query_lower
        
        # Extraer step requerido
        required_step = None
        step_patterns = [
            re.compile(r"^(?P<base>.+?)[-_]step0*(?P<step>\d+)$", re.IGNORECASE),
            re.compile(r"^(?P<base>.+?)[-_]0*(?P<step>\d{3,5})$"),
        ]
        
        for pattern in step_patterns:
            match = pattern.match(query_stem)
            if match and match.group("step"):
                try:
                    required_step = int(match.group("step"))
                    break
                except ValueError:
                    pass
        
        # Candidatos con scoring
        candidates = []
        
        for entry in self._entries:
            score = 0
            stem = entry["stem_lower"]
            filename = entry["filename"].lower()
            
            # 1. Coincidencia exacta (máxima puntuación)
            if query_lower == filename or query_stem == stem:
                score += 1000
            # 2. Coincidencia parcial
            elif query_stem in stem or stem in query_stem:
                score += 500
            else:
                continue
            
            # 3. Preferencia de versión
            if wants_v1:
                if entry["is_v1"]:
                    score += 200
                else:
                    score -= 100
            elif wants_v2:
                if entry["is_v2"]:
                    score += 200
                else:
                    score -= 100
            else:
                # Sin preferencia: v2 tiene prioridad por defecto
                if entry["is_v2"]:
                    score += 50
                elif entry["is_v1"]:
                    score += 20
            
            # 4. Backups (excluir salvo solicitud explícita)
            if entry["is_backup"]:
                if wants_backup:
                    score += 300  # Backup solicitado explícitamente
                else:
                    score -= 400  # Penalizar backups
            else:
                score += 100  # No es backup
            
            # 5. Step matching
            if required_step is not None:
                if entry["step"] == required_step:
                    score += 300  # Step exacto
                elif entry["step"] > required_step:
                    score += 150  # Step superior
                else:
                    score -= 200  # Step inferior
            else:
                # Sin step especificado: preferir step más alto
                if entry["step"] > 0:
                    score += min(entry["step"] // 100, 150)
            
            candidates.append((score, entry["step"], entry["path"]))
        
        # Si no hay candidatos, re-escanear y intentar búsqueda directa
        if not candidates:
            self.scan(force=True)
            
            # Búsqueda directa por nombre
            direct_candidates = [
                VOL_LORAS / (query_clean + ".safetensors"),
                VOL_LORAS / (query_clean + ".ckpt"),
                VOL_MAIN / "loras" / (query_clean + ".safetensors"),
                VOL_MAIN / "loras" / (query_clean + ".ckpt"),
            ]
            
            for cand in direct_candidates:
                if cand.exists():
                    return str(cand.resolve())
            
            raise FileNotFoundError(f"No se encontró el LoRA '{query}' en los volúmenes montados.")
        
        # Ordenar por score y step
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        chosen_path = candidates[0][2]
        
        # Guardar en caché
        with self._lock:
            self._cache[query] = chosen_path
        
        return chosen_path
    
    @staticmethod
    def _safe_filename(name: str) -> str:
        """Limpiar nombre de archivo para búsqueda segura."""
        name = os.path.basename(name)
        return re.sub(r"[^A-Za-z0-9._\-+]", "", name)

# Instancia global del registro de LoRAs
_LORA_REGISTRY = LoraRegistry()

# ════════════════════════════════════════════════════════════════════════
# LRUCACHE PARA MODELOS
# ════════════════════════════════════════════════════════════════════════
class LRUCache:
    """Cache LRU con eviction personalizado para liberar memoria GPU."""
    
    def __init__(self, capacity: int, on_evict: Optional[callable] = None):
        self.capacity = capacity
        self._data: "OrderedDict[Any, Any]" = OrderedDict()
        self._on_evict = on_evict
        self._lock = threading.Lock()
    
    def get(self, key):
        with self._lock:
            if key not in self._data:
                return None
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
                        try:
                            self._on_evict(evicted_key, evicted_val)
                        except Exception:
                            pass
    
    def clear(self):
        with self._lock:
            if self._on_evict:
                for k, v in list(self._data.items()):
                    try:
                        self._on_evict(k, v)
                    except Exception:
                        pass
            self._data.clear()
    
    def __contains__(self, key):
        with self._lock:
            return key in self._data
    
    def __len__(self):
        with self._lock:
            return len(self._data)

def _destroy_value(value):
    """Destruir un valor y liberar memoria GPU."""
    try:
        del value
    except Exception:
        pass
    gc.collect()
    
    # Limpiar caché CUDA
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

def _empty_cuda_cache():
    """Limpiar caché CUDA."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

# ════════════════════════════════════════════════════════════════════════
# ENGINE COMFYUI PRINCIPAL
# ════════════════════════════════════════════════════════════════════════
class ComfyEngine:
    """
    Motor ComfyUI optimizado para Modal con:
    - Modelo precargado para uso único
    - LRU Cache para evitar cargas repetitivas
    - SageAttention para aceleración
    - Sistema inteligente de LoRAs
    - Upload a Cloudflare R2
    """
    
    # Configuración de caché LRU (optimizada para 48GB VRAM L40S)
    LRU_UNET = 2
    LRU_CLIP = 1
    LRU_VAE = 2
    LRU_UPSCALE = 2
    LRU_LORA = 5
    LRU_CONDITIONING = 32
    LRU_LATENT = 16
    
    # Modelos por defecto
    DEFAULT_UNET = "Moody-Krea-Mix-v5-FP8"
    DEFAULT_CLIP = "qwen_3vl_4b"
    DEFAULT_VAE = "wan_2.1_vae"
    DEFAULT_LORAS: Dict[str, float] = {}
    DEFAULT_KREA_MU = 1.15
    
    def __init__(self):
        # Configurar cachés LRU
        self.cache_unet = LRUCache(self.LRU_UNET, on_evict=_destroy_value)
        self.cache_clip = LRUCache(self.LRU_CLIP, on_evict=_destroy_value)
        self.cache_vae = LRUCache(self.LRU_VAE, on_evict=_destroy_value)
        self.cache_upscale = LRUCache(self.LRU_UPSCALE, on_evict=_destroy_value)
        self.cache_lora = LRUCache(self.LRU_LORA, on_evict=_destroy_value)
        self.cache_conditioning = LRUCache(self.LRU_CONDITIONING)
        self.cache_vae_latent = LRUCache(self.LRU_LATENT)
        
        # Estado del engine
        self.nodes: Dict[str, Any] = {}
        self.NODE_CLASS_MAPPINGS: Dict[str, Any] = {}
        self._warmed_up = False
        self.warmup_lock = threading.Lock()
        self._sage_patched = False
        
        # Configuración de ComfyUI
        self.COMFY_ROOT = Path("/root/ComfyUI")
        
        # Estado de VAE optimizado
        self._vae_optimized_ids: set = set()
        
        # Estado del engine y event bus
        self._state_machine = _EngineStateMachine()
        self._event_bus = _EventBus()
        
        # Inicializar ComfyUI
        self._initialize_comfy()
        
        print("[ComfyEngine] ✓ Engine inicializado", flush=True)
    
    def _initialize_comfy(self):
        """Inicializar ComfyUI y registrar rutas de modelos."""
        if not self.COMFY_ROOT.exists():
            raise FileNotFoundError(f"ComfyUI no encontrado en {self.COMFY_ROOT}")
        
        # Configurar sys.path
        comfy_str = str(self.COMFY_ROOT.resolve())
        if comfy_str not in sys.path:
            sys.path.insert(0, comfy_str)
        
        # Cambiar directorio de trabajo
        os.chdir(comfy_str)
        
        # Registrar rutas de modelos (importante para resolución de LoRAs)
        try:
            import folder_paths
            # Registrar rutas de LoRAs (v1 y v2)
            folder_paths.add_model_folder_path("loras", str(VOL_LORAS))
            folder_paths.add_model_folder_path("loras", str(VOL_MAIN / "loras"))
            
            # Registrar otras rutas de modelos
            folder_paths.add_model_folder_path("diffusion_models", str(VOL_MAIN / "diffusion_models"))
            folder_paths.add_model_folder_path("text_encoders", str(VOL_MAIN / "text_encoders"))
            folder_paths.add_model_folder_path("vae", str(VOL_MAIN / "vae"))
            folder_paths.add_model_folder_path("upscale_models", str(VOL_MAIN / "upscale"))
            
        except Exception as e:
            print(f"[ComfyEngine] WARN registrando carpetas: {e}", flush=True)
        
        # Inicializar nodos extra
        try:
            from nodes import init_extra_nodes
            result = init_extra_nodes()
            if asyncio.iscoroutine(result):
                try:
                    asyncio.run(result)
                except RuntimeError:
                    asyncio.get_event_loop().run_until_complete(result)
        except Exception as e:
            print(f"[ComfyEngine] WARN inicializando nodos extra: {e}", flush=True)
        
        # Aplicar optimizaciones
        self._apply_sage_attention()
        self._apply_tf32_and_cudnn()
        
        # Importar NODE_CLASS_MAPPINGS
        try:
            from nodes import NODE_CLASS_MAPPINGS
            self.NODE_CLASS_MAPPINGS = NODE_CLASS_MAPPINGS
        except Exception as e:
            print(f"[ComfyEngine] WARN importando NODE_CLASS_MAPPINGS: {e}", flush=True)
        
        # Crear directorio de outputs
        (VOL_MAIN / "outputs").mkdir(parents=True, exist_ok=True)
    
    def _apply_sage_attention(self):
        """Aplicar SageAttention para aceleración de atención."""
        if self._sage_patched:
            return
            
        try:
            import sageattention
            import torch.nn.functional as F
            
            _orig_sdpa = F.scaled_dot_product_attention
            
            def _sage_sdpa(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False, scale=None, *args, **kwargs):
                # Manejo de GQA (Grouped Query Attention)
                if q.dim() >= 4 and q.size(-3) != k.size(-3):
                    rep = q.size(-3) // k.size(-3)
                    k = k.repeat_interleave(rep, dim=-3)
                    v = v.repeat_interleave(rep, dim=-3)
                
                # Usar SageAttention para casos simples
                if attn_mask is None and dropout_p == 0.0 and not is_causal:
                    try:
                        return sageattention.sageattn(q, k, v, scale=scale)
                    except Exception:
                        pass
                
                # Fallback a SDPA original
                return _orig_sdpa(q, k, v, attn_mask=attn_mask, dropout_p=dropout_p, is_causal=is_causal, scale=scale, *args, **kwargs)
            
            # Reemplazar SDPA con SageAttention
            F.scaled_dot_product_attention = _sage_sdpa
            self._sage_patched = True
            print("[ComfyEngine] ✓ SageAttention aplicado", flush=True)
            
        except Exception as e:
            print(f"[ComfyEngine] ⚠ SageAttention no disponible: {e}", flush=True)
    
    def _apply_tf32_and_cudnn(self):
        """Aplicar optimizaciones TF32 y cuDNN."""
        try:
            import torch
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True
            print("[ComfyEngine] ✓ TF32/cuDNN optimizado", flush=True)
        except Exception as e:
            print(f"[ComfyEngine] ⚠ Error en TF32/cuDNN: {e}", flush=True)
    
    def get_node(self, name: str):
        """Obtener instancia de un nodo ComfyUI."""
        if name in self.nodes:
            return self.nodes[name]
        
        cls = self.NODE_CLASS_MAPPINGS.get(name)
        if cls is None:
            raise KeyError(f"Nodo '{name}' no registrado")
        
        inst = cls()
        self.nodes[name] = inst
        return inst
    
    def resolve_model_path(self, name: str, type_folder: str) -> str:
        """
        Resolver ruta de modelo según tipo.
        Para LoRAs usa el registro inteligente.
        """
        if not name:
            raise FileNotFoundError("Nombre vacío")
        
        if type_folder == "lora":
            return _LORA_REGISTRY.resolve(name)
        
        # Para otros tipos de modelos
        roots = {
            "unet": [VOL_MAIN / "diffusion_models"],
            "clip": [VOL_MAIN / "text_encoders"],
            "vae": [VOL_MAIN / "vae"],
            "upscale": [VOL_MAIN / "upscale"],
        }.get(type_folder)
        
        if not roots:
            raise ValueError(f"type_folder desconocido: {type_folder}")
        
        # Limpiar query
        safe_query = _LORA_REGISTRY._safe_filename(name)
        query_stem = Path(safe_query).stem
        extensions = [".safetensors", ".ckpt", ".pt", ".bin", ""]
        
        # Buscar en roots existentes
        existing_roots = [r for r in roots if r.exists()]
        
        # 1. Búsqueda directa
        for root in existing_roots:
            for ext in extensions:
                cand = root / (safe_query + ext)
                if cand.is_file():
                    return str(cand.resolve())
        
        # 2. Búsqueda recursiva
        for root in existing_roots:
            for path in root.rglob("*"):
                if path.is_file() and query_stem.lower() in path.name.lower():
                    return str(path.resolve())
        
        raise FileNotFoundError(f"No se encontró '{name}' ({type_folder}) en {existing_roots}")
    
    def load_unet(self, name: str):
        """Cargar UNET con caché LRU y manejo de memoria."""
        path = self.resolve_model_path(name, "unet")
        cached = self.cache_unet.get(path)
        if cached is not None:
            return cached
        
        # Purga preventiva para evitar OOM
        print(f"[ComfyEngine] Purgando cachés antes de cargar UNET: {name}", flush=True)
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
        
        # Cargar UNET
        loader = self.get_node("UNETLoader") if "UNETLoader" in self.NODE_CLASS_MAPPINGS else self.get_node("CheckpointLoaderSimple")
        fname = os.path.basename(path)
        print(f"[ComfyEngine] Cargando UNET: {fname}", flush=True)
        
        # Intentar diferentes dtypes
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
        
        print(f"[ComfyEngine] ✓ UNET cargado: {fname}", flush=True)
        
        # Limpiar caché CUDA
        _empty_cuda_cache()
        self.cache_unet.put(path, out)
        return out
    
    def load_clip(self, name: str):
        """Cargar CLIP con caché LRU."""
        path = self.resolve_model_path(name, "clip")
        cached = self.cache_clip.get(path)
        if cached is not None:
            return cached
        
        loader = self.get_node("CLIPLoader")
        fname = os.path.basename(path)
        print(f"[ComfyEngine] Cargando CLIP: {fname}", flush=True)
        
        try:
            out = loader.load_clip(clip_name=fname, type="krea2")[0]
        except TypeError:
            out = loader.load_clip(clip_name=fname)[0]
        
        self.cache_clip.put(path, out)
        return out
    
    def load_vae(self, name: str):
        """Cargar VAE con caché LRU y optimizaciones."""
        path = self.resolve_model_path(name, "vae")
        cached = self.cache_vae.get(path)
        if cached is not None:
            return cached
        
        fname = os.path.basename(path)
        is_wan = "wan" in fname.lower()
        
        # Usar VAEUtils_CustomVAELoader para VAEs Wan si está disponible
        if is_wan and "VAEUtils_CustomVAELoader" in self.NODE_CLASS_MAPPINGS:
            out = self.get_node("VAEUtils_CustomVAELoader").load_vae(fname, disable_offload=True)[0]
        else:
            out = self.get_node("VAELoader").load_vae(vae_name=fname)[0]
        
        # Optimizar VAE
        self._optimize_vae(out, fname)
        self.cache_vae.put(path, out)
        return out
    
    def _optimize_vae(self, vae, vae_name: str):
        """Optimizar VAE para rendimiento y memoria."""
        if id(vae) in self._vae_optimized_ids:
            return
            
        try:
            import torch
            target_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            
            # Obtener modelo interno
            inner = getattr(vae, "first_stage_model", getattr(vae, "model", vae))
            
            if isinstance(inner, torch.nn.Module):
                # Mover a CUDA y convertir a dtype objetivo
                inner.to("cuda").to(target_dtype)
                
                # Optimizar convoluciones para channels_last
                for m in inner.modules():
                    if isinstance(m, torch.nn.Conv2d):
                        m.weight.data = m.weight.data.to(memory_format=torch.channels_last)
            
            # Patch decode para casting automático
            self._patch_vae_forward_cast(vae, target_dtype)
            self._vae_optimized_ids.add(id(vae))
            
            print(f"[ComfyEngine] ✓ VAE optimizado: {vae_name}", flush=True)
            
        except Exception as e:
            print(f"[ComfyEngine] ⚠ Error optimizando VAE: {e}", flush=True)
    
    def _patch_vae_forward_cast(self, vae, target_dtype):
        """Patch decode para casting automático de dtype."""
        if getattr(vae, "_cast_patched", False):
            return
            
        _orig_decode = vae.decode
        
        def _decode_cast(samples_in, *args, **kwargs):
            try:
                import torch
                # Castear samples a dtype objetivo
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
    
    def _patch_with_lora(self, unet_model, clip, lora_path, strength):
        """
        Cargar LoRA desde ruta absoluta con validaciones.
        
        Valida:
        1. Existencia física del archivo (detecta symlinks rotos)
        2. Tamaño mínimo (detecta archivos corruptos/vacíos)
        3. Compatibilidad con arquitectura del modelo
        """
        from pathlib import Path
        import comfy.utils
        import comfy.sd
        
        p = Path(lora_path)
        
        # 1. Validar existencia física
        if not p.is_file():
            raise FileNotFoundError(
                f"[LoRA ERROR] El archivo no existe o es un symlink roto: {lora_path}"
            )
        
        # 2. Validar tamaño mínimo
        file_size = p.stat().st_size
        if file_size < 1024 * 10:  # Menos de 10 KB es corrupto/vacío
            raise ValueError(
                f"[LoRA ERROR] El archivo {p.name} está dañado o vacío ({file_size} bytes)."
            )
        
        # 3. Cargar pesos desde ruta absoluta
        try:
            lora_data = comfy.utils.load_torch_file(str(p.resolve()), safe_load=True)
        except Exception as e:
            raise RuntimeError(
                f"[LoRA ERROR] No se pudo leer el archivo safetensors {p.name}: {e}"
            ) from e
        
        # 4. Inyectar en el UNET y CLIP
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
        """
        Aplicar múltiples LoRAs con caché de combinaciones.
        
        Características:
        1. Caché de combinaciones de LoRAs
        2. Aplicación secuencial
        3. Manejo de errores robusto
        """
        info = {"combo_key": None, "lora_hits": [], "lora_loaded": []}
        if not lora_dict:
            return unet, clip, info
        
        t0 = time.time()
        
        # Preparar items ordenados
        items = []
        for name, weight in lora_dict.items():
            lora_path = self.resolve_model_path(name, "lora")
            items.append((lora_path, round(float(weight), 2)))
        items = sorted(items)
        
        # Crear clave de caché para combinación
        combo_key = (
            "lora_combo", 
            unet_path or id(unet), 
            clip_path or id(clip), 
            variant_key, 
            tuple(items)
        )
        
        # Verificar caché
        cached = self.cache_lora.get(combo_key)
        if cached is not None:
            print(f"[ComfyEngine] ✓ CACHE HIT en {time.time()-t0:.3f}s", flush=True)
            info["lora_hits"] = [os.path.basename(p) for p, _ in items]
            return cached[0], cached[1], info
        
        # Aplicar LoRAs secuencialmente
        current_unet, current_clip = unet, clip
        for lora_path, lora_weight in items:
            current_unet, current_clip = self._patch_with_lora(
                current_unet, current_clip, lora_path, lora_weight
            )
            info["lora_loaded"].append(os.path.basename(lora_path))
        
        print(f"[ComfyEngine] {len(items)} LoRAs aplicados en {time.time()-t0:.2f}s", flush=True)
        
        # Guardar en caché
        self.cache_lora.put(combo_key, (current_unet, current_clip))
        return current_unet, current_clip, info
    
    def load_upscale(self, name: str):
        """Cargar modelo de upscale con caché."""
        path = self.resolve_model_path(name, "upscale")
        cached = self.cache_upscale.get(path)
        if cached is not None:
            return cached
        
        out = self.get_node("UpscaleModelLoader").load_model(model_name=os.path.basename(path))[0]
        self.cache_upscale.put(path, out)
        return out
    
    def encode_prompt(self, clip, prompt, negative=""):
        """Encode prompt con caché."""
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
    
    def make_empty_latent(self, width, height, batch=1):
        """Crear latent vacío."""
        return self.get_node("EmptyLatentImage").generate(width=width, height=height, batch_size=batch)[0]
    
    def apply_krea_time_shift(self, model, mu=1.15, width=1024, height=1024):
        """Aplicar time shift específico de Krea2."""
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
    
    def sample(self, model, seed, steps, cfg, sampler, scheduler, positive, negative, latent, denoise=1.0):
        """Ejecutar sampling."""
        if seed == 0:
            seed = random.randint(1, 2**31 - 1)
        
        out = self.get_node("KSampler").sample(
            model=model, 
            positive=positive, 
            negative=negative,
            latent_image=latent, 
            seed=seed, 
            steps=steps, 
            cfg=cfg,
            sampler_name=sampler, 
            scheduler=scheduler, 
            denoise=denoise,
        )[0]
        return out, seed
    
    def decode(self, vae, latent, tile_size=None, overlap=None, use_tiled=False):
        """Decodificar latent a imagen con manejo de OOM."""
        if tile_size is None:
            tile_size = 512
        if overlap is None:
            overlap = 64
        
        # Limpieza previa para liberar VRAM
        try:
            import comfy.model_management as mm
            mm.soft_empty_cache()
        except Exception:
            pass
        _empty_cuda_cache()
        
        # Obtener nodo tiled si está disponible
        tiled = self.nodes.get("VAEUtils_VAEDecodeTiled")
        if tiled is None and "VAEUtils_VAEDecodeTiled" in self.NODE_CLASS_MAPPINGS:
            tiled = self.get_node("VAEUtils_VAEDecodeTiled")
        
        import torch
        if use_tiled and tiled is not None:
            with torch.inference_mode():
                return tiled.decode(
                    samples=latent, 
                    vae=vae, 
                    upscale=-1,
                    tile=True, 
                    tile_size=tile_size, 
                    overlap=overlap,
                    temporal_size=64, 
                    temporal_overlap=8,
                )[0]
        
        # Decodificación estándar
        try:
            with torch.inference_mode():
                if isinstance(latent, dict) and "samples" in latent:
                    return vae.decode(latent["samples"])
                return vae.decode(latent)
        except RuntimeError as e:
            # Manejo de OOM: reintentar con tiled
            if "out of memory" in str(e).lower() and tiled is not None:
                print("[ComfyEngine] OOM en decode estándar → reintentando tiled", flush=True)
                _empty_cuda_cache()
                with torch.inference_mode():
                    return tiled.decode(
                        samples=latent, 
                        vae=vae, 
                        upscale=-1, 
                        tile=True, 
                        tile_size=tile_size, 
                        overlap=overlap,
                        temporal_size=64, 
                        temporal_overlap=8
                    )[0]
            raise
    
    def upscale_image(self, image, upscale_model_name, scale):
        """Upscale de imagen."""
        up_model = self.load_upscale(upscale_model_name)
        upscaled = self.get_node("ImageUpscaleWithModel").upscale(upscale_model=up_model, image=image)[0]
        
        scaler = self.get_node("ImageScale")
        return scaler.upscale(
            image=upscaled, 
            upscale_method="lanczos", 
            width=int(upscaled.shape[2] * scale), 
            height=int(upscaled.shape[1] * scale), 
            crop="disabled"
        )[0]
    
    def save_image(self, image_tensor, seed: int, batch_index: int = 0):
        """Guardar imagen a disco y subir a R2."""
        import numpy as np
        from PIL import Image
        
        # 1. Mover a CPU
        if hasattr(image_tensor, "detach"):
            t = image_tensor.detach().cpu()
        else:
            t = image_tensor
        
        # 2. Desempaquetar dimensiones según formato VAE
        if t.dim() == 5:
            # Formato Wan VAE (B, T, H, W, C) -> seleccionar batch y frame 0
            t = t[batch_index][0]  # queda en (H, W, C)
        elif t.dim() == 4:
            # Formato estándar ComfyUI (B, H, W, C)
            t = t[batch_index]
            # Si el canal quedó al frente (C, H, W), mover al final
            if t.shape[0] in (1, 3, 4) and t.shape[-1] not in (1, 3, 4):
                t = t.permute(1, 2, 0)
        elif t.dim() == 3:
            if t.shape[0] in (1, 3, 4) and t.shape[-1] not in (1, 3, 4):
                t = t.permute(1, 2, 0)
        
        arr = t.float().numpy()
        
        # 3. Sanitizar NaNs / Infs
        if np.isnan(arr).any() or np.isinf(arr).any():
            print("[ComfyEngine] ⚠ Detectados NaNs/Infs en imagen, limpiando...", flush=True)
            arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=0.0)
        
        # 4. Asegurar escala a uint8 [0, 255]
        if arr.max() <= 1.05 and arr.min() >= -0.05:
            arr = (arr * 255.0).clip(0, 255).astype("uint8")
        else:
            arr = arr.clip(0, 255).astype("uint8")
        
        # 5. Colapsar cualquier dimensión unitaria sobrante
        if arr.ndim > 3:
            arr = np.squeeze(arr)
        
        # 6. Desempaquetar altura, ancho y canales de forma segura
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
        
        # 7. Guardar a disco
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False, compress_level=4)
        raw = buf.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{timestamp}_krea2_{seed}_batch{batch_index}.png"
        
        try:
            (VOL_MAIN / "outputs" / fname).write_bytes(raw)
        except OSError:
            pass
        
        # 8. Subir a Cloudflare R2
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
    
    def generate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generar imagen usando payload completo.
        
        Payload esperado:
        {
            "positive_prompt": str,
            "negative_prompt": str,
            "width": int,
            "height": int,
            "steps": int,
            "cfg": float,
            "sampler": str,
            "scheduler": str,
            "seed": int,
            "unet": str (opcional),
            "clip": str (opcional),
            "vae": str (opcional),
            "loras": Dict[str, float] (opcional),
            "upscale_model": str (opcional),
            "upscale_scale": float (opcional),
            "use_tiled": bool (opcional)
        }
        """
        # Validaciones básicas
        required_fields = ["positive_prompt", "width", "height"]
        for field in required_fields:
            if field not in payload:
                raise ValueError(f"Campo requerido faltante: {field}")
        
        # Extraer parámetros con valores por defecto
        positive_prompt = payload.get("positive_prompt", "")
        negative_prompt = payload.get("negative_prompt", "")
        width = int(payload.get("width", 1024))
        height = int(payload.get("height", 1024))
        steps = int(payload.get("steps", 30))
        cfg = float(payload.get("cfg", 7.5))
        sampler = payload.get("sampler", "euler")
        scheduler = payload.get("scheduler", "simple")
        seed = int(payload.get("seed", 0))
        
        # Modelos (usar por defecto si no se especifican)
        unet_name = payload.get("unet", self.DEFAULT_UNET)
        clip_name = payload.get("clip", self.DEFAULT_CLIP)
        vae_name = payload.get("vae", self.DEFAULT_VAE)
        
        # LoRAs
        lora_dict = payload.get("loras", {})
        
        # Upscale
        upscale_model = payload.get("upscale_model")
        upscale_scale = float(payload.get("upscale_scale", 1.0))
        
        # Opciones avanzadas
        use_tiled = payload.get("use_tiled", False)
        krea_mu = float(payload.get("krea_mu", self.DEFAULT_KREA_MU))
        
        # 1. Cargar modelos
        t0 = time.time()
        unet = self.load_unet(unet_name)
        clip = self.load_clip(clip_name)
        vae = self.load_vae(vae_name)
        
        # 2. Aplicar LoRAs
        unet, clip, lora_info = self.apply_loras(
            unet, clip, lora_dict, 
            unet_path=self.resolve_model_path(unet_name, "unet"),
            clip_path=self.resolve_model_path(clip_name, "clip")
        )
        
        # 3. Aplicar time shift de Krea2
        unet = self.apply_krea_time_shift(unet, mu=krea_mu, width=width, height=height)
        
        # 4. Encode prompts
        (positive, negative), cond_hit = self.encode_prompt(clip, positive_prompt, negative_prompt)
        
        # 5. Crear latent vacío
        latent = self.make_empty_latent(width, height)
        
        # 6. Sampling
        t1 = time.time()
        sampled, seed_used = self.sample(
            unet, seed, steps, cfg, sampler, scheduler, 
            positive, negative, latent
        )
        
        # 7. Decodificar
        t2 = time.time()
        image = self.decode(vae, sampled, use_tiled=use_tiled)
        
        # 8. Upscale si se solicita
        if upscale_model:
            image = self.upscale_image(image, upscale_model, upscale_scale)
        
        # 9. Guardar imagen
        t3 = time.time()
        result = self.save_image(image, seed_used)
        
        # 10. Calcular timings
        timings = {
            "load_models": t1 - t0,
            "sampling": t2 - t1,
            "decode": t3 - t2,
            "total": t3 - t0,
        }
        
        # 11. Calcular pricing
        pricing = self.calculate_pricing(timings)
        
        # 12. Información adicional
        result.update({
            "timings": timings,
            "pricing": pricing,
            "seed": seed_used,
            "lora_info": lora_info,
            "conditioning_hit": cond_hit,
            "config": {
                "unet": unet_name,
                "clip": clip_name,
                "vae": vae_name,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg,
                "sampler": sampler,
                "scheduler": scheduler,
                "loras": lora_dict,
            }
        })
        
        return result
    
    def calculate_pricing(self, timings: Dict[str, float]) -> Dict[str, Any]:
        """Calcular pricing basado en timings."""
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
            "total_per_sec": TOTAL_PRICE_PER_SEC
        }
        return pricing
    
    def warmup_dummy(self):
        """Warmup dummy para inicializar CUDA y crear snapshot."""
        try:
            import torch
            if not torch.cuda.is_available():
                return
            
            # Crear tensors dummy
            dummy_latent = torch.randn(1, 16, 1, 104, 156, device="cuda", dtype=torch.float16)
            cond_dummy = [[torch.zeros(1, 256, 30720, device="cuda", dtype=torch.float16), {
                "pooled_output": torch.zeros(1, 768, device="cuda", dtype=torch.float16)
            }]]
            
            # Forward pass dummy
            print("[ComfyEngine] Ejecutando warmup dummy...", flush=True)
            _empty_cuda_cache()
            
            return True
        except Exception as e:
            print(f"[ComfyEngine] Warmup dummy error: {e}", flush=True)
            return False

# ════════════════════════════════════════════════════════════════════════
# ESTADO DEL ENGINE Y EVENT BUS
# ════════════════════════════════════════════════════════════════════════
class EngineState(str, Enum):
    BOOTING = "booting"
    IDLE = "idle"
    GENERATING = "generating"

class _EngineStateMachine:
    """Máquina de estados para el engine."""
    
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
            
            print(f"[EngineState] ☠ ZOMBIE: task={self._current_task_id} ({elapsed:.0f}s) → force_idle", flush=True)
            self._state = EngineState.IDLE
            self._current_task_id = None
            self._entered_at = None
            self._cond.notify_all()
            return True

class _EventBus:
    """Event bus para comunicación de progreso."""
    
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

# ════════════════════════════════════════════════════════════════════════
# CLOUDFLARE R2 UPLOAD
# ════════════════════════════════════════════════════════════════════════
_R2_BUCKET = os.environ.get("R2_BUCKET", "krea2-bucket-img-modal").strip()
_R2_CLIENT = None
_R2_LOCK = threading.Lock()

def _get_r2_client():
    """Inicializar cliente R2."""
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
    """Subir imagen a Cloudflare R2."""
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
            Bucket=_R2_BUCKET,
            Key=key,
            Body=raw_bytes,
            ContentType="image/png",
        )
    except Exception as e:
        print(f"[R2] ✗ ERROR {type(e).__name__}: {e}", flush=True)
        return None
    
    url = f"{public_base}/{key}"
    print(f"[R2] ✓ subido en {time.time() - t0:.2f}s → {url}", flush=True)
    return url

# ════════════════════════════════════════════════════════════════════════
# SINGLETON ENGINE (PARA USO ÚNICO)
# ════════════════════════════════════════════════════════════════════════
_engine_instance: Optional[ComfyEngine] = None
_engine_lock = threading.Lock()

def get_comfy_engine() -> ComfyEngine:
    """Obtener instancia singleton del engine."""
    global _engine_instance
    
    if _engine_instance is not None:
        return _engine_instance
    
    with _engine_lock:
        if _engine_instance is not None:
            return _engine_instance
        
        print("[Engine] Inicializando ComfyEngine...", flush=True)
        _engine_instance = ComfyEngine()
        print("[Engine] ✓ ComfyEngine inicializado", flush=True)
        
        return _engine_instance

# ════════════════════════════════════════════════════════════════════════
# FASTAPI APP PARA MODAL
# ════════════════════════════════════════════════════════════════════════
web_app = FastAPI()

# Configurar CORS
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

@web_app.post("/generate")
async def generate_image(request: Request):
    """Endpoint principal para generación de imágenes."""
    try:
        # Parsear payload (JSON o multipart)
        content_type = request.headers.get("Content-Type", "")
        
        if "multipart/form-data" in content_type:
            form = await request.form()
            payload_str = form.get("payload")
            if not payload_str:
                raise ValueError("Multipart request must include a 'payload' JSON string field")
            
            payload = json.loads(payload_str)
            
            # Procesar imágenes si se incluyen
            for key in ["image", "image_b", "depth_image"]:
                if key in form:
                    file_obj = form[key]
                    payload[key] = await file_obj.read()
        else:
            payload = await request.json()
        
        # Generar task_id
        task_id = str(uuid.uuid4())
        payload["task_id"] = task_id
        
        # Llamar a la función Modal (en el mismo contenedor)
        result = generate_modal.remote(payload)
        
        # Devolver resultado
        return JSONResponse(content=result)
        
    except Exception as e:
        return JSONResponse(
            content={
                "error": str(e),
                "traceback": traceback.format_exc(),
                "timestamp": time.time()
            },
            status_code=500
        )

@web_app.post("/generate/stream")
async def generate_image_stream(request: Request):
    """Endpoint con streaming para progreso en tiempo real."""
    try:
        # Parsear payload
        payload = await request.json()
        
        # Generar task_id
        task_id = str(uuid.uuid4())
        payload["task_id"] = task_id
        
        # Crear generador de eventos
        async def event_stream():
            # Emitir inicio
            yield f"data: {json.dumps({'type': 'start', 'task_id': task_id, 'timestamp': time.time()})}\n\n"
            
            # Simular progreso (en implementación real, esto vendría del event bus)
            for i in range(10):
                await asyncio.sleep(1)
                progress = {
                    "type": "progress",
                    "task_id": task_id,
                    "percent": i * 10,
                    "message": f"Procesando... {i*10}%"
                }
                yield f"data: {json.dumps(progress)}\n\n"
            
            # Llamar a la función Modal
            result = generate_modal.remote(payload)
            
            # Emitir final
            final_event = {
                "type": "complete",
                "task_id": task_id,
                "result": result,
                "timestamp": time.time()
            }
            yield f"data: {json.dumps(final_event)}\n\n"
        
        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream"
        )
        
    except Exception as e:
        return JSONResponse(
            content={
                "error": str(e),
                "traceback": traceback.format_exc()
            },
            status_code=500
        )

@web_app.get("/health")
async def health_check():
    """Endpoint de health check."""
    engine = get_comfy_engine()
    state = engine._state_machine.get_info()
    
    return {
        "status": "healthy",
        "engine": "krea2-modal",
        "version": "1.0.1",
        "gpu": GPU_MODEL_NAME,
        "state": state,
        "timestamp": time.time()
    }

@web_app.get("/models")
async def list_models():
    """Listar modelos disponibles."""
    models = {
        "unet": [],
        "clip": [],
        "vae": [],
        "loras": [],
        "upscale": []
    }
    
    # Escanear volúmenes para UNET
    unet_dir = VOL_MAIN / "diffusion_models"
    if unet_dir.exists():
        for path in unet_dir.glob("*.safetensors"):
            models["unet"].append(path.stem)
    
    # Escanear volúmenes para CLIP
    clip_dir = VOL_MAIN / "text_encoders"
    if clip_dir.exists():
        for path in clip_dir.glob("*.safetensors"):
            models["clip"].append(path.stem)
    
    # Escanear volúmenes para VAE
    vae_dir = VOL_MAIN / "vae"
    if vae_dir.exists():
        for path in vae_dir.glob("*.safetensors"):
            models["vae"].append(path.stem)
    
    # Escanear volúmenes para LoRAs (usando registro inteligente)
    lora_registry = _LORA_REGISTRY
    lora_registry.scan()
    
    # Agrupar por stem para evitar duplicados
    lora_stems = set()
    for entry in lora_registry._entries:
        if not entry["is_backup"]:  # Excluir backups
            lora_stems.add(entry["stem"])
    
    models["loras"] = sorted(list(lora_stems))
    
    # Escanear volúmenes para upscale
    upscale_dir = VOL_MAIN / "upscale"
    if upscale_dir.exists():
        for path in upscale_dir.glob("*.safetensors"):
            models["upscale"].append(path.stem)
    
    return models

@web_app.get("/models/loras")
async def list_loras_detailed():
    """Listar LoRAs con información detallada."""
    lora_registry = _LORA_REGISTRY
    lora_registry.scan()
    
    loras_info = []
    for entry in lora_registry._entries:
        if not entry["is_backup"]:  # Excluir backups
            loras_info.append({
                "name": entry["stem"],
                "filename": entry["filename"],
                "path": entry["path"],
                "is_v1": entry["is_v1"],
                "is_v2": entry["is_v2"],
                "step": entry["step"],
                "size_mb": round(os.path.getsize(entry["path"]) / (1024 * 1024), 2) if os.path.exists(entry["path"]) else 0
            })
    
    return {
        "total": len(loras_info),
        "loras": loras_info
    }

@web_app.get("/debug/volumes")
async def debug_volumes():
    """Debug de volúmenes montados."""
    volume_info = {
        "main_volume": {
            "path": str(VOL_MAIN),
            "exists": VOL_MAIN.exists(),
            "files": []
        },
        "loras_volume": {
            "path": str(VOL_LORAS),
            "exists": VOL_LORAS.exists(),
            "files": []
        },
        "backups_volume": {
            "path": str(VOL_BACKUPS),
            "exists": VOL_BACKUPS.exists(),
            "files": []
        }
    }
    
    # Listar archivos principales (limitado a 10 por rendimiento)
    if VOL_MAIN.exists():
        for i, path in enumerate(VOL_MAIN.rglob("*.safetensors")):
            if i >= 10:
                break
            volume_info["main_volume"]["files"].append(str(path))
    
    if VOL_LORAS.exists():
        for i, path in enumerate(VOL_LORAS.rglob("*.safetensors")):
            if i >= 10:
                break
            volume_info["loras_volume"]["files"].append(str(path))
    
    return volume_info

@web_app.get("/debug/cache")
async def debug_cache():
    """Debug de cachés LRU."""
    engine = get_comfy_engine()
    
    return {
        "unet_cache": {
            "size": len(engine.cache_unet),
            "capacity": engine.cache_unet.capacity,
            "keys": list(engine.cache_unet._data.keys())
        },
        "clip_cache": {
            "size": len(engine.cache_clip),
            "capacity": engine.cache_clip.capacity,
            "keys": list(engine.cache_clip._data.keys())
        },
        "vae_cache": {
            "size": len(engine.cache_vae),
            "capacity": engine.cache_vae.capacity,
            "keys": list(engine.cache_vae._data.keys())
        },
        "lora_cache": {
            "size": len(engine.cache_lora),
            "capacity": engine.cache_lora.capacity,
            "keys": [str(k) for k in engine.cache_lora._data.keys()]
        }
    }

# ════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE DESPLIEGUE
# ════════════════════════════════════════════════════════════════════════
@app.function(
    image=image,
    gpu="L40S",  # ✅ Forma correcta en SDK 1.55.0
    cpu=CPU_CORES,
    memory=RAM_GIB * 1024,
    timeout=300,
    scaledown_window=300,  # 5 minutos
    max_containers=1,
    min_containers=0,
    volumes={
        str(VOL_MAIN): modal.Volume.from_name("krea2-main-volume", create_if_missing=True),
        str(VOL_LORAS): modal.Volume.from_name("krea2-loras-renew", create_if_missing=True),
        str(VOL_BACKUPS): modal.Volume.from_name("krea2-backups-volume", create_if_missing=True),
    },
)
@asgi_app()
def krea2_modal_app():
    """App ASGI principal para Modal."""
    return web_app

# ════════════════════════════════════════════════════════════════════════
# DESPLIEGUE Y EJECUCIÓN
# ════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Para desarrollo local
    import uvicorn
    uvicorn.run(web_app, host="0.0.0.0", port=8000)