#!/bin/bash
# ¿El relay devuelve la URL de catbox en el body? ¿Es repetible?
CATHASH='b127cc80eb578b94bf81d9d3f'
cd /tmp
printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB\x60\x82' > tiny.png
URL=$(python -c "import urllib.parse;print(urllib.parse.quote('https://catbox.moe/user/api.php', safe=''))")

for i in 1 2 3; do
  echo "── intento $i ──"
  BODY=$(curl -s -m 25 -X POST "https://test.cors.workers.dev/?$URL" \
    -F 'reqtype=fileupload' -F "userhash=$CATHASH" -F 'fileToUpload=@tiny.png' \
    -H 'Origin: https://krea2-webui.pages.dev')
  echo "$BODY" | head -c 300
  echo
done
