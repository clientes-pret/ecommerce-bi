-- Scorecard diario (ventas, rentabilidad y ads por canal) — hasta hoy vivía en la base del artifact
-- "Scorecard Pret & Laván". Una fila por día × marca × canal; los "pending" del artifact quedan en NULL
-- (NULL = sin dato todavía; 0 = cero real).

create table if not exists scorecard_canal_dia (
  fecha              date    not null,
  marca              text    not null check (marca in ('pret','lavan')),
  canal              text    not null check (canal in ('tn','ml','local')),
  ventas             numeric(18,2),   -- TN/ML: revenue de órdenes · local: total recibido
  ordenes            integer,
  facturacion_neta   numeric(18,2),   -- ML ya trae el envío descontado
  costo_productos    numeric(18,2),
  costo_envio        numeric(18,2),   -- solo TN (en ML va dentro de la neta)
  local_efectivo     numeric(18,2),
  local_otros_medios numeric(18,2),
  local_gastado      numeric(18,2),
  fuente             text not null default 'manual',
  actualizado_en     timestamptz not null default now(),
  primary key (fecha, marca, canal)
);

create table if not exists scorecard_ads_dia (
  fecha          date not null,
  marca          text not null check (marca in ('pret','lavan')),
  plataforma     text not null check (plataforma in ('meta','google','ml')),
  inversion      numeric(18,2),
  fuente         text not null default 'manual',
  actualizado_en timestamptz not null default now(),
  primary key (fecha, marca, plataforma)
);

create table if not exists scorecard_notas (
  fecha          date primary key,
  nota           text not null,
  actualizado_en timestamptz not null default now()
);

-- Totales por mes: lo que lee el P&L. "rentabilidad_s_ads" solo suma días con neta y costo cargados.
create or replace view v_scorecard_mensual as
select date_trunc('month', fecha)::date as periodo, marca, canal,
       sum(ventas)                                         as ventas,
       sum(ordenes)                                        as ordenes,
       sum(facturacion_neta)                               as facturacion_neta,
       sum(costo_productos)                                as costo_productos,
       sum(costo_envio)                                    as costo_envio,
       sum(facturacion_neta - costo_productos - coalesce(costo_envio,0))
         filter (where facturacion_neta is not null and costo_productos is not null) as rentabilidad_s_ads,
       count(*) filter (where facturacion_neta is not null and costo_productos is not null) as dias_con_rentabilidad,
       count(*)                                            as dias
from scorecard_canal_dia
group by 1,2,3;

create or replace view v_scorecard_ads_mensual as
select date_trunc('month', fecha)::date as periodo, marca, plataforma, sum(inversion) as inversion
from scorecard_ads_dia
group by 1,2,3;

alter table scorecard_canal_dia enable row level security;
alter table scorecard_ads_dia   enable row level security;
alter table scorecard_notas     enable row level security;

drop policy if exists scorecard_read on scorecard_canal_dia;
create policy scorecard_read on scorecard_canal_dia for select to authenticated using (cc_rol() in ('admin','dueno'));
drop policy if exists scorecard_ads_read on scorecard_ads_dia;
create policy scorecard_ads_read on scorecard_ads_dia for select to authenticated using (cc_rol() in ('admin','dueno'));
drop policy if exists scorecard_notas_read on scorecard_notas;
create policy scorecard_notas_read on scorecard_notas for select to authenticated using (cc_rol() in ('admin','dueno'));

-- Las vistas respetan el RLS de quien consulta.
alter view v_scorecard_mensual     set (security_invoker = true);
alter view v_scorecard_ads_mensual set (security_invoker = true);

revoke all on scorecard_canal_dia, scorecard_ads_dia, scorecard_notas, v_scorecard_mensual, v_scorecard_ads_mensual from anon;
grant select, insert, update, delete on scorecard_canal_dia, scorecard_ads_dia, scorecard_notas to service_role;
grant select on v_scorecard_mensual, v_scorecard_ads_mensual to service_role;
grant select on scorecard_canal_dia, scorecard_ads_dia, scorecard_notas, v_scorecard_mensual, v_scorecard_ads_mensual to authenticated;
