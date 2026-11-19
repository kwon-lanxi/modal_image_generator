"""Small source resolver: paste a HF URI/URL or a Civitai .com/.red URL."""
from __future__ import annotations

import os
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import requests

HF_HOSTS = {"huggingface.co", "www.huggingface.co"}
CIVITAI_HOSTS = {"civitai.com", "www.civitai.com", "civitai.red", "www.civitai.red"}


def hf_source(item: dict, uri: str):
    from huggingface_hub import hf_hub_url

    repo, filename = item.get("repo_id", ""), item.get("filename", "")
    revision = item.get("revision", "main")
    if uri:
        parsed = urlparse(uri)
        if parsed.username or parsed.password or parsed.port:
            raise ValueError("URL de Hugging Face inválida")
        if parsed.scheme == "hf":
            pieces = parsed.path.strip("/").split("/")
            if len(pieces) < 2:
                raise ValueError("Formato: hf://autor/repositorio/carpeta/archivo.safetensors")
            repo = parsed.netloc + "/" + pieces[0]
            filename = unquote("/".join(pieces[1:]))
            revision = parse_qs(parsed.query).get("revision", [revision])[0]
        elif parsed.scheme == "https" and parsed.hostname in HF_HOSTS:
            pieces = parsed.path.strip("/").split("/")
            if len(pieces) < 5 or pieces[2] not in {"resolve", "blob"}:
                raise ValueError("Usar el enlace resolve/blob del archivo de Hugging Face")
            repo, revision = "/".join(pieces[:2]), unquote(pieces[3])
            filename = unquote("/".join(pieces[4:]))
        else:
            raise ValueError("URL de Hugging Face inválida")
    elif item.get("hf_subfolder"):
        filename = item["hf_subfolder"].strip("/") + "/" + filename
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not filename:
        raise ValueError("Indicar un enlace HF, hf:// o repo_id + filename")
    if filename.startswith("/") or ".." in PurePosixPath(filename).parts or "\\" in filename:
        raise ValueError("Ruta HF inválida")
    url = hf_hub_url(repo, filename, revision=revision)
    token = os.getenv("HF_TOKEN", "")
    return url, ({"Authorization": f"Bearer {token}"} if token else {}), {
        "source": "huggingface", "repo_id": repo, "filename": filename,
        "revision": revision, "source_url": url,
    }


def civitai_source(item: dict, uri: str):
    host = "civitai.com"
    model_id, version_id = item.get("model_id"), item.get("model_version_id")
    selection = {}
    direct = False
    if uri:
        parsed = urlparse(uri)
        if parsed.scheme != "https" or parsed.hostname not in CIVITAI_HOSTS or parsed.username or parsed.password or parsed.port:
            raise ValueError("Usar un enlace HTTPS de civitai.com o civitai.red")
        host = parsed.hostname.removeprefix("www.")
        query = parse_qs(parsed.query)
        selection = {key: values[0] for key, values in query.items()
                     if key in {"fileId", "type", "format", "size", "fp"}}
        match = re.match(r"^/api/download/models/(\d+)/?$", parsed.path)
        if match:
            version_id, direct = match.group(1), True
        else:
            match = re.match(r"^/models/(\d+)(?:/[^/]*)?/?$", parsed.path)
            if not match:
                raise ValueError("Pegar la página del modelo o su enlace de descarga Civitai")
            model_id = match.group(1)
            version_id = query.get("modelVersionId", [version_id])[0]
    for value in (model_id, version_id):
        if value is not None and (not str(value).isdigit() or int(value) <= 0):
            raise ValueError("ID Civitai inválido")
    token = (os.getenv("CIVITAI_RED_API_KEY", "") if host == "civitai.red" else "") or os.getenv("CIVITAI_API_KEY", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    origin = {"source": "civitai", "host": host}
    # A direct/version link plus a custom name requires no metadata lookup.
    lookup = not version_id or not item.get("name")
    info = None
    if lookup:
        if not version_id and not model_id:
            raise ValueError("Indicar enlace Civitai o model_version_id")
        path = f"model-versions/{version_id}" if version_id else f"models/{model_id}"
        with requests.get(f"https://{host}/api/v1/{path}", headers=headers, timeout=(15, 45)) as response:
            if response.status_code != 200:
                raise ValueError(f"Civitai no pudo resolver el modelo: HTTP {response.status_code}")
            data = response.json()
        if version_id:
            info = data
        else:
            versions = data.get("modelVersions", [])
            if not versions:
                raise ValueError("Civitai no publicó versiones descargables")
            info = versions[0]
            version_id = info["id"]
        files = [f for f in info.get("files", [])
                 if f.get("name", "").lower().endswith(".safetensors")
                 and f.get("type", "Model") == "Model"]
        if selection.get("fileId"):
            files = [f for f in files if str(f.get("id")) == selection["fileId"]]
        for key, value in selection.items():
            field = {"format": "format", "size": "size", "fp": "fp"}.get(key)
            if field:
                files = [f for f in files if str(f.get("metadata", {}).get(field, "")).lower() == value.lower()]
        if not files:
            raise ValueError("No hay un archivo safetensors compatible con la selección")
        file = next((f for f in files if f.get("primary")), files[0])
        if file.get("id"):
            selection = {"fileId": str(file["id"])}
        origin.update(filename=file["name"], model_name=info.get("model", {}).get("name"),
                      model_type=info.get("model", {}).get("type") or data.get("type"),
                      version_name=info.get("name"), trained_words=info.get("trainedWords", []),
                      base_model=info.get("baseModel"), sha256=file.get("hashes", {}).get("SHA256"),
                      file_id=file.get("id"))
    if not version_id:
        raise ValueError("No se pudo resolver la versión Civitai")
    url = f"https://{host}/api/download/models/{version_id}"
    if selection:
        url += "?" + urlencode(selection)
    origin.update(model_id=str(model_id) if model_id else None, model_version_id=str(version_id),
                  filename=origin.get("filename", f"civitai_{version_id}.safetensors"), source_url=url)
    return url, headers, origin


def download_source(item: dict):
    uri = (item.get("url") or item.get("hf_uri") or "").strip()
    source = item.get("source", "auto").lower()
    if source == "auto":
        parsed = urlparse(uri)
        if parsed.hostname in CIVITAI_HOSTS or item.get("model_version_id") or item.get("model_id"):
            source = "civitai"
        else:
            source = "huggingface"
    if source in {"huggingface", "hf"}:
        return hf_source(item, uri)
    if source == "civitai":
        return civitai_source(item, uri)
    raise ValueError("Source debe ser auto, huggingface o civitai")
