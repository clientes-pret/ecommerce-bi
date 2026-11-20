-- Recepción de mercadería desde el celular del depósito, POR PROVEEDOR (no por OC).
--
-- Mati elige el proveedor, marca qué productos llegaron y los cuenta. Al confirmar, lo contado se reparte
-- entre las líneas abiertas de las OC de ese proveedor, de la OC más vieja a la más nueva (mismo criterio
-- que reposicion/recepciones.py). Así no hace falta saber a qué OC corresponde cada caja: el proveedor
-- puede mandar variado a medida que produce y hay varias OC abiertas a la vez.
--
-- Reglas:
--  * Lo contado a mano gana sobre la estimación automática: toda línea abierta de ese SKU y proveedor queda con
--    recibido_manual = true, para que recepciones.py no vuelva a "detectar" el mismo ingreso por el stock.
--  * Los pedidos en bultos no se tocan (no se sabe cuántas unidades tiene un bulto).
--  * Lo que llega de más, o de un producto que no estaba pedido, no se pierde: queda registrado en el ingreso
--    con su tipo ('de_mas' / 'no_pedido' / 'en_bultos') y se le avisa a Mati al confirmar.
--  * Se puede anular un ingreso (devuelve lo repartido).

-- 1) Un ingreso = una recepción confirmada. Sus renglones dicen cuánto se contó de cada SKU y qué pasó con eso.
create table if not exists oc_ingresos (
  id           uuid primary key default gen_random_uuid(),   -- lo genera el celular: reintentar no duplica
  proveedor    text not null,                                -- nombre como figura en repo_pedidos.proveedor
  creado_por   uuid references auth.users(id) default auth.uid(),
  creado_at    timestamptz not null default now(),
  remito_paths text[] not null default '{}',                 -- fotos en el bucket privado "remitos"
  notas        text,
  estado       text not null default 'confirmado' check (estado in ('confirmado', 'anulado')),
  anulado_at   timestamptz,
  anulado_por  uuid references auth.users(id)
);
create index if not exists oc_ingresos_prov_idx on oc_ingresos (proveedor, creado_at desc);

create table if not exists oc_ingreso_items (
  id              bigint generated always as identity primary key,
  ingreso_id      uuid not null references oc_ingresos(id) on delete cascade,
  sku             text not null,
  cantidad        integer not null check (cantidad > 0),      -- lo que contó Mati
  pendiente_antes integer not null default 0,                 -- lo que las OC abiertas esperaban de ese SKU
  repartido       integer not null default 0,                 -- cuánto se asignó a líneas de OC
  sobrante        integer not null default 0,                 -- lo que no entró en ninguna línea
  tipo            text not null check (tipo in ('completo', 'falta', 'de_mas', 'no_pedido', 'en_bultos')),
  reparto         jsonb not null default '[]'::jsonb          -- [{i: id de línea, q: unidades, m: estaba marcada a mano antes}]
);
create index if not exists oc_ingreso_items_ing_idx on oc_ingreso_items (ingreso_id);

alter table oc_ingresos enable row level security;
alter table oc_ingreso_items enable row level security;
-- Solo lectura directa: depósito ve lo de los últimos 14 días, admin/dueño todo. Todo lo que escribe va por funciones.
create policy oc_ingresos_read on oc_ingresos for select to authenticated
  using (cc_rol() in ('admin', 'dueno') or (cc_rol() = 'deposito' and creado_at > now() - interval '14 days'));
create policy oc_ingreso_items_read on oc_ingreso_items for select to authenticated
  using (exists (select 1 from oc_ingresos g where g.id = ingreso_id));
revoke all on oc_ingresos, oc_ingreso_items from anon;

