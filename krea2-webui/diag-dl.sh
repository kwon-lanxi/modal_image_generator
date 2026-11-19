#!/usr/bin/env bash
MAN="https://krea2-manager-cpu-2202764-v16.app.beam.cloud"
KEY="qvutkjIejz7cICasb6Dk8eoZwdrXb0IT4sks7lNyRmHfo1EUy0llptP-drJhBenGc_m-oxZ4zY6tEfTwiukDGA=="
echo "=== health ==="
curl -s -m 15 -X POST https://krea2-webui.pages.dev/api/beam \
  -H 'Content-Type: application/json' \
  -d "{\"url\":\"$MAN/health\",\"method\":\"GET\",\"apiKey\":\"$KEY\"}" | head -c 400
echo; echo "=== download civitai (prueba) ==="
curl -s -m 60 -X POST https://krea2-webui.pages.dev/api/beam \
  -H 'Content-Type: application/json' \
  -d "{\"url\":\"$MAN/download\",\"method\":\"POST\",\"apiKey\":\"$KEY\",\"body\":{\"source\":\"civitai\",\"url\":\"https://civitai.com/api/download/models/123456\",\"name\":\"test_x\",\"target\":\"lora\",\"overwrite\":false}}" | head -c 600
echo; echo "=== download batch ==="
curl -s -m 30 -X POST https://krea2-webui.pages.dev/api/beam \
  -H 'Content-Type: application/json' \
  -d "{\"url\":\"$MAN/download/batch\",\"method\":\"POST\",\"apiKey\":\"$KEY\",\"body\":{\"items\":[{\"source\":\"huggingface\",\"repo_id\":\"x\",\"filename\":\"y\",\"name\":\"t1\",\"target\":\"lora\"}]}}" | head -c 400
echo; echo "=== list_all (solo keys y tamaño) ==="
curl -s -m 60 -X POST https://krea2-webui.pages.dev/api/beam \
  -H 'Content-Type: application/json' \
  -d "{\"url\":\"$MAN/list_all\",\"method\":\"GET\",\"apiKey\":\"$KEY\"}" -o /tmp/listall.json -w 'bytes:%{size_download} http:%{http_code}\n'
python -c "
import json
try:
    d=json.load(open('/tmp/listall.json'))
    print('keys:', list(d.keys()))
    for k in ['loras','unets','clips','vaes','upscalers']:
        m=d.get(k,{})
        total=0
        for mount,info in (m.items() if isinstance(m,dict) else []):
            total+=len((info.get('tree',{}) or {}).get('_root',[]))
        print(k, 'mounts:', len(m), 'root_files:', total)
except Exception as e:
    print('parse error', e)
"
