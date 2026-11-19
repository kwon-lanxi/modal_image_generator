import json
from pathlib import Path
from .test_qwen_service import generated,FailedR2
from qwen_service.backend import Backend
from qwen_service.contracts import normalize


def test_batch_has_one_gpu_call_distinct_seeds_and_all_saved_metadata(models):
    calls=[]
    def render(settings,request,*args):
        calls.append(request)
        result=generated()
        result['batch_pixels']=[result['pixels']]*request['n']
        return result
    backend=Backend(lambda:None,render)
    backend.r2=FailedR2()
    payload={'positive_prompt':'test','n':4,'seed':2**63-2,'return_base64':False}
    first=backend.execute('generate',payload,'e'*32)
    assert first['success'] and len(first['images'])==4
    assert len(set(first['seeds']))==4 and all(0<s<2**63 for s in first['seeds'])
    assert len({i['filename'] for i in first['images']})==4
    assert [x['metadata']['seed'] for x in first['batch']]==first['seeds']
    assert backend.execute('generate',payload,'e'*32)==first
    assert len(calls)==1


def test_parallel_batch_noise_is_seeded_independently():
    import pytest
    torch=pytest.importorskip('torch')
    from qwen_service.engine import batch_noise
    latent=torch.zeros(1,4,8,8)
    def noise(x,seed):return torch.randn(x.shape,generator=torch.Generator().manual_seed(seed))
    batch,values=batch_noise(latent,[42,43,44,45],noise)
    assert batch.shape==(4,4,8,8)
    assert not torch.equal(values[0],values[1])
    assert torch.equal(values[2:3],noise(latent,44))


def test_batch_flag_and_vae_aliases():
    from qwen_tg_logic import parse_prompt
    r=parse_prompt('test --n 4 --vae base',{})
    assert r['n']==4 and r['vae_name']


def test_batch_delivery_resumes_without_resending_completed_items(monkeypatch):
    import copy,pytest
    from .test_qwen_telegram import memory
    import qwen_telegram as bot
    db={};memory(monkeypatch,db)
    entries=[{'success':True,'seed':i+1} for i in range(4)]
    images=[{'filename':str(i)+'.png'} for i in range(4)]
    job={'chat':1,'message_id':20,'recipe':{'n':4},'result':{'success':True,'images':images,'batch':entries}}
    db['job:parent']=copy.deepcopy(job)
    calls=[]
    def tg(method,**kwargs):
        if method=='sendDocument':
            calls.append(kwargs['_files']['document'][0])
            if len(calls)==2:raise bot.TelegramDeliveryError('connection_error',ambiguous=True)
        return {'message_id':30,'document':{'file_id':'file'}}
    monkeypatch.setattr(bot,'tg',tg)
    monkeypatch.setattr(bot,'api',lambda *a:bot.Result(content=b'png'))
    with pytest.raises(bot.TelegramDeliveryError):bot.deliver('parent',copy.deepcopy(job))
    assert db['job:parent']['status']=='needs_review'
    assert db['job:parent_0']['status']=='done'
    pending=copy.deepcopy(db['job:parent_1']);pending['status']='result'
    bot.deliver('parent_1',pending)
    bot.deliver('parent',copy.deepcopy(db['job:parent']))
    assert calls==['0.png','1.png','1.png','2.png','3.png']
    assert db['job:parent']['status']=='done'
