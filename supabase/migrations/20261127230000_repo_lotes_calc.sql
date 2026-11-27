-- Los lotes se calculan una vez y quedan guardados: hacerlo al abrir la pantalla tardaba más de lo que la base permite
-- a un usuario (8 s). El job diario recalcula todo en tandas y cada corrección del usuario recalcula sus productos.
-- Lo que depende del plazo de pago se resuelve al leer (se guarda lo que quedaba cada día).

create table if not exists repo_lotes_calc (
  ingreso_id   bigint primary key references repo_ingresos(id) on delete cascade,
  quedan       integer not null,
  delante      integer not null,      -- stock anterior al lote que todavía está: sale antes que el lote
  agotado_el   date,
  fechas       date[] not null,       -- días con foto de stock, en orden
  serie        integer[] not null,    -- cuánto quedaba del lote cada uno de esos días
  calculado_at timestamptz not null default now()
);
alter table repo_lotes_calc enable row level security;
revoke all on repo_lotes_calc from anon, authenticated;
grant select, insert, update, delete on repo_lotes_calc to service_role;

-- ─── Cálculo ─────────────────────────────────────────────────────────────────
-- Primero sale lo anterior: lo que queda de un lote es el stock del día (depósito + Full) menos lo que entró DESPUÉS,
-- con tope en su tamaño. Así cuentan todas las salidas (web, local, revendedores), no solo las ventas online.
create or replace function repo_calcular_lotes(p_skus text[] default null)
returns integer language plpgsql security definer set search_path = public as $$
declare v_skus text[]; v_n integer;
begin
  select array_agg(distinct sku) into v_skus from repo_ingresos where p_skus is null or sku = any (p_skus);
  if v_skus is null then return 0; end if;

  with lote as (
    select id, sku, fecha, unidades from repo_ingresos where sku = any (v_skus)
  ), stock as (
    -- Stock total de cada día y lo ingresado hasta ese día (sin lo descartado).
    select s.sku, s.fecha, s.deposito + s.en_full as total,
           coalesce(sum(i.unidades) over (partition by s.sku order by s.fecha), 0) as entrado
      from repo_stock_diario s
      left join repo_ingresos i on i.sku = s.sku and i.fecha = s.fecha and i.estado <> 'descartado'
     where s.sku = any (v_skus) and s.fecha >= date '2026-08-12'
  ), dia as (
    select l.id, s.fecha, l.unidades, s.total, s.entrado - first_value(s.entrado) over (partition by l.id order by s.fecha) as despues
      from lote l join stock s on s.sku = l.sku and s.fecha >= l.fecha and s.total is not null
  ), queda as (
    select id, fecha, total, despues, least(unidades, greatest(total - despues, 0))::int as q from dia
  ), resumen as (
    select id,
           (array_agg(q order by fecha desc))[1] as quedan,
           (array_agg(greatest(total - despues - q, 0) order by fecha desc))[1]::int as delante,
           -- Agotado desde: el primer día después del último en que quedaba algo (un bajón pasajero no cuenta).
           max(fecha) filter (where q > 0) as ultimo_con_stock, min(fecha) as primero,
           array_agg(fecha order by fecha) as fechas, array_agg(q order by fecha) as serie
      from queda group by id
  ), agotado as (
    select q.id, min(q.fecha) as el
      from queda q join resumen r on r.id = q.id
     where r.quedan = 0 and q.fecha > coalesce(r.ultimo_con_stock, r.primero - 1)
     group by q.id
  )
  insert into repo_lotes_calc (ingreso_id, quedan, delante, agotado_el, fechas, serie)
  select r.id, r.quedan, r.delante, g.el, r.fechas, r.serie from resumen r left join agotado g on g.id = r.id
  on conflict (ingreso_id) do update
     set quedan = excluded.quedan, delante = excluded.delante, agotado_el = excluded.agotado_el,
         fechas = excluded.fechas, serie = excluded.serie, calculado_at = now();
  get diagnostics v_n = row_count;
  return v_n;
end $$;

-- ─── Lectura ─────────────────────────────────────────────────────────────────
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
      'quedan', coalesce(k.quedan, l.unidades),
      -- Lo que va a quedar sin vender el día del pago: real si ya pasó; si no, proyectado.
      'quedan_al_pago', case when l.pago <= h.d
          then coalesce(k.serie[(select max(n) from generate_subscripts(k.fechas, 1) n where k.fechas[n] <= l.pago)], l.unidades)
          else greatest(coalesce(k.quedan, l.unidades)
                 - greatest(floor(coalesce(rt.vel, 0) * (l.pago - h.d))::int - coalesce(k.delante, 0), 0), 0) end,
      'agotado_el', k.agotado_el,
      'termina_est', case when coalesce(k.quedan, l.unidades) > 0 and rt.vel > 0
          then h.d + least(ceil((coalesce(k.delante, 0) + coalesce(k.quedan, l.unidades)) / rt.vel), 3650)::int end,
      'vel', round(coalesce(rt.vel, 0), 4),
      'pendiente_u', coalesce(pc.u, 0)
    ) order by l.fecha desc, l.proveedor, l.nombre, l.sku), '[]'::jsonb)
    into v
    from lote l
    cross join hoy h
    left join repo_lotes_calc k on k.ingreso_id = l.id
    left join ritmo rt on rt.sku = l.sku
    left join por_cargar pc on pc.id = l.id;
  return v;
end $$;

-- Corregir un ingreso cambia cuánto "entró después" para los lotes anteriores del mismo producto: se recalculan.
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
  if v_n > 0 then perform repo_calcular_lotes(array(select distinct sku from repo_ingresos where id = any (p_ids))); end if;
  return v_n;
end $$;

revoke all on function repo_calcular_lotes(text[]) from public, anon, authenticated;
grant execute on function repo_calcular_lotes(text[]) to service_role;

select repo_calcular_lotes();
