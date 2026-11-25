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
import qwen_telegram as bot
from qwen_tg_logic import OWNER,parse_prompt,validate_init,progress_text


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
    bot.handle({'message':{'chat':{'id':OWNER},'text':'/generar@qwen_bot portrait !style:0.6',
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
    ('1:1',(1216,1216)),('16:9',(1632,928)),('9:16',(928,1632)),
    ('4:3',(1408,1056)),('3:4',(1056,1408)),('3:2',(1504,992)),('2:3',(992,1504))])
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
    monkeypatch.setenv('TELEGRAM_QWEN21_BOT_TOKEN','test')
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


def photo(message_id,file_id,caption=None):
    return {'chat':{'id':OWNER},'message_id':message_id,'photo':[{'file_id':file_id,'file_unique_id':file_id,'width':1024,'height':768,'file_size':1000}],**({'caption':caption} if caption else {})}


def test_album_collects_in_order_without_generating_on_caption(monkeypatch):
    db={};memory(monkeypatch,db)
    monkeypatch.setattr(bot,'_refs_changed',set())
    monkeypatch.setattr(bot,'draft',lambda *a:pytest.fail('No auto generate partial album'))
    for msg in [photo(12,'b'),photo(11,'a','combine the two'),photo(12,'b')]:bot.handle({'message':{**msg,'media_group_id':'album'}})
    group=db[bot.reference_key(OWNER)]['group_key']
    session=db[group]
    assert [r['file_id'] for r in session['items']]==['a','b']
    assert session['caption']=='combine the two'
    assert bot._refs_changed=={(OWNER,group)}


def test_document_images_and_reference_limits(monkeypatch):
    db={};memory(monkeypatch,db);monkeypatch.setattr(bot,'_refs_changed',set())
    msg={'chat':{'id':OWNER},'message_id':1,'media_group_id':'album','document':{'file_id':'png','mime_type':'image/png'}}
    bot.receive_media(msg)
    assert len(db[db[bot.reference_key(OWNER)]['group_key']]['items'])==1
    with pytest.raises(ValueError):bot.receive_media({**msg,'document':{'file_id':'x','mime_type':'application/pdf'}})
    for i in range(2,11):bot.receive_media({**photo(i,str(i)),'media_group_id':'album'})
    with pytest.raises(ValueError,match='10'):bot.receive_media({**photo(11,'eleven'),'media_group_id':'album'})


def test_reply_snapshots_all_refs_and_clear_does_not_touch_job(monkeypatch):
    refs=[{'identity':'a','file_id':'file','message_id':1}]
    group='media:album'
    db={group:{'items':refs},'media-message:'+str(OWNER)+':1':group};memory(monkeypatch,db)
    monkeypatch.setattr(bot,'tg',lambda *a,**k:{'message_id':50})
    bot.handle({'message':{'chat':{'id':OWNER},'message_id':2,'text':'/editar make it blue',
                          'reply_to_message':{'message_id':1}}})
    job=next(v for k,v in db.items() if k.startswith('job:'))
    assert job['recipe']['positive_prompt']=='make it blue' and job['operation']=='edit'
    bot.handle({'message':{'chat':{'id':OWNER},'message_id':3,'text':'/limpiar'}})
    assert job['telegram_inputs']==refs


def test_inputs_are_saved_on_cpu_and_reused_without_network(models,monkeypatch):
    import io
    from PIL import Image
    from qwen_service.backend import Backend
    from qwen_service.contracts import normalize
    out=io.BytesIO();Image.new('RGB',(64,64),'red').save(out,'PNG');raw=out.getvalue()
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_content(self,*a):yield raw
    monkeypatch.setenv('TELEGRAM_QWEN21_BOT_TOKEN','test')
    monkeypatch.setattr(bot,'tg',lambda *a,**k:{'file_path':'photos/test.png','file_size':len(raw)})
    monkeypatch.setattr(bot,'_http',SimpleNamespace(get=lambda *a,**k:Response()))
    backend=Backend(lambda:None,lambda *a:pytest.fail('CPU upload must not call GPU'))
    recipe=parse_prompt('make blue',{})
    refs=[{'identity':'a','file_id':'file','message_id':1},{'identity':'b','file_id':'file2','message_id':2}]
    payload,op=bot.prepare_inputs({'telegram_inputs':refs},recipe,'edit',backend)
    assert op=='edit' and len(payload['input_images'])==2
    assert normalize(payload,'edit')['mode']=='edit'
    assert (models/'inputs'/payload['input_images'][0]).exists()
    monkeypatch.setattr(bot,'tg',lambda *a,**k:pytest.fail('Saved references must be reused'))
    assert bot.prepare_inputs({'telegram_inputs':refs},payload,op,backend)==(payload,op)


def test_reference_budget_and_alignment_before_confirmation(monkeypatch):
    monkeypatch.setattr(bot,'send',lambda *a,**k:pytest.fail('Invalid payload should fail before confirmation'))
    with pytest.raises(ValueError):parse_prompt('x --ref 777',{})
    with pytest.raises(ValueError):parse_prompt('x -w 1328',{})
    with pytest.raises(ValueError,match='referencias'):
        bot.draft(OWNER,parse_prompt('x --ref 1024',{}),references=[{}]*10)
