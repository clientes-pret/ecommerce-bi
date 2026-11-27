-- Saldo por proveedor: lo que cada proveedor debe entregar, por producto.
--
-- Regla del usuario (27/11/2026): un pedido nuevo PISA al anterior del mismo proveedor + SKU, no suma (los pedidos se
-- van actualizando). Vale el pedido más nuevo, sea para Depósito o Full, en unidades o bultos. Las líneas de un mismo
-- pedido sí se suman entre sí.
--
-- Cómo se guarda: la línea vieja pasa a recibido_completo = true con reemplazado_por = id de la OC nueva, SIN tocar
-- cuánto se pidió ni cuánto se recibió. Igual que "cerrar con faltante", así el resto del sistema deja de contarla sola
-- (punto rojo del tablero, recepción del depósito, planilla). Cancelar o quitar el pedido nuevo la vuelve a abrir.
--
-- Cambio de proveedor (usuario, 27/11/2026): una OC armada (o algunos de sus productos) se puede pasar a otro
-- proveedor con prov_cambiar_proveedor(). El mismo producto puede estar pedido a varios proveedores a la vez: la
-- regla de reemplazo es por proveedor. Pasar un pedido cuenta como el pedido más nuevo del proveedor que lo recibe,
-- y en el proveedor de origen no revive nada de lo que ese pedido había reemplazado.
--
-- Los pedidos ya cargados NO se tocan al aplicar esta migración: se hace aparte con prov_aplicar_reemplazos(),
-- después de revisar prov_reemplazos_preview(). (Un pedido nuevo sí aplica la regla a sus productos.)

alter table repo_pedido_items
  add column if not exists reemplazado_por   bigint,        -- OC que reemplazó a esta línea (sin FK: se recalcula)
  add column if not exists movido_a          bigint,        -- OC de otro proveedor a la que se pasó (queda cerrada)
  add column if not exists estimado_recibido integer,       -- lo que cree recepciones.py que llegó (solo sugerencia)
  add column if not exists revisado_at       timestamptz;   -- "Sigo esperando" en Atrasados
alter table repo_pedidos
  add column if not exists vigente_desde      timestamptz,   -- si se le cambió el proveedor: desde cuándo vale (si no, creado_at)
  add column if not exists proveedor_anterior text,
  add column if not exists clave_origen       uuid;
create unique index if not exists repo_pedidos_clave_origen_idx on repo_pedidos (clave_origen) where clave_origen is not null;
create index if not exists idx_repo_pedido_items_reemplazo on repo_pedido_items (reemplazado_por) where reemplazado_por is not null;

-- ─── Estado de una OC según sus líneas ───────────────────────────────────────
-- Mismo criterio de siempre, con una diferencia: una línea reemplazada no cuenta como "llegó algo".
create or replace function prov_estado_pedido(p_pedido_id bigint)
returns text language sql stable security definer set search_path = public as $$
  select case
    when not exists (select 1 from repo_pedido_items where pedido_id = p_pedido_id and not recibido_completo)
      then 'recibido_completo'
    when exists (select 1 from repo_pedido_items where pedido_id = p_pedido_id
                  and (cantidad_recibida > 0 or (recibido_completo and reemplazado_por is null and movido_a is null)))
      then 'recibido_parcial'
    else 'enviado' end
$$;

-- Las funciones de recepción que ya existen calculan el estado contando toda línea completa como "llegó algo".
-- Se corrige acá, en un solo lugar: sin nada recibido de verdad no es "parcial".
create or replace function prov_trg_estado()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if new.estado = 'recibido_parcial' and prov_estado_pedido(new.id) = 'enviado' then
    new.estado := 'enviado';
  end if;
  return new;
end $$;
drop trigger if exists prov_estado on repo_pedidos;
create trigger prov_estado before update of estado on repo_pedidos
  for each row execute function prov_trg_estado();

-- ─── La regla de reemplazo ───────────────────────────────────────────────────
-- Deja las líneas de (proveedor, sku) como corresponde. Es idempotente: sirve para alta, baja, cancelación y reapertura.
create or replace function prov_recalcular(p_proveedor text, p_sku text)
returns integer language plpgsql security definer set search_path = public as $$
declare
  v_gana record; v_ids bigint[]; v_ped bigint; v_n integer := 0; v_r record;
  v_tocados bigint[] := '{}';
