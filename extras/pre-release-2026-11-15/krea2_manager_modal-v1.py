"""
krea2_manager_modal-v1.py — v1.1.0
Servicio CPU-only de gestión de modelos, descargas de alta velocidad e historial para Modal.
Soporte nativo para segregación v1/v2 de LoRAs personalizados en krea2-loras-renew.

v1.1.0:
  - Ignora carpetas loras_v1, caches, krea-2-raw-diffusers, logs, _backups.
  - Solo acepta .safetensors para LoRAs.
  - Colapsa steps en variantes: {character} y {character}-{num_steps}.
  - JSON rediseñado para web-UI: agrupado en 'characters' y 'downloaded'.

Deploy:
    modal deploy krea2_manager_modal-v1.py
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
import zipfile
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

import modal

# ═══════════════════════════════════════════════════════════════════════════════
# ENV VARS + TUNABLES
# ═══════════════════════════════════════════════════════════════════════════════

os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")

DOWNLOAD_CHUNK_SIZE = int(os.environ.get("KREA2_CHUNK_SIZE", str(1024 * 1024)))
DOWNLOAD_LOG_EVERY_MB = float(os.environ.get("KREA2_LOG_EVERY_MB", "50"))
DOWNLOAD_LOG_EVERY_SEC = float(os.environ.get("KREA2_LOG_EVERY_SEC", "3"))
RESUME_MIN_SIZE = int(os.environ.get("KREA2_RESUME_MIN", str(5 * 1024 * 1024)))

_downloads_sem = threading.BoundedSemaphore(
    int(os.environ.get("KREA2_MAX_CONCURRENT_DOWNLOADS", "3"))
)

LIST_ALL_CACHE_TTL = int(os.environ.get("KREA2_CACHE_TTL", "300"))
MAX_TRACKED_TASKS = int(os.environ.get("KREA2_MAX_TASKS", "100"))

CIVITAI_USER_AGENT = os.environ.get(
    "KREA2_USER_AGENT",
    "Mozilla/5.0 (compatible; Krea2Manager/1.1; +https://krea2.example.com)",
)

ANYIO_THREAD_LIMIT = int(os.environ.get("KREA2_THREADPOOL_LIMIT", "200"))
os.environ.setdefault("ANYIO_DEFAULT_THREAD_LIMIT", str(ANYIO_THREAD_LIMIT))

SERVICE_VERSION = "1.1.0"

# ═══════════════════════════════════════════════════════════════════════════════
# MODAL CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

APP_NAME = "krea2-manager-cpu-v1-1-0"
app = modal.App(APP_NAME)

IMAGE = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install(
        "fastapi", "uvicorn[standard]", "python-multipart",
        "requests", "huggingface_hub[hf-transfer]", "hf_transfer",
        "safetensors", "boto3",
    )
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "PYTHONUNBUFFERED": "1",
        "KREA2_CHUNK_SIZE": "1048576",
        "KREA2_MAX_CONCURRENT_DOWNLOADS": "3",
        "KREA2_CACHE_TTL": "300",
        "KREA2_THREADPOOL_LIMIT": "200",
    })
)

VOL_MAIN = modal.Volume.from_name("krea2-volume")
VOL_LORAS = modal.Volume.from_name("krea2-lora")

VOLUMES = {
    "/krea2-beam-volume": VOL_MAIN,
    "/krea2-loras-renew": VOL_LORAS,
}

# Secretos existentes en tu workspace de Modal
R2_SECRET = modal.Secret.from_name("r2-secrets")
CIVITAI_SECRET = modal.Secret.from_name("civitai-apikey")
HF_SECRET = modal.Secret.from_name("huggingface-secret")

# ═══════════════════════════════════════════════════════════════════════════════
# SECRETS & PRICING
# ═══════════════════════════════════════════════════════════════════════════════

def _get_civitai_key() -> str:
    return os.environ.get("CIVITAI_API_KEY", "").strip()

def _get_hf_token() -> str:
    return os.environ.get("HF_TOKEN", "").strip()

CPU_PRICE_PER_SEC_PER_CORE = float(os.environ.get("BEAM_CPU_PRICE_PER_SEC", "0.0000125"))
RAM_PRICE_PER_SEC_PER_GIB = float(os.environ.get("BEAM_RAM_PRICE_PER_SEC", "0.0000021"))
CPU_CORES = 0.75
RAM_GIB = 2
TOTAL_PRICE_PER_SEC = (CPU_PRICE_PER_SEC_PER_CORE * CPU_CORES) + (RAM_PRICE_PER_SEC_PER_GIB * RAM_GIB)

# ═══════════════════════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════════════════════

VOL_MAIN_PATH = Path("/krea2-beam-volume")
VOL_LORAS_PATH = Path("/krea2-loras-renew")

DIR_DIFFUSION = VOL_MAIN_PATH / "diffusion_models"
DIR_LORAS_MAIN = VOL_MAIN_PATH / "loras"
DIR_TEXT_ENCODERS = VOL_MAIN_PATH / "text_encoders"
DIR_UPSCALE = VOL_MAIN_PATH / "upscale"
DIR_VAE = VOL_MAIN_PATH / "vae"
DIR_OUTPUTS = VOL_MAIN_PATH / "outputs"
DIR_ARCHIVED = VOL_MAIN_PATH / "archived"
DIR_MANAGER = VOL_MAIN_PATH / "_manager"
DIR_LOGS = DIR_MANAGER / "logs"
DIR_CACHE = DIR_MANAGER / "cache"
DIR_TEMP = Path("/tmp/krea2_manager")

HISTORY_FILE = DIR_MANAGER / "history.json"
HISTORY_MAX_ENTRIES = 5000
CACHE_FILE = DIR_CACHE / "list_all.json"
CACHE_META_FILE = DIR_CACHE / "list_all.meta.json"

_MODEL_EXTS: Set[str] = {".safetensors", ".ckpt", ".pt", ".bin", ".gguf"}
_LORA_EXTS: Set[str] = {".safetensors"}
_IMAGE_EXTS: Set[str] = {".png", ".jpg", ".jpeg", ".webp"}
_TARGET_MAP: Dict[str, Path] = {}

# Carpetas ignoradas al escanear LoRAs
_EXCLUDED_LORA_DIRS: Set[str] = {
    "loras_v1", "loras-v1", "v1_loras", "v1",
    "caches", "cache",
    "krea-2-raw-diffusers", "_backups", "save_lora", "logs",
}

# ═══════════════════════════════════════════════════════════════════════════════
# UTILIDADES PURAS
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_filename(name: str) -> str:
    name = os.path.basename(name)
    return re.sub(r"[^A-Za-z0-9._\-+]", "", name)

def _safe_subfolder(sub: str) -> str:
    if not sub:
        return ""
    sub = sub.replace("\\", "/").strip("/")
    parts = [p for p in sub.split("/") if p]
    if any(p in ("..", ".") for p in parts):
        raise ValueError(f"Subfolder inválido (path traversal): {sub}")
    safe_parts = [re.sub(r"[^A-Za-z0-9._\-+]", "", p) for p in parts]
    return "/".join(p for p in safe_parts if p)

def _safe_relative_path(rel: str) -> str:
    rel = rel.replace("\\", "/")
    parts = [p for p in Path(rel).parts if p]
    if ".." in parts or "." in parts:
        raise ValueError(f"Path traversal detectado: {rel}")
    return str(Path(*parts)) if parts else ""

def _human_size(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"

def _human_speed(bps: float) -> str:
    return f"{_human_size(int(bps))}/s"

def _human_eta(seconds: float) -> str:
    if seconds <= 0 or seconds == float("inf"):
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"

def _is_excluded_lora_path(rel_path: str) -> bool:
    """Devuelve True si el path está dentro de una carpeta ignorada."""
    parts = Path(rel_path.replace("\\", "/")).parts
    return any(p.lower() in _EXCLUDED_LORA_DIRS for p in parts)

# ─── Parsing de steps ────────────────────────────────────────────────────────
_STEP_RE_STEPWORD = re.compile(r"^(?P<base>.+?)[-_]step0*(?P<step>\d+)$", re.IGNORECASE)
_STEP_RE_NUMERIC = re.compile(r"^(?P<base>.+?)[-_](?P<step>\d{3,5})$")

def _parse_step(stem: str) -> Tuple[str, int]:
    """
    Extrae (base, step) de un nombre tipo:
      'seungcheol_BXL-step00003000' -> ('seungcheol_BXL', 3000)
      'seungcheol_BXL-3000'         -> ('seungcheol_BXL', 3000)
      'seungcheol_BXL'              -> ('seungcheol_BXL', -1)
    """
    m = _STEP_RE_STEPWORD.match(stem)
    if m and m.group("step"):
        try:
            return m.group("base"), int(m.group("step"))
        except ValueError:
            pass
    m = _STEP_RE_NUMERIC.match(stem)
    if m and m.group("step"):
        try:
            return m.group("base"), int(m.group("step"))
        except ValueError:
            pass
    return stem, -1

def _parse_hf_uri(uri: str) -> Optional[Dict[str, str]]:
    if not uri:
        return None
    s = str(uri).strip()
    if not s:
        return None
    m = re.match(r"^hf\s+download\s+(.+)$", s, re.IGNORECASE)
    if m:
        s = m.group(1).strip()
    if s.lower().startswith("hf://"):
        s = s[5:]
    elif s.lower().startswith("hf:/"):
        s = s[4:]
    elif s.startswith(("http://", "https://")):
        m = re.match(r"https?://(?:www\.)?huggingface\.co/(.+)$", s)
        if not m:
            return None
        s = m.group(1)
    parts = [p for p in s.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    repo_id = f"{parts[0]}/{parts[1]}"
    remaining = parts[2:]
    if len(remaining) >= 2 and remaining[0] in ("blob", "resolve"):
        remaining = remaining[2:]
    if not remaining:
        return None
    filename = remaining[-1]
    hf_subfolder = "/".join(remaining[:-1])
    return {"repo_id": repo_id, "filename": filename, "hf_subfolder": hf_subfolder}

def _build_serve_url(filename: str) -> str:
    base = os.environ.get("R2_PUBLIC_BASE", "").strip().rstrip("/")
    if base:
        return f"{base}/outputs/{filename}"
    return f"/serve_image?filename={filename}"

# ═══════════════════════════════════════════════════════════════════════════════
# FILE INDEX — EN MEMORIA
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FileEntry:
    rel_path: str
    name: str
    stem: str
    ext: str
    parent: str
    top_level: str
    size: int
    mtime: float
    is_file: bool = True
    is_dir: bool = False

class FileIndex:
    def __init__(self):
        self._files: Dict[str, FileEntry] = {}
        self._dirs: Set[str] = set()
        self._lock = threading.RLock()
        self._version = 0
        self._last_rebuild = 0.0

    def replace_all(self, entries: List[FileEntry], dirs: Set[str]) -> None:
        with self._lock:
            self._files = {e.rel_path: e for e in entries}
            self._dirs = set(dirs)
            self._version += 1
            self._last_rebuild = time.time()

    def add_file(self, rel_path: str, size: int, mtime: float) -> None:
        p = Path(rel_path)
        ext = p.suffix.lower()
        parent = str(p.parent) if str(p.parent) != "." else ""
        top = p.parts[0] if p.parts else ""
        entry = FileEntry(
            rel_path=rel_path, name=p.name, stem=p.stem, ext=ext,
            parent=parent, top_level=top, size=size, mtime=mtime,
        )
        with self._lock:
            self._files[rel_path] = entry
            parts = p.parts
            for i in range(1, len(parts)):
                self._dirs.add("/".join(parts[:i]))
            self._version += 1

    def remove_path(self, rel_path: str) -> None:
        with self._lock:
            self._files.pop(rel_path, None)
            self._dirs = {d for d in self._dirs if not (d == rel_path or d.startswith(rel_path + "/"))}
            self._files = {k: v for k, v in self._files.items() if not (k == rel_path or k.startswith(rel_path + "/"))}
            self._version += 1

    def move_path(self, src_rel: str, dst_rel: str) -> None:
        with self._lock:
            for f in list(self._files.keys()):
                if f == src_rel or f.startswith(src_rel + "/"):
                    new_f = dst_rel + f[len(src_rel):]
                    entry = self._files.pop(f)
                    entry.rel_path = new_f
                    new_p = Path(new_f)
                    entry.parent = str(new_p.parent) if str(new_p.parent) != "." else ""
                    entry.top_level = new_p.parts[0] if new_p.parts else ""
                    self._files[new_f] = entry
            for d in list(self._dirs):
                if d == src_rel or d.startswith(src_rel + "/"):
                    new_d = dst_rel + d[len(src_rel):]
                    self._dirs.discard(d)
                    self._dirs.add(new_d)
            self._version += 1

    def get(self, rel_path: str) -> Optional[FileEntry]:
        with self._lock:
            return self._files.get(rel_path)

    def list_by_top_level(self, top: str) -> List[FileEntry]:
        with self._lock:
            return [e for e in self._files.values() if e.top_level == top]

    def list_by_parent(self, parent: str) -> List[FileEntry]:
        with self._lock:
            return [e for e in self._files.values() if e.parent == parent]

    def search_by_name(self, query: str, top: Optional[str] = None) -> List[FileEntry]:
        q = query.lower()
        results = []
        with self._lock:
            for e in self._files.values():
                if top and e.top_level != top:
                    continue
                if q == e.stem.lower() or q in e.stem.lower():
                    results.append(e)
        return results

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            by_ext: Dict[str, int] = {}
            total_size = 0
            for e in self._files.values():
                by_ext[e.ext] = by_ext.get(e.ext, 0) + 1
                total_size += e.size
            return {
                "files": len(self._files),
                "dirs": len(self._dirs),
                "total_size_bytes": total_size,
                "total_size_human": _human_size(total_size),
                "by_ext": by_ext,
                "version": self._version,
                "last_rebuild": self._last_rebuild,
            }

_file_index = FileIndex()

# ═══════════════════════════════════════════════════════════════════════════════
# LIST ALL CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class ListAllCache:
    def __init__(self, cache_dir: Path, ttl: int = LIST_ALL_CACHE_TTL):
        self._dir = cache_dir
        self._ttl = ttl
        self._cache_path = CACHE_FILE
        self._meta_path = CACHE_META_FILE
        self._lock = threading.RLock()
        self._status = "loading"
        self._payload: Optional[Dict[str, Any]] = None
        self._ts: float = 0.0
        self._error: Optional[str] = None
        self._request_counter = 0
        self._build_thread: Optional[threading.Thread] = None
        self._dir.mkdir(parents=True, exist_ok=True)

    def _load_from_disk(self) -> bool:
        if not self._cache_path.exists():
            return False
        try:
            raw = self._cache_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            payload = data.get("payload")
            ts = float(data.get("ts", 0))
            if payload is None:
                return False
            with self._lock:
                self._payload = payload
                self._ts = ts
                self._status = "stale" if (time.time() - ts) > self._ttl else "fresh"
            self._rebuild_index_from_payload(payload)
            return True
        except Exception as e:
            print(f"[cache] WARN load_from_disk: {e}", flush=True)
            return False

    def _save_to_disk(self, payload: Dict[str, Any]) -> None:
        try:
            tmp = self._cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"ts": time.time(), "payload": payload, "version": 3}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._cache_path)
        except Exception as e:
            print(f"[cache] WARN save_to_disk: {e}", flush=True)

    def _rebuild_index_from_payload(self, payload: Dict[str, Any]) -> None:
        entries: List[FileEntry] = []
        dirs: Set[str] = set()

        def _walk_tree(tree: Dict[str, Any], top: str) -> None:
            for f in tree.get("_root", []):
                rel = f.get("relative_path", "")
                if not rel:
                    continue
                full = f"{top}/{rel}" if (top and not rel.startswith(top)) else rel
                p = Path(full)
                entries.append(FileEntry(
                    rel_path=full, name=f.get("name", p.name), stem=p.stem,
                    ext=p.suffix.lower(),
                    parent=str(p.parent) if str(p.parent) != "." else "",
                    top_level=p.parts[0] if p.parts else top,
                    size=f.get("size_bytes", 0), mtime=0.0,
                ))
            for folder_name, child_tree in tree.get("folders", {}).items():
                child_full = f"{top}/{folder_name}" if (top and top != folder_name and not folder_name.startswith(top + "/")) else folder_name
                dirs.add(child_full)
                _walk_tree(child_tree, child_full)

        for section_key in ("loras", "unets", "clips", "vaes", "upscalers"):
            for vol_name, vol_data in payload.get(section_key, {}).items():
                path_str = vol_data.get("path", "")
                top = Path(path_str).name if path_str else vol_name
                _walk_tree(vol_data.get("tree", {}), top)

        for out_file in payload.get("outputs", {}).get("files", []):
            fname = out_file.get("filename", "")
            if fname:
                rel = f"outputs/{fname}"
                entries.append(FileEntry(
                    rel_path=rel, name=fname, stem=Path(fname).stem,
                    ext=Path(fname).suffix.lower(), parent="outputs",
                    top_level="outputs", size=out_file.get("size_bytes", 0),
                    mtime=out_file.get("mtime", 0.0),
                ))

        dirs.add("outputs")
        dirs.add("archived")
        _file_index.replace_all(entries, dirs)

    def warm_up(self) -> None:
        if self._load_from_disk():
            return
        self._trigger_background_build()

    def _trigger_background_build(self) -> None:
        with self._lock:
            if self._build_thread and self._build_thread.is_alive():
                return
            self._status = "building"
        t = threading.Thread(target=self._background_build_worker, daemon=True, name="cache-builder")
        self._build_thread = t
        t.start()

    def _background_build_worker(self) -> None:
        try:
            t0 = time.time()
            payload = _build_list_all_payload()
            self._save_to_disk(payload)
            with self._lock:
                self._payload = payload
                self._ts = time.time()
                self._status = "fresh"
                self._error = None
            self._rebuild_index_from_payload(payload)
            elapsed = time.time() - t0
            print(f"[cache] background_build: OK en {elapsed:.1f}s ({_file_index.stats()['files']} archivos)", flush=True)
        except Exception as e:
            with self._lock:
                self._status = "error"
                self._error = str(e)[:500]
            print(f"[cache] background_build ERROR: {e}", flush=True)

    def get(self) -> Dict[str, Any]:
        with self._lock:
            self._request_counter += 1
            if self._payload is not None:
                return {
                    **self._payload,
                    "cached": self._status == "fresh",
                    "cache_status": self._status,
                    "cache_age_sec": round(time.time() - self._ts, 1),
                    "index_stats": _file_index.stats(),
                }
        return {
            "success": True, "cached": False, "cache_status": self._status,
            "building": True, "message": "Cache construyéndose en background...",
            "index_stats": _file_index.stats(),
            "loras": {}, "unets": {}, "clips": {}, "vaes": {}, "upscalers": {},
            "outputs": {"path": str(DIR_OUTPUTS), "count": 0, "files": []},
        }

    def on_file_added(self, rel_path: str, size: int) -> None:
        _file_index.add_file(rel_path, size, time.time())
        with self._lock:
            self._status = "dirty"

    def on_path_removed(self, rel_path: str) -> None:
        _file_index.remove_path(rel_path)
        with self._lock:
            self._status = "dirty"

    def on_path_moved(self, src_rel: str, dst_rel: str) -> None:
        _file_index.move_path(src_rel, dst_rel)
        with self._lock:
            self._status = "dirty"

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "has_payload": self._payload is not None,
                "age_sec": round(time.time() - self._ts, 1) if self._ts else None,
                "request_count": self._request_counter,
                "ttl_sec": self._ttl,
                "build_thread_alive": self._build_thread.is_alive() if self._build_thread else False,
                "error": self._error,
                "index": _file_index.stats(),
            }

_listall_cache: Optional[ListAllCache] = None

# ═══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD LOG & SSE ASÍNCRONO
# ═══════════════════════════════════════════════════════════════════════════════

class DownloadLog:
    def __init__(self, log_dir: Path):
        self._dir = log_dir
        self._locks: Dict[Path, threading.Lock] = {}
        self._locks_meta = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._mem: Dict[str, List[str]] = {}
        self._mem_meta = threading.Lock()

    def _get_lock(self, p: Path) -> threading.Lock:
        with self._locks_meta:
            lk = self._locks.get(p)
            if lk is None:
                lk = threading.Lock()
                self._locks[p] = lk
            if len(self._locks) > 500:
                self._locks.clear()
                self._locks[p] = lk
            return lk

    def _path(self, task_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_\-]", "", task_id)
        return self._dir / f"{safe}.log"

    def write(self, task_id: str, level: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level.upper():7s}] {message}\n"
        with self._mem_meta:
            if task_id not in self._mem:
                self._mem[task_id] = []
            self._mem[task_id].append(line)
            if len(self._mem[task_id]) > 5000:
                self._mem[task_id] = self._mem[task_id][-4000:]
            if len(self._mem) > 500:
                self._mem = {k: v for k, v in list(self._mem.items())[-300:]}

        p = self._path(task_id)
        with self._get_lock(p):
            try:
                with open(p, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
            except OSError:
                pass

    def info(self, task_id: str, msg: str) -> None:
        self.write(task_id, "info", msg)
        print(f"[dl:{task_id}] {msg}", flush=True)

    def warn(self, task_id: str, msg: str) -> None:
        self.write(task_id, "warn", msg)
        print(f"[dl:{task_id}] WARN: {msg}", flush=True)

    def error(self, task_id: str, msg: str) -> None:
        self.write(task_id, "error", msg)
        print(f"[dl:{task_id}] ERROR: {msg}", flush=True)

    def line_count(self, task_id: str) -> int:
        with self._mem_meta:
            return len(self._mem.get(task_id, []))

    def read(self, task_id: str, tail: int = 200) -> List[str]:
        with self._mem_meta:
            lines = list(self._mem.get(task_id, []))
        return lines[-tail:] if tail > 0 else lines

    def raw(self, task_id: str) -> str:
        with self._mem_meta:
            return "".join(self._mem.get(task_id, []))

    async def stream_async(self, task_id: str, poll_sec: float = 0.5):
        cursor = 0
        idle_loops = 0
        max_idle_loops = int(os.environ.get("KREA2_SSE_MAX_IDLE", "1800"))
        sentinel_done = "[DONE]"
        sentinel_fail = "[FAIL]"

        while True:
            with self._mem_meta:
                all_lines = list(self._mem.get(task_id, []))

            if cursor < len(all_lines):
                new_lines = all_lines[cursor:]
                cursor = len(all_lines)
                idle_loops = 0
                for raw_line in new_lines:
                    split_lines = raw_line.rstrip("\r\n").split("\n")
                    formatted = "\n".join(f"data: {ln}" for ln in split_lines)
                    yield f"{formatted}\n\n"
                    if sentinel_done in raw_line or sentinel_fail in raw_line:
                        return
            else:
                task_meta = _task_registry.get(task_id)
                if task_meta and task_meta.get("status") in ("done", "done_with_errors", "all_skipped", "error"):
                    yield "data: [SSE-CLOSED] Task finished\n\n"
                    return

                idle_loops += 1
                if idle_loops >= max_idle_loops:
                    yield "data: [SSE-CLOSED] max_idle reached\n\n"
                    return
                yield ": ping\n\n"
                await asyncio.sleep(poll_sec)

_dlog: Optional[DownloadLog] = None

# ═══════════════════════════════════════════════════════════════════════════════
# TASK REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

class TaskRegistry:
    def __init__(self, max_size: int = MAX_TRACKED_TASKS):
        self._tasks: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_size

    def create(self, task_type: str, meta: Dict[str, Any]) -> str:
        task_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id, "type": task_type, "status": "running",
                "created_at": time.time(), "progress": {}, "results": [],
                "errors": [], "meta": meta,
            }
            self._tasks.move_to_end(task_id)
            while len(self._tasks) > self._max:
                self._tasks.popitem(last=False)
        self._persist_meta(task_id)
        return task_id

    def update(self, task_id: str, **kwargs) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(kwargs)
                self._tasks.move_to_end(task_id)
        self._persist_meta(task_id)

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if task_id in self._tasks:
                return dict(self._tasks[task_id])
        meta_file = DIR_LOGS / f"{task_id}.meta.json"
        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                with self._lock:
                    self._tasks[task_id] = data
                return data
            except Exception:
                pass
        return None

    def finish(self, task_id: str, status: str = "done") -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = status
                self._tasks[task_id]["finished_at"] = time.time()
                self._tasks.move_to_end(task_id)
        self._persist_meta(task_id)

    def _persist_meta(self, task_id: str) -> None:
        try:
            with self._lock:
                item = self._tasks.get(task_id)
                if not item:
                    return
                raw = json.dumps(item, ensure_ascii=False, default=str)
            p = DIR_LOGS / f"{task_id}.meta.json"
            p.write_text(raw, encoding="utf-8")
        except Exception:
            pass

_task_registry = TaskRegistry()

# ═══════════════════════════════════════════════════════════════════════════════
# OPERATION HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

class OperationHistory:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "version": 1, "updated_at": 0.0, "entries": [],
            "hash_index": {}, "name_index": {},
        }
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict) and "entries" in data:
                    self._data = data
                    self._data.setdefault("hash_index", {})
                    self._data.setdefault("name_index", {})
            except Exception as e:
                print(f"[history] WARN no se pudo cargar {self._path}: {e}", flush=True)

    def _save(self):
        self._data["updated_at"] = time.time()
        if len(self._data["entries"]) > HISTORY_MAX_ENTRIES:
            self._data["entries"] = self._data["entries"][-HISTORY_MAX_ENTRIES:]
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self._path)

    def log(self, *, action: str, details: Dict[str, Any], result: str = "success",
            final_path: str = "", size_bytes: int = 0, file_hash: str = "",
            error: str = "") -> None:
        entry = {
            "ts": time.time(),
            "iso": datetime.now().isoformat(timespec="seconds"),
            "action": action, "details": details, "result": result,
            "final_path": final_path, "size_bytes": size_bytes,
            "file_hash": file_hash,
        }
        if error:
            entry["error"] = error[:500]
        with self._lock:
            self._data["entries"].append(entry)
            if file_hash and result == "success":
                self._data["hash_index"][file_hash] = final_path
            if final_path and result == "success":
                self._data["name_index"][final_path] = file_hash
            self._save()

    def get_entries(self, action: str = "", limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            entries = self._data["entries"]
            if action:
                entries = [e for e in entries if e.get("action") == action]
            return entries[offset:offset + limit]

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            entries = self._data["entries"]
            by_action: Dict[str, int] = {}
            by_result: Dict[str, int] = {}
            for e in entries:
                a = e.get("action", "unknown")
                by_action[a] = by_action.get(a, 0) + 1
                r = e.get("result", "unknown")
                by_result[r] = by_result.get(r, 0) + 1
            return {
                "total_entries": len(entries),
                "hashes": len(self._data.get("hash_index", {})),
                "by_action": by_action, "by_result": by_result,
                "file": str(self._path),
            }

    def rebuild_hash_index(self, scan_dirs: List[Path]) -> Dict[str, Any]:
        new_hash_index: Dict[str, str] = {}
        new_name_index: Dict[str, str] = {}
        scanned = 0
        errors = 0
        for base_dir in scan_dirs:
            if not base_dir.exists():
                continue
            for p in base_dir.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in _MODEL_EXTS and p.suffix.lower() not in _IMAGE_EXTS:
                    continue
                try:
                    rel = str(p.relative_to(VOL_MAIN_PATH))
                    file_hash = _compute_sha256(p)
                    new_hash_index[file_hash] = rel
                    new_name_index[rel] = file_hash
                    scanned += 1
                except Exception:
                    errors += 1
        with self._lock:
            self._data["hash_index"] = new_hash_index
            self._data["name_index"] = new_name_index
            self._save()
        return {"scanned": scanned, "errors": errors, "hashes": len(new_hash_index)}

    def check_duplicate_hash(self, file_hash: str) -> Optional[str]:
        with self._lock:
            return self._data.get("hash_index", {}).get(file_hash)

    def check_duplicate_name(self, path: str) -> Optional[str]:
        with self._lock:
            return self._data.get("name_index", {}).get(path)

def _compute_sha256(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def _compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING INTERNO
# ═══════════════════════════════════════════════════════════════════════════════

_manager_log_lock = threading.Lock()
MAX_MANAGER_LOG_BYTES = int(os.environ.get("KREA2_MANAGER_LOG_MAX_BYTES", str(50 * 1024 * 1024)))
_audit_queue: "queue.Queue" = queue.Queue(maxsize=10000)
_audit_ring: "deque" = deque(maxlen=2000)
_audit_ring_lock = threading.Lock()

_GPU_LOG_TTL_SEC = float(os.environ.get("KREA2_GPU_LOG_TTL", "5.0"))
_gpu_log_cache: Dict[str, Any] = {"ts": 0.0, "lines": []}
_gpu_log_lock = threading.Lock()

def _read_gpu_log_cached() -> List[str]:
    now = time.time()
    with _gpu_log_lock:
        if _gpu_log_cache["lines"] and (now - _gpu_log_cache["ts"]) < _GPU_LOG_TTL_SEC:
            return _gpu_log_cache["lines"]
        path = DIR_LOGS / "gpu_service.log"
        if not path.exists():
            return []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            _gpu_log_cache["ts"] = time.time()
            _gpu_log_cache["lines"] = lines
            return lines
        except OSError:
            return _gpu_log_cache["lines"]

def _audit_writer_loop() -> None:
    while True:
        batch: List[Dict[str, Any]] = []
        try:
            for _ in range(50):
                batch.append(_audit_queue.get_nowait())
                if len(batch) >= 50:
                    break
        except queue.Empty:
            pass
        if not batch:
            time.sleep(0.1)
            continue
        try:
            path = DIR_LOGS / "manager_service.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with _manager_log_lock:
                try:
                    if path.exists() and path.stat().st_size > MAX_MANAGER_LOG_BYTES:
                        backup = Path(str(path) + ".1")
                        if backup.exists():
                            backup.unlink()
                        path.replace(backup)
                except OSError:
                    pass
                with open(path, "a", encoding="utf-8") as f:
                    for entry in batch:
                        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                    f.flush()
        except Exception:
            time.sleep(0.1)

def _log_manager(endpoint: str, *, phase: str = "done", ok: bool = True,
                 error: Any = None, task_id: Any = None, duration: Optional[float] = None,
                 extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        entry: Dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "epoch": round(time.time(), 3),
            "service": "krea2-manager-cpu",
            "endpoint": endpoint, "phase": phase, "ok": bool(ok),
        }
        if task_id is not None:
            entry["task_id"] = task_id
        if duration is not None:
            entry["duration_s"] = round(duration, 3)
        if error is not None:
            entry["error"] = str(error)[:500]
        if extra:
            entry["extra"] = extra

        with _audit_ring_lock:
            _audit_ring.append(entry)
        try:
            _audit_queue.put_nowait(entry)
        except Exception:
            pass
    except Exception:
        pass

_history: Optional[OperationHistory] = None

# ═══════════════════════════════════════════════════════════════════════════════
# VOLUME REBIND (Modal)
# ═══════════════════════════════════════════════════════════════════════════════

def _rebind_volume_globals():
    global VOL_MAIN, VOL_LORAS, DIR_DIFFUSION, DIR_LORAS_MAIN
    global DIR_TEXT_ENCODERS, DIR_UPSCALE, DIR_VAE, DIR_OUTPUTS, DIR_ARCHIVED
    global DIR_MANAGER, DIR_LOGS, DIR_CACHE, HISTORY_FILE, _TARGET_MAP

    VOL_MAIN = VOL_MAIN_PATH
    VOL_LORAS = VOL_LORAS_PATH

    DIR_DIFFUSION = VOL_MAIN / "diffusion_models"
    DIR_LORAS_MAIN = VOL_MAIN / "loras"
    DIR_TEXT_ENCODERS = VOL_MAIN / "text_encoders"
    DIR_UPSCALE = VOL_MAIN / "upscale"
    DIR_VAE = VOL_MAIN / "vae"
    DIR_OUTPUTS = VOL_MAIN / "outputs"
    DIR_ARCHIVED = VOL_MAIN / "archived"
    DIR_MANAGER = VOL_MAIN / "_manager"
    DIR_LOGS = DIR_MANAGER / "logs"
    DIR_CACHE = DIR_MANAGER / "cache"
    HISTORY_FILE = DIR_MANAGER / "history.json"

    _TARGET_MAP.clear()
    _TARGET_MAP.update({
        "lora": DIR_LORAS_MAIN, "loras": DIR_LORAS_MAIN,
        "unet": DIR_DIFFUSION, "model": DIR_DIFFUSION,
        "diffusion": DIR_DIFFUSION, "diffusion_models": DIR_DIFFUSION,
        "clip": DIR_TEXT_ENCODERS, "text_encoder": DIR_TEXT_ENCODERS,
        "text_encoders": DIR_TEXT_ENCODERS,
        "vae": DIR_VAE, "upscale": DIR_UPSCALE, "upscaler": DIR_UPSCALE,
    })

def _resolve_target_dir(target: str) -> Path:
    t = target.strip().lower()
    if t not in _TARGET_MAP:
        raise ValueError(f"Target desconocido: '{target}'. Válidos: {sorted(set(_TARGET_MAP.keys()))}")
    return _TARGET_MAP[t]

# ═══════════════════════════════════════════════════════════════════════════════
# SCAN HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _is_dir_fast(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False

def _scan_dir_fast(base_path: Path, exts: Set[str],
                   exclude_dirs: Optional[Set[str]] = None) -> Dict[str, Any]:
    if not base_path.exists():
        return {"_exists": False, "_path": str(base_path), "_root": [],
                "folders": {}, "_total_files": 0, "_total_size_bytes": 0}

    exclude_lower = {d.lower() for d in (exclude_dirs or set())}

    def _new_node(path: str) -> Dict[str, Any]:
        return {"_exists": True, "_path": path, "_root": [], "folders": {},
                "_total_files": 0, "_total_size_bytes": 0}

    def _walk(node: Dict[str, Any], dir_path: str) -> Tuple[int, int]:
        try:
            with os.scandir(dir_path) as it:
                entries = list(it)
        except OSError as ex:
            node["_error"] = f"No se pudo leer: {ex}"
            return 0, 0

        entries.sort(key=lambda e: (not _is_dir_fast(e), e.name.lower()))
        files, size = 0, 0
        for entry in entries:
            try:
                if _is_dir_fast(entry):
                    if exclude_lower and entry.name.lower() in exclude_lower:
                        continue
                    child = _new_node(entry.path)
                    node["folders"][entry.name] = child
                    sub_files, sub_size = _walk(child, entry.path)
                    child["_total_files"] = sub_files
                    child["_total_size_bytes"] = sub_size
                    child["_total_size_human"] = _human_size(sub_size)
                    files += sub_files
                    size += sub_size
                elif entry.is_file(follow_symlinks=False):
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext not in exts:
                        continue
                    st = entry.stat(follow_symlinks=False)
                    rel = str(Path(entry.path).relative_to(base_path))
                    node["_root"].append({
                        "name": entry.name,
                        "logical_name": os.path.splitext(entry.name)[0],
                        "relative_path": rel,
                        "size_bytes": st.st_size,
                        "size_human": _human_size(st.st_size),
                    })
                    files += 1
                    size += st.st_size
            except OSError:
                continue
        return files, size

    root = _new_node(str(base_path))
    total_files, total_size = _walk(root, str(base_path))
    root["_total_files"] = total_files
    root["_total_size_bytes"] = total_size
    root["_total_size_human"] = _human_size(total_size)
    return root

# ═══════════════════════════════════════════════════════════════════════════════
# LIST HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def list_outputs(limit: int = 200) -> List[Dict[str, Any]]:
    entries = _file_index.list_by_top_level("outputs")
    if entries:
        entries.sort(key=lambda e: e.mtime, reverse=True)
        return [
            {
                "filename": e.name, "size_bytes": e.size,
                "size_human": _human_size(e.size), "mtime": e.mtime,
                "url": _build_serve_url(e.name),
            }
            for e in entries[:limit]
        ]
    return []

# ═══════════════════════════════════════════════════════════════════════════════
# LIST MODELS — JSON para Web-UI
# ═══════════════════════════════════════════════════════════════════════════════

def _list_loras_structured() -> Dict[str, Any]:
    """
    Estructura JSON óptima para web-UI:
      - 'characters': LoRAs de personajes (volumen krea2-loras-renew).
          Cada personaje agrupa sus variantes (final + steps).
      - 'downloaded': LoRAs descargados (carpeta krea2-beam-volume/loras).
    """
    # ── Characters ───────────────────────────────────────────────────────────
    char_groups: Dict[str, Dict[str, Any]] = {}

    for e in _file_index.list_by_top_level("krea2-loras-renew"):
        if e.ext not in _LORA_EXTS:
            continue
        if _is_excluded_lora_path(e.rel_path):
            continue

        rel_inside = e.rel_path.replace("krea2-loras-renew/", "")
        parts = Path(rel_inside).parts
        if len(parts) < 2:
            continue  # Ignorar archivos en la raíz del volumen

        character = parts[0]
        base, step = _parse_step(e.stem)
        key = character.lower()

        full_path = VOL_LORAS_PATH / rel_inside

        variant = {
            "name": f"{base}-{step}" if step > 0 else base,
            "filename": e.name,
            "steps": step if step > 0 else None,
            "size_bytes": e.size,
            "size_human": _human_size(e.size),
            "absolute_path": str(full_path),
        }

        if key not in char_groups:
            char_groups[key] = {
                "name": character,
                "character": character,
                "volume": "krea2-loras-renew",
                "variants": [],
            }
        char_groups[key]["variants"].append(variant)

    # Ordenar variantes: final primero, luego steps descendentes
    for model in char_groups.values():
        model["variants"].sort(key=lambda v: (v["steps"] is not None, -(v["steps"] or 0)))
        # Default: la variante sin steps (final) o el step más alto
        default = None
        for v in model["variants"]:
            if v["steps"] is None:
                default = v["name"]
                break
        if default is None and model["variants"]:
            default = model["variants"][0]["name"]
        model["default"] = default

    characters_list = sorted(char_groups.values(), key=lambda m: m["character"].lower())

    # ── Downloaded LoRAs ─────────────────────────────────────────────────────
    downloaded: List[Dict[str, Any]] = []
    for e in _file_index.list_by_top_level("loras"):
        if e.ext not in _LORA_EXTS:
            continue
        if _is_excluded_lora_path(e.rel_path):
            continue

        full_path = VOL_MAIN_PATH / e.rel_path
        rel_inside = e.rel_path.replace("loras/", "") if e.rel_path.startswith("loras/") else e.rel_path
        downloaded.append({
            "name": e.stem,
            "filename": e.name,
            "relative_path": rel_inside,
            "absolute_path": str(full_path),
            "size_bytes": e.size,
            "size_human": _human_size(e.size),
        })

    downloaded.sort(key=lambda m: m["name"].lower())

    total = len(characters_list) + len(downloaded)
    return {
        "count": total,
        "characters": {"count": len(characters_list), "models": characters_list},
        "downloaded": {"count": len(downloaded), "models": downloaded},
    }


def _list_other_structured(type_folder: str) -> Dict[str, Any]:
    """
    Para unet/clip/vae/upscale: lista plana.
    """
    top_map = {
        "unet": "diffusion_models",
        "clip": "text_encoders",
        "vae": "vae",
        "upscale": "upscale",
    }
    top = top_map[type_folder]
    models: List[Dict[str, Any]] = []

    for e in _file_index.list_by_top_level(top):
        if e.ext not in _MODEL_EXTS:
            continue

        full_path = VOL_MAIN_PATH / e.rel_path
        models.append({
            "name": e.stem,
            "filename": e.name,
            "relative_path": e.rel_path,
            "absolute_path": str(full_path),
            "size_bytes": e.size,
            "size_human": _human_size(e.size),
        })

    models.sort(key=lambda m: m["name"].lower())
    return {"count": len(models), "models": models}


def list_models_recursive(type_folder: str, version: Optional[str] = None) -> Dict[str, Any]:
    valid_types = {"lora", "unet", "clip", "vae", "upscale"}
    if type_folder not in valid_types:
        raise ValueError(f"type_folder desconocido: {type_folder}. Válidos: {sorted(valid_types)}")

    if type_folder == "lora":
        return _list_loras_structured()
    return _list_other_structured(type_folder)

# ═══════════════════════════════════════════════════════════════════════════════
# RESOLVE MODEL PATH
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_model_path(name: str, type_folder: str, version: Optional[str] = None) -> str:
    if not name:
        raise FileNotFoundError("Nombre vacío")

    top_map = {
        "unet": "diffusion_models", "lora": "loras",
        "clip": "text_encoders", "vae": "vae", "upscale": "upscale",
    }
    if type_folder not in top_map:
        raise ValueError(f"type_folder desconocido: {type_folder}")

    clean_name = name.strip()

    # ─── LoRAs ─────────────────────────────────────────────────────────────
    if type_folder == "lora":
        base_query, req_step = _parse_step(clean_name)
        q_low = base_query.lower()
        full_low = clean_name.lower()

        candidates: List[Dict[str, Any]] = []

        # Personajes (krea2-loras-renew)
        for e in _file_index.list_by_top_level("krea2-loras-renew"):
            if e.ext not in _LORA_EXTS:
                continue
            if _is_excluded_lora_path(e.rel_path):
                continue

            rel_inside = e.rel_path.replace("krea2-loras-renew/", "")
            parts = Path(rel_inside).parts
            if len(parts) < 2:
                continue
            character = parts[0]

            f_base, f_step = _parse_step(e.stem)
            f_base_low = f_base.lower()
            f_char_low = character.lower()

            # Match: por base, por character, o por nombre completo
            if not (q_low == f_base_low
                    or q_low == f_char_low
                    or q_low in f_base_low
                    or f_base_low in q_low
                    or q_low in f_char_low):
                continue

            full_path = VOL_LORAS_PATH / rel_inside
            if not full_path.exists():
                continue

            candidates.append({
                "path": str(full_path),
                "base": f_base,
                "step": f_step,
                "is_final": f_step == -1,
                "group": "characters",
            })

        # Descargados (krea2-beam-volume/loras)
        for e in _file_index.list_by_top_level("loras"):
            if e.ext not in _LORA_EXTS:
                continue
            if _is_excluded_lora_path(e.rel_path):
                continue

            f_base, f_step = _parse_step(e.stem)
            f_base_low = f_base.lower()

            if not (q_low == f_base_low or q_low in f_base_low or f_base_low in q_low):
                continue

            full_path = VOL_MAIN_PATH / e.rel_path
            if not full_path.exists():
                continue

            candidates.append({
                "path": str(full_path),
                "base": f_base,
                "step": f_step,
                "is_final": f_step == -1,
                "group": "downloaded",
            })

        if not candidates:
            raise FileNotFoundError(f"No se encontró LoRA '{name}' en los volúmenes montados.")

        # Si se pidió un step específico, buscarlo
        if req_step > 0:
            for c in candidates:
                if c["step"] == req_step:
                    return c["path"]
            raise FileNotFoundError(
                f"No se encontró '{base_query}' con step={req_step}. "
                f"Steps disponibles: {sorted([c['step'] for c in candidates if c['step'] > 0], reverse=True)}"
            )

        # Sin step específico: priorizar final, luego step más alto
        finals = [c for c in candidates if c["is_final"]]
        if finals:
            return finals[0]["path"]

        candidates.sort(key=lambda c: c["step"], reverse=True)
        return candidates[0]["path"]

    # ─── Otros tipos ───────────────────────────────────────────────────────
    target_top = top_map[type_folder]
    safe_query = _safe_filename(clean_name)
    query_stem = Path(safe_query).stem
    extensions = [".safetensors", ".ckpt", ".pt", ".bin", ".gguf", ""]

    if _file_index.stats()["files"] > 0:
        for ext in extensions:
            for rel in [f"{target_top}/{safe_query}{ext}"]:
                e = _file_index.get(rel)
                if e:
                    full = VOL_MAIN_PATH / rel
                    if full.exists():
                        return str(full)

        for e in _file_index.search_by_name(safe_query, target_top):
            if query_stem.lower() in e.name.lower():
                full = VOL_MAIN_PATH / e.rel_path
                if full.exists():
                    return str(full)

    raise FileNotFoundError(f"No se encontró '{name}' (tipo={type_folder}, version={version})")

# ═══════════════════════════════════════════════════════════════════════════════
# VALIDACIÓN DE MODELOS
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_model_type(path: Path) -> Tuple[str, str]:
    try:
        size = path.stat().st_size
        if size < 1024:
            return "too_small", f"Tamaño demasiado pequeño ({size} B)"

        with open(path, "rb") as f:
            header_sample = f.read(8192)

        sample_lower = header_sample.lower()
        if any(h in sample_lower for h in (b"<html", b"<!doctype", b"cloudflare", b"cf-chl", b"access denied", b"error code")):
            snippet = header_sample[:250].decode("utf-8", errors="replace")
            return "html_error", f"Respuesta HTML en vez de binario: {snippet}"

        if len(header_sample) >= 8:
            header_len = int.from_bytes(header_sample[:8], byteorder="little")
            if 8 < header_len < 100_000_000 and (header_len + 8) <= size:
                try:
                    with open(path, "rb") as f:
                        f.seek(8)
                        header_json = f.read(header_len)
                    parsed = json.loads(header_json)
                    if isinstance(parsed, dict):
                        return "safetensors", f"Safetensors válido ({size / 1024 / 1024:.1f}MB)"
                except Exception:
                    pass

        if header_sample.startswith(b"GGUF"):
            return "gguf", f"GGUF válido ({size / 1024 / 1024:.1f}MB)"
        if header_sample.startswith(b"PK"):
            return "pytorch_zip", f"PyTorch Zip válido ({size / 1024 / 1024:.1f}MB)"
        if header_sample.startswith(b"\x80"):
            return "pytorch_pickle", f"PyTorch Pickle válido ({size / 1024 / 1024:.1f}MB)"

        return "unknown", "Formato no identificado"
    except Exception as e:
        return "error", str(e)

def _validate_model_file(path: Path) -> Tuple[bool, str]:
    if not path.exists():
        return False, "Archivo no existe"
    dtype, msg = _detect_model_type(path)
    if dtype in ("safetensors", "gguf", "pytorch_zip", "pytorch_pickle"):
        return True, msg
    return False, msg

# ═══════════════════════════════════════════════════════════════════════════════
# STREAMING DOWNLOAD A /tmp LOCAL
# ═══════════════════════════════════════════════════════════════════════════════

def _stream_download_to_local(
    url: str, dest_path: Path, task_id: str,
    headers: Dict[str, str], timeout: int = 7200, allow_resume: bool = True,
) -> Tuple[int, int, float]:
    DIR_TEMP.mkdir(parents=True, exist_ok=True)
    partial = DIR_TEMP / f"{task_id}_{dest_path.name}.partial"
    already_have = partial.stat().st_size if (allow_resume and partial.exists()) else 0

    req_headers = dict(headers)
    req_headers.setdefault("User-Agent", CIVITAI_USER_AGENT)
    if already_have >= RESUME_MIN_SIZE:
        req_headers["Range"] = f"bytes={already_have}-"
        if _dlog:
            _dlog.info(task_id, f"Resume Range desde {_human_size(already_have)}")

    t0 = time.time()
    last_log_t = t0
    last_log_bytes = already_have
    bytes_downloaded = already_have

    resp = requests.get(url, headers=req_headers, stream=True, timeout=timeout)

    if already_have > 0 and resp.status_code == 200:
        if _dlog:
            _dlog.warn(task_id, "Servidor no aceptó Range, reiniciando descarga desde 0")
        already_have = 0
        bytes_downloaded = 0
        partial.unlink(missing_ok=True)

    if resp.status_code not in (200, 206):
        resp.close()
        partial.unlink(missing_ok=True)
        raise ValueError(f"HTTP {resp.status_code} en {url}")

    total_size = int(resp.headers.get("Content-Length", 0)) + (already_have if resp.status_code == 206 else 0)

    mode = "ab" if already_have > 0 else "wb"
    bytes_this_session = 0
    try:
        with open(partial, mode, buffering=DOWNLOAD_CHUNK_SIZE) as f:
            for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if not chunk:
                    continue
                f.write(chunk)
                bytes_downloaded += len(chunk)
                bytes_this_session += len(chunk)
                now = time.time()
                if (bytes_downloaded - last_log_bytes) >= DOWNLOAD_LOG_EVERY_MB * 1024 * 1024 or (now - last_log_t) >= DOWNLOAD_LOG_EVERY_SEC:
                    elapsed = max(now - t0, 0.001)
                    speed = (bytes_downloaded - already_have) / elapsed
                    if total_size:
                        pct = (bytes_downloaded / total_size) * 100
                        eta = (total_size - bytes_downloaded) / speed if speed > 0 else 0
                        _dlog.info(task_id, f"progreso {pct:5.1f}% ({_human_size(bytes_downloaded)}/{_human_size(total_size)}) vel={_human_speed(speed)} eta={_human_eta(eta)}")
                    else:
                        _dlog.info(task_id, f"progreso {_human_size(bytes_downloaded)} vel={_human_speed(speed)}")
                    last_log_t = now
                    last_log_bytes = bytes_downloaded
    finally:
        resp.close()

    elapsed = max(time.time() - t0, 0.001)
    return bytes_downloaded, total_size, bytes_this_session / elapsed

# ═══════════════════════════════════════════════════════════════════════════════
# CIVITAI & HF DOWNLOAD
# ═══════════════════════════════════════════════════════════════════════════════

def _civitai_resolve_download_url(url: str, api_key: str) -> Tuple[str, Dict[str, str], Optional[str]]:
    req_headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": CIVITAI_USER_AGENT,
        "Accept": "*/*",
    }
    resp = requests.get(url, headers=req_headers, allow_redirects=False, timeout=30)
    discovered_filename = None

    if resp.status_code in (301, 302, 303, 307, 308):
        target_url = resp.headers.get("Location", "")
        clean_headers = {"User-Agent": CIVITAI_USER_AGENT, "Accept": "*/*"}
        cd = resp.headers.get("Content-Disposition", "")
        if 'filename="' in cd:
            discovered_filename = cd.split('filename="')[1].split('"')[0]
        return target_url, clean_headers, discovered_filename

    if resp.status_code == 200:
        return url, req_headers, discovered_filename

    raise ValueError(f"CivitAI API respondió HTTP {resp.status_code}")

def download_civitai(
    url: str, name: str, target: str,
    overwrite: bool = False, skip_duplicate: bool = True,
    skip_validation: bool = False, subfolder: str = "", task_id: str = "",
) -> Dict[str, Any]:
    global _history

    if not task_id:
        task_id = uuid.uuid4().hex[:12]
    if not _dlog:
        raise RuntimeError("DownloadLog no inicializado")

    api_key = _get_civitai_key()
    if not api_key:
        raise ValueError("CIVITAI_API_KEY no configurada")

    dest_dir = _resolve_target_dir(target)
    sub = _safe_subfolder(subfolder)
    if sub:
        dest_dir = dest_dir / sub
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(name)
    if not safe_name:
        raise ValueError(f"Nombre inválido: '{name}'")

    final_path = dest_dir / f"{safe_name}.safetensors"
    relative_path = str(final_path.relative_to(VOL_MAIN))

    if final_path.exists() and not overwrite:
        _dlog.warn(task_id, f"Skipped: {final_path.name} ya existe. Usá overwrite=true.")
        _dlog.write(task_id, "INFO", "[DONE]")
        return {
            "success": False, "skipped": True, "reason": "duplicate_name",
            "path": str(final_path), "relative_path": relative_path,
            "task_id": task_id,
        }

    _dlog.info(task_id, f"Iniciando descarga CivitAI: {safe_name}")
    _downloads_sem.acquire()
    try:
        t_start = time.time()
        final_download_url, clean_headers, cd_name = _civitai_resolve_download_url(url, api_key)

        if cd_name and not name:
            safe_name = _safe_filename(Path(cd_name).stem)
            final_path = dest_dir / f"{safe_name}{Path(cd_name).suffix}"
            relative_path = str(final_path.relative_to(VOL_MAIN))

        _stream_download_to_local(
            url=final_download_url, dest_path=final_path,
            task_id=task_id, headers=clean_headers,
            timeout=7200, allow_resume=True,
        )

        local_partial = DIR_TEMP / f"{task_id}_{final_path.name}.partial"
        if not local_partial.exists():
            raise IOError("No se encontró el archivo temporal descargado")

        if not skip_validation:
            valid, msg = _validate_model_file(local_partial)
            if not valid:
                local_partial.unlink(missing_ok=True)
                raise ValueError(f"Validación fallida: {msg}")

        file_hash = ""
        final_size = local_partial.stat().st_size

        if skip_duplicate and _history:
            _dlog.info(task_id, "Calculando SHA256...")
            file_hash = _compute_sha256(local_partial)
            existing_path = _history.check_duplicate_hash(file_hash)
            if existing_path:
                check_path = VOL_MAIN / existing_path
                if check_path.exists() and check_path != final_path:
                    local_partial.unlink(missing_ok=True)
                    _dlog.warn(task_id, f"Duplicado por hash: {existing_path}")
                    _dlog.write(task_id, "INFO", "[DONE]")
                    return {
                        "success": False, "skipped": True, "reason": "duplicate_hash",
                        "duplicate_of": existing_path, "file_hash": file_hash,
                        "task_id": task_id,
                    }

        if final_path.exists():
            final_path.unlink()
        shutil.move(str(local_partial), str(final_path))

        elapsed_total = max(time.time() - t_start, 0.001)

        if _history:
            _history.log(
                action="download",
                details={"source": "civitai", "url": url, "name": safe_name, "target": target},
                result="success", file_hash=file_hash,
                final_path=relative_path, size_bytes=final_size,
            )

        if _listall_cache:
            _listall_cache.on_file_added(relative_path, final_size)

        _dlog.info(task_id, f"✓ Completado: {final_path.name} ({_human_size(final_size)}) en {elapsed_total:.1f}s")
        _dlog.write(task_id, "INFO", "[DONE]")

        return {
            "success": True, "source": "civitai", "name": safe_name,
            "filename": final_path.name, "path": str(final_path),
            "relative_path": relative_path, "target": target,
            "size_bytes": final_size, "size_human": _human_size(final_size),
            "file_hash": file_hash, "download_time_sec": round(elapsed_total, 2),
            "task_id": task_id, "log_url": f"/download/log?task_id={task_id}",
        }
    except Exception as e:
        _dlog.error(task_id, str(e))
        _dlog.write(task_id, "ERROR", "[FAIL]")
        raise
    finally:
        _downloads_sem.release()

def download_huggingface(
    repo_id: str, filename: str, target: str,
    subfolder: str = "", hf_subfolder: str = "",
    name: str = "", overwrite: bool = False,
    skip_duplicate: bool = True, task_id: str = "",
) -> Dict[str, Any]:
    global _history

    if not task_id:
        task_id = uuid.uuid4().hex[:12]
    if not _dlog:
        raise RuntimeError("DownloadLog no inicializado")

    hf_token = _get_hf_token()
    dest_dir = _resolve_target_dir(target)
    sub = _safe_subfolder(subfolder)
    if sub:
        dest_dir = dest_dir / sub
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_filename(name) if name else Path(filename).stem
    ext = Path(filename).suffix or ".safetensors"
    final_path = dest_dir / f"{safe_name}{ext}"
    relative_path = str(final_path.relative_to(VOL_MAIN))

    if final_path.exists() and not overwrite:
        _dlog.warn(task_id, f"Skipped: {final_path.name} ya existe.")
        _dlog.write(task_id, "INFO", "[DONE]")
        return {
            "success": False, "skipped": True, "reason": "duplicate_name",
            "path": str(final_path), "relative_path": relative_path,
            "task_id": task_id,
        }

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise RuntimeError("huggingface_hub no instalado")

    _dlog.info(task_id, f"Iniciando descarga HF: {repo_id}/{filename}")
    _downloads_sem.acquire()
    tmp_local_dir = DIR_TEMP / f"hf_{task_id}"
    tmp_local_dir.mkdir(parents=True, exist_ok=True)

    try:
        t_start = time.time()
        dl_path = Path(hf_hub_download(
            repo_id=repo_id, filename=filename,
            subfolder=hf_subfolder or None,
            token=hf_token or None,
            local_dir=tmp_local_dir,
            local_dir_use_symlinks=False,
        ))

        valid, msg = _validate_model_file(dl_path)
        if not valid:
            raise ValueError(f"Validación fallida: {msg}")

        final_size = dl_path.stat().st_size
        file_hash = ""

        if skip_duplicate and _history:
            file_hash = _compute_sha256(dl_path)
            existing_path = _history.check_duplicate_hash(file_hash)
            if existing_path:
                check_path = VOL_MAIN / existing_path
                if check_path.exists() and check_path != final_path:
                    _dlog.warn(task_id, f"Duplicado por hash: {existing_path}")
                    _dlog.write(task_id, "INFO", "[DONE]")
                    return {
                        "success": False, "skipped": True, "reason": "duplicate_hash",
                        "duplicate_of": existing_path, "file_hash": file_hash,
                        "task_id": task_id,
                    }

        if final_path.exists():
            final_path.unlink()
        shutil.move(str(dl_path), str(final_path))

        elapsed_total = max(time.time() - t_start, 0.001)

        if _history:
            _history.log(
                action="download",
                details={"source": "huggingface", "repo_id": repo_id, "filename": filename, "name": safe_name},
                result="success", file_hash=file_hash,
                final_path=relative_path, size_bytes=final_size,
            )

        if _listall_cache:
            _listall_cache.on_file_added(relative_path, final_size)

        _dlog.info(task_id, f"✓ Completado HF: {final_path.name} ({_human_size(final_size)}) en {elapsed_total:.1f}s")
        _dlog.write(task_id, "INFO", "[DONE]")

        return {
            "success": True, "source": "huggingface", "repo_id": repo_id,
            "filename": final_path.name, "path": str(final_path),
            "relative_path": relative_path, "target": target,
            "size_bytes": final_size, "size_human": _human_size(final_size),
            "file_hash": file_hash, "download_time_sec": round(elapsed_total, 2),
            "task_id": task_id, "log_url": f"/download/log?task_id={task_id}",
        }
    except Exception as e:
        _dlog.error(task_id, str(e))
        _dlog.write(task_id, "ERROR", "[FAIL]")
        raise
    finally:
        shutil.rmtree(tmp_local_dir, ignore_errors=True)
        _downloads_sem.release()

def download_model(item: Dict[str, Any], task_id: str = "") -> Dict[str, Any]:
    source = item.get("source", "").lower()
    name = item.get("name", "")
    target = item.get("target", "lora")
    overwrite = bool(item.get("overwrite", False))
    skip_duplicate = bool(item.get("skip_duplicate", True))
    subfolder = item.get("subfolder", "") or ""

    if not name and source != "huggingface":
        raise ValueError("'name' es obligatorio")
    if not target:
        raise ValueError("'target' es obligatorio")

    if not task_id:
        task_id = uuid.uuid4().hex[:12]

    if source == "civitai":
        url = item.get("url", "")
        if not url:
            raise ValueError("CivitAI requiere 'url'")
        return download_civitai(
            url=url, name=name, target=target,
            overwrite=overwrite, skip_duplicate=skip_duplicate,
            subfolder=subfolder, task_id=task_id,
        )
    elif source in ("huggingface", "hf"):
        repo_id = item.get("repo_id", "")
        filename = item.get("filename", "")
        hf_subfolder = item.get("hf_subfolder", "")
        hf_uri = item.get("hf_uri", "") or item.get("url", "")
        if hf_uri:
            parsed = _parse_hf_uri(hf_uri)
            if parsed:
                repo_id = parsed["repo_id"]
                filename = filename or parsed["filename"]
                hf_subfolder = hf_subfolder or parsed["hf_subfolder"]
        if not repo_id or not filename:
            raise ValueError("HuggingFace requiere 'repo_id' y 'filename' (o 'hf_uri')")
        return download_huggingface(
            repo_id=repo_id, filename=filename, target=target,
            subfolder=subfolder, hf_subfolder=hf_subfolder, name=name,
            overwrite=overwrite, skip_duplicate=skip_duplicate, task_id=task_id,
        )
    else:
        raise ValueError(f"Source desconocido: '{source}'. Válidos: civitai, huggingface")

# ═══════════════════════════════════════════════════════════════════════════════
# FILE & FOLDER OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_volume_path(relative: str) -> Path:
    # Resuelve una ruta relativa contra los volúmenes montados.
    # En Modal son mounts SEPARADOS: /krea2-beam-volume (principal) y
    # /krea2-loras-renew (personajes). Se aceptan dos formas para el renew:
    #   1) prefijo explícito: "krea2-loras-renew/Personaje/x.safetensors"
    #   2) fallback por existencia: "Personaje/x.safetensors" (como lo manda
    #      la WebUI) → si no existe en el principal, se prueba en el renew.
    # Todo lo demás sigue resolviendo contra VOL_MAIN (comportamiento legacy).
    safe = _safe_relative_path(relative).replace("\\", "/")
    if not safe:
        raise ValueError("Path vacío")
    base = VOL_MAIN
    rel = safe
    if safe == "krea2-loras-renew" or safe.startswith("krea2-loras-renew/"):
        rel = safe[len("krea2-loras-renew"):].lstrip("/")
        if not rel:
            raise ValueError("No se puede operar sobre la raíz del volumen renew")
        base = VOL_LORAS
    elif not (VOL_MAIN / rel).exists() and (VOL_LORAS / rel).exists():
        base = VOL_LORAS
    full = base / rel
    try:
        full.resolve().relative_to(base.resolve())
    except ValueError:
        raise ValueError(f"Path fuera del volumen: {relative}")
    return full

def delete_files(paths: List[str]) -> Dict[str, Any]:
    results = []
    for rel in paths:
        try:
            full = _resolve_volume_path(rel)
            if not full.exists():
                results.append({"path": rel, "success": False, "error": "No existe"})
                continue
            size = full.stat().st_size if full.is_file() else 0
            if full.is_dir():
                shutil.rmtree(str(full))
            else:
                full.unlink()
            results.append({"path": rel, "success": True, "size_bytes": size})
            if _listall_cache:
                _listall_cache.on_path_removed(rel)
        except Exception as e:
            results.append({"path": rel, "success": False, "error": str(e)})
    return {
        "deleted": sum(1 for r in results if r["success"]),
        "failed": sum(1 for r in results if not r["success"]),
        "results": results,
    }

def move_file(src_rel: str, target_rel: str) -> Dict[str, Any]:
    src = _resolve_volume_path(src_rel)
    if not src.exists():
        raise FileNotFoundError(f"No existe: {src_rel}")
    dst = _resolve_volume_path(target_rel)
    if dst.is_dir() or target_rel.endswith("/"):
        dst = dst / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    dst_rel = str(dst.relative_to(VOL_MAIN))
    if _listall_cache:
        _listall_cache.on_path_moved(src_rel, dst_rel)
    return {"success": True, "from": src_rel, "to": dst_rel}

def rename_file(path_rel: str, new_name: str) -> Dict[str, Any]:
    full = _resolve_volume_path(path_rel)
    if not full.exists():
        raise FileNotFoundError(f"No existe: {path_rel}")
    safe_new = _safe_filename(new_name)
    new_path = full.parent / safe_new
    if new_path.exists():
        raise ValueError(f"Ya existe destino: {safe_new}")
    full.rename(new_path)
    new_rel = str(new_path.relative_to(VOL_MAIN))
    if _listall_cache:
        _listall_cache.on_path_moved(path_rel, new_rel)
    return {"success": True, "from": path_rel, "to": new_rel}

def create_folder(rel_path: str, parents: bool = True) -> Dict[str, Any]:
    target = _resolve_volume_path(rel_path)
    if target.exists():
        return {"success": False, "skipped": True, "reason": "already_exists", "path": rel_path}
    target.mkdir(parents=parents, exist_ok=False)
    rel = str(target.relative_to(VOL_MAIN))
    with _file_index._lock:
        _file_index._dirs.add(rel)
        _file_index._version += 1
    if _listall_cache:
        with _listall_cache._lock:
            _listall_cache._status = "dirty"
    return {"success": True, "path": rel}

def delete_folder(rel_path: str, recursive: bool = False) -> Dict[str, Any]:
    target = _resolve_volume_path(rel_path)
    if not target.exists():
        return {"success": False, "error": "No existe"}
    if not target.is_dir():
        raise ValueError("No es carpeta")
    rel = str(target.relative_to(VOL_MAIN))
    shutil.rmtree(str(target))
    if _listall_cache:
        _listall_cache.on_path_removed(rel)
    return {"success": True, "path": rel}

def move_folder(src_rel: str, dst_rel: str) -> Dict[str, Any]:
    src = _resolve_volume_path(src_rel)
    dst = _resolve_volume_path(dst_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    src_r = str(src.relative_to(VOL_MAIN))
    dst_r = str(dst.relative_to(VOL_MAIN))
    if _listall_cache:
        _listall_cache.on_path_moved(src_r, dst_r)
    return {"success": True, "from": src_r, "to": dst_r}

def list_folder(rel_path: str = "") -> Dict[str, Any]:
    target = VOL_MAIN if not rel_path else _resolve_volume_path(rel_path)
    if not target.exists() or not target.is_dir():
        return {"success": False, "error": "Carpeta inválida"}
    rel = str(target.relative_to(VOL_MAIN)) if rel_path else ""
    folders, files = [], []
    for d in _file_index._dirs:
        if rel:
            if d.startswith(rel + "/") and "/" not in d[len(rel) + 1:]:
                folders.append({"name": Path(d).name, "path": d})
        elif "/" not in d:
            folders.append({"name": d, "path": d})
    for e in _file_index.list_by_parent(rel):
        files.append({"name": e.name, "size_bytes": e.size, "size_human": _human_size(e.size), "mtime": e.mtime})
    return {"success": True, "path": rel, "folders": folders, "files": files}

def cleanup_outputs(older_than_days: int, dry_run: bool = True) -> Dict[str, Any]:
    if not DIR_OUTPUTS.exists():
        return {"deleted": 0, "files": []}
    cutoff = time.time() - (older_than_days * 86400)
    to_delete: List[Path] = []
    try:
        with os.scandir(str(DIR_OUTPUTS)) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if os.path.splitext(entry.name)[1].lower() != ".png":
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        to_delete.append(Path(entry.path))
                except OSError:
                    continue
    except OSError:
        return {"deleted": 0, "files": []}
    if dry_run:
        return {
            "dry_run": True, "would_delete": len(to_delete),
            "files": [p.name for p in to_delete],
            "space_freed": _human_size(sum(p.stat().st_size for p in to_delete if p.exists())),
        }
    deleted = 0
    freed = 0
    deleted_names = []
    for p in to_delete:
        try:
            freed += p.stat().st_size
            p.unlink()
            deleted += 1
            deleted_names.append(p.name)
        except OSError:
            continue
    if _listall_cache and deleted > 0:
        for name in deleted_names:
            _listall_cache.on_path_removed(f"outputs/{name}")
    if _history and deleted > 0:
        _history.log(
            action="cleanup",
            details={"older_than_days": older_than_days, "deleted_count": deleted, "deleted_files": deleted_names[:50]},
            result="success", size_bytes=freed,
        )
    return {"dry_run": False, "deleted": deleted, "space_freed": _human_size(freed), "files": deleted_names}

def archive_outputs(filenames: List[str]) -> Dict[str, Any]:
    now = datetime.now()
    archive_dir = DIR_ARCHIVED / now.strftime("%Y-%m")
    archive_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    errors = []
    for fname in filenames:
        safe = _safe_filename(fname)
        src = DIR_OUTPUTS / safe
        if not src.exists():
            errors.append({"filename": fname, "error": "No existe"})
            continue
        dst = archive_dir / safe
        if dst.exists():
            stem = dst.stem
            suffix = dst.suffix
            counter = 1
            while dst.exists():
                dst = archive_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        size = src.stat().st_size
        shutil.move(str(src), str(dst))
        dst_rel = str(dst.relative_to(VOL_MAIN))
        moved.append({"filename": fname, "archived_to": dst_rel})
        if _listall_cache:
            _listall_cache.on_path_moved(f"outputs/{safe}", dst_rel)
        if _history:
            _history.log(action="archive", details={"from": f"outputs/{safe}", "to": dst_rel},
                         result="success", final_path=dst_rel, size_bytes=size)
    return {"moved": len(moved), "errors": len(errors), "results": moved, "errors_detail": errors}

def create_outputs_zip(filenames: List[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in filenames:
            safe = _safe_filename(fname)
            path = DIR_OUTPUTS / safe
            if path.exists() and path.is_file():
                zf.write(str(path), arcname=safe)
    buf.seek(0)
    return buf.getvalue()

# ═══════════════════════════════════════════════════════════════════════════════
# BUILD LIST_ALL PAYLOAD
# ═══════════════════════════════════════════════════════════════════════════════

def _build_list_all_payload() -> Dict[str, Any]:
    output_files = list_outputs()
    return {
        "success": True,
        "ts": time.time(),
        "loras": {
            "krea2-beam-volume": {
                "mount": str(VOL_MAIN), "path": str(DIR_LORAS_MAIN),
                "tree": _scan_dir_fast(
                    DIR_LORAS_MAIN, _LORA_EXTS,
                    exclude_dirs=_EXCLUDED_LORA_DIRS,
                ),
            },
            "krea2-loras-renew": {
                "mount": str(VOL_LORAS), "path": str(VOL_LORAS),
                "tree": _scan_dir_fast(
                    VOL_LORAS, _LORA_EXTS,
                    exclude_dirs=_EXCLUDED_LORA_DIRS,
                ),
            },
        },
        "unets": {
            "krea2-beam-volume": {
                "mount": str(VOL_MAIN), "path": str(DIR_DIFFUSION),
                "tree": _scan_dir_fast(DIR_DIFFUSION, _MODEL_EXTS),
            }
        },
        "clips": {
            "krea2-beam-volume": {
                "mount": str(VOL_MAIN), "path": str(DIR_TEXT_ENCODERS),
                "tree": _scan_dir_fast(DIR_TEXT_ENCODERS, _MODEL_EXTS),
            }
        },
        "vaes": {
            "krea2-beam-volume": {
                "mount": str(VOL_MAIN), "path": str(DIR_VAE),
                "tree": _scan_dir_fast(DIR_VAE, _MODEL_EXTS),
            }
        },
        "upscalers": {
            "krea2-beam-volume": {
                "mount": str(VOL_MAIN), "path": str(DIR_UPSCALE),
                "tree": _scan_dir_fast(DIR_UPSCALE, _MODEL_EXTS),
            }
        },
        "outputs": {
            "path": str(DIR_OUTPUTS),
            "count": len(output_files),
            "files": output_files,
        },
    }

# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════════

def handler(context) -> FastAPI:
    global _history, _dlog, _listall_cache

    threading.Thread(target=_audit_writer_loop, name="audit-writer", daemon=True).start()

    _rebind_volume_globals()
    DIR_TEMP.mkdir(parents=True, exist_ok=True)
    DIR_MANAGER.mkdir(parents=True, exist_ok=True)
    DIR_LOGS.mkdir(parents=True, exist_ok=True)
    DIR_CACHE.mkdir(parents=True, exist_ok=True)

    _history = OperationHistory(HISTORY_FILE)
    _dlog = DownloadLog(DIR_LOGS)
    _listall_cache = ListAllCache(DIR_CACHE, ttl=LIST_ALL_CACHE_TTL)
    _listall_cache.warm_up()

    _log_manager("service", phase="boot", extra={"version": SERVICE_VERSION, "pid": os.getpid()})

    app = FastAPI(
        title="Krea2 Manager API (CPU-only)",
        version=SERVICE_VERSION,
        description="v1.1.0: LoRAs colapsados por personaje con variantes de steps. Ignora v1/caches.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _NO_AUDIT_PATHS = {
        "/health", "/pricing", "/gpu-log", "/manager-log",
        "/download/status", "/download/log/stream", "/outputs", "/list_models",
    }

    @app.middleware("http")
    async def _audit_middleware(request: Request, call_next):
        ep = request.url.path
        should_audit = ep not in _NO_AUDIT_PATHS
        t0 = time.time()
        if should_audit:
            _log_manager(ep, phase="received", extra={"method": request.method})
        try:
            response = await call_next(request)
            if should_audit:
                _log_manager(ep, phase="done", ok=response.status_code < 400,
                             duration=time.time() - t0,
                             extra={"method": request.method, "status": response.status_code})
            return response
        except Exception as e:
            if should_audit:
                _log_manager(ep, phase="error", ok=False, error=str(e),
                             duration=time.time() - t0, extra={"method": request.method})
            raise

    @app.get("/health")
    async def health():
        return {
            "status": "ok", "service": "krea2-manager-cpu", "version": SERVICE_VERSION,
            "gpu": False, "ts": time.time(),
            "cache": _listall_cache.status() if _listall_cache else {},
            "index": _file_index.stats(),
            "history": {"entries": len(_history._data["entries"]) if _history else 0},
        }

    @app.get("/pricing")
    async def pricing():
        return {
            "service": "krea2-manager-cpu", "gpu": False,
            "cpu_cores": CPU_CORES, "ram_gib": RAM_GIB,
            "total_price_per_sec": round(TOTAL_PRICE_PER_SEC, 9),
            "total_price_per_hour": round(TOTAL_PRICE_PER_SEC * 3600, 4),
        }

    @app.post("/download")
    async def download(request: Request):
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "JSON inválido")
        try:
            result = await run_in_threadpool(download_model, payload)
            return JSONResponse(result)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/download/batch")
    async def download_batch(request: Request):
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "JSON inválido")
        items = payload.get("items", [])
        if not items:
            raise HTTPException(400, "'items' vacío")
        task_id = _task_registry.create("download_batch", {"count": len(items)})

        def _run_batch():
            results, errors, skipped = [], [], []
            for i, item in enumerate(items):
                sub_task = f"{task_id}-{i:02d}"
                _task_registry.update(task_id, progress={"current": i + 1, "total": len(items), "current_item": item.get("name", "")})
                try:
                    r = download_model(item, task_id=sub_task)
                    if r.get("skipped"):
                        skipped.append(r)
                    else:
                        results.append(r)
                except Exception as e:
                    errors.append({"index": i, "name": item.get("name", ""), "error": str(e)})
            _task_registry.update(task_id, results=results, errors=errors, skipped=skipped)
            _task_registry.finish(task_id, "done_with_errors" if errors else "done")

        threading.Thread(target=_run_batch, daemon=True, name=f"batch-{task_id}").start()
        return {
            "success": True, "task_id": task_id, "items_count": len(items),
            "status_url": f"/download/status?task_id={task_id}",
            "log_stream_url": f"/download/log/stream?task_id={task_id}",
        }

    @app.get("/download/status")
    async def download_status(task_id: str):
        task = _task_registry.get(task_id)
        if not task:
            raise HTTPException(404, f"Task no encontrado: {task_id}")
        return task

    @app.get("/download/log")
    async def download_log(task_id: str, tail: int = 200, raw: bool = False):
        if not _dlog:
            raise HTTPException(503, "DownloadLog no inicializado")
        if raw:
            return StreamingResponse(io.BytesIO(_dlog.raw(task_id).encode("utf-8")), media_type="text/plain; charset=utf-8")
        lines = _dlog.read(task_id, tail)
        return {"success": True, "task_id": task_id, "lines": len(lines), "log": lines}

    @app.get("/download/log/stream")
    async def download_log_stream(task_id: str):
        if not _dlog:
            raise HTTPException(503, "DownloadLog no inicializado")
        return StreamingResponse(
            _dlog.stream_async(task_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.get("/list_all")
    async def list_all(refresh: bool = False):
        if not _listall_cache:
            return {"success": True, "error": "cache no inicializado"}
        if refresh:
            _listall_cache._trigger_background_build()
        return _listall_cache.get()

    @app.post("/cache/rebuild")
    async def cache_rebuild():
        if not _listall_cache:
            raise HTTPException(503, "cache no inicializado")
        _listall_cache._trigger_background_build()
        return {"success": True, "status": "rebuilding"}

    @app.get("/cache/status")
    async def cache_status():
        return _listall_cache.status() if _listall_cache else {}

    @app.get("/list_models")
    async def list_models(type: str = "lora", version: Optional[str] = None):
        """
        Lista modelos en formato listo para web-UI.

        Para `type=lora`:
          - `characters.models[]`: LoRAs de personajes. Cada uno con `variants[]`.
              - `name` = "{character}" o "{character}-{num_steps}"
              - `default` = variante por defecto (final o step más alto)
          - `downloaded.models[]`: LoRAs descargados (lista plana).

        Para otros tipos (unet/clip/vae/upscale):
          - `models[]`: lista plana.
        """
        try:
            data = list_models_recursive(type, version=version)
            return {
                "success": True,
                "version": SERVICE_VERSION,
                "type": type,
                **data,
            }
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.get("/resolve")
    async def resolve(name: str, type: str = "lora", version: Optional[str] = None):
        """
        Resuelve la ruta de un archivo.

        Para LoRAs, acepta:
          - "seungcheol_BXL"             → final
          - "seungcheol_BXL-3000"        → step 3000
          - "seungcheol_BXL-step00003000"→ step 3000
        """
        try:
            path = resolve_model_path(name, type, version=version)
            return {"success": True, "name": name, "type": type, "version": version, "resolved_path": path}
        except Exception as e:
            raise HTTPException(404, str(e))

    @app.post("/folders/create")
    async def folders_create(request: Request):
        payload = await request.json()
        return await run_in_threadpool(create_folder, payload.get("path", ""), payload.get("parents", True))

    @app.post("/folders/delete")
    async def folders_delete(request: Request):
        payload = await request.json()
        return await run_in_threadpool(delete_folder, payload.get("path", ""), payload.get("recursive", False))

    @app.post("/folders/move")
    async def folders_move(request: Request):
        payload = await request.json()
        return await run_in_threadpool(move_folder, payload.get("from", ""), payload.get("to", ""))

    @app.get("/folders/list")
    async def folders_list(path: str = ""):
        return list_folder(path)

    @app.post("/models/delete")
    async def models_delete(request: Request):
        payload = await request.json()
        return await run_in_threadpool(delete_files, payload.get("paths", []))

    @app.post("/models/move")
    async def models_move(request: Request):
        payload = await request.json()
        return await run_in_threadpool(move_file, payload.get("path", ""), payload.get("target_path", ""))

    @app.post("/models/rename")
    async def models_rename(request: Request):
        payload = await request.json()
        return await run_in_threadpool(rename_file, payload.get("path", ""), payload.get("new_name", ""))

    @app.get("/outputs")
    async def outputs(limit: int = 200):
        files = list_outputs(limit)
        return {"success": True, "count": len(files), "files": files}

    @app.get("/outputs/download")
    async def outputs_download(filenames: str):
        names = [n.strip() for n in filenames.split(",") if n.strip()]
        zip_bytes = await run_in_threadpool(create_outputs_zip, names)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=krea2_outputs_{ts}.zip"},
        )

    @app.get("/gpu-log")
    async def gpu_log(tail: int = 200, raw: bool = False, grep: str = ""):
        lines = await run_in_threadpool(_read_gpu_log_cached)
        if grep:
            lines = [l for l in lines if grep in l]
        lines = lines[-tail:] if tail > 0 else lines
        if raw:
            return StreamingResponse(io.BytesIO("\n".join(lines).encode("utf-8")), media_type="text/plain; charset=utf-8")
        entries = []
        for l in lines:
            try:
                entries.append(json.loads(l))
            except Exception:
                entries.append({"raw": l})
        return {"success": True, "count": len(entries), "entries": entries}

    @app.get("/manager-log")
    async def manager_log(tail: int = 200, raw: bool = False, grep: str = ""):
        with _audit_ring_lock:
            raw_entries = list(_audit_ring)
        if grep:
            raw_entries = [e for e in raw_entries if grep in json.dumps(e)]
        raw_entries = raw_entries[-tail:] if tail > 0 else raw_entries
        if raw:
            body = "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in raw_entries)
            return StreamingResponse(io.BytesIO(body.encode("utf-8")), media_type="text/plain; charset=utf-8")
        return {"success": True, "count": len(raw_entries), "entries": raw_entries}

    @app.post("/outputs/upload")
    async def outputs_upload(
        file: UploadFile = File(...),
        name: str = Form(""),
        subfolder: str = Form(""),
    ):
        ext = Path(file.filename or "upload.png").suffix.lower()
        if ext not in _IMAGE_EXTS:
            raise HTTPException(400, f"Extensión no permitida: {ext}")
        if name:
            safe_name = _safe_filename(name)
            if not safe_name:
                raise HTTPException(400, f"Nombre inválido: '{name}'")
            if not Path(safe_name).suffix:
                safe_name += ext
            final_name = safe_name
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            final_name = f"{ts}_upload{ext}"
        sub = _safe_subfolder(subfolder)
        target_dir = DIR_OUTPUTS / sub if sub else DIR_OUTPUTS
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / final_name
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        content = await file.read()
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(400, "Archivo demasiado grande (max 50MB)")
        file_hash = _compute_sha256_bytes(content)
        if _history:
            existing = _history.check_duplicate_hash(file_hash)
            if existing:
                check_path = VOL_MAIN / existing
                if check_path.exists():
                    return {"success": False, "skipped": True, "reason": "duplicate_hash",
                            "error": f"Imagen idéntica ya existe: {existing}",
                            "duplicate_of": existing}
        dest.write_bytes(content)
        dest_rel = str(dest.relative_to(VOL_MAIN))
        if _listall_cache:
            _listall_cache.on_file_added(dest_rel, len(content))
        if _history:
            _history.log(action="upload",
                         details={"original_filename": file.filename, "name": final_name, "subfolder": sub},
                         result="success", file_hash=file_hash,
                         final_path=dest_rel, size_bytes=len(content))
        return {"success": True, "filename": dest.name, "relative_path": dest_rel,
                "size_bytes": len(content), "size_human": _human_size(len(content)),
                "file_hash": file_hash, "url": _build_serve_url(dest.name)}

    @app.post("/outputs/delete")
    async def outputs_delete(request: Request):
        payload = await request.json()
        filenames = payload.get("filenames", [])
        sub = _safe_subfolder(payload.get("subfolder", ""))
        if not filenames:
            raise HTTPException(400, "'filenames' vacío")
        prefix = f"outputs/{sub + '/' if sub else ''}"
        paths = [f"{prefix}{_safe_filename(f)}" for f in filenames]
        result = await run_in_threadpool(delete_files, paths)
        return result

    @app.post("/outputs/move")
    async def outputs_move(request: Request):
        payload = await request.json()
        filenames = payload.get("filenames", [])
        target = payload.get("target", "")
        sub = _safe_subfolder(payload.get("subfolder", ""))
        if not filenames or not target:
            raise HTTPException(400, "'filenames' y 'target' obligatorios")
        results = []
        prefix = f"outputs/{sub + '/' if sub else ''}"
        for fname in filenames:
            safe = _safe_filename(fname)
            src_rel = f"{prefix}{safe}"
            dst_rel = f"{target}/{safe}"
            try:
                r = await run_in_threadpool(move_file, src_rel, dst_rel)
                results.append(r)
            except Exception as e:
                results.append({"path": src_rel, "success": False, "error": str(e)})
        return {"results": results}

    @app.post("/outputs/rename")
    async def outputs_rename(request: Request):
        payload = await request.json()
        filename = payload.get("filename", "")
        new_name = payload.get("new_name", "")
        sub = _safe_subfolder(payload.get("subfolder", ""))
        if not filename or not new_name:
            raise HTTPException(400, "'filename' y 'new_name' obligatorios")
        prefix = f"outputs/{sub + '/' if sub else ''}"
        try:
            result = await run_in_threadpool(
                rename_file, f"{prefix}{_safe_filename(filename)}", new_name
            )
            return result
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.post("/outputs/archive")
    async def outputs_archive(request: Request):
        payload = await request.json()
        filenames = payload.get("filenames", [])
        if not filenames:
            raise HTTPException(400, "'filenames' vacío")
        result = await run_in_threadpool(archive_outputs, filenames)
        return result

    @app.post("/outputs/cleanup")
    async def outputs_cleanup(request: Request):
        payload = await request.json()
        days = int(payload.get("older_than_days", 7))
        dry_run = bool(payload.get("dry_run", True))
        if days < 1:
            raise HTTPException(400, "older_than_days debe ser >= 1")
        result = await run_in_threadpool(cleanup_outputs, days, dry_run)
        return result

    @app.get("/history/stats")
    async def history_stats():
        return _history.get_stats() if _history else {"total_entries": 0}

    @app.post("/history/rebuild_index")
    async def history_rebuild_index():
        scan_dirs = [DIR_DIFFUSION, DIR_LORAS_MAIN, DIR_TEXT_ENCODERS,
                     DIR_VAE, DIR_UPSCALE, DIR_OUTPUTS]
        result = await run_in_threadpool(_history.rebuild_hash_index, scan_dirs)
        return {"success": True, **result}

    @app.get("/history/download")
    async def history_download():
        return StreamingResponse(
            io.BytesIO(json.dumps(_history._data, indent=2, ensure_ascii=False).encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=krea2_history.json"},
        )

    @app.post("/cache/invalidate")
    async def cache_invalidate(scope: str = "list_all"):
        if not _listall_cache:
            raise HTTPException(503, "cache no inicializado")
        with _listall_cache._lock:
            _listall_cache._status = "dirty"
        _listall_cache._trigger_background_build()
        return {"success": True, "invalidated": scope, "status": "rebuilding"}

    @app.get("/history")
    async def history_list(action: str = "", limit: int = 100, offset: int = 0):
        entries = _history.get_entries(action=action, limit=limit, offset=offset) if _history else []
        return {"success": True, "count": len(entries), "entries": entries}

    return app

# ═══════════════════════════════════════════════════════════════════════════════
# MODAL DEPLOY
# ═══════════════════════════════════════════════════════════════════════════════

@app.function(
    image=IMAGE,
    cpu=CPU_CORES,
    memory=RAM_GIB * 1024,
    volumes=VOLUMES,
    secrets=[R2_SECRET, CIVITAI_SECRET, HF_SECRET],
    timeout=3600,
    scaledown_window=60,
    min_containers=0,
    max_containers=1,
)
@modal.asgi_app()
def fastapi_app():
    return handler(None)

@app.local_entrypoint()
def main():
    print(f"App v{SERVICE_VERSION} configurada.")
    print("Despliega con: modal deploy krea2_manager_modal-v1.py")