"""Download a selected acceleration recipe through the deployed CPU manager."""
import argparse
import json
import uuid
from moss_service.acceleration import ALIASES, PRESETS, IDM_ASSETS, FLOW_ASSET, MODEL_ASSETS


def assets_for(mode, upscale=False):
    preset=PRESETS[ALIASES.get(mode,mode)]
    assets=[preset["asset"]] if preset.get("asset") else list(IDM_ASSETS)
    if preset.get("model"):
        assets += [item for item in MODEL_ASSETS if item["name"]==preset["model"]+".safetensors"]
    if upscale:assets.append(FLOW_ASSET)
    return assets


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=sorted(set(PRESETS)|set(ALIASES)))
    parser.add_argument("--upscale",action="store_true",help="Also download FlowUpscaler")
    args=parser.parse_args()
    import modal
    result=modal.Cls.from_name("moss-image-personal-v1","CPUWriter")().execute.remote(
        "download_batch",{"items":assets_for(args.mode,args.upscale)},uuid.uuid4().hex)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
