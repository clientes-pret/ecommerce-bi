-- Sugerencia de Full antes de aplicar el tope por ritmo de venta de calendario (reposicion/core.py: tope_full).
-- Solo informativo: el tablero lo muestra al abrir el producto. Aplicar ANTES de subir el cálculo que la escribe.
alter table repo_calculo_semanal add column if not exists a_reponer_full_pret_sin_tope integer;
alter table repo_calculo_semanal add column if not exists a_reponer_full_lavan_sin_tope integer;