begin
  -- Un solo recálculo a la vez por producto (dos pedidos confirmados al mismo tiempo).
  perform pg_advisory_xact_lock(hashtext(p_proveedor || '|' || p_sku));

  select p.id, coalesce(p.vigente_desde, p.creado_at) as desde into v_gana
    from repo_pedidos p
    join repo_pedido_items i on i.pedido_id = p.id
   where p.proveedor = p_proveedor and i.sku = p_sku and i.cantidad_pedida > 0
     and p.estado <> 'cancelado' and p.cancelado_at is null
   order by coalesce(p.vigente_desde, p.creado_at) desc, p.id desc
   limit 1;
  if v_gana.id is null then return 0; end if;

  -- 1) El pedido vigente no puede figurar reemplazado (pasa al cancelar el que lo había pisado).
  for v_r in
    update repo_pedido_items
       set reemplazado_por = null,
           recibido_completo = (cantidad_recibida >= cantidad_pedida),
           recibido_at = case when cantidad_recibida >= cantidad_pedida then recibido_at else null end
     where pedido_id = v_gana.id and sku = p_sku and reemplazado_por is not null
    returning pedido_id
  loop
    v_n := v_n + 1; v_tocados := v_tocados || v_r.pedido_id;
  end loop;

  -- 2) Las líneas abiertas (o ya reemplazadas) de pedidos anteriores quedan reemplazadas por el vigente.
  --    Las que de verdad se recibieron o se cerraron con faltante no se tocan.
  for v_r in
    update repo_pedido_items i
       set reemplazado_por = v_gana.id,
           recibido_completo = true
      from repo_pedidos p
     where p.id = i.pedido_id and p.proveedor = p_proveedor and i.sku = p_sku and p.id <> v_gana.id
       and p.estado <> 'cancelado' and p.cancelado_at is null
       and (coalesce(p.vigente_desde, p.creado_at), p.id) < (v_gana.desde, v_gana.id)
       and (not i.recibido_completo or (i.reemplazado_por is not null and i.reemplazado_por <> v_gana.id))
    returning i.pedido_id
  loop
    v_n := v_n + 1; v_tocados := v_tocados || v_r.pedido_id;
  end loop;

  for v_ped in select distinct unnest(v_tocados) loop
    update repo_pedidos set estado = prov_estado_pedido(v_ped) where id = v_ped and estado <> 'cancelado';
  end loop;
  return v_n;
end $$;

create or replace function prov_trg_items()
returns trigger language plpgsql security definer set search_path = public as $$
declare v_prov text;
begin
  -- Los cambios que hace el propio recálculo no vuelven a dispararlo; "deshacer" restaura sin recalcular acá.
  if pg_trigger_depth() > 1 or coalesce(current_setting('prov.deshacer', true), '') = 'si' then return null; end if;
  -- De los cambios solo interesan: cantidad, SKU u OC de la línea, y una línea que se REABRE (anular una entrega,
  -- reabrir una OC). Marcar algo como recibido no recalcula: así la recepción puede repartir entre varias líneas.
  if tg_op = 'UPDATE' and old.cantidad_pedida = new.cantidad_pedida and old.sku = new.sku
     and old.pedido_id = new.pedido_id and not (old.recibido_completo and not new.recibido_completo) then
    return null;
  end if;
  if tg_op in ('DELETE', 'UPDATE') then
    select proveedor into v_prov from repo_pedidos where id = old.pedido_id;
    if v_prov is not null then perform prov_recalcular(v_prov, old.sku); end if;
  end if;
  if tg_op in ('INSERT', 'UPDATE') then
    select proveedor into v_prov from repo_pedidos where id = new.pedido_id;
    if v_prov is not null then perform prov_recalcular(v_prov, new.sku); end if;
  end if;
  return null;
end $$;
drop trigger if exists prov_items on repo_pedido_items;
create trigger prov_items
  after insert or delete or update of cantidad_pedida, sku, pedido_id, recibido_completo on repo_pedido_items
  for each row execute function prov_trg_items();

-- Cancelar una OC (o cambiarle el proveedor) recalcula sus productos.
create or replace function prov_trg_pedidos()
returns trigger language plpgsql security definer set search_path = public as $$
declare v_sku text;
begin
  if (old.estado = 'cancelado') is distinct from (new.estado = 'cancelado')
     or old.cancelado_at is distinct from new.cancelado_at
     or old.proveedor is distinct from new.proveedor then
    for v_sku in select distinct sku from repo_pedido_items where pedido_id = new.id loop
      perform prov_recalcular(new.proveedor, v_sku);
      if old.proveedor is distinct from new.proveedor then perform prov_recalcular(old.proveedor, v_sku); end if;
    end loop;
  end if;
  return null;
