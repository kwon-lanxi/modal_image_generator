import copy
from types import SimpleNamespace
import pytest
import telegram_bot as bot


def setup(monkeypatch,image):
    job={'chat':7112930476,'message_id':12,'request_id':'job-test','result':{'success':True,'images':[image]}}
    db={}
    monkeypatch.setattr(bot,'state',lambda op,key='',value=None:db.update({key:copy.deepcopy(value)}) if op=='put' else db.get(key,value))
    return job,db


def large():return {'filename':'large.png','size_bytes':14_000_000,'r2':{'status':'uploaded','url':'https://example.r2.dev/image.png'}}


def test_large_delivery_is_only_r2_link_and_never_reads_png(monkeypatch):
    job,db=setup(monkeypatch,large());calls=[]
    monkeypatch.setattr(bot,'api',lambda *a:pytest.fail('No file read for large link'))
    monkeypatch.setattr(bot,'tg',lambda method,**data:calls.append((method,data)) or {'message_id':20})
    bot.deliver('x',job)
    assert [m for m,k in calls]==['sendMessage','deleteMessage']
    assert calls[0][1]['text']=='https://example.r2.dev/image.png'
    assert calls[0][1]['link_preview_options']=={'is_disabled':True}
    assert db['job:x']['status']=='done' and db['job:x']['delivery_method']=='r2_link'


def test_small_upload_falls_back_once_without_retrying_document(monkeypatch):
    image=large();image['size_bytes']=100
    job,db=setup(monkeypatch,image);calls=[]
    def tg(method,**data):
        calls.append(method)
        if method=='sendDocument':raise bot.TelegramDeliveryError('connection_error',ambiguous=True)
        return {'message_id':20}
    monkeypatch.setattr(bot,'tg',tg)
    bot.deliver('x',job,b'PNG')
    assert calls==['sendDocument','sendMessage','deleteMessage']
    assert db['job:x']['document_delivery_ambiguous'] is True


@pytest.mark.parametrize('code',[401,403,429])
def test_permission_or_rate_limit_does_not_send_more_messages(monkeypatch,code):
    image=large();image['size_bytes']=100
    job,db=setup(monkeypatch,image)
    monkeypatch.setattr(bot,'tg',lambda *a,**k:(_ for _ in ()).throw(bot.TelegramDeliveryError('rejected',code=code)))
    with pytest.raises(bot.TelegramDeliveryError):bot.deliver('x',job,b'PNG')
    assert db['job:x']['status']=='delivery_unknown'


def test_missing_public_url_cannot_become_broken_volume_link(monkeypatch):
    image=large();image['r2']={'status':'uploaded','url':None};image['url']='/serve_image?filename=large.png'
    job,db=setup(monkeypatch,image)
    assert bot.r2_link(job['result']) is None
    assert bot.should_upload(job['result'],job)


def test_upload_timeout_and_error_logging_do_not_expose_secret(monkeypatch,capsys):
    import requests
    calls=[]
    secret='VERY_PRIVATE_TOKEN_1234'
    monkeypatch.setenv('TELEGRAM_KREA2_BOT_TOKEN',secret)
    def post(*a,**k):
        calls.append(k)
        raise requests.exceptions.ConnectionError('https://api.telegram.org/bot'+secret+'/sendDocument')
    monkeypatch.setattr(bot,'_http',SimpleNamespace(post=post));monkeypatch.setattr(bot,'_not_before',0)
    with pytest.raises(bot.TelegramDeliveryError) as error:
        bot.tg('sendDocument',_files={'document':('x.png',b'PNG','image/png')})
    assert error.value.kind=='connection_error' and error.value.ambiguous
    assert calls[0]['timeout']==(20,120)
    output=capsys.readouterr().out
    assert 'connection_error' in output and 'upload_bytes' in output
    assert secret not in output and 'api.telegram.org' not in output


def test_provider_rejection_is_logged_with_redacted_description(monkeypatch,capsys):
    secret='NEVER_PRINT_TOKEN_123'
    monkeypatch.setenv('TELEGRAM_KREA2_BOT_TOKEN',secret)
    monkeypatch.setattr(bot,'_not_before',0)
    response=SimpleNamespace(status_code=400,json=lambda:{'ok':False,'error_code':400,'description':'Bad Request: '+secret})
    monkeypatch.setattr(bot,'_http',SimpleNamespace(post=lambda *a,**k:response))
    with pytest.raises(bot.TelegramDeliveryError):bot.tg('sendDocument',document='bad-file-id')
    output=capsys.readouterr().out
    assert 'telegram.rejected' in output and '400' in output and '[redacted]' in output
    assert secret not in output


def test_only_connect_timeout_is_retried_once(monkeypatch):
    import requests
    calls=[]
    def first(*a,**k):calls.append('first');raise requests.exceptions.ConnectTimeout('hidden')
    def second(*a,**k):calls.append('second');return SimpleNamespace(status_code=200,json=lambda:{'ok':True,'result':{'message_id':1}})
    monkeypatch.setattr(bot,'_not_before',0)
    monkeypatch.setattr(bot,'_http',SimpleNamespace(post=first,close=lambda:None))
    monkeypatch.setattr(requests,'Session',lambda:SimpleNamespace(post=second))
    monkeypatch.setenv('TELEGRAM_KREA2_BOT_TOKEN','test')
    assert bot.tg('sendMessage',chat_id=1,text='x')=={'message_id':1}
    assert calls==['first','second']
