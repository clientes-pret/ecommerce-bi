-- Link para el proveedor: ve lo que tiene pendiente y contesta cuánto entrega y cuándo (o que no lo va a hacer).
--
-- Decisiones del usuario (27/11/2026): el proveedor ve SOLO lo pedido y pendiente (nunca stock, ventas, costos ni
-- sugerencias); su respuesta no cambia ningún pedido, solo se muestra en "Pendientes"; el link se puede dar de baja.
--
-- El link lleva un código secreto de 64 caracteres. La página pública llama a la función proveedor-portal, que usa
-- prov_portal_datos / prov_portal_responder con la clave de servicio. La clave pública del proyecto no puede ejecutar
-- ninguna de estas funciones ni leer las tablas.

create table if not exists prov_links (
  id               uuid primary key default gen_random_uuid(),
  proveedor        text not null,
  token            text not null unique default replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''),
  creado_at        timestamptz not null default now(),
  creado_por       uuid references auth.users(id) default auth.uid(),
  baja_at          timestamptz,
  ultimo_acceso_at timestamptz
);
-- Un solo link activo por proveedor.
create unique index if not exists prov_links_activo_idx on prov_links (proveedor) where baja_at is null;

create table if not exists prov_respuestas (
  id             bigint generated always as identity primary key,
  link_id        uuid not null references prov_links(id) on delete cascade,
  proveedor      text not null,
  sku            text not null,
  unidad         text not null check (unidad in ('unidades', 'bultos')),
  cantidad       integer check (cantidad between 1 and 100000),
  fecha_entrega  date,
  no_entrega     boolean not null default false,
  creado_at      timestamptz not null default clock_timestamp()
);
create index if not exists prov_respuestas_idx on prov_respuestas (proveedor, sku, creado_at desc);
create index if not exists prov_respuestas_link_idx on prov_respuestas (link_id, creado_at);

alter table prov_links enable row level security;
alter table prov_respuestas enable row level security;
drop policy if exists prov_links_read on prov_links;
create policy prov_links_read on prov_links for select to authenticated using (cc_rol() in ('admin', 'dueno'));
drop policy if exists prov_respuestas_read on prov_respuestas;
create policy prov_respuestas_read on prov_respuestas for select to authenticated using (cc_rol() in ('admin', 'dueno'));
revoke all on prov_links, prov_respuestas from anon;
revoke insert, update, delete, truncate on prov_links, prov_respuestas from authenticated;

