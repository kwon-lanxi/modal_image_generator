"""Snapshot hygiene and timings; imported only in the GPU lifecycle."""
import gc
import json
import time
from contextlib import contextmanager


@contextmanager
def timed(event):
    started=time.perf_counter()
    try:
        yield
    finally:
        print(json.dumps({"event":event,"seconds":round(time.perf_counter()-started,4)}))


def memory_stats():
    import psutil
    import torch
    return {"rss_bytes":psutil.Process().memory_info().rss,
            "cuda_allocated_bytes":torch.cuda.memory_allocated(),
            "cuda_reserved_bytes":torch.cuda.memory_reserved()}


def compact_snapshot():
    import torch
    torch.cuda.synchronize()
    before=memory_stats()
    started=time.perf_counter()
    gc.collect()
    # Release only unused allocator blocks. Live model tensors stay resident.
    torch.cuda.empty_cache()
    # Return freed host heap pages on Linux, without touching live allocations.
    import ctypes
    trim=getattr(ctypes.CDLL(None),"malloc_trim",None)
    if trim is not None:
        trim.argtypes=[ctypes.c_size_t];trim.restype=ctypes.c_int
        trim(0)
    torch.cuda.synchronize()
    after=memory_stats()
    print(json.dumps({"event":"snapshot.compacted","seconds":round(time.perf_counter()-started,4),
                      "before":before,"after":after}))
