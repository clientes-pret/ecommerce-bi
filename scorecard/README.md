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

Pendiente: carga diaria automática (reemplaza al botón "Actualizar hoy" y a la carga manual) y
que el Scorecard lea de acá en vez del artifact.
