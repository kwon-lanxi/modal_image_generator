# Lectura de costos de Modal ·16/11/2026

Capturas aportadas por el usuario: totalUSD0,21; recursoL40S0,15; CPU0,03; RAM0,02; funciónGPU0,19 y writer0,01. Son vistas alternativas de los mismos recursos y los valores visibles están redondeados por separado. No sumar funciones a recursos.0,15/0,000542≈276,75segundos GPU acumulados; es aproximado, no duración exacta de una imagen.

La tarifa L40S vigente es0,000542USD/s (0,03252/min); CPU0,0000131/core/s; RAM0,00000222/GiB/s. Se cobran recursos usados/solicitados, CPU/RAM por max(reserva, uso). Una funciónCPU bloqueada esperando la llamada GPU conserva su reservaCPU/RAM. Cola previa a asignarGPU no esGPU ocupada; inicialización/carga/warmup/restauración con recursos asignados no se deben equiparar a cola gratuita. No hay mínimo de minutos ni cargo fijo documentado por asignación. single_use elimina la espera ociosa entre trabajos después del retorno/teardown.

El RPC observado es latencia, no medición de segundos facturados GPU. En el job del usuario90398fbb3e9a070b328f64b47f4b9315: método19,3479s y RPC126,8042s. La diferencia107,4563s es un intervalo mixto. Los logs por sí solos no prueban una avería de hardware ni descomponen ese tiempo con precisión.

La factura exacta depende del reporteModal y créditos. Uso del dashboard se agrega por período y puede actualizarse con demora. Un redespliegue invalida snapshots: no asumir reutilización del anterior tras cambiar código.

Fuentes primarias consultadas:
- [Tarifas y planes](https://modal.com/pricing)
- [Billing, retrasos y créditos](https://modal.com/docs/guide/billing)
- [Reservas y uso CPU/RAM](https://modal.com/docs/guide/resources)
- [Cola y cold starts](https://modal.com/docs/guide/cold-start)
- [Snapshots e invalidación](https://modal.com/docs/guide/memory-snapshots)
