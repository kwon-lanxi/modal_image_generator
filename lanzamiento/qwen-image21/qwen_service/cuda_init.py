"""Bounded workaround documented by Modal for flaky L4 CUDA initialization."""
import ctypes
import time
from .logging_utils import logger


def ensure_cuda():
    cuda = ctypes.CDLL("libcuda.so.1")
    for attempt in range(10):
        code = cuda.cuInit(0)
        if code == 0:
            return
        logger("gpu.startup").warning("cuda.init.retry", extra={"fields": {"attempt": attempt + 1, "code": code}})
        if attempt < 9:
            time.sleep(0.5)
    raise RuntimeError("CUDA de L4 no inició después de 10 intentos; consultar el estado de Modal")
