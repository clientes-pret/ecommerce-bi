# Scorecard → Supabase

El Scorecard diario guardaba sus datos en la base del artifact. Esta carpeta los pasa a Supabase
(proyecto de reposición, `fvkbclkofsggbvwcxipa`) para que el panel de control (Ventas, P&L, Cashflow)
los lea de un solo lugar.

- Migración: `supabase/migrations/20261023190000_scorecard.sql` — tablas `scorecard_canal_dia`,
  `scorecard_ads_dia`, `scorecard_notas` y vistas `v_scorecard_mensual`, `v_scorecard_ads_mensual`.
  Lectura solo para usuarios logueados con rol admin/dueño; escritura solo service_role.
- Historia: `data/artifact_days.json` (export del 2026-10-23, 151 días). No se commitea (`data/` está en .gitignore).
- Carga: `venv/bin/python scorecard/migrar_desde_artifact.py` (simula) y `--aplicar` (carga; se puede repetir).

Pasos:
1. `supabase db push` (aplica la migración).
2. `venv/bin/python scorecard/migrar_desde_artifact.py --aplicar`.

Tiendanube (Pret y Laván) se carga solo todos los días con `reposicion/scorecard_tn_sync.py` (paso final de
`cashflow_sync.yml`): ventas, órdenes, costo de productos y facturación neta, sin Excel. No carga `costo_envio`
(su definición no coincide con ningún campo de la API). Para cargar historia: `gh workflow run cashflow_sync.yml -f desde=YYYY-MM-DD`.

Mercado Libre (Pret y Laván) también se carga solo con `reposicion/scorecard_ml_sync.py` (último paso del mismo workflow,
siempre los últimos 4 días): ventas, órdenes, neto en MercadoPago y costo de productos, con los criterios de la planilla de
rentabilidad de ML (día = cierre de la orden, solo órdenes con pago aprobado; costo de Tiendanube, y para SKUs que ya no
están ahí la tabla `scorecard_costos_extra`). Verificado contra la exportación de la planilla: ventas idénticas casi todos
los días, neta y costo dentro de ±1 %. Historia a mano: `python3 -m reposicion.scorecard_ml_sync --desde YYYY-MM-DD --aplicar`
(tarda ~1 min por cada 300 órdenes).

Pendiente (ads, local): carga diaria automática (reemplaza al botón "Actualizar hoy" y a la carga manual) y
que el Scorecard lea de acá en vez del artifact.
