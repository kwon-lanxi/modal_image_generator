"""Small HTTP client. Needs KREA2_URL and KREA2_API_TOKEN in environment."""
import argparse
import json
import os
import time
import uuid
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["health", "preflight", "loras", "outputs", "prepare-assets",
                                           "convert-fp8", "generate", "preview"])
    parser.add_argument("--prompt", default="a red apple on a wooden table, soft natural light")
    parser.add_argument("--profile", default="turbo")
    parser.add_argument("--unet")
    parser.add_argument("--clip")
    parser.add_argument("--vae")
    parser.add_argument("--model-type", choices=["turbo", "raw"])
    parser.add_argument("--precision", choices=["default", "fp8_e4m3fn_fast", "fp8_e4m3fn", "fp8_e5m2"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--lora", action="append", default=[], help="Exact ID=weight, may be repeated")
    parser.add_argument("--idempotency-key", default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    deployment = json.loads((root / ".deployment.json").read_text()) if (root / ".deployment.json").exists() else {}
    base = (os.getenv("KREA2_URL") or deployment.get("api_url", "")).rstrip("/")
    if not base:
        raise SystemExit("Configurar KREA2_URL o .deployment.json")
    session = requests.Session()
    token = os.getenv("KREA2_API_TOKEN")
    if not token and (root / ".local-api-token").exists():
        token = (root / ".local-api-token").read_text().strip()
    if not token:
        raise SystemExit("Configurar KREA2_API_TOKEN")
    session.headers["Authorization"] = "Bearer " + token
    gets = {"health": "/healthz", "preflight": "/preflight?profile=" + args.profile,
            "loras": "/list_models?type=lora", "outputs": "/outputs"}
    if args.action in gets:
        response = session.get(base + gets[args.action], timeout=180)
        response.raise_for_status()
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        return
    routes = {"prepare-assets": "/assets/prepare", "convert-fp8": "/models/convert-fp8"}
    payload = {}
    if args.action in {"generate", "preview"}:
        loras = {}
        for value in args.lora:
            name, weight = value.rsplit("=", 1)
            loras[name] = float(weight)
        payload = {"operation": args.action, "positive_prompt": args.prompt, "profile": args.profile,
                   "seed": args.seed, "width": args.width, "height": args.height,
                   "lora": loras, "return_base64": False}
        for source, field in (("unet", "unet_name"), ("clip", "clip_name"), ("vae", "vae_name"),
                              ("model_type", "model_type"), ("precision", "weight_dtype")):
            if getattr(args, source):
                payload[field] = getattr(args, source)
    key = args.idempotency_key or uuid.uuid4().hex
    # No automatic POST retries. Reuse the printed key if retrying an interrupted submission.
    print("Idempotency-Key:", key)
    response = session.post(base + routes.get(args.action, "/jobs"), json=payload,
                            headers={"Idempotency-Key": key}, timeout=180)
    response.raise_for_status()
    accepted = response.json()
    print(json.dumps(accepted, indent=2, ensure_ascii=False))
    while True:
        response = session.get(base + accepted["status_url"], timeout=180)
        response.raise_for_status()
        if response.status_code == 202:
            time.sleep(3)
            continue
        result = response.json()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get("success"):
            raise SystemExit(1)
        break


if __name__ == "__main__":
    main()
