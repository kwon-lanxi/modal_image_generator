"""Telegram integration for the main Krea Modal app. No schedules or self-HTTP."""
import hashlib
from contextlib import closing
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
import modal
volume=modal.Volume.from_name("krea2-telegram-state")
catalog_store=modal.Dict.from_name("krea2-telegram-catalog",create_if_missing=True)
_http = None
_not_before = 0
PUBLIC_URL="https://kwon-lanxi--krea2-personal-v2-telegram-web.us-east.modal.run"
class TelegramState:
    @modal.method()
    def call(self, op, key="", value=None):
        volume.reload()
        with closing(sqlite3.connect("/state/bot.sqlite")) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
            if op=="get":
                row=db.execute("SELECT v FROM kv WHERE k=?",(key,)).fetchone();result=json.loads(row[0]) if row else value
            elif op=="put":
                db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)",(key,json.dumps(value)));result=value
            elif op=="insert":
                result=db.execute("INSERT OR IGNORE INTO kv VALUES (?,?)",(key,json.dumps(value))).rowcount==1
            elif op in {"merge","review"}:
                row=db.execute("SELECT v FROM kv WHERE k=?",(key,)).fetchone()
                item=json.loads(row[0]) if row else {}
                if op=="merge":item.update(value)
                elif item.get("status")=="running":item["status"]="needs_review"
                db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)",(key,json.dumps(item)));result=item
            elif op=="alias":
                row=db.execute("SELECT v FROM kv WHERE k='aliases'").fetchone()
                aliases=json.loads(row[0]) if row else {}
                old=aliases.get(key)
                if old and old["id"]!=value["id"]:raise ValueError("Alias existente para otro archivo")
                if len(aliases)>=1000 and key not in aliases:raise ValueError("Máximo1000 alias")
                # Keep older names usable, but present the most recently saved
                # alias for a file as its preferred name in the library.
                aliases.pop(key, None)
                aliases[key]=value
                db.execute("INSERT OR REPLACE INTO kv VALUES ('aliases',?)",(json.dumps(aliases),));result=aliases
            elif op=="model_alias":
                row=db.execute("SELECT v FROM kv WHERE k='model_aliases'").fetchone()
                aliases=json.loads(row[0]) if row else {}
                if value is None: aliases.pop(key,None)
                else:
                    old=aliases.get(key)
                    if old and old.get("id")!=value.get("id"):
                        raise ValueError("Ese alias ya está asignado a otro modelo")
                    if any(name!=key and item.get("id")==value.get("id") for name,item in aliases.items()):
                        aliases={name:item for name,item in aliases.items() if item.get("id")!=value.get("id")}
                    aliases[key]=value
                db.execute("INSERT OR REPLACE INTO kv VALUES ('model_aliases',?)",(json.dumps(aliases),));result=aliases
            elif op=="model_alias_sync":
                row=db.execute("SELECT v FROM kv WHERE k='model_aliases'").fetchone()
                aliases=json.loads(row[0]) if row else {}
                replacements=(value or {}).get("replacements",{})
                deleted=set((value or {}).get("deleted",[]))
                aliases={name:{**item,"id":replacements[item.get("id")]} if item.get("id") in replacements else item
                         for name,item in aliases.items() if item.get("id") not in deleted}
                db.execute("INSERT OR REPLACE INTO kv VALUES ('model_aliases',?)",(json.dumps(aliases),));result=aliases
            elif op=="list": result={k:json.loads(v) for k,v in db.execute("SELECT k,v FROM kv WHERE k LIKE ? ORDER BY rowid DESC LIMIT 100",(key+"%",))}
            elif op=="active":
                statuses=("new",) if key=="update:" else ("ready","running","result","submitting","submission_unknown")
                placeholders=",".join("?" for _ in statuses)
                result={k:json.loads(v) for k,v in db.execute("SELECT k,v FROM kv WHERE k LIKE ? AND json_extract(v,'$.status') IN ("+placeholders+")",(key+"%",*statuses))}
            else: raise ValueError("Unknown state operation")
        if op not in {"get", "list", "active"}:
            volume.commit()
        return result


def state(op,key="",value=None): return modal.Cls.from_name("krea2-personal-v2","TelegramState")().call.remote(op,key,value)


def lora_catalog(refresh=False):
    cached=catalog_store.get("loras",None)
    if not refresh and cached and time.time()-cached["at"]<300:
        return cached["models"]
    models=api("GET","/list_models?type=lora").json()["models"]
    catalog_store.put("loras",{"at":time.time(),"models":models})
    return models


def invalidate_catalog():
    catalog_store.pop("loras",None)
    catalog_store.pop("unet_models",None)


def model_catalog(refresh=False):
    cached=catalog_store.get("unet_models",None)
    if not refresh and cached and time.time()-cached["at"]<300:
        return cached["models"]
    models=api("GET","/list_models?type=unet").json()["models"]
    catalog_store.put("unet_models",{"at":time.time(),"models":models})
    return models


class Result:
    def __init__(self,data=None,status=200,content=b"",mime="image/png"):
        self.data,self.status_code,self.content,self.headers=data,status,content,{"content-type":mime}
    def json(self):return self.data


