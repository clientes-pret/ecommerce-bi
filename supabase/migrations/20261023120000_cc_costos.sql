-- Costos fijos y variables del negocio (los carga administración, los ve administración/dueño).
create table if not exists cc_costos (
  id bigint generated always as identity primary key,
  tipo text not null check (tipo in ('fijo','variable')),
  categoria text not null,
  concepto text,
  monto numeric(18,2) not null check (monto >= 0),
  periodo date not null,            -- siempre el día 1 del mes al que corresponde el costo
  nota text,
  creado_por uuid references auth.users(id) default auth.uid(),
  created_at timestamptz not null default now()
);
create index if not exists cc_costos_tipo_periodo_idx on cc_costos(tipo, periodo);
alter table cc_costos enable row level security;
drop policy if exists costos_all on cc_costos;
create policy costos_all on cc_costos for all to authenticated
  using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));
revoke all on cc_costos from anon;
