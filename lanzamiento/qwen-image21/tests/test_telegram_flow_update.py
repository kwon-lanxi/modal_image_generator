import copy
from io import BytesIO
from PIL import Image
import qwen_telegram as bot


def memory(monkeypatch):
    db={}
    def state(op,key="",value=None):
        if op=="get":return copy.deepcopy(db.get(key,value))
        if op=="put":db[key]=copy.deepcopy(value)
        if op in {"active","list"}:return {k:copy.deepcopy(v) for k,v in db.items() if k.startswith(key)}
    monkeypatch.setattr(bot,"state",state)
    return db


def test_download_model_targets_cpu_unet(monkeypatch):
    db=memory(monkeypatch)
    bot.handle({"message":{"message_id":1,"chat":{"id":1},"text":"/descargar_modelo hf://owner/repo/model.safetensors custom.safetensors"}})
    job=next(iter(db.values()))
    assert job["operation"]=="download" and job["recipe"]["target"]=="unet"
    assert job["recipe"]["name"]=="custom.safetensors"


def test_six_image_album_uses_small_jpeg_and_cached_resend(monkeypatch):
    db=memory(monkeypatch);calls=[]
    buf=BytesIO();Image.new("RGB",(2048,2048),"orange").save(buf,format="PNG")
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=buf.getvalue()))
    def tg(method,**kw):
        calls.append((method,kw))
        if method=="sendMediaGroup":return [{"message_id":100+i,"photo":[{"file_id":"f"+str(i)}]} for i in range(6)]
        return {}
    monkeypatch.setattr(bot,"tg",tg)
    job={"status":"result","chat":1,"message_id":10,"recipe":{"n":6},"result":{"success":True,
         "images":[{"filename":str(i)+".png","size_bytes":6_000_000} for i in range(6)],
         "batch":[{"seed":i+1} for i in range(6)]}}
    bot.deliver("a",job)
    args=next(k for m,k in calls if m=="sendMediaGroup")
    assert len(args["_files"])==6 and args["media"][0]["caption"]=="1\n2\n3\n4\n5\n6"
    for filename,raw,mime in args["_files"].values():
        assert mime=="image/jpeg" and len(raw)<5_000_000
        with Image.open(BytesIO(raw)) as im:assert max(im.size)<=1600
    assert not any(m=="sendDocument" for m,k in calls)
    monkeypatch.setattr(bot,"api",lambda *a:(_ for _ in ()).throw(AssertionError("must reuse file_id")))
    bot.deliver("a",copy.deepcopy(db["job:a"]))
    assert "_files" not in [k for m,k in calls if m=="sendMediaGroup"][-1]


def photo(mid,group=None,caption=None):
    msg={"chat":{"id":1},"message_id":mid,"photo":[{"file_id":"f"+str(mid),"file_unique_id":"u"+str(mid),"width":100,"height":100}]}
    if group:msg["media_group_id"]=group
    if caption:msg["caption"]=caption
    return msg


def test_plain_prompt_does_not_reuse_photo_and_reply_selects_album(monkeypatch):
    memory(monkeypatch);bot._refs_changed.clear();drafts=[]
    monkeypatch.setattr(bot,"validated_prompt",lambda text:{"positive_prompt":text})
    monkeypatch.setattr(bot,"draft",lambda *args:drafts.append(args))
    first=photo(1,"album");bot.handle({"message":first});bot.handle({"message":photo(2,"album")})
    bot.handle({"message":{"chat":{"id":1},"message_id":3,"text":"a landscape"}})
    assert drafts[-1][-1]==[]
    bot.handle({"message":{"chat":{"id":1},"message_id":4,"text":"change background","reply_to_message":first}})
    assert len(drafts[-1][-1])==2


def test_caption_creates_one_edit_draft_for_album(monkeypatch):
    memory(monkeypatch);bot._refs_changed.clear();drafts=[]
    monkeypatch.setattr(bot,"validated_prompt",lambda text:{"positive_prompt":text})
    monkeypatch.setattr(bot,"draft",lambda *args:drafts.append(args))
    bot.handle({"message":photo(1,"album","make it blue")})
    bot.handle({"message":photo(2,"album")})
    bot.telegram_process()
    assert len(drafts)==1 and drafts[0][1]["positive_prompt"]=="make it blue"
    assert len(drafts[0][-1])==2
    bot.telegram_process();assert len(drafts)==1