def api(method,path,**kwargs):
    """Internal Modal calls only; never HTTP back into the Krea API."""
    from urllib.parse import urlsplit,parse_qs
    url=urlsplit(path);query=parse_qs(url.query);path=url.path
    reader=modal.Cls.from_name("krea2-personal-v2","CPUReader")()
    if path=="/list_models":return Result(reader.read.remote("list_models",{"type":query.get("type",["lora"])[0]}))
    if path=="/outputs":return Result(reader.read.remote("outputs",{"limit":int(query.get("limit",[100])[0])}))
    if path=="/serve_image":
        data=reader.read.remote("file",{"filename":query["filename"][0]})
        return Result(content=data["bytes"],mime="application/json" if data["suffix"]==".json" else "image/png")
    if path.startswith("/records/"):
        jobid=path.split("/")[2]
        if path.endswith("/progress"):
            return Result({"progress":modal.Dict.from_name("krea2-personal-v2-progress").get(jobid,None)})
        return Result(reader.read.remote("job",{"job_id":jobid}))
    if path.startswith("/jobs/"):
        try:return Result(modal.FunctionCall.from_id(path.split("/")[-1]).get(timeout=0))
        except TimeoutError:return Result(status=202)
    if path=="/health":return Result({"ok":True,"transport":"internal-modal","gpu_probed":False})
    raise ValueError("Unsupported internal operation")


class TelegramDeliveryError(RuntimeError):
    def __init__(self,kind,*,code=None,ambiguous=False):
        self.kind,self.code,self.ambiguous=kind,code,ambiguous
        super().__init__("Telegram "+kind+(" "+str(code) if code else ""))


def tg(method, **data):
    import requests
    global _http, _not_before
    if time.time()<_not_before:raise TelegramDeliveryError("rate_limit",code=429)
    if _http is None:_http=requests.Session()
    files=data.pop("_files",None)
    upload_bytes=sum(len(v[1]) for v in files.values() if isinstance(v[1],(bytes,bytearray))) if files else 0
    if files:data={k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in data.items()}
    started=time.perf_counter()
    fields={"event":"telegram.http","method":method,"upload_bytes":upload_bytes,"region":os.getenv("MODAL_REGION")}
    try:
        for attempt in range(2):
            try:
                r=_http.post("https://api.telegram.org/bot"+os.environ["TELEGRAM_KREA2_BOT_TOKEN"]+"/"+method,
                             data=data if files else None,json=None if files else data,files=files,
                             timeout=(20,120) if files else (5,15))
                break
            except requests.exceptions.ConnectTimeout:
                # ConnectTimeout occurs before an HTTP request can be sent.
                # Never retry read/write/connection errors with uncertain delivery.
                if attempt:raise
                fields["connect_retries"]=1
                _http.close();_http=requests.Session()
        fields["http_status"]=getattr(r,"status_code",None)
        result=r.json()
    except Exception as exc:
        # Never log provider messages, request bodies or URLs: those may hold tokens.
        kind="connect_timeout" if isinstance(exc,requests.exceptions.ConnectTimeout) else "read_timeout" if isinstance(exc,requests.exceptions.ReadTimeout) else "connection_error" if isinstance(exc,requests.exceptions.ConnectionError) else "transport_error"
        fields.update(error_kind=kind,exception_type=type(exc).__name__,ambiguous=kind!="connect_timeout")
        raise TelegramDeliveryError(kind,ambiguous=kind!="connect_timeout") from None
    finally:
        fields["seconds"]=round(time.perf_counter()-started,3)
        print(json.dumps(fields))
    if not result.get("ok"):
        description=str(result.get("description",""));code=result.get("error_code")
        if "message is not modified" in description:return None
        kind="rate_limit" if code==429 else "file_too_large" if code==413 or "too big" in description.lower() or "too large" in description.lower() else "telegram_rejected"
        if code==429:_not_before=time.time()+int(result.get("parameters",{}).get("retry_after",1))
        from krea_service.logging_utils import redact
        print(json.dumps({"event":"telegram.rejected","method":method,"code":code,"error_kind":kind,"description":redact(description)[:512]}))
        raise TelegramDeliveryError(kind,code=code)
    return result.get("result")


LINK_THRESHOLD_BYTES=8*1024*1024


def r2_link(result):
    from urllib.parse import urlsplit
    images=result.get("images") or []
    if not images:return None
    r2=images[0].get("r2") or {}
    url=r2.get("url")
    if r2.get("status")!="uploaded" or not isinstance(url,str):return None
    parsed=urlsplit(url)
    return url if parsed.scheme=="https" and parsed.netloc and not parsed.username else None


def should_upload(result,job):
    if job.get("telegram_file_id"):return False
    image=(result.get("images") or [{}])[0]
    return not (r2_link(result) and image.get("size_bytes",0)>LINK_THRESHOLD_BYTES)


