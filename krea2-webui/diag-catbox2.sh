#!/bin/bash
# ¿Algún relay CORS puede devolver la URL de catbox? + ¿imgchest acepta description?
CATHASH='b127cc80eb578b94bf81d9d3f'
cd /tmp
printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\x0d\x0a\x2d\xb4\x00\x00\x00\x00IEND\xaeB\x60\x82' > tiny.png

echo '════ 1) proxy catbox DESPLEGADO de nuevo (ee08aba4) ════'
curl -s -m 15 -X POST 'https://ee08aba4.krea2-webui.pages.dev/api/upload/catbox' \
  -F 'file=@tiny.png' -F "userhash=$CATHASH" | head -c 200
echo; echo

echo '════ 2) relay allorigins (raw) a catbox ════'
URL=$(python -c "import urllib.parse;print(urllib.parse.quote('https://catbox.moe/user/api.php', safe=''))")
curl -s -m 20 -i -X POST "https://api.allorigins.win/raw?url=$URL" \
  -F 'reqtype=fileupload' -F "userhash=$CATHASH" -F 'fileToUpload=@tiny.png' \
  -H 'Origin: https://krea2-webui.pages.dev' | head -25
echo

echo '════ 3) relay test.cors.workers.dev a catbox ════'
curl -s -m 20 -i -X POST "https://test.cors.workers.dev/?$URL" \
  -F 'reqtype=fileupload' -F "userhash=$CATHASH" -F 'fileToUpload=@tiny.png' \
  -H 'Origin: https://krea2-webui.pages.dev' | head -15
echo

echo '════ 4) imgchest con campo description ════'
IMGKEY='pJd3tnw82B6dtkk8Af03Ue0yBYlwB7X8a8AJU00mba853131'
curl -s -m 20 -X POST 'https://api.imgchest.com/v1/post' \
  -H "Authorization: Bearer $IMGKEY" \
  -F 'images[]=@tiny.png' \
  -F 'privacy=hidden' \
  -F 'description=# Creado el: 2026-08-04
**Modo**: generate
**Seed**: 424242' | head -c 500
echo