end $$;
drop trigger if exists prov_pedidos on repo_pedidos;
create trigger prov_pedidos after update on repo_pedidos
  for each row execute function prov_trg_pedidos();

-- Borrar una OC entera: sus líneas se van en cascada cuando la OC ya no existe, así que el disparador de líneas no
-- sabe de qué proveedor eran. Se recalculan acá los productos que esa OC había reemplazado.
create or replace function prov_trg_pedido_borrado()
returns trigger language plpgsql security definer set search_path = public as $$
declare v_sku text;
begin
  for v_sku in select distinct sku from repo_pedido_items where reemplazado_por = old.id and pedido_id <> old.id loop
    perform prov_recalcular(old.proveedor, v_sku);
  end loop;
  return null;
end $$;
drop trigger if exists prov_pedido_borrado on repo_pedidos;
create trigger prov_pedido_borrado after delete on repo_pedidos
  for each row execute function prov_trg_pedido_borrado();

-- Una línea reemplazada o pasada a otro proveedor no se marca a mano: se carga en la OC que la sigue
-- (marcar todo, cerrar con faltante y reabrir no la tocan: solo miran líneas abiertas o cerradas con faltante).
create or replace function prov_trg_no_marcar_reemplazada()
returns trigger language plpgsql as $$
begin
  if coalesce(current_setting('prov.deshacer', true), '') = 'si' then return new; end if;
  -- Anular una recepción vieja sí puede devolver unidades; cargar de más, no.
  if new.cantidad_recibida > old.cantidad_recibida then
    if old.reemplazado_por is not null and new.reemplazado_por is not null then
      raise exception 'Esta línea fue reemplazada por la OC %: cargá lo que llegó ahí', old.reemplazado_por;
    end if;
    if old.movido_a is not null then
      raise exception 'Esta línea se pasó a otro proveedor (OC %): cargá lo que llegó ahí', old.movido_a;
    end if;
  end if;
  if new.movido_a is not null then new.recibido_completo := true; end if;
  return new;
end $$;
drop trigger if exists prov_no_marcar on repo_pedido_items;
create trigger prov_no_marcar before update on repo_pedido_items
  for each row execute function prov_trg_no_marcar_reemplazada();

-- ─── Registro para deshacer ──────────────────────────────────────────────────
create table if not exists prov_acciones (
  id          uuid primary key default gen_random_uuid(),
  creado_at   timestamptz not null default now(),
  creado_por  uuid references auth.users(id) default auth.uid(),
  accion      text not null check (accion in ('esperar', 'llego', 'cancelar', 'reemplazo_inicial')),
  proveedor   text,
  antes       jsonb not null,                    -- foto de cada línea antes del cambio
  deshecha_at timestamptz
);
alter table prov_acciones enable row level security;
drop policy if exists prov_acciones_read on prov_acciones;
create policy prov_acciones_read on prov_acciones for select to authenticated using (cc_rol() in ('admin', 'dueno'));
revoke all on prov_acciones from anon;

create or replace function prov_foto(p_ids bigint[])
returns jsonb language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', id, 'cantidad_recibida', cantidad_recibida, 'recibido_completo', recibido_completo, 'recibido_at', recibido_at,
    'recibido_manual', recibido_manual, 'cerrado_con_faltante', cerrado_con_faltante, 'revisado_at', revisado_at,
    'reemplazado_por', reemplazado_por)), '[]'::jsonb)
  from repo_pedido_items where id = any (p_ids)
$$;

create or replace function prov_permiso()
returns void language plpgsql stable security definer set search_path = public as $$
begin
  if coalesce(cc_rol(), '') not in ('admin', 'dueno') then raise exception 'Sin permiso'; end if;
end $$;

