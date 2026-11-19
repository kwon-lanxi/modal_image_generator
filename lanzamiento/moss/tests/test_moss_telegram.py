import copy
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlencode
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import moss_telegram as bot
from moss_tg_logic import OWNER,parse_prompt,validate_init,progress_text


def signed(token="token",age=0,user=OWNER):
    data={"auth_date":str(int(time.time())-age),"user":json.dumps({"id":user})}
    key=hmac.new(b"WebAppData",token.encode(),hashlib.sha256).digest()
    data["hash"]=hmac.new(key,"\n".join(k+"="+v for k,v in sorted(data.items())).encode(),hashlib.sha256).hexdigest()
    return urlencode(data)


def test_miniapp_auth_signature_age_owner_duplicates():
    raw=signed();assert validate_init(raw,"token")["id"]==OWNER
    for bad in [raw+"&auth_date=1",signed(age=4000),signed(user=123)]:
        with pytest.raises(ValueError):validate_init(bad,"token")
    with pytest.raises(ValueError):validate_init(raw,"wrong")


def test_alias_weights_paths_and_size_override():
    aliases={"person":{"id":"krea2-loras-renew/person/a.safetensors","weight":.8},"photo":{"id":"loras/photo.safetensors","weight":.4}}
    data=parse_prompt('portrait !person:0.7 !photo --size vertical -w 896 -h 1152 --seed 42',aliases)
    assert data["width"]==896 and data["height"]==1152
    assert data["lora"]=={aliases['person']['id']:.7,aliases['photo']['id']:.4}


@pytest.mark.parametrize('text',['a !unknown','a @unknown','a --cfg nan','a -w 123','a --steps 0','a --seed -1'])
def test_invalid_recipes(text):
    with pytest.raises(ValueError):parse_prompt(text,{})


def test_alias_negative_weight_legacy_prefix_and_duplicate():
    aliases={'style':{'id':'loras/style.safetensors','weight':.8}}
    assert parse_prompt('portrait !style:-0.25',aliases)['lora']=={'loras/style.safetensors':-.25}
    assert parse_prompt('portrait @style',aliases)['lora']=={'loras/style.safetensors':.8}
    with pytest.raises(ValueError,match='repetida'):
        parse_prompt('portrait !style @style',aliases)


def test_native_command_bot_suffix_with_bang_alias(monkeypatch):
    memory(monkeypatch,{'aliases':{'style':{'id':'loras/style.safetensors','weight':.8}}})
    recipes=[]
    monkeypatch.setattr(bot,'draft',lambda chat,recipe,*args:recipes.append(recipe))
    bot.handle({'message':{'chat':{'id':OWNER},'text':'/generar@imagekrea2_bot portrait !style:0.6',
                          'entities':[{'type':'bot_command','offset':0,'length':23}]}})
    assert recipes[0]['positive_prompt']=='portrait'
    assert recipes[0]['lora']=={'loras/style.safetensors':.6}


def memory(monkeypatch,db):
    def state(op,key='',value=None):
        if op=='get':return copy.deepcopy(db.get(key,value))
        if op=='put':db[key]=copy.deepcopy(value)
        if op=='merge':db[key].update(value)
        if op=='review' and db[key]['status']=='running':db[key]['status']='needs_review'
    monkeypatch.setattr(bot,'state',state)


def test_confirmation_once_and_no_http_or_polling(monkeypatch):
    db={'job:abc':{'status':'draft','chat':OWNER,'recipe':{'positive_prompt':'x','width':512,'height':512}}};memory(monkeypatch,db)
    monkeypatch.setattr(bot,'tg',lambda *a,**k:{'message_id':1})
    monkeypatch.setattr(bot,'api',lambda *a,**k:pytest.fail('No self HTTP/polling'))
    calls=[]
    def spawn(*args):calls.append(args);return SimpleNamespace(object_id='fc-test')
    monkeypatch.setattr(bot.modal.Cls,'from_name',lambda *a:lambda:SimpleNamespace(execute_bot=SimpleNamespace(spawn=spawn)))
    u={'callback_query':{'id':'x','data':'go:abc'}}
    bot.handle(u);bot.handle(u);bot.advance('abc',db['job:abc']);bot.advance('abc',db['job:abc'])
    assert len(calls)==1 and db['job:abc']['status']=='running'