def deliver_link(job,result,markup):
    url=r2_link(result)
    if not url:raise TelegramDeliveryError("r2_link_unavailable")
    args={"chat_id":job["chat"],"text":url,"reply_markup":markup,
          "link_preview_options":{"is_disabled":True}}
    if job.get("source_message_id"):
        args["reply_parameters"]={"message_id":job["source_message_id"],"allow_sending_without_reply":True}
    sent=tg("sendMessage",**args) or {}
    job.update(delivery_method="r2_link",output_message_id=sent.get("message_id"))
    print(json.dumps({"event":"telegram.delivery.r2_link","job_id":job.get("request_id")}))


def buttons(rows): return {"inline_keyboard":rows}
def button(text,data): return {"text":text,"callback_data":data}
def library(tab="library"): return buttons([[{"text":"Biblioteca y galería", "web_app":{"url":PUBLIC_URL.rstrip("/")+"/mini"+(("?tab="+tab) if tab!="library" else "")}}]])
def send(chat,text,markup=None,reply_to=None):
    return tg("sendMessage",chat_id=chat,text=text[:4096],
              **({"reply_markup":markup} if markup else {}),
              **({"reply_parameters":{"message_id":reply_to,"allow_sending_without_reply":True}} if reply_to else {}))


def cleanup(job):
    """Only our temporary message, never the user's prompt or the output."""
    message_id=job.get("message_id") or job.get("confirmation_id")
    if message_id:
        try:tg("deleteMessage",chat_id=job["chat"],message_id=message_id)
        except RuntimeError:pass


def draft(chat,recipe,source_message_id=None):
    key=uuid.uuid4().hex[:16]
    from tg_logic import ASPECT_RATIOS
    ratio=next((k for k,v in ASPECT_RATIOS.items() if v==(recipe['width'],recipe['height'])),"Personalizado")
    loras=", ".join(Path(k).stem[:40]+":"+str(v) for k,v in recipe.get("lora",{}).items()) or "ninguna"
    text=f"Krea2 · {ratio} · {recipe['width']}×{recipe['height']} · {recipe.get('steps',8)} pasos · {recipe.get('n',1)} imágenes\nLoRAs: {loras}"
    message=send(chat,text,buttons([[button("Generar","go:"+key),button("Descartar","drop:"+key)]]),reply_to=source_message_id)
    state("put","job:"+key,{"status":"draft","chat":chat,"recipe":recipe,
          "source_message_id":source_message_id,"confirmation_id":message["message_id"]})


