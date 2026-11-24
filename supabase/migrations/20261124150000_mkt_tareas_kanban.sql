-- Tareas de Marketing en tablero Kanban: tres estados (Por hacer / En curso / Hecho).
-- Las tareas que ya existían como 'en_curso' siguen igual. "tareas_en_curso" del tablero ahora cuenta las abiertas (por hacer + en curso).

alter table mkt_tareas drop constraint if exists mkt_tareas_estado_check;
alter table mkt_tareas add constraint mkt_tareas_estado_check check (estado in ('pendiente','en_curso','hecho'));
alter table mkt_tareas alter column estado set default 'pendiente';

create or replace function mkt_tareas_antes() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    new.autor := coalesce(new.autor, auth.uid());
    new.autor_nombre := coalesce(new.autor_nombre, (select nombre from cc_perfiles where id = auth.uid()));
  end if;
  if new.estado = 'hecho' and (tg_op = 'INSERT' or old.estado <> 'hecho') then new.hecho_at := now(); end if;
  if new.estado <> 'hecho' then new.hecho_at := null; end if;
  return new;
end $$;

create index if not exists mkt_tareas_estado on mkt_tareas (estado, created_at desc);

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
    select t.product_id, count(*) as n from mkt_tareas t where t.estado <> 'hecho' group by t.product_id
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
