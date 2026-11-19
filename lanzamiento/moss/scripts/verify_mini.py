import modal
app=modal.App("moss-mini-check")
image=modal.Image.debian_slim(python_version="3.12").pip_install("requests==2.32.5")
@app.function(image=image,secrets=[modal.Secret.from_name("tg-token-image")],cpu=.125,memory=256,timeout=90)
def verify():
 import os,time,json,hmac,hashlib,requests
 from urllib.parse import urlencode
 token=os.environ["TELEGRAM_MOSS_BOT_TOKEN"]
 data={"auth_date":str(int(time.time())),"user":json.dumps({"id":7112930476})}
 secret=hmac.new(b"WebAppData",token.encode(),hashlib.sha256).digest()
 data["hash"]=hmac.new(secret,"\n".join(k+"="+v for k,v in sorted(data.items())).encode(),hashlib.sha256).hexdigest()
 base="https://kwon-lanxi--moss-image-personal-v1-telegram-web.us-east.modal.run"
 result={}
 for path in ["/mini/loras?all=true","/mini/aliases","/mini/history"]:
  response=requests.get(base+path,headers={"X-Telegram-Init-Data":urlencode(data)},timeout=45)
  result[path]=response.status_code
  if path.startswith("/mini/loras") and response.ok:result["lora_count"]=len(response.json()["items"])
 result["unauthenticated"]=requests.get(base+"/mini/loras",timeout=30).status_code
 return result
@app.local_entrypoint()
def main():print(verify.remote())
