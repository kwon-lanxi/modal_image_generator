"""CPU-safe catalog matching Krea2 and the pinned ComfyUI runtime."""
import json
from pathlib import Path
OPTIONS=json.loads(Path(__file__).with_name("sampling_options.json").read_text())
SAMPLERS=OPTIONS["samplers"]
SCHEDULERS=OPTIONS["schedulers"]

def validate_choices(data, fixed=None, mode="este modo"):
    for key,choices,flag in (("sampler_name",SAMPLERS,"--sample"),("scheduler",SCHEDULERS,"--scheduler")):
        value=data.get(key)
        if value is not None and value not in choices:
            raise ValueError(flag+" desconocido: "+repr(value)+". Opciones: "+", ".join(choices))
        if fixed and value is not None and value!=fixed[key]:
            raise ValueError(mode+" requiere "+flag+" "+fixed[key]+"; quitá el flag o usá el modo normal.")
    return data