def handle(update):
    from tg_logic import parse_prompt
    callback=update.get("callback_query")
    if callback:
        try: tg("answerCallbackQuery",callback_query_id=callback["id"])
        except RuntimeError: pass
        action,_,key=callback.get("data","").partition(":")
        job=state("get","job:"+key)
        if not job: return
        if action=="go" and job["status"]=="draft":
            job["status"]="ready";state("put","job:"+key,job)
        elif action=="drop" and job["status"]=="draft":
            job["status"]="discarded";state("put","job:"+key,job)
            cleanup(job)
        elif action in {"repeat","vary"} and job["status"] in {"done","delivery_unknown"}:
            recipe=dict(job["recipe"])
            if action=="repeat":
                result=job.get("result",{})
                seeds=result.get("seeds") or [item.get("seed") for item in result.get("batch",[]) if item.get("seed") is not None]
                if not seeds:seeds=[result.get("seed",recipe.get("seed",0))]
                recipe["seed"]=seeds[0]
                recipe["seeds"]=seeds
            else:
                recipe["seed"]=secrets.randbelow(2**63-1)+1
                recipe.pop("seeds",None)
            draft(job["chat"],recipe,job.get("source_message_id"))
        elif action=="recover" and job["status"] in {"needs_review","running","delivery_unknown","result"}:
            job["status"]="submission_unknown";state("put","job:"+key,job)
        elif action=="deliver" and job.get("result"):
            job["status"]="result";state("put","job:"+key,job)
        return
    msg=update.get("message",{});chat=msg["chat"]["id"];text=msg.get("text","")
    first=text.split(maxsplit=1)[0] if text.strip() else ""
    command=first.split("@",1)[0].lower() if first.startswith("/") else ""
    if command:text=command+(" "+text.split(maxsplit=1)[1] if len(text.split(maxsplit=1))>1 else "")
    if command=="/galeria":
        send(chat,"Tus imágenes y recetas guardadas.",library("gallery"));return
    if command in {"/start","/help","/loras"}:
        send(chat,"Krea2 personal\nPrompt !alias:0.8 --model alias --size 9:16 --n 4\n--profile turbo/raw elige la familia del modelo · --neg \"texto negativo\"\n--neg requiere CFG > 1 (usa --profile raw o --cfg > 1)\n--vae base/wanx2/realvae/HDvae\nProporciones: 1:1 · 16:9 · 9:16 · 4:3 · 3:4 · 3:2 · 2:3\n-w 896 -h 1152 --seed 42 --steps 8 --cfg 1\n/descargar ENLACE nombre\n/convertir_modelo --model alias --fp8-scaled (o --fp8-mixed)\nLos alias y pesos se editan en Biblioteca; los modelos, en Modelos. Cada generación pide confirmación.",library())
        return
    if command=="/modelos":
        send(chat,"Administrá alias, conversiones FP8 y checkpoints.",library("models"));return
    if command=="/estado":
        jobs=state("list","job:")
        for key,job in jobs.items():
            if job.get("status") in {"running","needs_review","delivery_unknown","result"}:
                event=api("GET","/records/"+job.get("request_id","")+"/progress").json().get("progress") or {}
                from tg_logic import progress_text
                send(chat,progress_text(event)+" · "+job["status"],buttons([[button("Recuperar / consultar","recover:"+key[4:])]]));return
        send(chat,"Sin trabajos activos.",library());return
    if command=="/trabajos":
        jobs=state("list","job:")
        lines=[k[4:]+" · "+v["status"] for k,v in list(jobs.items())[:10]]
        send(chat,"Últimos trabajos\n"+"\n".join(lines),library());return
    if text.startswith("/descargar "):
        import shlex
        words=shlex.split(text)
        if not 2<=len(words)<=3: raise ValueError("/descargar ENLACE [nombre]")
        data={"url":words[1],"target":"lora"}
        if len(words)==3:data["name"]=words[2]
        key=uuid.uuid4().hex[:16]
        state("put","job:"+key,{"status":"ready","chat":chat,"operation":"download","recipe":data});return
    if command=="/convertir_modelo":
        import shlex
        words=shlex.split(text);name=None;precision=None;i=1
        while i<len(words):
            token=words[i]
            if token=="--model":
                i+=1
                if i==len(words):raise ValueError("Falta el alias o ID después de --model")
                name=words[i]
            elif token in {"--fp8-scaled","--fp8-sclaed"}:
                if precision:raise ValueError("Elegí una sola precisión")
                precision="fp8_scaled"
            elif token in {"--fp8-mixed","--fp8-mixted"}:
                if precision:raise ValueError("Elegí una sola precisión")
                precision="fp8_mixed"
            else:raise ValueError("Uso: /convertir_modelo --model alias --fp8-scaled|--fp8-mixed")
            i+=1
        if not name or not precision:raise ValueError("Uso: /convertir_modelo --model alias --fp8-scaled|--fp8-mixed")
        aliases=state("get","model_aliases",{})
        found=next((item for alias,item in aliases.items() if alias.casefold()==name.casefold()),None)
        model_id=found["id"] if found else name
        if model_id not in {item["id"] for item in model_catalog()}:raise ValueError("Modelo no encontrado. Abrí /modelos para copiar su alias.")
        key=uuid.uuid4().hex[:16]
        state("put","job:"+key,{"status":"ready","chat":chat,"operation":"convert_fp8",
              "recipe":{"target":"unet","name":model_id,"precision":precision}})
        return
    if command and command not in {"/generar","/descargar"}:
        send(chat,"Comando desconocido. Abrí el menú o /help");return
    if command=="/generar":text=text[len("/generar"):].strip()
    draft(chat,parse_prompt(text,state("get","aliases",{}),state("get","model_aliases",{})),msg.get("message_id"))


def notify_progress(job,event):
    from tg_logic import progress_text
    text=progress_text(event)
    now=time.monotonic()
    if text!=job.get("last_text") and now-job.get("last_edit",-100)>=2:
        tg("editMessageText",chat_id=job["chat"],message_id=job["message_id"],text=text)
        job["last_text"]=text
        job["last_edit"]=now


def notify_complete(key,result,raw=None):
    job=state("get","job:"+key)
    if not job:return
    job.update(status="result",result=result);state("put","job:"+key,job)
    deliver(key,job,raw)


ALBUM_IMAGE_LIMIT_BYTES=5_000_000


def deliver_album(key,job):
    """Photo gallery; originals, metadata and recipes remain in Volume/R2."""
    from urllib.parse import quote
    result=job["result"]
    images=result["images"]
    cached=job.get("telegram_album_file_ids",[])
    media=[];files={}
    seeds=[str(item.get("seed_string",item.get("seed"))) for item in result["batch"]]
    for index,image in enumerate(images):
        if len(cached)==len(images) and all(cached):
            source=cached[index]
        else:
            raw=api("GET","/serve_image?filename="+quote(image["filename"],safe="")).content
            # Verify actual bytes too, before any Telegram delivery.
            if len(raw)>=ALBUM_IMAGE_LIMIT_BYTES:return False
            name="image"+str(index)
            files[name]=(image["filename"],raw,"image/png")
            source="attach://"+name
        item={"type":"photo","media":source}
        if index==0:item["caption"]="\n".join(seeds)
        media.append(item)
    args={"chat_id":job["chat"],"media":media}
    if files:args["_files"]=files
    if job.get("source_message_id"):
        args["reply_parameters"]={"message_id":job["source_message_id"],"allow_sending_without_reply":True}
    job["status"]="delivery_unknown";state("put","job:"+key,job)
    try:
        delivered=tg("sendMediaGroup",**args)
    except Exception:
        # Do not repeat an upload whose acceptance by Telegram is uncertain.
        if job.get("message_id"):
            try:
                tg("editMessageText",chat_id=job["chat"],message_id=job["message_id"],
                   text="Lote guardado. Revisá el chat antes de reenviar el álbum.",
                   reply_markup=buttons([[button("Reenviar álbum sin GPU","deliver:"+key)]]))
            except RuntimeError:pass
        raise
    job.update(status="done",delivery_method="telegram_album",
               telegram_album_file_ids=[item.get("photo",[{}])[-1].get("file_id") for item in delivered],
               output_message_ids=[item["message_id"] for item in delivered])
    state("put","job:"+key,job)
    # Albums cannot carry inline keyboards. Reuse our existing status message.
    markup=buttons([[button("Repetir","repeat:"+key),button("Nueva variante","vary:"+key)],
                    [button("Reenviar álbum sin GPU","deliver:"+key)]])
    try:
        if job.get("message_id"):
            tg("editMessageText",chat_id=job["chat"],message_id=job["message_id"],
               text="Listo · "+str(len(images))+" imágenes",reply_markup=markup)
        else:send(job["chat"],"Listo · "+str(len(images))+" imágenes",markup)
    except RuntimeError:pass
    return True


