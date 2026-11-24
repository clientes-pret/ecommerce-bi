-- Marketing: gasto de Meta Ads por producto y por página de destino (lo carga reposicion/meta_ads_sync.py con la API de Meta).
-- Cada peso se cuenta una sola vez, repartido en dos tablas:
--   · mkt_meta_producto_dia: lo que Meta reparte por producto del catálogo en los anuncios de catálogo (el id del producto del
--     catálogo es el SKU; se cruza con las variantes de la tienda para saber de qué producto es).
--   · mkt_meta_anuncio_dia: el resto de cada anuncio (lo que Meta no repartió por producto), con la página de la tienda a la que
--     lleva el anuncio (null si no se pudo saber o lleva a varias).
-- Al sumar por producto: gasto de catálogo del producto + el resto de los anuncios que llevan a /productos/<handle>.

create table if not exists mkt_meta_producto_dia (
  fecha date not null,
  sku text not null,
  product_id bigint,                      -- null si el SKU no está en la tienda o está repetido en más de un producto
  gasto numeric not null default 0,
  impresiones integer not null default 0,
  clics integer not null default 0,
  compras integer not null default 0,
  valor numeric not null default 0,       -- ventas que Meta atribuye a esos anuncios, en pesos
  primary key (fecha, sku)
);
create index if not exists mkt_meta_producto_prod on mkt_meta_producto_dia (product_id, fecha desc);

create table if not exists mkt_meta_anuncio_dia (
  fecha date not null,
  ad_id text not null,
  campana text not null default '',
  anuncio text not null default '',
  pagina text,                             -- ruta de la tienda a la que lleva (sin dominio ni parámetros); null si no se sabe o son varias
  gasto numeric not null default 0,        -- solo lo que NO se repartió por producto
  impresiones integer not null default 0,
  clics integer not null default 0,
  compras integer not null default 0,
  valor numeric not null default 0,
  primary key (fecha, ad_id)
);
create index if not exists mkt_meta_anuncio_pagina on mkt_meta_anuncio_dia (pagina, fecha desc);

alter table mkt_meta_producto_dia enable row level security;
alter table mkt_meta_anuncio_dia enable row level security;

drop policy if exists mkt_meta_producto_lectura on mkt_meta_producto_dia;
create policy mkt_meta_producto_lectura on mkt_meta_producto_dia for select to authenticated using (mkt_rol() is not null);
drop policy if exists mkt_meta_anuncio_lectura on mkt_meta_anuncio_dia;
create policy mkt_meta_anuncio_lectura on mkt_meta_anuncio_dia for select to authenticated using (mkt_rol() is not null);

revoke all on mkt_meta_producto_dia, mkt_meta_anuncio_dia from anon;
grant select on mkt_meta_producto_dia, mkt_meta_anuncio_dia to authenticated;
grant select, insert, update, delete on mkt_meta_producto_dia, mkt_meta_anuncio_dia to service_role;

-- Resúmenes para las pantallas. La ventana es de p_dias días completos hasta ayer (igual que el resto de Marketing).
create or replace function mkt_ads_meta_producto(p_dias integer default 7)
returns table (product_id bigint, gasto numeric, compras bigint, valor numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select x.product_id, round(sum(x.gasto), 0), sum(x.compras)::bigint, round(sum(x.valor), 0)
  from (
    select m.product_id, m.gasto, m.compras, m.valor
    from mkt_meta_producto_dia m
    where m.product_id is not null and m.fecha between v_hoy - p_dias and v_hoy - 1
    union all
    select p.product_id, a.gasto, a.compras, a.valor
    from mkt_meta_anuncio_dia a
    join mkt_producto p on a.pagina = '/productos/' || lower(p.handle)
    where a.fecha between v_hoy - p_dias and v_hoy - 1
  ) x
  group by x.product_id
  order by 2 desc, 1;
end $$;
revoke execute on function mkt_ads_meta_producto(integer) from public, anon;
grant execute on function mkt_ads_meta_producto(integer) to authenticated;

-- Páginas de destino (categorías, inicio y productos): lo que no se repartió por producto del catálogo.
create or replace function mkt_ads_meta_paginas(p_dias integer default 7)
returns table (pagina text, gasto numeric, compras bigint, valor numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select a.pagina, round(sum(a.gasto), 0), sum(a.compras)::bigint, round(sum(a.valor), 0)
  from mkt_meta_anuncio_dia a
  where a.pagina is not null and a.fecha between v_hoy - p_dias and v_hoy - 1
  group by a.pagina
  order by 2 desc, 1;
end $$;
revoke execute on function mkt_ads_meta_paginas(integer) from public, anon;
grant execute on function mkt_ads_meta_paginas(integer) to authenticated;

-- Una sola fila: cuánto se gastó en Meta y a dónde fue (por producto del catálogo, páginas conocidas o sin destino).
create or replace function mkt_ads_meta_resumen(p_dias integer default 7)
returns table (gasto_total numeric, gasto_catalogo numeric, gasto_paginas numeric, gasto_sin_destino numeric, compras bigint, valor numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare
  v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
  v_cat numeric; v_pag numeric; v_sin numeric; v_cmp bigint; v_val numeric;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  select coalesce(sum(m.gasto), 0), coalesce(sum(m.compras), 0), coalesce(sum(m.valor), 0)
    into v_cat, v_cmp, v_val
    from mkt_meta_producto_dia m where m.fecha between v_hoy - p_dias and v_hoy - 1;
  select coalesce(sum(a.gasto) filter (where a.pagina is not null), 0), coalesce(sum(a.gasto) filter (where a.pagina is null), 0),
         v_cmp + coalesce(sum(a.compras), 0), v_val + coalesce(sum(a.valor), 0)
    into v_pag, v_sin, v_cmp, v_val
    from mkt_meta_anuncio_dia a where a.fecha between v_hoy - p_dias and v_hoy - 1;
  return query select round(v_cat + v_pag + v_sin, 0), round(v_cat, 0), round(v_pag, 0), round(v_sin, 0), v_cmp::bigint, round(v_val, 0);
end $$;
revoke execute on function mkt_ads_meta_resumen(integer) from public, anon;
grant execute on function mkt_ads_meta_resumen(integer) to authenticated;