def test_completion_can_race_spawn_response_without_losing_result(monkeypatch):
    db={'job:abc':{'status':'ready','chat':OWNER,'recipe':{}}};memory(monkeypatch,db)
    monkeypatch.setattr(bot,'tg',lambda *a,**k:{'message_id':1})
    def spawn(*a):db['job:abc'].update(status='done',result={'success':True});return SimpleNamespace(object_id='fc-fast')
    monkeypatch.setattr(bot.modal.Cls,'from_name',lambda *a:lambda:SimpleNamespace(execute_bot=SimpleNamespace(spawn=spawn)))
    bot.advance('abc',copy.deepcopy(db['job:abc']))
    assert db['job:abc']['status']=='done' and db['job:abc']['result']['success']


def test_uncertain_spawn_is_not_retried(monkeypatch):
    db={'job:abc':{'status':'ready','chat':OWNER,'recipe':{}}};memory(monkeypatch,db)
    monkeypatch.setattr(bot,'tg',lambda *a,**k:{'message_id':1})
    count=[]
    def spawn(*a):count.append(1);raise TimeoutError()
    monkeypatch.setattr(bot.modal.Cls,'from_name',lambda *a:lambda:SimpleNamespace(execute_bot=SimpleNamespace(spawn=spawn)))
    bot.advance('abc',db['job:abc']);bot.advance('abc',db['job:abc'])
    assert count==[1] and db['job:abc']['status']=='needs_review'


def test_webhook_security(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv('TELEGRAM_WEBHOOK_SECRET','test-secret')
    factory=getattr(bot.telegram_web,'local',bot.telegram_web)
    client=TestClient(factory())
    assert client.post('/webhook',json={'update_id':1}).status_code==403
    u={'update_id':1,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'x'}}
    assert client.post('/webhook',json=u,headers={'X-Telegram-Bot-Api-Secret-Token':'test-secret'}).status_code==200
    assert client.get('/mini/aliases').status_code==401


def test_queue_is_not_a_fake_percent():
    assert '%' not in progress_text({'stage':'gpu_call'})
    assert '3/8' in progress_text({'event':'sampling.progress','step':3,'total':8})


@pytest.mark.parametrize('ratio,dims',[
    ('1:1',(1328,1328)),('16:9',(1664,928)),('9:16',(928,1664)),
    ('4:3',(1472,1104)),('3:4',(1104,1472)),('3:2',(1584,1056)),('2:3',(1056,1584))])
def test_requested_aspect_ratios(ratio,dims):
    recipe=parse_prompt('portrait --size '+ratio,{})
    assert (recipe['width'],recipe['height'])==dims


def test_draft_replies_to_prompt_without_repeating_it(monkeypatch):
    db={};memory(monkeypatch,db);calls=[]
    def tg(method,**kwargs):calls.append((method,kwargs));return {'message_id':12}
    monkeypatch.setattr(bot,'tg',tg)
    bot.draft(OWNER,parse_prompt('secret long prompt !style:0.7',{'style':{'id':'loras/style.safetensors'}}),99)
    assert 'secret long prompt' not in calls[0][1]['text']
    assert 'style:0.7' in calls[0][1]['text']
    assert calls[0][1]['reply_parameters']['message_id']==99
    assert next(iter(db.values()))['confirmation_id']==12


def test_confirmation_becomes_progress_without_extra_message(monkeypatch):
    db={'job:x':{'status':'ready','chat':OWNER,'recipe':{},'confirmation_id':12}}
    memory(monkeypatch,db);calls=[]
    monkeypatch.setattr(bot,'tg',lambda m,**k:calls.append((m,k)))
    monkeypatch.setattr(bot.modal.Cls,'from_name',lambda *a:lambda:SimpleNamespace(execute_bot=SimpleNamespace(spawn=lambda *a:SimpleNamespace(object_id='fc-x'))))
    bot.advance('x',db['job:x'])
    assert [m for m,k in calls]==['editMessageText']
    assert db['job:x']['message_id']==12