def deliver(key,job,raw=None):
    result=job["result"]
    if len(result.get("images",[]))>1:
        if job.get("status")=="delivery_unknown":
            raise RuntimeError("Consultar entrega pendiente del álbum antes de reenviar")
        # Older partial deliveries must continue individually, avoiding duplicates.
        partial=any(state("get","job:"+key+"_"+str(i)) for i in range(len(result["images"])))
        if (not partial and all(0<image.get("size_bytes",0)<ALBUM_IMAGE_LIMIT_BYTES for image in result["images"])
                and deliver_album(key,job)):
            return
        for index,image in enumerate(result["images"]):
            child_key=key+"_"+str(index)
            child=state("get","job:"+child_key)
            if child and child.get("status")=="done":continue
            if child and child.get("status")=="delivery_unknown":
                raise RuntimeError("Consultar entrega pendiente del elemento del lote")
            item={**result["batch"][index],"images":[image]}
            recipe={**job["recipe"],"n":1,"seed":item["seed"],"seeds":[item["seed"]]}
            child={**job,"result":item,"recipe":recipe,"status":"result"}
            child.pop("telegram_file_id",None)
            child.pop("message_id",None)
            child.pop("confirmation_id",None)
            try:
                deliver(child_key,child)
            except Exception:
                job["status"]="needs_review"
                state("put","job:"+key,job)
                if job.get("message_id"):
                    try:
                        tg("editMessageText",chat_id=job["chat"],message_id=job["message_id"],
                           text=f"Lote guardado. Entrega {index+1}/{len(result['images'])} pendiente. Revisá Biblioteca antes de reenviar.",
                           reply_markup=buttons([[button("Reintentar entrega pendiente","deliver:"+child_key)],
                                                 [button("Continuar lote","deliver:"+key)]]))
                    except RuntimeError:pass
                raise
        job["status"]="done"
        state("put","job:"+key,job)
        cleanup(job)
        return
    job["status"]="delivery_unknown";state("put","job:"+key,job)
    markup=buttons([[button("Repetir","repeat:"+key),button("Nueva variante","vary:"+key)], [button("Reenviar original sin GPU","deliver:"+key)]])
    if not result.get("success"):
        # Keep a useful error in the same message; do not label it successful.
        tg("editMessageText",chat_id=job["chat"],message_id=job["message_id"],
           text="El trabajo falló · "+str(result.get("error","Consultar logs")),reply_markup=library())
        job["status"]="failed";state("put","job:"+key,job)
        return
    elif result.get("images"):
        from urllib.parse import quote
        im=result["images"][0]
        file_id=job.get("telegram_file_id")
        if not should_upload(result,job) and not file_id:
            deliver_link(job,result,markup)
        else:
            if raw is None and not file_id:raw=api("GET","/serve_image?filename="+quote(im["filename"],safe="")).content
            if raw is not None and len(raw)>49_000_000:
                if r2_link(result):deliver_link(job,result,markup)
                else:
                    send(job["chat"],"Original guardado en Biblioteca; no hay enlace público de R2 y supera el límite de Telegram.",library())
                    job["delivery_method"]="gallery_notice"
            else:
                args={"chat_id":job["chat"],"caption":str(result.get("seed_string",result.get("seed"))),"reply_markup":markup}
                if job.get("source_message_id"):
                    args["reply_parameters"]={"message_id":job["source_message_id"],"allow_sending_without_reply":True}
                if file_id:args["document"]=file_id
                else:args["_files"]={"document":(im["filename"],raw,"image/png")}
                try:
                    delivered=tg("sendDocument",**args) or {}
                except TelegramDeliveryError as exc:
                    if not r2_link(result) or exc.code in {401,403,429}:raise
                    job["document_delivery_ambiguous"]=exc.ambiguous
                    print(json.dumps({"event":"telegram.delivery.fallback","job_id":job.get("request_id"),"error_kind":exc.kind,"code":exc.code,"ambiguous":exc.ambiguous}))
                    # A URL notification is distinct from retrying an uncertain upload.
                    deliver_link(job,result,markup)
                else:
                    job["telegram_file_id"]=delivered.get("document",{}).get("file_id",file_id)
                    job["output_message_id"]=delivered.get("message_id")
                    job["delivery_method"]="telegram_document"

    elif job.get("operation")=="convert_fp8":
        item=result.get("file",{})
        source_text="El original BF16/FP16 se eliminó tras validar y guardar el resultado." if result.get("source_removed") else "El original se conservó."
        send(job["chat"],f"Conversión terminada · {item.get('id','modelo')}\n{result.get('precision','FP8')} · {item.get('size_human','')}\n{source_text}",library("models"))
    else:send(job["chat"],"Descarga terminada. Asigná un alias en la biblioteca.",library())
    job["status"]="done";state("put","job:"+key,job)
    cleanup(job)



