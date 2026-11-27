-- Ingresos de mercadería y seguimiento por lote: ¿lo que entra se vende antes de pagarlo?
--
-- Pedido del usuario (27/11/2026): paga a 60-90 días y quiere vender antes. Ecomm-App (donde se carga el stock) no
-- informa movimientos por su API, así que los ingresos se DETECTAN desde las subas del stock de depósito guardado
-- cada día (repo_stock_snapshot, desde el 12/08/2026) y el usuario los corrige. Es una estimación.
--
-- Qué cuenta como ingreso (por producto y día):
--   * suba de 3 u. o más en depósito (las devoluciones sueltas no cuentan), menos lo que bajó de Full sin venderse;
--   * no cuenta si solo recupera una caída sin ventas de los días anteriores (falla de sincronización) ni si al día
--     siguiente vuelve exactamente a lo de antes (carga errónea);
--   * un producto que aparece por primera vez, con stock y sin ventas anteriores, cuenta como ingreso;
--   * los stocks de 50.000 o más son valores falsos y se ignoran.
--
-- Cada ingreso es un lote: primero sale lo que ya había y después el lote.

-- ─── Stock Full inflado antes del 19/11/2026 ─────────────────────────────────
-- Hasta ese día las publicaciones Full sin depósito propio repetían el stock de depósito (core.ml_stock_by_sku). Acá
-- queda cuántas copias tenía cada producto, para descontarlas de las fotos viejas.
create table if not exists repo_full_fantasma (
  sku    text primary key,
  copias integer not null check (copias > 0)
);
insert into repo_full_fantasma (sku, copias)
select a.sku, round((a.stock_full_pret - b.stock_full_pret)::numeric / a.stock_deposito)::int
  from repo_stock_snapshot a
  join repo_stock_snapshot b on b.sku = a.sku and b.fecha = date '2026-11-19'
 where a.fecha = date '2026-11-18' and a.stock_deposito between 1 and 49999 and a.stock_full_pret - b.stock_full_pret >= 3
   and round((a.stock_full_pret - b.stock_full_pret)::numeric / a.stock_deposito) >= 1
   and abs((a.stock_full_pret - b.stock_full_pret)::numeric / a.stock_deposito
           - round((a.stock_full_pret - b.stock_full_pret)::numeric / a.stock_deposito)) <= 0.15
on conflict (sku) do nothing;
-- Los que el 18/11 no tenían depósito no se ven con esa comparación: se reconocen porque antes del 19/11 el Full subía o
-- bajaba exactamente igual (o en múltiplos) que el depósito, de un día al otro.
insert into repo_full_fantasma (sku, copias)
select sku, k from (
  select sku, k, row_number() over (partition by sku order by count(*) desc, k) as orden
    from (
      select sku, round(df::numeric / dd)::int as k
        from (select sku, fecha,
                     stock_deposito - lag(stock_deposito) over x as dd,
                     stock_full_pret - lag(stock_full_pret) over x as df
                from repo_stock_snapshot where fecha < date '2026-11-19' and stock_deposito < 50000
              window x as (partition by sku order by fecha)) c
       where abs(dd) >= 10 and df <> 0 and round(df::numeric / dd) between 1 and 8
         and abs(df::numeric / dd - round(df::numeric / dd)) <= 0.05
    ) m
   group by sku, k
) t where orden = 1
on conflict (sku) do nothing;

create or replace view repo_stock_diario with (security_invoker = true) as
  select s.sku, s.fecha,
         case when s.stock_deposito < 50000 then greatest(coalesce(s.stock_deposito, 0), 0) end as deposito,
         greatest(case when s.fecha < date '2026-11-19' and s.stock_deposito < 50000
                       then coalesce(s.stock_full_pret, 0) - coalesce(f.copias, 0) * greatest(coalesce(s.stock_deposito, 0), 0)
                       else coalesce(s.stock_full_pret, 0) end, 0)
           + greatest(coalesce(s.stock_full_lavan, 0), 0) as en_full
    from repo_stock_snapshot s
    left join repo_full_fantasma f on f.sku = s.sku;

