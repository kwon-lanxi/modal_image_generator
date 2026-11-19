"""Best-effort live progress. Full logs remain in Volume/stdout."""
import time


def publisher(store):
    last = {}
    gpus = {}

    def publish(event):
        if event["event"] not in {"stage.start", "sampling.progress", "download.progress", "job.start", "job.complete",
                                  "job.failed", "stage.failed", "output.ready", "gpu.ready"}:
            return
        job = event["job_id"]
        now = time.monotonic()
        if event["event"] == "sampling.progress":
            # Keep all steps in logs; bound remote writes during fast sampling.
            if event["step"] not in (1, event["total"]) and now - last.get(job, 0) < 1:
                return
        if event.get("gpu"):
            gpus[job] = event["gpu"]
        elif job not in gpus:
            # A CPU publisher resumes after the GPU publisher in another container.
            previous = store.get(job, None) if hasattr(store, "get") else None
            if previous and previous.get("gpu"):
                gpus[job] = previous["gpu"]
        published = {**event, "gpu": gpus[job]} if job in gpus else event
        store.put(job, published)
        last[job] = now
        if len(last) > 128:
            oldest = next(iter(last))
            last.pop(oldest)
            gpus.pop(oldest, None)

    return publish