def advance(key,job):
    if job["status"]=="ready":
        job.update(status="running",request_id=hashlib.sha256(("tg-krea-"+key).encode()).hexdigest()[:32])
        if job.get("confirmation_id"):
            job["message_id"]=job["confirmation_id"]
            tg("editMessageText",chat_id=job["chat"],message_id=job["message_id"],
               text="Preparando trabajo…",reply_markup=buttons([]))
        else:
            job["message_id"]=send(job["chat"],"Trabajo aceptado. Preparando…")["message_id"]
        state("put","job:"+key,job)
        try:
            call=modal.Cls.from_name("krea2-personal-v2","CPUWriter")().execute_bot.spawn(job.get("operation","generate"),job["recipe"],job["request_id"],key)
            state("merge","job:"+key,{"status_url":"/jobs/"+call.object_id})
        except Exception:
            state("review","job:"+key,None)
        return
    if job["status"]=="result":deliver(key,job)
    elif job["status"]=="submission_unknown":
        if job.get("status_url"):
            response=api("GET",job["status_url"])
            if response.status_code==202:send(job["chat"],"El trabajo sigue en curso. /estado");return
            notify_complete(key,response.json());return
        send(job["chat"],"Envío incierto: revisar historial antes de generar otra vez.",library())
        job["status"]="needs_review";state("put","job:"+key,job)


def telegram_process():
    # Wake only on a webhook. No timer, polling loop or scheduled recovery.
    for k,entry in state("active","update:").items():
        if entry["status"]!="new":continue
        started=time.perf_counter()
        queued=max(0,time.time()-entry.get("received_at",time.time()))
        entry["status"]="handling";state("put",k,entry)
        try:handle(entry["update"])
        except Exception as exc:
            print(json.dumps({"event":"telegram.update.failed","update_key":k,"error":type(exc).__name__}))
            msg=entry["update"].get("message",{})
            if msg.get("chat"):send(msg["chat"]["id"],"Revisá alias, pesos y parámetros. /help")
        entry["status"]="done";state("put",k,entry)
        print(json.dumps({"event":"telegram.update.handled","update_key":k,
                          "queue_seconds":round(queued,3),"seconds":round(time.perf_counter()-started,3)}))
    for key,job in state("active","job:").items():
        if job["status"] in {"ready","result","submission_unknown"}:
            try:advance(key[4:],job)
            except Exception as exc:print(json.dumps({"event":"telegram.dispatch.failed","job":key,"error":type(exc).__name__}))


