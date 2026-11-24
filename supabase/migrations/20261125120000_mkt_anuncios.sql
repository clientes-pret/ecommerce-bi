-- Marketing: visitas y compras que llegan desde anuncios (Meta y Google Ads), por producto y por página de destino.
-- Salen de GA4 (fuente de la sesión + página de destino) y las carga reposicion/mkt_sync.py. El gasto de Google Ads por campaña
-- también viene de GA4 (tiene Google Ads vinculado). El gasto de Meta por producto llega en otra etapa (necesita un token).

-- Por producto y día: visitas de producto (itemsViewed) y unidades compradas (itemsPurchased) de sesiones que vinieron de un anuncio.
create table if not exists mkt_producto_fuente_dia (
  fecha date not null,
  product_id bigint not null,
  fuente text not null check (fuente in ('meta', 'google')),
  visitas integer not null default 0,
  compras integer not null default 0,
  primary key (fecha, product_id, fuente)
);
create index if not exists mkt_producto_fuente_prod on mkt_producto_fuente_dia (product_id, fecha desc);

-- Por página de destino y día: sesiones y compras de esas sesiones. Es la URL a la que llevan los anuncios
-- (un producto, una categoría o la home); la ruta va sin dominio, sin parámetros y sin barra final.
create table if not exists mkt_pagina_fuente_dia (
  fecha date not null,
  pagina text not null,
  fuente text not null check (fuente in ('meta', 'google')),
  sesiones integer not null default 0,
  compras integer not null default 0,
  primary key (fecha, pagina, fuente)
);

-- Google Ads por campaña y día (gasto y clics de Google Ads, tal como los trae GA4).
create table if not exists mkt_google_campana_dia (
  fecha date not null,
  campana text not null,
  gasto numeric not null default 0,
  clics integer not null default 0,
  sesiones integer not null default 0,
  primary key (fecha, campana)
);

alter table mkt_producto_fuente_dia enable row level security;
alter table mkt_pagina_fuente_dia enable row level security;
alter table mkt_google_campana_dia enable row level security;

drop policy if exists mkt_producto_fuente_lectura on mkt_producto_fuente_dia;
create policy mkt_producto_fuente_lectura on mkt_producto_fuente_dia for select to authenticated using (mkt_rol() is not null);
drop policy if exists mkt_pagina_fuente_lectura on mkt_pagina_fuente_dia;
create policy mkt_pagina_fuente_lectura on mkt_pagina_fuente_dia for select to authenticated using (mkt_rol() is not null);
drop policy if exists mkt_google_campana_lectura on mkt_google_campana_dia;
create policy mkt_google_campana_lectura on mkt_google_campana_dia for select to authenticated using (mkt_rol() is not null);

revoke all on mkt_producto_fuente_dia, mkt_pagina_fuente_dia, mkt_google_campana_dia from anon;
grant select on mkt_producto_fuente_dia, mkt_pagina_fuente_dia, mkt_google_campana_dia to authenticated;
grant select, insert, update, delete on mkt_producto_fuente_dia, mkt_pagina_fuente_dia, mkt_google_campana_dia to service_role;

-- Resúmenes para las pantallas. La ventana es de p_dias días completos hasta ayer (igual que el semáforo).
create or replace function mkt_ads_producto(p_dias integer default 7)
returns table (product_id bigint, visitas_meta bigint, compras_meta bigint, visitas_google bigint, compras_google bigint)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select f.product_id,
         coalesce(sum(f.visitas) filter (where f.fuente = 'meta'), 0)::bigint,
         coalesce(sum(f.compras) filter (where f.fuente = 'meta'), 0)::bigint,
         coalesce(sum(f.visitas) filter (where f.fuente = 'google'), 0)::bigint,
         coalesce(sum(f.compras) filter (where f.fuente = 'google'), 0)::bigint
  from mkt_producto_fuente_dia f
  where f.fecha between v_hoy - p_dias and v_hoy - 1
  group by f.product_id
  order by f.product_id;
end $$;
revoke execute on function mkt_ads_producto(integer) from public, anon;
grant execute on function mkt_ads_producto(integer) to authenticated;

create or replace function mkt_ads_paginas(p_dias integer default 7)
returns table (pagina text, fuente text, sesiones bigint, compras bigint)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select f.pagina, f.fuente, sum(f.sesiones)::bigint, sum(f.compras)::bigint
  from mkt_pagina_fuente_dia f
  where f.fecha between v_hoy - p_dias and v_hoy - 1
  group by f.pagina, f.fuente
  order by 3 desc, f.pagina;
end $$;
revoke execute on function mkt_ads_paginas(integer) from public, anon;
grant execute on function mkt_ads_paginas(integer) to authenticated;

create or replace function mkt_ads_google(p_dias integer default 7)
returns table (campana text, gasto numeric, clics bigint, sesiones bigint)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select g.campana, round(sum(g.gasto), 0), sum(g.clics)::bigint, sum(g.sesiones)::bigint
  from mkt_google_campana_dia g
  where g.fecha between v_hoy - p_dias and v_hoy - 1
  group by g.campana
  order by 2 desc, g.campana;
end $$;
revoke execute on function mkt_ads_google(integer) from public, anon;
grant execute on function mkt_ads_google(integer) to authenticated;
