"""Safe, CPU-only ComfyUI FP8 checkpoint conversion shared by the model apps."""
from __future__ import annotations

import os
import json
import shutil
import subprocess


CONVERTER_VERSION = "pytorch-precision-converter==2.3.0"
FLOAT_DTYPES = {"F16", "BF16", "F32", "F64"}
QUANT_DTYPES = {"I8", "F8_E4M3", "F8_E4M3FN", "F8_E5M2"}


def _tensors(header):
    return {name: item for name, item in header.items() if name != "__metadata__"}


def _comfy_quantized(header):
    tensors = _tensors(header)
    return any(name.endswith(".comfy_quant") for name in tensors) or "_quantization_metadata" in header.get("__metadata__", {})


def _run(executable, args, report_path, timeout):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("wb") as report:
        result = subprocess.run([executable, *map(str, args)], stdout=report,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("Falló la conversión; consultá el informe privado del trabajo")


def _is_plain_bf16_fp16(header):
    weights = [item.get("dtype") for name, item in _tensors(header).items()
               if name.endswith(".weight")]
    return bool(weights) and set(weights) <= {"BF16", "F16"}


def _validate_comfy_fp8(path, header):
    """Check that each exported FP8 layer carries ComfyUI's per-layer metadata."""
    tensors = _tensors(header)
    quant_layers = [name for name in tensors if name.endswith(".comfy_quant")]
    if not quant_layers:
        raise ValueError("El resultado no contiene metadata quant ComfyUI por capa")
    with path.open("rb") as file:
        prefix = file.read(8)
        if len(prefix) != 8:
            raise ValueError("Safetensors truncado durante la verificación")
        import struct
        header_length = struct.unpack("<Q", prefix)[0]
        data_start = 8 + header_length
        for key in quant_layers:
            module = key[:-len(".comfy_quant")]
            quant = tensors[key]
            weight = tensors.get(module + ".weight")
            scale = tensors.get(module + ".weight_scale")
            if (quant.get("dtype") != "U8" or len(quant.get("shape", [])) != 1
                    or not weight or weight.get("dtype") not in {"F8_E4M3", "F8_E4M3FN"}
                    or not scale or scale.get("dtype") != "F32"):
                raise ValueError("Una capa quantizada no tiene weight/scale FP8 válidos")
            start, end = quant.get("data_offsets", [0, 0])
            if end - start != quant["shape"][0] or end - start > 4096:
                raise ValueError("Metadata comfy_quant con tamaño inválido")
            file.seek(data_start + start)
            try:
                metadata = json.loads(file.read(end - start).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Metadata comfy_quant no es JSON válido") from exc
            if not isinstance(metadata, dict) or metadata.get("format") != "float8_e4m3fn":
                raise ValueError("La capa no declara float8_e4m3fn de ComfyUI")
    return len(quant_layers)


def convert_comfy_fp8(*, source, destination, reference, precision, job_id, report_path,
                      log, commit, describe, file_id, safetensors_header, sha256, write_json,
                      source_for_conversion=None, profile_config=None, profile_field="model"):
    """Write a new ComfyUI fp8_scaled file, verify it, then optionally remove BF16/FP16 source."""
    import time

    if precision not in {"fp8_scaled", "fp8_mixed"}:
        raise ValueError("precision debe ser fp8_scaled o fp8_mixed")
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("La conversión nunca sobrescribe el original")
    if destination.exists():
        raise ValueError("El destino ya existe; elegí otro nombre")

    source_header = safetensors_header(source, validate=True)
    source_tensors = _tensors(source_header)
    source_dtypes = {item.get("dtype") for item in source_tensors.values()}
    quantized_input = bool(source_dtypes & QUANT_DTYPES)
    if quantized_input and not _comfy_quantized(source_header):
        raise ValueError("El INT8/FP8 no tiene metadata ComfyUI reconocible; se conserva sin convertir")
    if not (source_dtypes & FLOAT_DTYPES) and not quantized_input:
        raise ValueError("La entrada debe contener pesos BF16, FP16 o un checkpoint cuantizado ComfyUI")

    executable = shutil.which("precision-convert")
    if not executable:
        raise RuntimeError("Falta pytorch-precision-converter en la imagen CPU")

    destination.parent.mkdir(parents=True, exist_ok=True)
    work = report_path.parent / ("." + destination.stem + "." + job_id)
    temporary = work.with_suffix(".safetensors")
    dequantized = work.with_name(work.name + ".bf16.safetensors")
    conversion_input = source_for_conversion.resolve() if source_for_conversion else source
    started = time.monotonic()
    source_info = describe(source)
    original_provenance = source_info.get("provenance", {})
    log.event("conversion.start", source=file_id(source), destination=file_id(destination),
              precision=precision, input_quantized=quantized_input, reference=reference)
    try:
        temporary.unlink(missing_ok=True)
        dequantized.unlink(missing_ok=True)
        if quantized_input:
            # Dequantization is an explicit, separate CPU stage. Never remove its source.
            _run(executable, [source, "-p", "bf16", "-o", dequantized, "--report"],
                 report_path.with_name("dequantize_" + report_path.name), 3500)
            dequantized_header = safetensors_header(dequantized, validate=True)
            if _comfy_quantized(dequantized_header) or any(
                    item.get("dtype") in QUANT_DTYPES for item in _tensors(dequantized_header).values()):
                raise ValueError("El paso INT8/FP8 a BF16 no quitó toda la metadata cuantizada")
            conversion_input = dequantized

        args = [conversion_input, "-p", "fp8_scaled", "--like", reference,
                "--fallback", "bf16", "-o", temporary, "--report"]
        if precision == "fp8_mixed":
            # Keeps tensors above 5% relative error in BF16 while retaining Comfy scaled FP8.
            args += ["--max-error", "0.05"]
        _run(executable, args, report_path, 3500)
        converted = safetensors_header(temporary, validate=True)
        tensors = _tensors(converted)
        fp8_weights = [name for name, item in tensors.items()
                       if name.endswith(".weight") and item.get("dtype") in {"F8_E4M3", "F8_E4M3FN"}]
        comfy_layers = _validate_comfy_fp8(temporary, converted)
        if not fp8_weights or not comfy_layers:
            raise ValueError("El resultado no contiene capas FP8 con formato quant ComfyUI")
        if not any(name.endswith(".weight_scale") for name in tensors):
            raise ValueError("Falta weight_scale del formato FP8 escalado de ComfyUI")
        if any(item.get("dtype") == "I8" and name.endswith(".weight")
               for name, item in tensors.items()):
            raise ValueError("El resultado dejó pesos INT8 sin convertir")

        digest = sha256(temporary)
        os.replace(temporary, destination)
        profile_updates = {}
        if _is_plain_bf16_fp16(source_header) and profile_config is not None:
            source_names = {source.stem, source.name, file_id(source)}
            for name, item in profile_config.profiles().items():
                if item.get(profile_field) in source_names:
                    profile_updates[name] = {profile_field: file_id(destination)}
                    if profile_field == "model":
                        profile_updates[name]["weight_dtype"] = "default"
        write_json(destination.with_suffix(".source.json"), {
            "source": file_id(source), "source_version": source_info["version"],
            "source_provenance": original_provenance,
            "converter": CONVERTER_VERSION, "format": precision,
            "reference": reference, "max_relative_error": 0.05 if precision == "fp8_mixed" else None,
            "profile_updates": profile_updates, "sha256": digest,
        })
        # Persist the verified output before considering the explicitly requested source cleanup.
        commit()
        source_removed = False
        if _is_plain_bf16_fp16(source_header):
            if profile_updates:
                profile_config.save_profile_overrides(profile_updates)
                commit()
            source.unlink()
            source.with_suffix(".source.json").unlink(missing_ok=True)
            commit()
            source_removed = True
            log.event("model.original_removed", file=file_id(source), reason="verified_fp8_conversion")
        result = describe(destination)
        log.event("conversion.complete", file=file_id(destination), precision=precision,
                  fp8_layers=len(fp8_weights), comfy_quant_layers=comfy_layers,
                  source_removed=source_removed, sha256=digest,
                  seconds=round(time.monotonic() - started, 3), size_bytes=destination.stat().st_size)
        return {"success": True, "file": result, "sha256": digest, "precision": precision,
                "fp8_layers": len(fp8_weights), "comfy_quant_layers": comfy_layers,
                "source": file_id(source), "source_removed": source_removed,
                "profiles_updated": sorted(profile_updates),
                "report": "/records/" + job_id + "/conversion"}
    finally:
        temporary.unlink(missing_ok=True)
        dequantized.unlink(missing_ok=True)
        # Flush report and cleanup of partial files without hiding the original error.
        try:
            commit()
        except Exception as exc:
            log.event("conversion.cleanup_commit_failed", error=type(exc).__name__)