-- ─── Tablas ──────────────────────────────────────────────────────────────────
create table if not exists repo_ingresos (
  id           bigint generated always as identity primary key,
  fecha        date not null,
  sku          text not null references repo_productos(sku),
  detectadas   integer not null,
  unidades     integer not null check (unidades between 0 and 100000),  -- lo que vale: detectadas, salvo corrección
  estado       text not null default 'detectado' check (estado in ('detectado', 'confirmado', 'descartado')),
  origen       text not null default 'suba' check (origen in ('suba', 'producto_nuevo')),
  costo_unit   numeric,
  revisado_por uuid,
  revisado_at  timestamptz,
  creado_at    timestamptz not null default now(),
  unique (fecha, sku)
);
create index if not exists idx_repo_ingresos_sku on repo_ingresos (sku, fecha);

create table if not exists repo_proveedor_plazo (
  proveedor       text primary key,
  dias            integer not null check (dias between 0 and 365),
  actualizado_at  timestamptz not null default now(),
  actualizado_por uuid
);

alter table repo_full_fantasma   enable row level security;
alter table repo_ingresos        enable row level security;
alter table repo_proveedor_plazo enable row level security;
-- Sin policies: todo pasa por las funciones de abajo (admin / dueño) y por la clave de servicio.
revoke all on repo_full_fantasma, repo_ingresos, repo_proveedor_plazo, repo_stock_diario from anon, authenticated;
grant select, insert, update, delete on repo_full_fantasma, repo_ingresos, repo_proveedor_plazo to service_role;
grant select on repo_stock_diario to service_role;

-- ─── Detección ───────────────────────────────────────────────────────────────
-- Idempotente: recalcula desde p_desde. Lo que el usuario confirmó, corrigió o descartó no se pisa ni se borra.
create or replace function repo_detectar_ingresos(p_desde date default current_date - 14)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v jsonb; v_primera date;
begin
  select min(fecha) into v_primera from repo_stock_snapshot;

  with ventas as (
    select sku, fecha, sum(cantidad)::int as u from repo_ventas_items
     where estado = 'activa' and fecha >= p_desde - 10 group by sku, fecha
  ), dias as (
    select s.sku, s.fecha, s.deposito as d, s.en_full as f, coalesce(v.u, 0) as u
      from repo_stock_diario s left join ventas v on v.sku = s.sku and v.fecha = s.fecha
     where s.fecha >= p_desde - 10
  ), w as (
    select sku, fecha, d, f, u,
           lag(d, 1) over x as d1, lag(d, 2) over x as d2, lag(d, 3) over x as d3, lag(d, 4) over x as d4,
           lag(u, 1) over x as u1, lag(u, 2) over x as u2, lag(u, 3) over x as u3,
           lag(f, 1) over x as f1, lag(fecha, 1) over x as fecha1,
           lead(d, 1) over x as n1, lead(u, 1) over x as un1
      from dias window x as (partition by sku order by fecha)
  ), subas as (
    select w.*, d - d1 as alza,
           case when f1 - f - (u + u1) >= 3 then f1 - f - (u + u1) else 0 end as vuelta_full
      from w
     where fecha >= p_desde and d - d1 >= 3 and fecha - fecha1 <= 5
  ), filtradas as (
    select s.sku, s.fecha, s.alza - s.vuelta_full as unidades, 'suba'::text as origen
      from subas s
     where s.alza - s.vuelta_full >= 3
       -- Recupera una caída sin ventas: vuelve (casi) al nivel de hace 2, 3 o 4 días.
       and not exists (
         select 1 from (values (s.d2, s.u1), (s.d3, s.u1 + s.u2), (s.d4, s.u1 + s.u2 + s.u3)) as a(nivel, vendido)
          where a.nivel - s.d1 >= 3 and a.nivel - s.d1 - a.vendido >= 3
            and s.d between a.nivel - a.vendido - s.u - greatest(2, round(0.1 * a.nivel))
                        and a.nivel + greatest(2, round(0.1 * a.nivel)))
       -- Al día siguiente vuelve exactamente a lo de antes sin haberse vendido.
       and not coalesce(s.n1 is not null and s.alza >= 10 and abs(s.n1 - s.d1) <= 1
                        and s.u + coalesce(s.un1, 0) < s.alza * 0.5, false)
  ), nuevos as (
    select w.sku, w.fecha, w.d as unidades, 'producto_nuevo'::text as origen
      from w
     where w.d1 is null and w.d >= 3 and w.fecha >= p_desde and w.fecha > v_primera
       and w.fecha <> date '2026-08-20'   -- ese día se sumaron al historial los productos solo de Casa Laván
       and not exists (select 1 from repo_stock_snapshot p where p.sku = w.sku and p.fecha < w.fecha)
       and not exists (select 1 from repo_ventas_items i where i.sku = w.sku and i.fecha < w.fecha)
  ), cand as (
    select c.sku, c.fecha, c.unidades, c.origen from (select * from filtradas union all select * from nuevos) c
     where exists (select 1 from repo_productos r where r.sku = c.sku)
  ), alta as (
    insert into repo_ingresos (fecha, sku, detectadas, unidades, origen, costo_unit)
    select c.fecha, c.sku, c.unidades, c.unidades, c.origen, nullif(k.costo, 0)
      from cand c left join scorecard_costos_ecomapp k on k.sku = c.sku
    on conflict (fecha, sku) do update
       set detectadas = excluded.detectadas, origen = excluded.origen,
           unidades = case when repo_ingresos.estado = 'detectado' then excluded.unidades else repo_ingresos.unidades end,
           costo_unit = coalesce(repo_ingresos.costo_unit, excluded.costo_unit)
     where repo_ingresos.detectadas is distinct from excluded.detectadas
        or (repo_ingresos.costo_unit is null and excluded.costo_unit is not null)
    returning (xmax = 0) as nuevo
  ), baja as (
    delete from repo_ingresos i
     where i.fecha >= p_desde and i.estado = 'detectado'
       and not exists (select 1 from cand c where c.sku = i.sku and c.fecha = i.fecha)
    returning 1
  )
  select jsonb_build_object('nuevos', (select count(*) from alta where nuevo), 'cambiados', (select count(*) from alta where not nuevo),
                            'quitados', (select count(*) from baja)) into v;
  return v;