-- ─── Resolver líneas de a muchas ─────────────────────────────────────────────
create or replace function prov_resolver_lineas(p_item_ids bigint[], p_accion text)
returns uuid language plpgsql security definer set search_path = public as $$
declare v_ids bigint[]; v_id uuid; v_ped bigint; v_prov text;
begin
  perform prov_permiso();
  if p_accion not in ('esperar', 'llego', 'cancelar') then raise exception 'Acción desconocida'; end if;
  -- Solo líneas abiertas de pedidos no cancelados.
  select array_agg(i.id order by i.id), min(p.proveedor) into v_ids, v_prov
    from repo_pedido_items i join repo_pedidos p on p.id = i.pedido_id
   where i.id = any (p_item_ids) and not i.recibido_completo and p.estado <> 'cancelado';
  if v_ids is null then raise exception 'Esas líneas ya no están pendientes'; end if;

  insert into prov_acciones (accion, proveedor, antes) values (p_accion, v_prov, prov_foto(v_ids)) returning id into v_id;

  if p_accion = 'esperar' then
    update repo_pedido_items set revisado_at = now() where id = any (v_ids);
  elsif p_accion = 'llego' then
    update repo_pedido_items
       set cantidad_recibida = greatest(cantidad_recibida, cantidad_pedida), recibido_completo = true,
           recibido_at = coalesce(recibido_at, now()), recibido_manual = true
     where id = any (v_ids);
  else  -- cancelar lo que falta
    update repo_pedido_items
       set recibido_completo = true, recibido_at = coalesce(recibido_at, now()),
           cerrado_con_faltante = true, recibido_manual = true
     where id = any (v_ids);
  end if;

  if p_accion <> 'esperar' then
    for v_ped in select distinct pedido_id from repo_pedido_items where id = any (v_ids) loop
      update repo_pedidos set estado = prov_estado_pedido(v_ped) where id = v_ped and estado <> 'cancelado';
    end loop;
  end if;
  return v_id;
end $$;

create or replace function prov_deshacer(p_accion_id uuid)
returns void language plpgsql security definer set search_path = public as $$
declare v prov_acciones; v_ped bigint; v_ids bigint[]; v_r record;
begin
  perform prov_permiso();
  select * into v from prov_acciones where id = p_accion_id for update;
  if not found then raise exception 'No existe esa acción'; end if;
  if v.deshecha_at is not null then return; end if;

  perform set_config('prov.deshacer', 'si', true);
  update repo_pedido_items i
     set cantidad_recibida    = (a->>'cantidad_recibida')::int,
         recibido_completo    = (a->>'recibido_completo')::boolean,
         recibido_at          = (a->>'recibido_at')::timestamptz,
         recibido_manual      = (a->>'recibido_manual')::boolean,
         cerrado_con_faltante = (a->>'cerrado_con_faltante')::boolean,
         revisado_at          = (a->>'revisado_at')::timestamptz,
         reemplazado_por      = (a->>'reemplazado_por')::bigint
    from jsonb_array_elements(v.antes) a
   where i.id = (a->>'id')::bigint;
  perform set_config('prov.deshacer', '', true);
  -- Si desde entonces hubo un pedido más nuevo, la línea que vuelve sigue reemplazada.
  if v.accion <> 'reemplazo_inicial' then
    for v_r in
      select distinct p.proveedor, i.sku from repo_pedido_items i join repo_pedidos p on p.id = i.pedido_id
       where i.id in (select (a->>'id')::bigint from jsonb_array_elements(v.antes) a)
    loop
      perform prov_recalcular(v_r.proveedor, v_r.sku);
    end loop;
  end if;

  select array_agg((a->>'id')::bigint) into v_ids from jsonb_array_elements(v.antes) a;
  for v_ped in select distinct pedido_id from repo_pedido_items where id = any (v_ids) loop
    update repo_pedidos set estado = prov_estado_pedido(v_ped) where id = v_ped and estado <> 'cancelado';
  end loop;
  update prov_acciones set deshecha_at = now() where id = p_accion_id;
end $$;
-- Ojo: deshacer el reemplazo inicial deja las líneas como estaban; la regla vuelve a correr sobre un producto recién
-- cuando se toque (pedido nuevo, cancelación o reapertura).

-- ─── Aplicar la regla a lo ya cargado (una sola vez, revisable) ──────────────
create or replace function prov_reemplazos_preview()
returns table (item_id bigint, proveedor text, sku text, oc_vieja bigint, fecha_vieja date, unidad_vieja text,
               pendiente_viejo integer, oc_nueva bigint, fecha_nueva date, unidad_nueva text, pedido_nuevo integer)
