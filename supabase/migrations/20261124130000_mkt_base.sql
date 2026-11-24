-- Plataforma de Marketing (Tienda Nube Pret a Home), fase 1: rol, datos por producto, tareas y tablero con semáforo.
-- Pantallas en reposicion/dashboard/marketing/. Datos que escribe reposicion/mkt_sync.py (service_role).

-- 1) Rol nuevo "marketing".
--    cc_rol() lo ignora a propósito: así NINGUNA policy ni función existente (cuenta corriente, recepciones, finanzas,
--    scorecard, pedidos…) le abre acceso, ni siquiera las que solo piden "tener algún rol" (rec_* y los
--    "cc_rol() is not null"). Lo de Marketing se decide con mkt_rol().
alter table cc_perfiles drop constraint if exists cc_perfiles_rol_check;
alter table cc_perfiles add constraint cc_perfiles_rol_check check (rol in ('deposito','admin','dueno','marketing'));

create or replace function cc_rol() returns text
language sql stable security definer set search_path = public as $$
  select rol from cc_perfiles where id = auth.uid() and rol <> 'marketing'
$$;

create or replace function mkt_rol() returns text
language sql stable security definer set search_path = public as $$
  select rol from cc_perfiles where id = auth.uid() and rol in ('admin','dueno','marketing')
$$;
revoke execute on function mkt_rol() from public, anon;
grant execute on function mkt_rol() to authenticated;

-- 2) Configuración del semáforo (la edita admin/dueño desde la pantalla).
create table if not exists mkt_config (
  clave text primary key,
  valor numeric not null,
  nota text,
  actualizado_at timestamptz not null default now()
);
insert into mkt_config (clave, valor, nota) values
  ('umbral_visitas',    150, 'Visitas en 7 días desde las que un producto tiene buen volumen'),
  ('umbral_conversion', 2,   'Conversión (%, unidades ÷ visitas) desde la que es alta'),
  ('min_unidades',      2,   'Unidades vendidas mínimas para llamar "alta" a la conversión (evita 1 venta sobre 20 visitas)'),
  ('comision',          8.5, 'Comisión estimada de Pago Nube + impuestos (%), para ganancia, margen y ROI'),
  ('roi_piso',          15,  'ROI (%) por debajo del cual el margen se marca en amarillo'),
  ('stock_bajo',        40,  'Unidades de stock por debajo de las cuales se avisa stock bajo')
on conflict (clave) do nothing;

-- 3) Datos por producto de Tienda Nube (una fila por página de producto).
create table if not exists mkt_producto (
  product_id bigint primary key,
  handle text,
  nombre text,
  categoria text,
  marca text,
  publicado boolean not null default true,
  imagen text,
  variantes jsonb not null default '[]',       -- [{id, sku, nombre, precio, promo, costo, stock}]
  actualizado_at timestamptz not null default now()
);

create table if not exists mkt_producto_dia (
  fecha date not null,                         -- día en hora de Argentina
  product_id bigint not null,
  visitas integer,                             -- null = GA4 no estuvo disponible (no es lo mismo que 0)
  unidades integer not null default 0,
  pedidos integer not null default 0,
  ingresos numeric not null default 0,
  stock integer,                               -- estado al cierre de la última corrida del día
  precio_lista numeric,
  precio_final numeric,                        -- promocional si hay, si no el de lista
  costo numeric,
  primary key (fecha, product_id)
);
create index if not exists mkt_producto_dia_prod on mkt_producto_dia (product_id, fecha desc);

create table if not exists mkt_producto_hora (
  hora timestamptz not null,
  product_id bigint not null,
  visitas integer,
  unidades integer not null default 0,
  primary key (hora, product_id)
);

create table if not exists mkt_homepage (
  product_id bigint primary key,
  desde date not null,                         -- desde cuándo está en la homepage (racha actual)
  ultimo date not null                         -- última vez que se vio ahí
);

-- 4) Lo que va haciendo el equipo en cada producto.
create table if not exists mkt_tareas (
  id bigint generated always as identity primary key,
  product_id bigint not null,
  tipo text not null check (tipo in ('fotos','precio','publicidad','titulo','otro')),
  estado text not null default 'en_curso' check (estado in ('en_curso','hecho')),
  nota text,
  autor uuid default auth.uid() references auth.users(id) on delete set null,
  autor_nombre text,
  created_at timestamptz not null default now(),
  hecho_at timestamptz
);
create index if not exists mkt_tareas_prod on mkt_tareas (product_id, created_at desc);