end $$;

-- ─── Corrección del usuario ──────────────────────────────────────────────────
create or replace function repo_ingreso_revisar(p_ids bigint[], p_estado text, p_unidades integer default null)
returns integer language plpgsql security definer set search_path = public as $$
declare v_n integer;
begin
  perform prov_permiso();
  if p_estado not in ('detectado', 'confirmado', 'descartado') then raise exception 'Acción desconocida'; end if;
  if p_unidades is not null and (coalesce(array_length(p_ids, 1), 0) <> 1 or p_estado <> 'confirmado') then
    raise exception 'La cantidad se corrige de a un ingreso';
  end if;
  if p_unidades is not null and p_unidades not between 1 and 100000 then raise exception 'Cantidad inválida'; end if;
  update repo_ingresos
     set estado = p_estado,
         -- "detectado" deshace la corrección; confirmar sin cantidad conserva la que haya.
         unidades = case when p_estado = 'detectado' then detectadas else coalesce(p_unidades, unidades) end,
         revisado_por = case when p_estado = 'detectado' then null else auth.uid() end,
         revisado_at = case when p_estado = 'detectado' then null else now() end
   where id = any (p_ids);
  get diagnostics v_n = row_count;
  return v_n;
end $$;

create or replace function repo_plazo_guardar(p_proveedor text, p_dias integer)
returns void language plpgsql security definer set search_path = public as $$
begin
  perform prov_permiso();
  if coalesce(trim(p_proveedor), '') = '' or p_dias is null or p_dias not between 0 and 365 then
    raise exception 'El plazo tiene que estar entre 0 y 365 días';
  end if;
  insert into repo_proveedor_plazo (proveedor, dias, actualizado_por) values (p_proveedor, p_dias, auth.uid())
  on conflict (proveedor) do update set dias = excluded.dias, actualizado_at = now(), actualizado_por = excluded.actualizado_por;
end $$;