language sql stable security definer set search_path = public as $$
  with l as (
    select i.id, p.id as pid, p.proveedor, coalesce(p.vigente_desde, p.creado_at) as creado_at, p.unidad_medida, i.sku,
           i.cantidad_pedida, i.cantidad_recibida, i.recibido_completo
      from repo_pedidos p join repo_pedido_items i on i.pedido_id = p.id
     where p.estado <> 'cancelado' and p.cancelado_at is null
  ), gana as (
    select distinct on (l.proveedor, l.sku) l.proveedor, l.sku, l.pid, l.creado_at, l.unidad_medida
      from l where l.cantidad_pedida > 0
     order by l.proveedor, l.sku, l.creado_at desc, l.pid desc
  )
  select l.id, l.proveedor, l.sku, l.pid, (l.creado_at - interval '3 hours')::date, l.unidad_medida,
         greatest(l.cantidad_pedida - l.cantidad_recibida, 0),
         g.pid, (g.creado_at - interval '3 hours')::date, g.unidad_medida,
         (select sum(x.cantidad_pedida)::int from l x where x.pid = g.pid and x.sku = g.sku)
    from l join gana g on g.proveedor = l.proveedor and g.sku = l.sku
   where not l.recibido_completo and l.pid <> g.pid and (l.creado_at, l.pid) < (g.creado_at, g.pid)
$$;

create or replace function prov_aplicar_reemplazos()
returns jsonb language plpgsql security definer set search_path = public as $$
declare v_ids bigint[]; v_id uuid; v_r record;
begin
  -- Admin/dueño desde el panel, o el servidor (SQL Editor / service_role) para la aplicación inicial.
  if auth.uid() is not null then perform prov_permiso(); end if;
  select array_agg(item_id) into v_ids from prov_reemplazos_preview();
  if v_ids is null then return jsonb_build_object('lineas', 0); end if;
  insert into prov_acciones (accion, antes) values ('reemplazo_inicial', prov_foto(v_ids)) returning id into v_id;
  for v_r in select distinct proveedor, sku from prov_reemplazos_preview() loop
    perform prov_recalcular(v_r.proveedor, v_r.sku);
  end loop;
  return jsonb_build_object('lineas', cardinality(v_ids), 'accion_id', v_id);
end $$;

-- ─── Lo que hay que comprar hoy, por SKU ─────────────────────────────────────
-- Último cálculo de reposición. Misma regla que el tablero (index.html, excedenteDeposito / puedeCubrirseDesdeDeposito):
-- si lo que pide Full entra entero en el sobrante de depósito, se mueve stock y no se compra. Full Lavan no participa.
create or replace view prov_necesidad with (security_invoker = true) as
  with ult as (
    select distinct on (c.sku) c.*
      from repo_calculo_semanal c
     where c.fecha_corrida > now() - interval '8 days'
     order by c.sku, c.fecha_corrida desc
  ), base as (
    select sku,
           coalesce(a_reponer_deposito, 0) as dep,
           coalesce(a_reponer_full_pret, 0) as full_pret,
           coalesce(stock_deposito, 0) as stock_dep,
           coalesce(stock_full_pret, 0) as stock_full,
           coalesce(vendido_deposito, 0) + coalesce(vendido_full_pret, 0) as vendido,
           greatest(0, floor(coalesce(stock_deposito, 0)
             - coalesce(vel_diaria_deposito, 0) * coalesce(nullif(coverage_days_usado, 0), 30)))::int as excedente
      from ult
  )
  select sku, dep as deposito,
         case when full_pret > 0 and full_pret <= excedente then 0 else full_pret end as full_compra,
         stock_dep, stock_full, vendido
    from base;
revoke all on prov_necesidad from anon, authenticated;