-- 2) Proveedores: primero los que tienen pedidos abiertos (con cuánto falta), después el resto.
--    p_dias deja afuera OC muy viejas (en el depósito se ven solo las recientes).
create or replace function rec_proveedores(p_dias integer default 60)
returns table (proveedor text, ocs integer, lineas integer, unidades integer)
language plpgsql stable security definer set search_path = public as $$
begin
  if cc_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  with abiertos as (
    select p.proveedor as prov, count(distinct p.id)::int as n_ocs, count(*)::int as n_lineas,
           sum(greatest(i.cantidad_pedida - i.cantidad_recibida, 0))::int as unid
      from repo_pedidos p join repo_pedido_items i on i.pedido_id = p.id
     where p.estado in ('enviado', 'recibido_parcial')
       and p.unidad_medida is distinct from 'bultos'
       and not i.recibido_completo
       and p.creado_at >= now() - make_interval(days => p_dias)
     group by p.proveedor
    having sum(greatest(i.cantidad_pedida - i.cantidad_recibida, 0)) > 0
  ), todos as (
    select distinct coalesce(r.proveedor_manual, r.proveedor_auto) as prov
      from repo_productos r
     where coalesce(r.proveedor_manual, r.proveedor_auto) is not null
       and coalesce(r.proveedor_manual, r.proveedor_auto) <> 'Otros'
    union
    select distinct p.proveedor from repo_pedidos p
  )
  select t.prov, coalesce(a.n_ocs, 0), coalesce(a.n_lineas, 0), coalesce(a.unid, 0)
    from todos t left join abiertos a on a.prov = t.prov
   order by (a.prov is null), a.unid desc nulls last, t.prov;
end $$;

-- 3) Lo pendiente de un proveedor, sumado por SKU entre todas sus OC abiertas.
create or replace function rec_pendientes(p_proveedor text, p_dias integer default 60)
returns table (sku text, producto text, color text, medida text, pedido integer, recibido integer, pendiente integer, desde date)
language plpgsql stable security definer set search_path = public as $$
begin
  if cc_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select i.sku, coalesce(c.producto, r.nombre, i.sku), c.color, c.medida,
         sum(i.cantidad_pedida)::int, sum(i.cantidad_recibida)::int,
         sum(greatest(i.cantidad_pedida - i.cantidad_recibida, 0))::int, min(p.creado_at)::date
    from repo_pedido_items i
    join repo_pedidos p on p.id = i.pedido_id
    left join cc_productos c on c.sku = i.sku
    left join repo_productos r on r.sku = i.sku
   where p.estado in ('enviado', 'recibido_parcial')
     and p.proveedor = p_proveedor
     and p.unidad_medida is distinct from 'bultos'
     and not i.recibido_completo
     and p.creado_at >= now() - make_interval(days => p_dias)
   group by i.sku, c.producto, c.color, c.medida, r.nombre
  having sum(greatest(i.cantidad_pedida - i.cantidad_recibida, 0)) > 0
   order by 2, 3, 4, 1;
end $$;

-- 4) Buscador para lo que llegó y no estaba pedido (todo el catálogo; los del proveedor primero).
create or replace function rec_buscar(p_q text, p_proveedor text default null)
returns table (sku text, producto text, color text, medida text)
language plpgsql stable security definer set search_path = public as $$
declare v_tokens text[];
begin
  if cc_rol() is null then raise exception 'Sin permiso'; end if;
  select array_agg('%' || replace(replace(t, '%', ''), '_', '') || '%')
    into v_tokens from unnest(regexp_split_to_array(lower(trim(coalesce(p_q, ''))), '\s+')) t where t <> '';
  if v_tokens is null then return; end if;
  return query
  select r.sku, coalesce(c.producto, r.nombre, r.sku), c.color, c.medida
    from repo_productos r left join cc_productos c on c.sku = r.sku
   where lower(coalesce(r.sku, '') || ' ' || coalesce(c.producto, r.nombre, '') || ' ' || coalesce(c.color, '') || ' ' || coalesce(c.medida, '')) like all (v_tokens)
   order by (coalesce(r.proveedor_manual, r.proveedor_auto) is distinct from p_proveedor), 2, 3, 1
   limit 40;
end $$;

-- 5) Detalle de un ingreso (lo que se muestra al confirmar y en "Últimas cargas").
create or replace function rec_detalle(p_id uuid)
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  if cc_rol() is null then raise exception 'Sin permiso'; end if;
  select jsonb_build_object('id', g.id, 'proveedor', g.proveedor, 'creado_at', g.creado_at, 'estado', g.estado,
    'items', coalesce((select jsonb_agg(jsonb_build_object('sku', x.sku, 'producto', coalesce(c.producto, r.nombre, x.sku),
        'color', c.color, 'medida', c.medida, 'cantidad', x.cantidad, 'pendiente_antes', x.pendiente_antes,
        'sobrante', x.sobrante, 'tipo', x.tipo) order by coalesce(c.producto, r.nombre, x.sku), c.color, x.sku)
       from oc_ingreso_items x left join cc_productos c on c.sku = x.sku left join repo_productos r on r.sku = x.sku
      where x.ingreso_id = g.id), '[]'::jsonb))
    into v from oc_ingresos g
   where g.id = p_id and (cc_rol() in ('admin', 'dueno') or g.creado_por = auth.uid() or g.creado_at > now() - interval '14 days');
  return v;