-- ─── Lotes ───────────────────────────────────────────────────────────────────
-- Un renglón por ingreso. Primero sale lo anterior: lo que queda de un lote es el stock de hoy (depósito + Full) menos
-- lo que entró DESPUÉS, con tope en su tamaño. Así cuentan todas las salidas (web, local, revendedores), no solo las
-- ventas online. Con la fecha de pago (plazo del proveedor, 60 días si no se cargó) se ve cuánto salió a ese día, y
-- lo que falta se proyecta con el ritmo de venta que calcula Reposición.
create or replace function repo_lotes(p_desde date default date '2026-08-12')
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  perform prov_permiso();
  with hoy as (select (now() at time zone 'America/Argentina/Buenos_Aires')::date as d),
  ritmo as (
    select distinct on (c.sku) c.sku,
           coalesce(c.vel_diaria_deposito, 0) + coalesce(c.vel_diaria_full_pret, 0) + coalesce(c.vel_diaria_full_lavan, 0) as vel
      from repo_calculo_semanal c
     where c.semana_iso >= to_char(now() - interval '8 days', 'IYYY-"W"IW') and c.fecha_corrida > now() - interval '8 days'
     order by c.sku, c.semana_iso desc
  ), lote as (
    select i.*, coalesce(r.proveedor_manual, r.proveedor_auto, 'Sin proveedor') as proveedor,
           coalesce(r.nombre, c.producto, i.sku) as nombre, c.color,
           coalesce(r.categoria_manual, r.categoria_auto, 'Sin categoría') as categoria,
           coalesce(p.dias, 60) as plazo, i.fecha + coalesce(p.dias, 60) as pago
      from repo_ingresos i
      join repo_productos r on r.sku = i.sku
      left join cc_productos c on c.sku = i.sku
      left join repo_proveedor_plazo p on p.proveedor = coalesce(r.proveedor_manual, r.proveedor_auto, 'Sin proveedor')
     where i.fecha >= p_desde
  ), stock as (
    -- Stock total de cada día y lo ingresado hasta ese día (sin lo descartado).
    select s.sku, s.fecha, s.deposito + s.en_full as total,
           coalesce(sum(i.unidades) over (partition by s.sku order by s.fecha), 0) as entrado
      from repo_stock_diario s
      left join repo_ingresos i on i.sku = s.sku and i.fecha = s.fecha and i.estado <> 'descartado'
     where s.fecha >= p_desde and s.sku in (select sku from lote)
  ), dia as (
    select l.id, s.fecha, l.pago, l.unidades,
           s.total, s.entrado - first_value(s.entrado) over (partition by l.id order by s.fecha) as despues
      from lote l join stock s on s.sku = l.sku and s.fecha >= l.fecha and s.total is not null
  ), queda as (
    select id, fecha, pago, total, despues, least(unidades, greatest(total - despues, 0))::int as q from dia
  ), resumen as (
    select id,
           (array_agg(q order by fecha desc))[1] as quedan,
           -- Lo anterior al lote que todavía está: se vende antes que el lote.
           (array_agg(greatest(total - despues - q, 0) order by fecha desc))[1]::int as delante,
           (array_agg(q order by fecha desc) filter (where fecha <= pago))[1] as quedaban_al_pago,
           -- Agotado desde: el primer día después del último en que quedaba algo (un bajón pasajero no cuenta).
           max(fecha) filter (where q > 0) as ultimo_con_stock, min(fecha) as primero
      from queda group by id
  ), agotado as (
    select q.id, min(q.fecha) as el
      from queda q join resumen r on r.id = q.id
     where r.quedan = 0 and q.fecha > coalesce(r.ultimo_con_stock, r.primero - 1)
     group by q.id
  ), abiertas as (
    select it.sku, o.creado_at::date as pedido_el, greatest(it.cantidad_pedida - it.cantidad_recibida, 0) as falta
      from repo_pedido_items it join repo_pedidos o on o.id = it.pedido_id
     where not it.recibido_completo and o.estado in ('enviado', 'recibido_parcial') and o.cancelado_at is null
       and o.unidad_medida is distinct from 'bultos'
  ), por_cargar as (
    -- Unidades pedidas antes del ingreso y que siguen figurando pendientes: probablemente es esta entrega.
    select l.id, sum(a.falta)::int as u from lote l join abiertas a on a.sku = l.sku and a.pedido_el <= l.fecha group by l.id
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'id', l.id, 'fecha', l.fecha, 'sku', l.sku, 'nombre', l.nombre, 'color', l.color, 'categoria', l.categoria,
      'proveedor', l.proveedor, 'unidades', l.unidades, 'detectadas', l.detectadas, 'estado', l.estado, 'origen', l.origen,
      'costo', l.costo_unit, 'plazo', l.plazo, 'pago', l.pago,
      'quedan', coalesce(s.quedan, l.unidades),
      -- Lo que va a quedar sin vender el día del pago: real si ya pasó; si no, proyectado.
      'quedan_al_pago', case when l.pago <= h.d then coalesce(s.quedaban_al_pago, l.unidades)
          else greatest(coalesce(s.quedan, l.unidades)
                 - greatest(floor(coalesce(rt.vel, 0) * (l.pago - h.d))::int - coalesce(s.delante, 0), 0), 0) end,
      'agotado_el', g.el,
      'termina_est', case when coalesce(s.quedan, l.unidades) > 0 and rt.vel > 0
          then h.d + least(ceil((coalesce(s.delante, 0) + coalesce(s.quedan, l.unidades)) / rt.vel), 3650)::int end,
      'vel', round(coalesce(rt.vel, 0), 4),
      'pendiente_u', coalesce(pc.u, 0)
    ) order by l.fecha desc, l.proveedor, l.nombre, l.sku), '[]'::jsonb)
    into v
    from lote l
    cross join hoy h
    left join resumen s on s.id = l.id
    left join agotado g on g.id = l.id
    left join ritmo rt on rt.sku = l.sku
    left join por_cargar pc on pc.id = l.id;
  return v;
