import sys,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from moss_service.contracts import normalize

@pytest.mark.parametrize("profile",["base","sft"])
def test_profiles_batch_and_turbo(profile):
 r=normalize({"positive_prompt":"test","profile":profile,"superturbo":True,"n":4,"seed":42},"generate")
 assert r["steps"]==4 and r["cfg"]==1 and r["seeds"][0]==42
 assert len(r["seeds"])==4 and len(set(r["seeds"]))==4 and all(1<=seed<2**63 for seed in r["seeds"])
 assert r["lora"]["loras/moss-turbo-4step.safetensors"]==1
 assert r["effective_profile"]["family"]==profile
 assert r["effective_profile"]["vae"]=="flux2-vae_small"

def test_base_recipe_is_not_distilled():
 r=normalize({"positive_prompt":"test"},"generate")
 assert r["steps"]==50 and r["cfg"]==3 and not r["lora"]

def test_no_krea_tae_preview():
 with pytest.raises(ValueError):normalize({"positive_prompt":"test"},"preview")

def test_fp8_repack_preserves_scaled_rows_and_sensitive_tensors(tmp_path):
 torch=pytest.importorskip("torch")
 from safetensors.torch import save_file,load_file
 from prepare import convert
 source=tmp_path/"source.safetensors";dest=tmp_path/"converted.safetensors"
 weight=torch.tensor([[1.,2.],[3.,4.]]).to(torch.float8_e4m3fn)
 save_file({"layers.0.proj.q.weight":weight,"layers.0.proj.q._weight_scale":torch.tensor([[2.],[8.]]),"layers.0.norm1.weight":torch.ones(2)},str(source))
 convert(source,dest)
 data=load_file(str(dest));layer="layers.0.proj.q"
 recovered=data[layer+".weight"].float()*data[layer+".weight_scale"]
 assert torch.allclose(recovered,torch.tensor([[2.,4.],[24.,32.]]),rtol=.06)
 assert data["layers.0.norm1.weight"].dtype==torch.bfloat16
 assert json.loads(bytes(data[layer+".comfy_quant"].tolist()))["format"]=="float8_e4m3fn"


def test_cpu_batch_persists_metadata_and_replays_without_gpu(tmp_path,monkeypatch):
 import struct
 from moss_service import config
 from moss_service.backend import Backend
 from moss_service.storage import read_output
 from PIL import Image
 import io
 monkeypatch.setattr(config,"MAIN",tmp_path)
 monkeypatch.setattr(config,"LORAS",tmp_path/"loras")
 for folder,name in [("diffusion_models","moss-sft-comfy-fp8_scaled"),("text_encoders","moss-heretic-comfy-fp8_scaled-v2"),("vae","flux2-vae_small")]:
  file=tmp_path/folder/(name+".safetensors");file.parent.mkdir(exist_ok=True)
  header=json.dumps({"weight":{"dtype":"BF16","shape":[1],"data_offsets":[0,2]}}).encode()
  file.write_bytes(struct.pack("<Q",len(header))+header+b"00")
 calls=[]
 def gpu(settings,request,*args):
  calls.append(request)
  return {"pixels":bytes([128,64,32]*4),"batch_pixels":[bytes([i,64,32]*4) for i in range(request["n"])],"width":2,"height":2,"metadata":{"gpu":"NVIDIA L40S","loras":[]},"timing":{"sampling":.1}}
 class NoR2:
  def upload(self,*args):return {"status":"failed"}
 backend=Backend(lambda:None,gpu);backend.r2=NoR2()
 payload={"positive_prompt":"test","n":4,"seed":42,"return_base64":False}
 result=backend.execute("generate",payload,"a"*32)
 assert result["success"] and result["seeds"][0]==42
 assert len(result["seeds"])==4 and len(set(result["seeds"]))==4
 assert len(result["images"])==4
 for index,image in enumerate(result["images"]):
  raw,_=read_output(image["filename"])
  with Image.open(io.BytesIO(raw)) as png:
   assert png.info and png.size==(2,2)
  assert result["batch"][index]["metadata"]["seed"]==result["seeds"][index]
 assert backend.execute("generate",payload,"a"*32)==result and len(calls)==1


def test_heretic_preserves_qwen35_detection_keys(tmp_path):
 torch=pytest.importorskip("torch")
 from safetensors.torch import save_file,load_file
 from prepare import convert
 source=tmp_path/"heretic.safetensors";dest=tmp_path/"scaled.safetensors"
 prefix="model.language_model.layers.0."
 save_file({prefix+"linear_attn.A_log":torch.ones(2),prefix+"input_layernorm.weight":torch.ones(4096),prefix+"mlp.up_proj.weight":torch.ones(4,4)},str(source))
 convert([source],dest,encoder=True)
 state=load_file(str(dest))
 assert prefix+"linear_attn.A_log" in state
 assert state[prefix+"input_layernorm.weight"].shape==(4096,)
 assert state[prefix+"linear_attn.A_log"].dtype==torch.bfloat16
 assert state[prefix+"mlp.up_proj.weight"].dtype==torch.float8_e4m3fn


def test_turbo_is_eight_steps_and_flags_are_exclusive():
 r=normalize({"positive_prompt":"test","turbo":True},"generate")
 assert r["steps"]==8 and r["sampler_name"]=="lcm" and r["mu"]==4.4817
 assert r["lora"]["loras/moss_image_base_dmd2_v2_bf16.safetensors"]==1
 with pytest.raises(ValueError):normalize({"positive_prompt":"test","turbo":True,"superturbo":True},"generate")

def test_bot_sizes_models_and_aliases():
 from moss_tg_logic import parse_prompt
 r=parse_prompt("portrait !photo:0.7 --turbo --n 4 --profile base --model custom.safetensors --size 3:4",{"photo":{"id":"loras/photo.safetensors"}})
 assert (r["width"],r["height"])==(1056,1408)
 assert r["steps"]==8 and r["unet_name"]=="custom.safetensors" and r["profile"]=="base"
 assert r["lora"]["loras/photo.safetensors"]==.7
 assert parse_prompt("test --superturbo",{})["steps"]==4


def test_bot_preserves_json_quotes_bbox_and_negative():
 from moss_tg_logic import parse_prompt
 layout={"summary":{"caption":"A poster with !text and \"quotes\""},"items":[{"label":"text","text":"HELLO","bbox":[-400,-300,-200,300]}],"bbox_format":"yxyx","bbox_coord_space":"abs_centralized"}
 raw=json.dumps(layout,ensure_ascii=False,indent=2)
 r=parse_prompt('--size 1:1 --negative "blur, watermark" '+raw,{})
 assert r["positive_prompt"]==raw and json.loads(r["positive_prompt"])==layout
 assert r["negative_prompt"]=="blur, watermark"
 r=parse_prompt(raw+' --size 3:4',{})
 assert r["positive_prompt"]==raw and r["width"]==1056
 with pytest.raises(ValueError):parse_prompt('{"broken":}',{})
 with pytest.raises(ValueError,match="CFG"):parse_prompt('test --turbo --negative "blur"',{})