end $$;

-- 6) Confirmar: guarda el ingreso y reparte lo contado entre las líneas abiertas (la OC más vieja primero).
--    p_items = [{"sku": "...", "cantidad": 12}, ...]. Idempotente por p_id.
create or replace function rec_confirmar(p_id uuid, p_proveedor text, p_items jsonb, p_remito_paths text[] default '{}', p_notas text default null)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_it record; v_ln record;
  v_resto integer; v_pend integer; v_take integer; v_rep jsonb; v_tipo text; v_bultos boolean;
  v_pedidos bigint[] := '{}'; v_ped bigint;
begin
  if cc_rol() is null then raise exception 'Sin permiso'; end if;
  if exists (select 1 from oc_ingresos where id = p_id) then return rec_detalle(p_id); end if;   -- reintento
  if coalesce(trim(p_proveedor), '') = '' then raise exception 'Falta el proveedor'; end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then raise exception 'No hay productos para guardar'; end if;

  insert into oc_ingresos (id, proveedor, remito_paths, notas) values (p_id, p_proveedor, coalesce(p_remito_paths, '{}'), nullif(trim(p_notas), ''));

  for v_it in
    select e->>'sku' as sku, sum((e->>'cantidad')::int)::int as cantidad
      from jsonb_array_elements(p_items) e
     where coalesce(e->>'sku', '') <> '' and coalesce((e->>'cantidad')::int, 0) > 0
     group by e->>'sku' order by e->>'sku'
  loop
    v_resto := v_it.cantidad; v_pend := 0; v_rep := '[]'::jsonb;
    for v_ln in
      select i.id, i.pedido_id, i.cantidad_pedida, i.cantidad_recibida, i.recibido_manual
        from repo_pedido_items i join repo_pedidos p on p.id = i.pedido_id
       where p.proveedor = p_proveedor and p.estado in ('enviado', 'recibido_parcial')
         and p.unidad_medida is distinct from 'bultos'
         and i.sku = v_it.sku and not i.recibido_completo
       order by p.creado_at, p.id, i.id
         for update of i
    loop
      v_pend := v_pend + greatest(v_ln.cantidad_pedida - v_ln.cantidad_recibida, 0);
      v_take := least(v_resto, greatest(v_ln.cantidad_pedida - v_ln.cantidad_recibida, 0));
      v_resto := v_resto - v_take;
      update repo_pedido_items
         set cantidad_recibida = cantidad_recibida + v_take,
             recibido_completo = (cantidad_recibida + v_take >= cantidad_pedida),
             recibido_at = case when cantidad_recibida + v_take >= cantidad_pedida then coalesce(recibido_at, now()) else recibido_at end,
             recibido_manual = true
       where id = v_ln.id;
      v_rep := v_rep || jsonb_build_object('i', v_ln.id, 'q', v_take, 'm', v_ln.recibido_manual);
      if not (v_ln.pedido_id = any (v_pedidos)) then v_pedidos := v_pedidos || v_ln.pedido_id; end if;
    end loop;

    if v_pend = 0 then
      select exists (select 1 from repo_pedido_items i join repo_pedidos p on p.id = i.pedido_id
                      where p.proveedor = p_proveedor and p.estado in ('enviado', 'recibido_parcial')
                        and p.unidad_medida = 'bultos' and i.sku = v_it.sku and not i.recibido_completo) into v_bultos;
      v_tipo := case when v_bultos then 'en_bultos' else 'no_pedido' end;
    elsif v_it.cantidad > v_pend then v_tipo := 'de_mas';
    elsif v_it.cantidad = v_pend then v_tipo := 'completo';
    else v_tipo := 'falta'; end if;

    insert into oc_ingreso_items (ingreso_id, sku, cantidad, pendiente_antes, repartido, sobrante, tipo, reparto)
    values (p_id, v_it.sku, v_it.cantidad, v_pend, v_it.cantidad - v_resto, v_resto, v_tipo, v_rep);
  end loop;

  if not exists (select 1 from oc_ingreso_items where ingreso_id = p_id) then raise exception 'No hay productos con cantidad para guardar'; end if;

  -- Estado de cada OC tocada (mismo criterio que oc_marcar_recibido).
  foreach v_ped in array v_pedidos loop
    update repo_pedidos set estado = case
      when not exists (select 1 from repo_pedido_items where pedido_id = v_ped and not recibido_completo) then 'recibido_completo'
      when exists (select 1 from repo_pedido_items where pedido_id = v_ped and (recibido_completo or cantidad_recibida > 0)) then 'recibido_parcial'
      else 'enviado' end
    where id = v_ped and estado <> 'cancelado';
  end loop;

  return rec_detalle(p_id);