end $$;

-- ─── Foto del stock de hoy, con costo ────────────────────────────────────────
create or replace function repo_stock_valorizado()
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  perform prov_permiso();
  with ult as (
    select distinct on (c.sku) c.*
      from repo_calculo_semanal c
     where c.semana_iso >= to_char(now() - interval '8 days', 'IYYY-"W"IW') and c.fecha_corrida > now() - interval '8 days'
     order by c.sku, c.semana_iso desc
  ), f as (
    select u.sku, case when u.stock_deposito < 50000 then greatest(coalesce(u.stock_deposito, 0), 0) else 0 end as dep,
           greatest(coalesce(u.stock_full_pret, 0), 0) as full_pret, greatest(coalesce(u.stock_full_lavan, 0), 0) as full_lavan,
           coalesce(u.vel_diaria_deposito, 0) + coalesce(u.vel_diaria_full_pret, 0) + coalesce(u.vel_diaria_full_lavan, 0) as vel,
           coalesce(u.vendido_deposito, 0) + coalesce(u.vendido_full_pret, 0) + coalesce(u.vendido_full_lavan, 0) as vendido
      from ult u
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'sku', f.sku, 'nombre', coalesce(r.nombre, c.producto, f.sku), 'color', c.color,
      'categoria', coalesce(r.categoria_manual, r.categoria_auto, 'Sin categoría'),
      'proveedor', coalesce(r.proveedor_manual, r.proveedor_auto, 'Sin proveedor'),
      'dep', f.dep, 'full_pret', f.full_pret, 'full_lavan', f.full_lavan, 'vel', round(f.vel, 4), 'vendido', f.vendido,
      'costo', nullif(k.costo, 0), 'plazo', coalesce(p.dias, 60))), '[]'::jsonb)
    into v
    from f
    join repo_productos r on r.sku = f.sku
    left join cc_productos c on c.sku = f.sku
    left join scorecard_costos_ecomapp k on k.sku = f.sku
    left join repo_proveedor_plazo p on p.proveedor = coalesce(r.proveedor_manual, r.proveedor_auto, 'Sin proveedor')
   where f.dep + f.full_pret + f.full_lavan > 0;
  return v;
end $$;

revoke all on function repo_detectar_ingresos(date), repo_ingreso_revisar(bigint[], text, integer),
  repo_plazo_guardar(text, integer), repo_lotes(date), repo_stock_valorizado() from public, anon, authenticated;
grant execute on function repo_detectar_ingresos(date) to service_role;
grant execute on function repo_ingreso_revisar(bigint[], text, integer), repo_plazo_guardar(text, integer),
  repo_lotes(date), repo_stock_valorizado() to authenticated, service_role;

-- Carga del historial disponible.
select repo_detectar_ingresos(date '2026-08-12');
