import copy
from io import BytesIO
from PIL import Image

def png_bytes():
    output=BytesIO()
    Image.new("RGB",(32,32),"blue").save(output,format="PNG")
    return output.getvalue()

import pytest
import telegram_bot as bot
from test_telegram import memory


def batch_job(size=100):
    return {"status":"result","chat":1,"message_id":20,"source_message_id":10,"recipe":{"n":4},
            "result":{"success":True,"images":[{"filename":str(i)+".png","size_bytes":size} for i in range(4)],
                      "batch":[{"success":True,"seed":i+1,"seed_string":str(i+1)} for i in range(4)]}}


def test_album_one_request_seed_only_caption_cached_resend(monkeypatch):
    db={};memory(monkeypatch,db)
    calls=[]
    def tg(method,**kwargs):
        calls.append((method,kwargs))
        if method=="sendMediaGroup":
            return [{"message_id":30+i,"photo":[{"file_id":"small"},{"file_id":"photo"+str(i)}]} for i in range(4)]
        return {}
    monkeypatch.setattr(bot,"tg",tg)
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=png_bytes()))
    job=batch_job();bot.deliver("parent",job)
    album=next(args for method,args in calls if method=="sendMediaGroup")
    assert len(album["media"])==4 and len(album["_files"])==4
    assert album["reply_parameters"]["message_id"]==10
    assert album["media"][0]["caption"]=="1\n2\n3\n4"
    assert all(item["type"]=="photo" for item in album["media"])
    assert all("caption" not in item for item in album["media"][1:])
    assert [method for method,_ in calls]==["sendMediaGroup","editMessageText"]
    assert db["job:parent"]["status"]=="done"
    monkeypatch.setattr(bot,"api",lambda *a:pytest.fail("Cached album must not fetch originals"))
    bot.deliver("parent",copy.deepcopy(db["job:parent"]))
    assert "_files" not in calls[-2][1]
    assert calls[-2][1]["media"][2]["media"]=="photo2"


@pytest.mark.parametrize("size",[5_000_000,5_000_001])
def test_large_originals_still_deliver_as_photo_album(monkeypatch,size):
    db={};memory(monkeypatch,db);calls=[]
    def tg(method,**kwargs):
        calls.append((method,kwargs))
        if method=="sendMediaGroup":return [{"message_id":30+i,"photo":[{"file_id":"p"+str(i)}]} for i in range(4)]
        return {}
    monkeypatch.setattr(bot,"tg",tg)
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=png_bytes()))
    bot.deliver("parent",batch_job(size))
    assert len([args for method,args in calls if method=="sendMediaGroup"])==1
    assert not any(method=="sendDocument" for method,args in calls)


def test_album_uncertain_delivery_is_not_automatically_repeated(monkeypatch):
    db={};memory(monkeypatch,db);calls=[]
    def tg(method,**kwargs):
        calls.append(method)
        if method=="sendMediaGroup":raise bot.TelegramDeliveryError("read_timeout",ambiguous=True)
        return {}
    monkeypatch.setattr(bot,"tg",tg)
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=png_bytes()))
    with pytest.raises(bot.TelegramDeliveryError):bot.deliver("parent",batch_job())
    assert db["job:parent"]["status"]=="delivery_unknown"
    with pytest.raises(RuntimeError):bot.deliver("parent",db["job:parent"])
    assert calls.count("sendMediaGroup")==1


def test_album_upload_is_jpeg_instead_of_original_png(monkeypatch):
    db={};memory(monkeypatch,db);calls=[]
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=png_bytes()))
    def tg(method,**kwargs):
        calls.append((method,kwargs))
        if method=="sendMediaGroup":return [{"message_id":30+i,"photo":[{"file_id":"p"+str(i)}]} for i in range(4)]
        return {}
    monkeypatch.setattr(bot,"tg",tg)
    bot.deliver("parent",batch_job())
    album=next(args for method,args in calls if method=="sendMediaGroup")
    assert all(value[2]=="image/jpeg" for value in album["_files"].values())


def test_progress_preserves_assigned_gpu_across_events_and_cpu_handoff():
    from krea_service.progress import publisher
    from tg_logic import progress_text
    class Store:
        def __init__(self):self.data={}
        def get(self,key,default=None):return self.data.get(key,default)
        def put(self,key,value):self.data[key]=value
    store=Store();publish=publisher(store)
    event={"job_id":"x","event":"gpu.ready","gpu":"NVIDIA H100"}
    publish(event)
    step={"job_id":"x","event":"sampling.progress","step":1,"total":6}
    publish(step)
    assert "NVIDIA H100" in progress_text(store.data["x"])
    assert "gpu" not in step
    cpu=publisher(store)
    cpu({"job_id":"x","event":"stage.start","stage":"persist_and_upload"})
    assert "NVIDIA H100" in progress_text(store.data["x"])
    assert "pendiente de asignaciÃ³n" in progress_text({"stage":"gpu_call"})