-- ─── Saldo ───────────────────────────────────────────────────────────────────
-- Una fila por proveedor + SKU: lo pendiente (líneas abiertas) y lo que hoy hace falta comprar. Devuelve un solo jsonb
-- para no chocar con el tope de 1000 filas de la API.
create or replace function prov_saldo(p_proveedor text default null)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  perform prov_permiso();
  with otros as (
    -- El mismo producto puede estar pedido a varios proveedores: cuánto hay pendiente en cada uno.
    select p.sku_ as sku, p.proveedor, sum(p.pend)::int as pend_u
      from (select i.sku as sku_, o.proveedor, greatest(i.cantidad_pedida - i.cantidad_recibida, 0) as pend
              from repo_pedidos o join repo_pedido_items i on i.pedido_id = o.id
             where o.estado in ('enviado', 'recibido_parcial') and o.cancelado_at is null and not i.recibido_completo
               and o.unidad_medida is distinct from 'bultos') p
     group by 1, 2
  ), abiertas as (
    select p.proveedor, i.sku,
           sum(case when p.unidad_medida = 'bultos' then 0 else greatest(i.cantidad_pedida - i.cantidad_recibida, 0) end)::int as pend_u,
           sum(case when p.unidad_medida = 'bultos' then greatest(i.cantidad_pedida - i.cantidad_recibida, 0) else 0 end)::int as pend_b,
           sum(i.cantidad_recibida)::int as recibido,
           min(p.creado_at) as desde,
           jsonb_agg(jsonb_build_object('item', i.id, 'oc', p.id, 'fecha', p.creado_at, 'unidad', p.unidad_medida,
             'destino', i.destino, 'pedida', i.cantidad_pedida, 'recibida', i.cantidad_recibida,
             'estimado', i.estimado_recibido, 'revisado', i.revisado_at) order by p.creado_at, p.id, i.id) as lineas
      from repo_pedidos p join repo_pedido_items i on i.pedido_id = p.id
     where p.estado in ('enviado', 'recibido_parcial') and p.cancelado_at is null and not i.recibido_completo
       and (p_proveedor is null or p.proveedor = p_proveedor)
     group by p.proveedor, i.sku
  ), necesita as (
    select coalesce(r.proveedor_manual, r.proveedor_auto) as proveedor, n.sku, n.deposito, n.full_compra
      from prov_necesidad n join repo_productos r on r.sku = n.sku
     where n.deposito + n.full_compra > 0 and not coalesce(r.descontinuado, false)
       and not coalesce(r.fuera_de_temporada, false)
  ), dueno as (
    -- La necesidad de cada SKU va a un solo proveedor: el del pedido abierto más nuevo y, si no hay, el asignado.
    select n.sku, n.deposito, n.full_compra, coalesce((
             select p.proveedor from repo_pedidos p join repo_pedido_items i on i.pedido_id = p.id
              where i.sku = n.sku and not i.recibido_completo and p.estado in ('enviado', 'recibido_parcial')
                and p.cancelado_at is null
              order by coalesce(p.vigente_desde, p.creado_at) desc, p.id desc limit 1), n.proveedor) as proveedor
      from necesita n
  ), claves as (
    select proveedor, sku from abiertas
    union
    select proveedor, sku from dueno
     where proveedor is not null and proveedor <> 'Otros' and (p_proveedor is null or proveedor = p_proveedor)
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'proveedor', k.proveedor, 'sku', k.sku,
      'nombre', coalesce(r.nombre, c.producto, k.sku), 'color', c.color,
      'categoria', coalesce(r.categoria_manual, r.categoria_auto, 'Sin categoría'),
      'asignado', coalesce(r.proveedor_manual, r.proveedor_auto),
      'pend_u', coalesce(a.pend_u, 0), 'pend_b', coalesce(a.pend_b, 0), 'recibido', coalesce(a.recibido, 0),
      'desde', a.desde, 'lineas', coalesce(a.lineas, '[]'::jsonb),
      'nec_dep', coalesce(n.deposito, 0), 'nec_full', coalesce(n.full_compra, 0),
      'stock_dep', s.stock_dep, 'stock_full', s.stock_full, 'vendido', s.vendido,
      'otros', coalesce((select jsonb_agg(jsonb_build_object('proveedor', x.proveedor, 'pend_u', x.pend_u) order by x.proveedor)
                           from otros x where x.sku = k.sku and x.proveedor <> k.proveedor and x.pend_u > 0), '[]'::jsonb))), '[]'::jsonb)
    into v
    from claves k
    left join abiertas a on a.proveedor = k.proveedor and a.sku = k.sku
    left join repo_productos r on r.sku = k.sku
    left join cc_productos c on c.sku = k.sku
    left join dueno n on n.sku = k.sku and n.proveedor = k.proveedor
    left join prov_necesidad s on s.sku = k.sku;
  return v;
end $$;

