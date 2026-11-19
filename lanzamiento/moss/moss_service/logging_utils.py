from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def redact(message: str) -> str:
    message = str(message)
    for key, value in os.environ.items():
        if len(value) >= 8 and any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "ACCESS_KEY", "API_KEY")):
            message = message.replace(value, "[redacted]")
    message = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", message)
    message = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", message)
    return message[:2000]


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            # Exception class only: third-party exceptions may contain tokens/URLs.
            payload["exception"] = record.exc_info[0].__name__
            payload["detail"] = redact(record.exc_info[1])
            payload["stack"] = [
                {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                for f in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(payload, ensure_ascii=False, default=str)


def logger(component: str) -> logging.Logger:
    log = logging.getLogger(f"moss.{component}")
    if not log.handlers:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(JsonFormatter())
        log.addHandler(stream)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


class JobLog:
    """Per-job structured logs, committed by the sole CPU writer on completion."""

    def __init__(self, job_id: str, path: Path | None = None, component="cpu", sink=None):
        self.job_id, self.path, self.log = job_id, path, logger(component)
        self.sink = sink
        self.records = []

    def event(self, name: str, *, level=logging.INFO, **fields):
        safe = {k: v for k, v in fields.items() if k not in {
            "prompt", "positive_prompt", "negative_prompt", "token", "headers", "url", "payload"
        }}
        record = {"job_id": self.job_id, **safe}
        self.log.log(level, name, extra={"fields": record})
        item = {"ts": datetime.now(timezone.utc).isoformat(), "event": name,
                "level": logging.getLevelName(level), **record}
        self.records.append(item)
        if self.sink:
            try:
                self.sink(item)
            except Exception as exc:
                self.log.warning("progress.publish.failed", extra={"fields": {"error": type(exc).__name__}})
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")

    @contextmanager
    def stage(self, name: str, timings: dict):
        started = time.perf_counter()
        self.event("stage.start", stage=name)
        try:
            yield
        except Exception as exc:
            self.log.exception("stage.failed", extra={"fields": {"job_id": self.job_id, "stage": name}})
            self.event("stage.failed", stage=name, error=type(exc).__name__, detail=redact(exc), level=logging.ERROR)
            raise
        finally:
            elapsed = round(time.perf_counter() - started, 4)
            timings[name] = elapsed
            self.event("stage.end", stage=name, seconds=elapsed)