def telegram_web():
    from fastapi import FastAPI,Request,HTTPException
    from fastapi.responses import HTMLResponse,Response,JSONResponse
    from tg_logic import OWNER,validate_init
    api_app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    def auth(request):
        try:validate_init(request.headers.get("X-Telegram-Init-Data",""),os.environ["TELEGRAM_KREA2_BOT_TOKEN"])
        except Exception:raise HTTPException(401,"Reabrí la Mini App desde Telegram")
    @api_app.get("/mini")
    def mini():return HTMLResponse(Path("/root/tg_mini.html").read_text())
    @api_app.post("/webhook")
    async def webhook(request:Request):
        received_at=time.time()
        if not hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),os.environ["TELEGRAM_WEBHOOK_SECRET"]):raise HTTPException(403)
        raw=await request.body()
        if len(raw)>65536:raise HTTPException(413)
        try:u=json.loads(raw);assert isinstance(u["update_id"],int)
        except Exception:raise HTTPException(400)
        m=u.get("message") or u.get("callback_query",{}).get("message",{})
        sender=u.get("callback_query",{}).get("from") or u.get("message",{}).get("from",{})
        if sender.get("id")!=OWNER or m.get("chat",{}).get("type")!="private":return {"ok":True}
        inserted=await modal.Cls.from_name("krea2-personal-v2","TelegramState")().call.remote.aio("insert","update:"+str(u["update_id"]),{"status":"new","update":u,"received_at":received_at})
        # Persist first. A later user interaction can recover a missed wakeup; no cron.
        if inserted:await modal.Function.from_name("krea2-personal-v2","telegram_process").spawn.aio()
        return {"ok":True}
    @api_app.get("/mini/loras")
    def loras(request:Request,q:str="",page:int=0,all:bool=False,refresh:bool=False):
        auth(request)
        entries=lora_catalog(refresh=refresh)
        entries=[x for x in entries if q.lower() in (x["id"]+" "+x["name"]).lower()]
        page=max(0,page);return {"items":entries if all else entries[page*20:(page+1)*20],"total":len(entries)}
    @api_app.get("/mini/aliases")
    def aliases(request:Request):auth(request);return state("get","aliases",{})
    @api_app.post("/mini/aliases")
    async def alias(request:Request):
        import re,math
        auth(request);raw=await request.body()
        if len(raw)>4096:raise HTTPException(413)
        data=json.loads(raw);name=data.get("alias","");weight=float(data.get("weight",.8))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}",name) or not math.isfinite(weight) or abs(weight)>2:raise HTTPException(400,"Alias/peso inválido")
        entries=await __import__('asyncio').to_thread(lora_catalog)
        if data.get("id") not in {x["id"] for x in entries}:raise HTTPException(400,"Archivo no encontrado")
        # Serialized read/merge happens inside State (not in concurrent HTTP handlers).
        try:return await modal.Cls.from_name("krea2-personal-v2","TelegramState")().call.remote.aio("alias",name,{"id":data["id"],"weight":weight})
        except ValueError as exc:raise HTTPException(409,str(exc))
    @api_app.get("/mini/models")
    def models(request:Request,q:str="",refresh:bool=False):
        auth(request);entries=model_catalog(refresh=refresh)
        entries=[x for x in entries if q.lower() in (x["id"]+" "+x["name"]).lower()]
        return {"items":entries,"total":len(entries),"aliases":state("get","model_aliases",{})}
    @api_app.post("/mini/model-aliases")
    async def model_alias(request:Request):
        import re
        auth(request);raw=await request.body()
        if len(raw)>4096:raise HTTPException(413)
        data=json.loads(raw);name=data.get("alias","");model_id=data.get("id","")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}",name):raise HTTPException(400,"Alias inválido")
        if data.get("delete") is True:
            return await modal.Cls.from_name("krea2-personal-v2","TelegramState")().call.remote.aio("model_alias",name,None)
        entries=await __import__('asyncio').to_thread(model_catalog)
        if model_id not in {x["id"] for x in entries}:raise HTTPException(400,"Modelo no encontrado")
        try:return await modal.Cls.from_name("krea2-personal-v2","TelegramState")().call.remote.aio("model_alias",name,{"id":model_id})
        except ValueError as exc:raise HTTPException(409,str(exc))
    @api_app.post("/mini/models/convert")
    async def convert_model(request:Request):
        auth(request);raw=await request.body()
        if len(raw)>4096:raise HTTPException(413)
        data=json.loads(raw);model_id=data.get("id","");precision=data.get("precision")
        if precision not in {"fp8_scaled","fp8_mixed"}:raise HTTPException(400,"Precisión inválida")
        entries=await __import__('asyncio').to_thread(model_catalog)
        if model_id not in {x["id"] for x in entries}:raise HTTPException(404,"Modelo no encontrado")
        request_id=uuid.uuid4().hex
        try:
            writer=modal.Cls.from_name("krea2-personal-v2","CPUWriter")()
            call=await writer.execute.spawn.aio("convert_fp8",{"target":"unet","name":model_id,"precision":precision},request_id)
        except Exception as exc:raise HTTPException(503,"No se pudo iniciar la conversión: "+type(exc).__name__)
        return {"call_id":call.object_id,"request_id":request_id}
    @api_app.post("/mini/models/delete")
    async def delete_models(request:Request):
        auth(request);raw=await request.body()
        if len(raw)>8192:raise HTTPException(413)
        data=json.loads(raw);ids=data.get("ids")
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or not all(isinstance(item,str) for item in ids):
            raise HTTPException(400,"Elegí entre 1 y 100 modelos")
        if len(set(ids))!=len(ids):raise HTTPException(400,"Hay modelos repetidos")
        entries=await __import__('asyncio').to_thread(model_catalog)
        if not set(ids)<={x["id"] for x in entries}:raise HTTPException(404,"Algún modelo ya no existe")
        request_id=uuid.uuid4().hex
        writer=modal.Cls.from_name("krea2-personal-v2","CPUWriter")()
        call=await writer.execute.spawn.aio("delete_models",{"paths":ids},request_id)
        return {"call_id":call.object_id,"request_id":request_id}
    @api_app.get("/mini/model-jobs/{call_id}")
    async def model_job(call_id:str,request:Request):
        auth(request)
        try:return {"status":"done","result":await modal.FunctionCall.from_id(call_id).get.aio(timeout=0)}
        except TimeoutError:return JSONResponse({"status":"running"},status_code=202)
        except modal.exception.OutputExpiredError:return JSONResponse({"status":"expired"},status_code=410)
    @api_app.get("/mini/history")
    def history(request:Request):auth(request);return api("GET","/outputs?limit=100").json()
    @api_app.get("/mini/image")
    def original(request:Request,filename:str):
        from urllib.parse import quote
        auth(request);r=api("GET","/serve_image?filename="+quote(filename,safe=""));return Response(r.content,media_type=r.headers.get("content-type","image/png"))
    return api_app