-- ─── Pedido nuevo desde el panel ─────────────────────────────────────────────
-- p_items = [{"sku": "...", "cantidad": 150}]. La cantidad es el TOTAL que se quiere recibir (pisa lo pendiente).
-- p_clave la genera el navegador: reintentar no duplica la OC.
create or replace function prov_crear_pedido(p_clave uuid, p_proveedor text, p_items jsonb, p_unidad text default 'unidades', p_titulo text default null)
returns bigint language plpgsql security definer set search_path = public as $$
declare v_id bigint; v_nombre text; v_n integer;
begin
  perform prov_permiso();
  if p_clave is null then raise exception 'Falta la clave del pedido'; end if;
  select id into v_id from repo_pedidos where clave_origen = p_clave;
  if v_id is not null then return v_id; end if;
  if coalesce(trim(p_proveedor), '') = '' then raise exception 'Falta el proveedor'; end if;
  if p_unidad not in ('unidades', 'bultos') then raise exception 'La unidad tiene que ser unidades o bultos'; end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' then raise exception 'No hay productos'; end if;
  select count(*) into v_n from jsonb_to_recordset(p_items) as x(sku text, cantidad integer) where x.cantidad > 0;
  if v_n = 0 then raise exception 'No hay productos con cantidad'; end if;
  if exists (select 1 from jsonb_to_recordset(p_items) as x(sku text, cantidad integer) where x.cantidad > 0
              group by x.sku having count(*) > 1) then
    raise exception 'Hay productos repetidos';
  end if;
  if exists (select 1 from jsonb_to_recordset(p_items) as x(sku text, cantidad integer)
              where x.cantidad > 0 and not exists (select 1 from repo_productos r where r.sku = x.sku)) then
    raise exception 'Hay productos que no existen';
  end if;

  select nombre into v_nombre from cc_perfiles where id = auth.uid();
  insert into repo_pedidos (proveedor, creado_por, unidad_medida, titulo_extra, clave_origen, notas)
  values (p_proveedor, coalesce(v_nombre, 'Panel'), p_unidad, nullif(trim(p_titulo), ''), p_clave, 'Pendientes por proveedor')
  returning id into v_id;

  -- El total se reparte entre Full (lo que hoy hay que comprar para Full) y el resto a Depósito.
  with x as (
    select x.sku, x.cantidad, least(x.cantidad, coalesce(case when p_unidad = 'unidades' then n.full_compra end, 0)) as a_full,
           coalesce(case when p_unidad = 'unidades' then n.deposito + n.full_compra end, 0) as sugerida
      from jsonb_to_recordset(p_items) as x(sku text, cantidad integer)
      left join prov_necesidad n on n.sku = x.sku
     where x.cantidad > 0
  ), snap as (
    select distinct on (s.sku) s.sku, s.stock_deposito, s.stock_full_pret
      from repo_stock_snapshot s join x on x.sku = s.sku
     where s.fecha > current_date - 15
     order by s.sku, s.fecha desc
  ), partes as (
    select sku, 'deposito' as destino, cantidad - a_full as q, sugerida from x
    union all
    select sku, 'full_pret', a_full, sugerida from x
  )
  insert into repo_pedido_items (pedido_id, sku, destino, cantidad_pedida, cantidad_sugerida, stock_al_pedir)
  select v_id, p.sku, p.destino, p.q, p.sugerida,
         coalesce(case when p.destino = 'deposito' then s.stock_deposito else s.stock_full_pret end, 0)
    from partes p left join snap s on s.sku = p.sku
   where p.q > 0;
  return v_id;
end $$;

-- ─── Cambiar de proveedor un pedido ya armado ────────────────────────────────
-- p_skus null = toda la OC. Si se pasa todo y todavía no llegó nada (ni tiene facturas o remitos), es la misma OC con
-- otro proveedor. Si no, se crea una OC nueva con lo pendiente de esos productos y las líneas originales quedan
-- cerradas con movido_a. En los dos casos devuelve el número de la OC que queda a nombre del proveedor nuevo.
create or replace function prov_cambiar_proveedor(p_pedido_id bigint, p_proveedor text, p_skus text[] default null)
returns bigint language plpgsql security definer set search_path = public as $$
declare
  v_ped repo_pedidos; v_nuevo bigint; v_ids bigint[]; v_skus text[]; v_entera boolean; v_prov text := trim(p_proveedor);
  v_desde timestamptz; v_viejas bigint[]; v_o bigint;
