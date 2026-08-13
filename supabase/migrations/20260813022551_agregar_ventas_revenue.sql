-- Agrega unidades vendidas / revenue / ganancia / canal dominante al cálculo
-- semanal — el pedido original de reposición (Excel) los tenía y se habían
-- quedado afuera de la primera versión de repo_calculo_semanal.

alter table repo_calculo_semanal
  add column if not exists vendido_deposito   integer,
  add column if not exists vendido_full_pret  integer,
  add column if not exists vendido_full_lavan integer,
  add column if not exists revenue_60d        numeric,
  add column if not exists ganancia_60d       numeric,
  add column if not exists canal_dominante    text;
