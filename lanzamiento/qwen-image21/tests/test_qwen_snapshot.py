import json
from types import SimpleNamespace
import pytest
from qwen_service import snapshot


def test_compaction_preserves_live_weights_and_releases_only_cache(monkeypatch,capsys):
    torch=pytest.importorskip('torch')
    import ctypes
    weight=torch.tensor([1.,2.,3.]);original=weight.clone();calls=[]
    monkeypatch.setattr(torch.cuda,'synchronize',lambda:calls.append('sync'))
    monkeypatch.setattr(torch.cuda,'empty_cache',lambda:calls.append('empty'))
    monkeypatch.setattr(ctypes,'CDLL',lambda *a:SimpleNamespace())
    stats=iter([{'rss_bytes':20,'cuda_allocated_bytes':10,'cuda_reserved_bytes':18},
                {'rss_bytes':18,'cuda_allocated_bytes':10,'cuda_reserved_bytes':10}])
    monkeypatch.setattr(snapshot,'memory_stats',lambda:next(stats))
    snapshot.compact_snapshot()
    assert torch.equal(weight,original) and calls==['sync','empty','sync']
    event=json.loads(capsys.readouterr().out)
    assert event['event']=='snapshot.compacted' and event['after']['cuda_allocated_bytes']==10
    assert event['after']['cuda_reserved_bytes']<event['before']['cuda_reserved_bytes']
