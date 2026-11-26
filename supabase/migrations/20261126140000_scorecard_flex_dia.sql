-- Costo estimado de los envíos Flex de Mercado Libre por día y marca (reposicion/scorecard_flex.py). Informativo: el
-- Scorecard lo muestra en su propia columna para compararlo contra lo que se le paga a la mensajería; no entra en la
-- rentabilidad, ni en P&L ni en Cashflow.
create table if not exists scorecard_flex_dia (
  fecha          date not null,
  marca          text not null check (marca in ('pret', 'lavan')),
  envios         int not null,
  costo          numeric(18,2) not null,
  sin_zona       int not null default 0,   -- envíos Flex cuyo código postal no se pudo ubicar (no suman costo)
  actualizado_en timestamptz not null default now(),
  primary key (fecha, marca)
);
alter table scorecard_flex_dia enable row level security;
drop policy if exists scorecard_flex_dia_leer on scorecard_flex_dia;
create policy scorecard_flex_dia_leer on scorecard_flex_dia for select to authenticated using (cc_rol() in ('admin', 'dueno'));
revoke all on scorecard_flex_dia from anon, authenticated;
grant select on scorecard_flex_dia to authenticated;
grant select, insert, update, delete on scorecard_flex_dia to service_role;
