import copy
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
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=b"png"))
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
def test_album_threshold_falls_back_to_individual_delivery(monkeypatch,size):
    db={};memory(monkeypatch,db);calls=[]
    def tg(method,**kwargs):
        calls.append((method,kwargs))
        return {"message_id":30,"document":{"file_id":"doc"}}
    monkeypatch.setattr(bot,"tg",tg)
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=b"png"))
    bot.deliver("parent",batch_job(size))
    docs=[args for method,args in calls if method=="sendDocument"]
    assert len(docs)==4 and [args["caption"] for args in docs]==["1","2","3","4"]
    assert not any(method=="sendMediaGroup" for method,_ in calls)


def test_album_uncertain_delivery_is_not_automatically_repeated(monkeypatch):
    db={};memory(monkeypatch,db);calls=[]
    def tg(method,**kwargs):
        calls.append(method)
        if method=="sendMediaGroup":raise bot.TelegramDeliveryError("read_timeout",ambiguous=True)
        return {}
    monkeypatch.setattr(bot,"tg",tg)
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=b"png"))
    with pytest.raises(bot.TelegramDeliveryError):bot.deliver("parent",batch_job())
    assert db["job:parent"]["status"]=="delivery_unknown"
    with pytest.raises(RuntimeError):bot.deliver("parent",db["job:parent"])
    assert calls.count("sendMediaGroup")==1


def test_actual_size_checked_before_album_upload(monkeypatch):
    db={};memory(monkeypatch,db);calls=[]
    monkeypatch.setattr(bot,"api",lambda *a:bot.Result(content=b"x"*5_000_000))
    monkeypatch.setattr(bot,"tg",lambda method,**kwargs:calls.append(method) or {"message_id":30})
    bot.deliver("parent",batch_job())
    assert "sendMediaGroup" not in calls and calls.count("sendDocument")==4


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
    assert "pendiente de asignación" in progress_text({"stage":"gpu_call"})