create or replace function mkt_tareas_antes() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    new.autor := coalesce(new.autor, auth.uid());
    new.autor_nombre := coalesce(new.autor_nombre, (select nombre from cc_perfiles where id = auth.uid()));
  end if;
  if new.estado = 'hecho' and (tg_op = 'INSERT' or old.estado <> 'hecho') then new.hecho_at := now(); end if;
  if new.estado = 'en_curso' then new.hecho_at := null; end if;
  return new;
end $$;
drop trigger if exists mkt_tareas_antes on mkt_tareas;
create trigger mkt_tareas_antes before insert or update on mkt_tareas for each row execute function mkt_tareas_antes();

-- 5) Permisos: Marketing, admin y dueño leen; solo el sync (service_role) escribe los datos; las tareas las escribe el equipo.
alter table mkt_config enable row level security;
alter table mkt_producto enable row level security;
alter table mkt_producto_dia enable row level security;
alter table mkt_producto_hora enable row level security;
alter table mkt_homepage enable row level security;
alter table mkt_tareas enable row level security;

drop policy if exists mkt_config_lectura on mkt_config;
create policy mkt_config_lectura on mkt_config for select to authenticated using (mkt_rol() is not null);
drop policy if exists mkt_config_edicion on mkt_config;
create policy mkt_config_edicion on mkt_config for update to authenticated
  using (mkt_rol() in ('admin','dueno')) with check (mkt_rol() in ('admin','dueno'));

drop policy if exists mkt_producto_lectura on mkt_producto;
create policy mkt_producto_lectura on mkt_producto for select to authenticated using (mkt_rol() is not null);
drop policy if exists mkt_producto_dia_lectura on mkt_producto_dia;
create policy mkt_producto_dia_lectura on mkt_producto_dia for select to authenticated using (mkt_rol() is not null);
drop policy if exists mkt_producto_hora_lectura on mkt_producto_hora;
create policy mkt_producto_hora_lectura on mkt_producto_hora for select to authenticated using (mkt_rol() is not null);
drop policy if exists mkt_homepage_lectura on mkt_homepage;
create policy mkt_homepage_lectura on mkt_homepage for select to authenticated using (mkt_rol() is not null);

drop policy if exists mkt_tareas_lectura on mkt_tareas;
create policy mkt_tareas_lectura on mkt_tareas for select to authenticated using (mkt_rol() is not null);
drop policy if exists mkt_tareas_alta on mkt_tareas;
create policy mkt_tareas_alta on mkt_tareas for insert to authenticated with check (mkt_rol() is not null and autor = auth.uid());
drop policy if exists mkt_tareas_cambio on mkt_tareas;
create policy mkt_tareas_cambio on mkt_tareas for update to authenticated using (mkt_rol() is not null) with check (mkt_rol() is not null);
drop policy if exists mkt_tareas_baja on mkt_tareas;
create policy mkt_tareas_baja on mkt_tareas for delete to authenticated using (mkt_rol() in ('admin','dueno') or autor = auth.uid());

revoke all on mkt_config, mkt_producto, mkt_producto_dia, mkt_producto_hora, mkt_homepage, mkt_tareas from anon;
grant select on mkt_config, mkt_producto, mkt_producto_dia, mkt_producto_hora, mkt_homepage, mkt_tareas to authenticated;
grant update (valor) on mkt_config to authenticated;
grant insert, update, delete on mkt_tareas to authenticated;
grant select, insert, update, delete on mkt_config, mkt_producto, mkt_producto_dia, mkt_producto_hora, mkt_homepage, mkt_tareas to service_role;