def test_delivery_cleans_only_temporary_message_and_caches_file(monkeypatch):
    job={'status':'result','chat':OWNER,'message_id':12,'source_message_id':99,'result':{'success':True,'images':[{'filename':'a.png'}]}}
    db={'job:x':job};memory(monkeypatch,db);calls=[]
    def tg(m,**k):
        calls.append((m,k))
        return {'message_id':15,'document':{'file_id':'cached-file'}}
    monkeypatch.setattr(bot,'tg',tg)
    bot.deliver('x',copy.deepcopy(job),b'png-original')
    assert [m for m,k in calls]==['sendDocument','deleteMessage']
    assert calls[-1][1]['message_id']==12
    assert db['job:x']['telegram_file_id']=='cached-file'
    calls.clear()
    monkeypatch.setattr(bot,'api',lambda *a:pytest.fail('Cached resend must not read/upload PNG'))
    bot.deliver('x',copy.deepcopy(db['job:x']))
    assert calls[0][1]['document']=='cached-file' and '_files' not in calls[0][1]


def test_catalog_cache_expiry_and_refresh(monkeypatch):
    cache={};reads=[]
    monkeypatch.setattr(bot,'catalog_store',SimpleNamespace(get=lambda k,d:cache.get(k,d),put=lambda k,v:cache.update({k:v})))
    monkeypatch.setattr(bot,'api',lambda *a:reads.append(1) or bot.Result({'models':[{'id':'loras/a'}]}))
    assert bot.lora_catalog()==bot.lora_catalog()
    assert len(reads)==1
    bot.lora_catalog(refresh=True);assert len(reads)==2
    cache['loras']['at']-=301
    bot.lora_catalog();assert len(reads)==3


def test_progress_is_coalesced_and_throttled(monkeypatch):
    edits=[]
    monkeypatch.setattr(bot,'tg',lambda *a,**k:edits.append(k))
    ticks=iter([10,10.5,12.5]);monkeypatch.setattr(bot.time,'monotonic',lambda:next(ticks))
    job={'chat':OWNER,'message_id':12}
    for step in (1,2,3):bot.notify_progress(job,{'event':'sampling.progress','step':step,'total':8})
    assert len(edits)==2 and '3/8' in edits[-1]['text']


def test_telegram_http_reuses_connection_without_state_rpc(monkeypatch):
    calls=[]
    session=SimpleNamespace(post=lambda *a,**k:calls.append(k) or SimpleNamespace(json=lambda:{'ok':True,'result':{'message_id':1}}))
    monkeypatch.setattr(bot,'_http',session)
    monkeypatch.setattr(bot,'_not_before',0)
    monkeypatch.setenv('TELEGRAM_MOSS_BOT_TOKEN','test')
    monkeypatch.setattr(bot,'state',lambda *a:pytest.fail('No state RPC per Telegram request'))
    bot.tg('sendMessage',chat_id=OWNER,text='x')
    bot.tg('sendDocument',chat_id=OWNER,reply_parameters={'message_id':99},_files={'document':('x.png',b'png','image/png')})
    assert calls[0]['json']['text']=='x'
    assert json.loads(calls[1]['data']['reply_parameters'])=={'message_id':99}


def test_cleanup_failure_does_not_lose_delivered_result(monkeypatch):
    job={'chat':OWNER,'message_id':12,'result':{'success':True,'images':[{'filename':'x.png'}]}}
    db={'job:x':job};memory(monkeypatch,db)
    def tg(method,**kwargs):
        if method=='deleteMessage':raise RuntimeError('expired')
        return {'message_id':15,'document':{'file_id':'saved'}}
    monkeypatch.setattr(bot,'tg',tg)
    bot.deliver('x',job,b'png')
    assert db['job:x']['status']=='done' and db['job:x']['telegram_file_id']=='saved'


def test_model_download_is_cpu_job_and_keeps_custom_name(monkeypatch):
 db={};memory(monkeypatch,db)
 bot.handle({"message":{"chat":{"id":OWNER},"text":"/descargar_modelo hf://author/repo/model.safetensors my-model"}})
 job=next(iter(db.values()))
 assert job["operation"]=="download" and job["recipe"]["target"]=="unet"
 assert job["recipe"]["name"]=="my-model" and job["status"]=="ready"