begin
  -- Desde el panel (usuario logueado) solo admin/dueño; el tablero entra por la API con la clave de servicio.
  if auth.uid() is not null then perform prov_permiso(); end if;
  if coalesce(v_prov, '') = '' then raise exception 'Falta el proveedor nuevo'; end if;
  select * into v_ped from repo_pedidos where id = p_pedido_id for update;
  if not found then raise exception 'La OC no existe'; end if;
  if v_ped.estado = 'cancelado' or v_ped.cancelado_at is not null then raise exception 'La OC está cancelada'; end if;
  if v_ped.proveedor = v_prov then raise exception 'La OC ya es de %', v_prov; end if;

  select array_agg(id order by id), array_agg(distinct sku) into v_ids, v_skus
    from repo_pedido_items
   where pedido_id = p_pedido_id and not recibido_completo and (p_skus is null or sku = any (p_skus));
  if v_ids is null then raise exception 'No hay nada pendiente para pasar'; end if;
  v_desde := coalesce(v_ped.vigente_desde, v_ped.creado_at);

  v_entera := cardinality(v_ids) = (select count(*) from repo_pedido_items where pedido_id = p_pedido_id)
    and not exists (select 1 from repo_pedido_items where pedido_id = p_pedido_id and cantidad_recibida > 0)
    and not exists (select 1 from cc_movimientos where oc_id = p_pedido_id)
    and not exists (select 1 from cc_recepciones where oc_id = p_pedido_id);

  if v_entera then
    v_nuevo := p_pedido_id;
  else
    insert into repo_pedidos (proveedor, creado_por, unidad_medida, titulo_extra, proveedor_anterior, notas)
    values (v_prov, v_ped.creado_por, v_ped.unidad_medida, v_ped.titulo_extra, v_ped.proveedor,
            'Pasada desde OC ' || p_pedido_id || ' (' || v_ped.proveedor || ')')
    returning id into v_nuevo;
  end if;

  -- Lo que este pedido había reemplazado en el proveedor de origen queda cerrado: no revive al irse el pedido.
  with viejas as (
    update repo_pedido_items i
       set movido_a = v_nuevo, reemplazado_por = null, recibido_completo = true
      from repo_pedidos o
     where o.id = i.pedido_id and o.proveedor = v_ped.proveedor and o.id <> p_pedido_id and i.sku = any (v_skus)
       and o.estado <> 'cancelado' and (coalesce(o.vigente_desde, o.creado_at), o.id) < (v_desde, p_pedido_id)
       and (not i.recibido_completo or i.reemplazado_por is not null)
    returning i.pedido_id
  ) select array_agg(distinct pedido_id) into v_viejas from viejas;

  if v_entera then
    update repo_pedidos set proveedor = v_prov, proveedor_anterior = v_ped.proveedor, vigente_desde = clock_timestamp()
     where id = p_pedido_id;
  else
    insert into repo_pedido_items (pedido_id, sku, destino, cantidad_pedida, cantidad_sugerida, stock_al_pedir)
    select v_nuevo, sku, destino, greatest(cantidad_pedida - cantidad_recibida, 0), cantidad_sugerida, stock_al_pedir
      from repo_pedido_items where id = any (v_ids) and cantidad_pedida > cantidad_recibida;
    update repo_pedido_items set movido_a = v_nuevo, recibido_completo = true where id = any (v_ids);
    update repo_pedidos set estado = prov_estado_pedido(p_pedido_id) where id = p_pedido_id;
  end if;

  foreach v_o in array coalesce(v_viejas, '{}'::bigint[]) loop
    update repo_pedidos set estado = prov_estado_pedido(v_o) where id = v_o and estado <> 'cancelado';
  end loop;
  return v_nuevo;
end $$;

-- ─── Permisos ────────────────────────────────────────────────────────────────
do $$
declare f text;
begin
  -- Internas: nadie las llama por la API.
  foreach f in array array['prov_estado_pedido(bigint)', 'prov_recalcular(text,text)', 'prov_foto(bigint[])', 'prov_permiso()',
    'prov_trg_estado()', 'prov_trg_items()', 'prov_trg_pedidos()', 'prov_trg_pedido_borrado()', 'prov_trg_no_marcar_reemplazada()', 'prov_reemplazos_preview()'] loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
    execute format('grant execute on function %s to service_role', f);
  end loop;
  -- Del panel (cada una valida admin/dueño).
  foreach f in array array['prov_resolver_lineas(bigint[],text)', 'prov_deshacer(uuid)', 'prov_aplicar_reemplazos()',
    'prov_saldo(text)', 'prov_crear_pedido(uuid,text,jsonb,text,text)', 'prov_cambiar_proveedor(bigint,text,text[])'] loop
    execute format('revoke all on function %s from public, anon', f);
    execute format('grant execute on function %s to authenticated, service_role', f);
  end loop;
end $$;
