"""Published Modal rates; estimates are deliberately NOT called billed costs."""
from __future__ import annotations

RATES = {
    "source": "https://modal.com/pricing", "verified_at": "2026-11-16",
    "currency": "USD", "l4_per_second": 0.000222,
    "cpu_physical_core_per_second": 0.0000131, "ram_gib_per_second": 0.00000222,
    "volume_gib_per_month": 0.09, "egress_gib": 0.04,
}


def estimate(timing: dict, generation: dict, writer_seconds: float = 0) -> dict:
    method = max(float(timing.get("gpu_method_total", 0)), 0)
    rpc = max(float(timing.get("gpu_call", method)), method)
    # A process RSS sample cannot establish time-integrated RAM usage or startup peak.
    ram = max(24.0, generation.get("rss_bytes", 0) / 1024**3)
    gpu = rpc * RATES["l4_per_second"]
    gpu_cpu = rpc * 4 * RATES["cpu_physical_core_per_second"]
    gpu_ram = rpc * ram * RATES["ram_gib_per_second"]
    writer = max(writer_seconds, 0) * (0.25 * RATES["cpu_physical_core_per_second"] + 0.5 * RATES["ram_gib_per_second"])
    host_rate = (RATES["l4_per_second"] + 4 * RATES["cpu_physical_core_per_second"]
                 + ram * RATES["ram_gib_per_second"])
    return {
        "total": round(gpu + gpu_cpu + gpu_ram + writer, 8),
        "total_basis": "rpc_wall_scenario_not_billed_time",
        "method_only_total": round(method * host_rate + writer, 8),
        "rpc_scenario_total": round(gpu + gpu_cpu + gpu_ram + writer, 8),
        "scenario_note": "Método excluye arranque/restauración; RPC incluye cola/transporte sin GPU. "
                         "Son dos referencias, no límites garantizados ni factura.",
        "is_estimate": True, "billing_verified": False, "currency": "USD",
        "rates": dict(RATES),
        "components": {"gpu": round(gpu, 8), "gpu_host_cpu": round(gpu_cpu, 8),
                       "gpu_host_ram": round(gpu_ram, 8), "cpu_writer_until_metadata": round(writer, 8)},
        "measured": {"gpu_method_seconds": method, "gpu_rpc_wall_seconds": rpc,
                     "cpu_writer_seconds_until_metadata": round(max(writer_seconds, 0), 4)},
        "assumptions": {"gpu_host_cpu_cores": 4, "gpu_host_ram_gib": round(ram, 4),
                        "writer_cpu_cores": 0.25, "writer_ram_gib": 0.5},
        "scope": "Render y espera RPC observados, con tarifas publicadas; no factura por invocation",
        "excluded": ["API/polling", "compresión y persistencia posteriores a la captura de metadatos",
                     "upload R2", "almacenamiento", "egreso", "teardown",
                     "uso CPU/RAM no instrumentado y créditos/descuentos"],
        "limitations": ["RPC incluye aprovisionamiento/transporte que no equivalen necesariamente a GPU facturada",
                        "RAM y CPU se cobran por max(reserva, uso); aquí se aproximan",
                        "El dashboard/reporte de facturación de Modal es la fuente del cobro real"],
    }
