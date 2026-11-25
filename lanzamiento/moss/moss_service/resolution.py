"""Aspect ratios sized by real decimal megapixels; no GPU imports."""
import math
import re

RATIOS=("1:1","16:9","9:16","4:3","3:4","3:2","2:3")
ALIASES={"square":"1:1","cuadrado":"1:1","vertical":"9:16","horizontal":"16:9"}
DEFAULT_MP=1.5
MAX_PIXELS=4_194_304

def parse_mp(value):
    text=str(value).lower().strip()
    if text.endswith("mp"):text=text[:-2]
    try:mp=float(text)
    except (ValueError,TypeError):raise ValueError("Megapíxeles inválidos; usá 0.5, 1.5 o 2mp.") from None
    if not math.isfinite(mp) or not .25<=mp<=4:
        raise ValueError("El tamaño debe estar entre 0.25 y 4 MP reales.")
    return mp

def dimensions(ratio="1:1",megapixels=DEFAULT_MP,alignment=16):
    ratio=ALIASES.get(ratio,ratio)
    if ratio not in RATIOS:raise ValueError("Proporción inválida; usar "+", ".join(RATIOS))
    mp=parse_mp(megapixels)
    a,b=map(int,ratio.split(":"));r=a/b
    width=max(256,round(math.sqrt(mp*1_000_000*r)/alignment)*alignment)
    height=max(256,round(math.sqrt(mp*1_000_000/r)/alignment)*alignment)
    if width>4096 or height>4096 or width*height>MAX_PIXELS:
        raise ValueError("La resolución alineada supera 4 MP o 4096 px por lado.")
    return width,height

def extract_size(tokens):
    """Consume the optional MP token directly after --size without touching prompt numbers elsewhere."""
    tokens=list(tokens);mp=None
    for i,t in enumerate(tokens):
        if t=="--size" and i+2<len(tokens):
            value=tokens[i+2]
            if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:mp)?",value,re.I) or value.lower().endswith("mp"):
                mp=parse_mp(value);del tokens[i+2]
                break
    return tokens,mp

def request_dimensions(data,alignment):
    if not isinstance(data,dict):return data
    data=dict(data)
    if "width" not in data and "height" not in data:
        ratio=data.get("aspect_ratio") or "1:1"
        mp=data.get("resolution_mp") or DEFAULT_MP
        data["width"],data["height"]=dimensions(ratio,mp,alignment)
        data.update(aspect_ratio=ratio,resolution_mp=mp)
    return data
