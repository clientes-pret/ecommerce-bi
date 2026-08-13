-- Desglose de unidades vendidas por canal (para el detalle expandible del
-- tablero) — mismo dato que ya mostraba reporte_reposicion_*.xlsx.

alter table repo_calculo_semanal
  add column if not exists unid_ml_pret  integer,
  add column if not exists unid_ml_lavan integer,
  add column if not exists unid_tn_pret  integer,
  add column if not exists unid_tn_lavan integer;