-- ─── Panel: crear y dar de baja ──────────────────────────────────────────────
create or replace function prov_link_crear(p_proveedor text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v prov_links;
begin
  perform prov_permiso();
  if not exists (select 1 from repo_pedidos where proveedor = p_proveedor) then
    raise exception 'Ese proveedor todavía no tiene pedidos';
  end if;
  insert into prov_links (proveedor) values (p_proveedor)
    on conflict (proveedor) where baja_at is null do nothing;
  select * into v from prov_links where proveedor = p_proveedor and baja_at is null;
  return jsonb_build_object('id', v.id, 'token', v.token);
end $$;

create or replace function prov_link_baja(p_id uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  perform prov_permiso();
  update prov_links set baja_at = now() where id = p_id and baja_at is null;
end $$;

-- ─── Lo pendiente de un proveedor, solo con lo que puede ver ─────────────────
create or replace function prov_portal_pendiente(p_proveedor text)
returns table (sku text, pend_u integer, pend_b integer, desde timestamptz, vigente timestamptz)
language sql stable security definer set search_path = public as $$
  select i.sku,
         sum(case when p.unidad_medida = 'bultos' then 0 else greatest(i.cantidad_pedida - i.cantidad_recibida, 0) end)::int,
         sum(case when p.unidad_medida = 'bultos' then greatest(i.cantidad_pedida - i.cantidad_recibida, 0) else 0 end)::int,
         min(p.creado_at), max(coalesce(p.vigente_desde, p.creado_at))
    from repo_pedidos p join repo_pedido_items i on i.pedido_id = p.id
   where p.proveedor = p_proveedor and p.estado in ('enviado', 'recibido_parcial') and p.cancelado_at is null
     and not i.recibido_completo and i.cantidad_pedida > i.cantidad_recibida
   group by i.sku
$$;

-- Devuelve null si el código no existe o está dado de baja.
create or replace function prov_portal_datos(p_token text)
returns jsonb language plpgsql security definer set search_path = public as $$
declare v prov_links; v_prod jsonb;
begin
  select * into v from prov_links where token = p_token and baja_at is null;
  if not found then return null; end if;
  update prov_links set ultimo_acceso_at = now() where id = v.id;

  with pend as (
    select * from prov_portal_pendiente(v.proveedor)
  ), resp as (
    select distinct on (x.sku) x.* from prov_respuestas x
     where x.proveedor = v.proveedor order by x.sku, x.creado_at desc, x.id desc
  ), codigos as (
    select a.sku, string_agg(distinct a.codigo_proveedor, ', ') as codigo
      from cc_alias_sku a join cc_proveedores c on c.id = a.proveedor_id
     where v.proveedor = any (c.repo_nombres)
     group by a.sku
  )
  select coalesce(jsonb_agg(jsonb_build_object(
      'sku', q.sku, 'codigo', k.codigo, 'nombre', coalesce(r.nombre, c.producto, q.sku), 'color', c.color,
      'categoria', coalesce(r.categoria_manual, r.categoria_auto, 'Otros'),
      'pend_u', q.pend_u, 'pend_b', q.pend_b, 'desde', q.desde,
      'respuesta', case when rr.sku is not null then jsonb_build_object('cantidad', rr.cantidad, 'unidad', rr.unidad,
          'fecha', rr.fecha_entrega, 'no_entrega', rr.no_entrega, 'creado_at', rr.creado_at,
          'vieja', rr.creado_at < q.vigente) end)), '[]'::jsonb)
    into v_prod
    from pend q
    left join repo_productos r on r.sku = q.sku
    left join cc_productos c on c.sku = q.sku
    left join resp rr on rr.sku = q.sku
    left join codigos k on k.sku = q.sku;

  return jsonb_build_object('proveedor', v.proveedor, 'ahora', now(), 'productos', v_prod);
end $$;

-- p_items = [{"sku": "...", "cantidad": 12, "fecha": "2026-12-05", "no_entrega": false}]. Se guarda todo o nada.
create or replace function prov_portal_responder(p_token text, p_items jsonb)
returns integer language plpgsql security definer set search_path = public as $$
declare
  v prov_links; v_it record; v_pend record; v_n integer; v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
  v_total integer := 0;
begin
  select * into v from prov_links where token = p_token and baja_at is null for update;
  if not found then return null; end if;
  if p_items is null or jsonb_typeof(p_items) <> 'array' then raise exception 'No hay respuestas para guardar'; end if;
  v_n := jsonb_array_length(p_items);
  if v_n = 0 then raise exception 'No hay respuestas para guardar'; end if;
  if v_n > 600 then raise exception 'Son demasiados productos de una vez'; end if;
  -- Tope contra abusos: 3000 respuestas por hora y por link.
  if (select count(*) from prov_respuestas where link_id = v.id and creado_at > now() - interval '1 hour') + v_n > 3000 then
    raise exception 'Se mandaron demasiadas respuestas seguidas: probá de nuevo en un rato';
  end if;

  for v_it in select * from jsonb_to_recordset(p_items) as x(sku text, cantidad integer, fecha date, no_entrega boolean) loop
    select * into v_pend from prov_portal_pendiente(v.proveedor) q where q.sku = v_it.sku;
    if not found then raise exception 'El producto % ya no está pendiente: actualizá la página', left(coalesce(v_it.sku, '?'), 80); end if;
    if not coalesce(v_it.no_entrega, false) then
      if v_it.cantidad is null and v_it.fecha is null then raise exception 'Falta la cantidad o la fecha de %', v_it.sku; end if;
      if v_it.cantidad is not null and (v_it.cantidad < 1 or v_it.cantidad > 100000) then raise exception 'Cantidad inválida en %', v_it.sku; end if;
      if v_it.fecha is not null and (v_it.fecha < v_hoy or v_it.fecha > v_hoy + 365) then
        raise exception 'La fecha de % tiene que ser de hoy en adelante', v_it.sku;
      end if;
    end if;
    insert into prov_respuestas (link_id, proveedor, sku, unidad, cantidad, fecha_entrega, no_entrega)
    values (v.id, v.proveedor, v_it.sku, case when v_pend.pend_u > 0 then 'unidades' else 'bultos' end,
            case when coalesce(v_it.no_entrega, false) then null else v_it.cantidad end,
            case when coalesce(v_it.no_entrega, false) then null else v_it.fecha end,
            coalesce(v_it.no_entrega, false));
    v_total := v_total + 1;
  end loop;
  return v_total;
end $$;

-- ─── Saldo: más rápido y con la respuesta del proveedor ──────────────────────
create or replace view prov_necesidad with (security_invoker = true) as
  with ult as (
    select distinct on (c.sku) c.*
      from repo_calculo_semanal c
     where c.semana_iso >= to_char(now() - interval '8 days', 'IYYY-"W"IW')
       and c.fecha_corrida > now() - interval '8 days'
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

create or replace function prov_saldo(p_proveedor text default null)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  perform prov_permiso();
  with nec as materialized (select * from prov_necesidad), resp as (
    select distinct on (x.proveedor, x.sku) x.proveedor, x.sku, x.cantidad, x.unidad, x.fecha_entrega, x.no_entrega, x.creado_at
      from prov_respuestas x
     where p_proveedor is null or x.proveedor = p_proveedor
     order by x.proveedor, x.sku, x.creado_at desc, x.id desc
  ), otros as (
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
      from nec n join repo_productos r on r.sku = n.sku
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
      -- "vieja": contestó antes del pedido vigente (lo actualizaste después).
      'respuesta', case when rr.sku is not null then jsonb_build_object('cantidad', rr.cantidad, 'unidad', rr.unidad,
          'fecha', rr.fecha_entrega, 'no_entrega', rr.no_entrega, 'creado_at', rr.creado_at,
          'vieja', coalesce(rr.creado_at < (select max(coalesce(o.vigente_desde, o.creado_at))
                     from repo_pedidos o join repo_pedido_items i on i.pedido_id = o.id
                    where o.proveedor = k.proveedor and i.sku = k.sku and not i.recibido_completo
                      and o.estado in ('enviado', 'recibido_parcial') and o.cancelado_at is null), false)) end,
      'otros', coalesce((select jsonb_agg(jsonb_build_object('proveedor', x.proveedor, 'pend_u', x.pend_u) order by x.proveedor)
                           from otros x where x.sku = k.sku and x.proveedor <> k.proveedor and x.pend_u > 0), '[]'::jsonb))), '[]'::jsonb)
    into v
    from claves k
    left join abiertas a on a.proveedor = k.proveedor and a.sku = k.sku
    left join repo_productos r on r.sku = k.sku
    left join cc_productos c on c.sku = k.sku
    left join dueno n on n.sku = k.sku and n.proveedor = k.proveedor
    left join nec s on s.sku = k.sku
    left join resp rr on rr.proveedor = k.proveedor and rr.sku = k.sku;
  return v;
end $$;

-- ─── Permisos ────────────────────────────────────────────────────────────────
do $$
declare f text;
begin
  -- Solo la función proveedor-portal (clave de servicio).
  foreach f in array array['prov_portal_pendiente(text)', 'prov_portal_datos(text)', 'prov_portal_responder(text,jsonb)'] loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
    execute format('grant execute on function %s to service_role', f);
  end loop;
  -- Del panel (validan admin/dueño).
  foreach f in array array['prov_link_crear(text)', 'prov_link_baja(uuid)', 'prov_saldo(text)'] loop
    execute format('revoke all on function %s from public, anon', f);
    execute format('grant execute on function %s to authenticated, service_role', f);
  end loop;
end $$;