def telegram_configure(url):
    from tg_logic import OWNER
    me=tg("getMe")
    commands=[{"command":k,"description":v} for k,v in [("generar","Crear una imagen"),("loras","Biblioteca de LoRAs y alias"),("modelos","Administrar modelos y alias"),("convertir_modelo","Convertir un checkpoint a FP8"),("galeria","Imágenes y recetas"),("estado","Consultar trabajo actual"),("trabajos","Últimos trabajos"),("descargar","Descargar una LoRA por enlace"),("help","Ayuda y parámetros")]]
    tg("setMyCommands",commands=commands)
    tg("setChatMenuButton",chat_id=OWNER,menu_button={"type":"web_app","text":"Biblioteca","web_app":{"url":url.rstrip("/")+"/mini"}})
    result=tg("setWebhook",url=url.rstrip("/")+"/webhook",secret_token=os.environ["TELEGRAM_WEBHOOK_SECRET"],allowed_updates=["message","callback_query"],max_connections=2,drop_pending_updates=False)
    print(json.dumps({"username":me["username"],"webhook_set":result}))


def telegram_diagnostics():
    me=tg("getMe");info=tg("getWebhookInfo")
    return {"username":me["username"],"webhook_url":info.get("url"),"pending_updates":info.get("pending_update_count"),
            "has_delivery_error":bool(info.get("last_error_date")),"state_ok":isinstance(state("get","aliases",{}),dict),
            "krea_health":api("GET","/health").json(),"menu":tg("getChatMenuButton",chat_id=7112930476),"commands":tg("getMyCommands")}


def telegram_redeliver(key):
    """Explicit operator recovery of an existing result. Never invokes CPUWriter/GPU."""
    job=state("get","job:"+key)
    if not job or not job.get("result"):raise ValueError("Resultado no encontrado")
    if job.get("status")=="done" and job.get("delivery_method")=="r2_link":
        return {"status":"already_delivered","delivery_method":"r2_link"}
    deliver(key,job)
    return {"status":job["status"],"delivery_method":job.get("delivery_method")}


def telegram_network_diagnostics():
    """Read-only network probe; no chat messages or secrets in returned data."""
    import socket,ssl
    result={"region":os.getenv("MODAL_REGION"),"cloud":os.getenv("MODAL_CLOUD_PROVIDER"),"tcp_tls":[]}
    addresses=socket.getaddrinfo("api.telegram.org",443,type=socket.SOCK_STREAM)
    for family,kind,proto,_,address in addresses[:4]:
        started=time.perf_counter()
        row={"family":"IPv6" if family==socket.AF_INET6 else "IPv4"}
        try:
            with socket.socket(family,kind,proto) as connection:
                connection.settimeout(5);connection.connect(address)
                with ssl.create_default_context().wrap_socket(connection,server_hostname="api.telegram.org"):
                    pass
            row["ok"]=True
        except Exception as exc:row.update(ok=False,error=type(exc).__name__)
        row["seconds"]=round(time.perf_counter()-started,3)
        result["tcp_tls"].append(row)
    try:
        me=tg("getMe");result["get_me"]={"ok":True,"username":me["username"]}
    except TelegramDeliveryError as exc:result["get_me"]={"ok":False,"kind":exc.kind}
    return result


def register(app,image):
    global TelegramState,telegram_process,telegram_web,telegram_configure,telegram_diagnostics,telegram_redeliver,telegram_network_diagnostics
    credentials=[modal.Secret.from_name("tg-token-image"),modal.Secret.from_name("krea2-telegram-config")]
    TelegramState=app.cls(image=image,volumes={"/state":volume},cpu=.125,memory=256,max_containers=1,scaledown_window=2)(modal.concurrent(max_inputs=1)(TelegramState))
    telegram_process=app.function(image=image,region="eu-west",secrets=credentials,cpu=.125,memory=256,max_containers=1,scaledown_window=2,timeout=120,retries=0)(telegram_process)
    telegram_web=app.function(image=image,region="eu-west",secrets=credentials,cpu=.125,memory=256,max_containers=1,scaledown_window=2)(modal.concurrent(max_inputs=16)(modal.asgi_app()(telegram_web)))
    telegram_configure=app.function(image=image,region="eu-west",secrets=credentials,cpu=.125,memory=256)(telegram_configure)
    telegram_diagnostics=app.function(image=image,region="eu-west",secrets=credentials,cpu=.125,memory=256)(telegram_diagnostics)
    telegram_redeliver=app.function(image=image,region="eu-west",secrets=credentials,cpu=.125,memory=256,scaledown_window=2,timeout=180,retries=0)(telegram_redeliver)
    telegram_network_diagnostics=app.function(image=image,region="eu-west",secrets=credentials,cpu=.25,memory=256,scaledown_window=2,timeout=90)(telegram_network_diagnostics)