-- 6) Tablero: una fila por producto publicado, con 7 días (completos, hasta ayer) y últimas 24 hs, stock, precio, ganancia,
--    margen, ROI, semáforo, homepage y tareas en curso. El semáforo y los umbrales viven acá (una sola fuente).
--    Ganancia = precio final × (1 − comisión) − costo · Margen = ganancia ÷ precio final · ROI = ganancia ÷ costo.
create or replace function mkt_tablero(p_product bigint default null)
returns table (
  product_id bigint, handle text, nombre text, categoria text, marca text, imagen text,
  visitas_7d bigint, unidades_7d bigint, conv_7d numeric,
  visitas_24h bigint, unidades_24h bigint, conv_24h numeric,
  stock integer, precio_lista numeric, precio_final numeric, costo numeric,
  ganancia numeric, margen numeric, roi numeric,
  semaforo text, en_home boolean, home_desde date, tareas_en_curso bigint, actualizado_at timestamptz
)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare
  v_uv numeric := coalesce((select valor from mkt_config where clave = 'umbral_visitas'), 150);
  v_uc numeric := coalesce((select valor from mkt_config where clave = 'umbral_conversion'), 2);
  v_mu numeric := coalesce((select valor from mkt_config where clave = 'min_unidades'), 2);
  v_com numeric := coalesce((select valor from mkt_config where clave = 'comision'), 8.5) / 100;
  v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  with d7 as (
    select d.product_id, sum(d.visitas)::bigint as v, sum(d.unidades)::bigint as u
    from mkt_producto_dia d where d.fecha between v_hoy - 7 and v_hoy - 1 group by d.product_id
  ), h24 as (
    select h.product_id, sum(h.visitas)::bigint as v, sum(h.unidades)::bigint as u
    from mkt_producto_hora h where h.hora >= now() - interval '24 hours' group by h.product_id
  ), est as (
    select distinct on (d.product_id) d.product_id, d.stock, d.precio_lista, d.precio_final, d.costo
    from mkt_producto_dia d where d.stock is not null order by d.product_id, d.fecha desc
  ), tk as (
    select t.product_id, count(*) as n from mkt_tareas t where t.estado = 'en_curso' group by t.product_id
  ), b as (
    select p.product_id, p.handle, p.nombre, p.categoria, p.marca, p.imagen, p.actualizado_at,
           d7.v as v7, coalesce(d7.u, 0) as u7, h24.v as v24, coalesce(h24.u, 0) as u24,
           e.stock, e.precio_lista, e.precio_final, e.costo,
           case when e.costo > 0 and e.precio_final > 0 then e.precio_final * (1 - v_com) - e.costo end as gan,
           (hp.product_id is not null) as en_home, hp.desde as home_desde, coalesce(tk.n, 0) as n_tk
    from mkt_producto p
    left join d7 on d7.product_id = p.product_id
    left join h24 on h24.product_id = p.product_id
    left join est e on e.product_id = p.product_id
    left join tk on tk.product_id = p.product_id
    left join mkt_homepage hp on hp.product_id = p.product_id and hp.ultimo >= v_hoy - 1
    where (p_product is null and p.publicado) or p.product_id = p_product   -- sin parámetro: los publicados; con parámetro: ese producto (publicado o no)
  )
  select b.product_id, b.handle, b.nombre, b.categoria, b.marca, b.imagen,
         b.v7, b.u7, case when b.v7 > 0 then round(b.u7::numeric / b.v7 * 100, 2) end,
         b.v24, b.u24, case when b.v24 > 0 then round(b.u24::numeric / b.v24 * 100, 2) end,
         b.stock, b.precio_lista, b.precio_final, b.costo,
         round(b.gan, 0),
         case when b.gan is not null then round(b.gan / b.precio_final * 100, 1) end,
         case when b.gan is not null then round(b.gan / b.costo * 100, 1) end,
         case
           when b.v7 is null then null
           when b.v7 >= v_uv and b.u7 >= v_mu and b.u7::numeric / b.v7 * 100 >= v_uc then 'killer'
           when b.v7 >= v_uv then 'potencial'
           when b.v7 > 0 and b.u7 >= v_mu and b.u7::numeric / b.v7 * 100 >= v_uc then 'impulsar'
           else 'rojo'
         end,
         b.en_home, b.home_desde, b.n_tk, b.actualizado_at
  from b
  order by b.v7 desc nulls last, b.product_id;
end $$;
revoke execute on function mkt_tablero(bigint) from public, anon;
grant execute on function mkt_tablero(bigint) to authenticated;

-- 7) Visitas y unidades de toda la tienda por día (para el gráfico del Dashboard).
create or replace function mkt_serie_tienda(p_dias integer default 30)
returns table (fecha date, visitas bigint, unidades bigint)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select d.fecha, sum(d.visitas)::bigint, sum(d.unidades)::bigint
  from mkt_producto_dia d
  where d.fecha >= (now() at time zone 'America/Argentina/Buenos_Aires')::date - p_dias
  group by d.fecha order by d.fecha;
end $$;
revoke execute on function mkt_serie_tienda(integer) from public, anon;
grant execute on function mkt_serie_tienda(integer) to authenticated;
