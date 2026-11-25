Native adapter follows the data-space algorithm and FP32 unmerged LoRA/time-path method documented in akhaliq/MOSS-Image-IDM-2-Steps/inference.py, revision 24737b181dd49246324d18a3c1bc170c1ff2e09d.
Upstream credits darask0/MOSS-Image_4-step_adapter_lora and uses Apache-2.0. The implementation in moss_service/idm.py is an adapter for native ComfyUI fused QKV and batched inference; no remote pipeline is executed.
IDM v1 export750 is the T2I selection; v2 editing weights are intentionally not selected. Base use is experimental.