end $$;

-- 7) Anular: devuelve lo repartido. Depósito puede anular lo propio hasta 24 hs; admin/dueño siempre.
create or replace function rec_anular(p_id uuid)
returns void
language plpgsql security definer set search_path = public as $$
declare
  v_g oc_ingresos; v_r record; v_pedidos bigint[] := '{}'; v_ped bigint;
begin
  if cc_rol() is null then raise exception 'Sin permiso'; end if;
  select * into v_g from oc_ingresos where id = p_id for update;
  if not found then raise exception 'No existe esa recepción'; end if;
  if v_g.estado = 'anulado' then return; end if;
  if cc_rol() not in ('admin', 'dueno') and (v_g.creado_por is distinct from auth.uid() or v_g.creado_at < now() - interval '24 hours') then
    raise exception 'Solo se puede anular una recepción propia dentro de las 24 horas';
  end if;

  for v_r in
    select (e->>'i')::bigint as item_id, (e->>'q')::int as q, (e->>'m')::boolean as m
      from oc_ingreso_items x, jsonb_array_elements(x.reparto) e where x.ingreso_id = p_id
  loop
    update repo_pedido_items
       set cantidad_recibida = greatest(cantidad_recibida - v_r.q, 0),
           recibido_completo = (greatest(cantidad_recibida - v_r.q, 0) >= cantidad_pedida),
           recibido_at = case when greatest(cantidad_recibida - v_r.q, 0) >= cantidad_pedida then recibido_at else null end,
           -- vuelve a como estaba la marca "a mano", salvo que otro ingreso vigente también haya tocado esa línea
           recibido_manual = case when exists (
               select 1 from oc_ingreso_items x2 join oc_ingresos g2 on g2.id = x2.ingreso_id, jsonb_array_elements(x2.reparto) e2
                where g2.estado = 'confirmado' and g2.id <> p_id and (e2->>'i')::bigint = v_r.item_id) then recibido_manual else v_r.m end
     where id = v_r.item_id
    returning pedido_id into v_ped;
    if v_ped is not null and not (v_ped = any (v_pedidos)) then v_pedidos := v_pedidos || v_ped; end if;
  end loop;

  foreach v_ped in array v_pedidos loop
    update repo_pedidos set estado = case
      when not exists (select 1 from repo_pedido_items where pedido_id = v_ped and not recibido_completo) then 'recibido_completo'
      when exists (select 1 from repo_pedido_items where pedido_id = v_ped and (recibido_completo or cantidad_recibida > 0)) then 'recibido_parcial'
      else 'enviado' end
    where id = v_ped and estado <> 'cancelado';
  end loop;

  update oc_ingresos set estado = 'anulado', anulado_at = now(), anulado_por = auth.uid() where id = p_id;
end $$;

-- 8) Últimas cargas (inicio de la pantalla del depósito).
create or replace function rec_ultimos(p_limite integer default 15)
returns table (id uuid, proveedor text, creado_at timestamptz, estado text, productos integer, unidades integer, avisos integer)
language plpgsql stable security definer set search_path = public as $$
begin
  if cc_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select g.id, g.proveedor, g.creado_at, g.estado, count(x.id)::int, coalesce(sum(x.cantidad), 0)::int,
         count(x.id) filter (where x.tipo in ('de_mas', 'no_pedido'))::int
    from oc_ingresos g left join oc_ingreso_items x on x.ingreso_id = g.id
   where cc_rol() in ('admin', 'dueno') or g.creado_at > now() - interval '14 days'
   group by g.id order by g.creado_at desc limit p_limite;
end $$;

do $$
declare f text;
begin
  foreach f in array array[
    'rec_proveedores(integer)', 'rec_pendientes(text,integer)', 'rec_buscar(text,text)', 'rec_detalle(uuid)',
    'rec_confirmar(uuid,text,jsonb,text[],text)', 'rec_anular(uuid)', 'rec_ultimos(integer)'] loop
    execute format('revoke all on function %s from public, anon', f);
    execute format('grant execute on function %s to authenticated, service_role', f);
  end loop;
end $$;
