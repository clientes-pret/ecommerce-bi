-- Cancelaciones y devoluciones de ventas de revendedores (19/11/2026, acordado con el usuario):
--   - Cancelada ANTES de despachar → el stock vuelve solo (reposicion = 'inmediata').
--   - Cancelada o devuelta DESPUÉS de despachar → el producto no está en el depósito: el stock vuelve recién cuando el
--     depósito lo recibe y lo marca en Operaciones → Devoluciones (reposicion = 'al_recibir'). Si no llega o llega dañado,
--     no se repone (y se puede pasar a Stock perdido).
--   - Devolución en curso (la venta sigue paga en ML pero hay un reclamo con envío de vuelta): se guarda en `devolucion`.
-- En modo sombra no se mueve stock en Ecomm-App: solo se registra qué se haría. Quién paga el costo de una devolución
-- todavía no está definido (pendiente del usuario).
alter table public.rev_ordenes add column if not exists despachada       boolean;      -- ¿había salido del depósito al cancelarse?
alter table public.rev_ordenes add column if not exists reposicion       text check (reposicion in ('inmediata', 'al_recibir'));
alter table public.rev_ordenes add column if not exists devolucion       jsonb;        -- reclamo/devolución de ML (estado, envío de vuelta)
alter table public.rev_ordenes add column if not exists recibido_at      timestamptz;  -- el depósito recibió la devolución
alter table public.rev_ordenes add column if not exists recibido_por     text;
alter table public.rev_ordenes add column if not exists recibido_estado  text check (recibido_estado in ('bien', 'danado', 'no_llego'));

-- Lista para Operaciones: lo que está volviendo o tiene que volver al depósito.
create or replace function public.rev_devoluciones_listar()
returns table (id bigint, revendedor text, ml_order_id bigint, sku text, titulo text, cantidad integer, estado text,
               despachada boolean, reposicion text, devolucion jsonb, recibido_at timestamptz, recibido_por text,
               recibido_estado text, actualizado_at timestamptz)
language sql stable security definer set search_path = public as $$
  select o.id, r.nickname, o.ml_order_id, o.sku, coalesce(i.titulo, p.nombre), o.cantidad, o.estado,
         o.despachada, o.reposicion, o.devolucion, o.recibido_at, o.recibido_por, o.recibido_estado, o.actualizado_at
  from public.rev_ordenes o
  join public.rev_revendedores r on r.id = o.revendedor_id
  left join public.rev_catalogo_info i on i.sku = o.sku
  left join public.repo_productos p on p.sku = o.sku
  where public.rev_es_admin()
    and (o.reposicion = 'al_recibir' or o.devolucion is not null)
  order by (o.recibido_at is not null), o.actualizado_at desc
  limit 500
$$;

-- El depósito marca que recibió (o no) una devolución. En modo sombra solo se registra; en modo real, 'bien' repone stock.
create or replace function public.rev_devolucion_recibir(p_id bigint, p_estado text, p_por text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.rev_es_admin() then raise exception 'Sin permiso.'; end if;
  if p_estado not in ('bien', 'danado', 'no_llego') then raise exception 'Estado inválido.'; end if;
  update public.rev_ordenes
     set recibido_at = now(), recibido_estado = p_estado, recibido_por = nullif(trim(p_por), ''), actualizado_at = now()
   where id = p_id and (reposicion = 'al_recibir' or devolucion is not null) and recibido_at is null;
  if not found then raise exception 'No encontré esa devolución pendiente.'; end if;
end $$;

-- Deshacer una marca hecha por error.
create or replace function public.rev_devolucion_deshacer(p_id bigint) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.rev_es_admin() then raise exception 'Sin permiso.'; end if;
  update public.rev_ordenes set recibido_at = null, recibido_estado = null, recibido_por = null, actualizado_at = now() where id = p_id;
end $$;

revoke all on function public.rev_devoluciones_listar(), public.rev_devolucion_recibir(bigint, text, text), public.rev_devolucion_deshacer(bigint) from public, anon;
grant execute on function public.rev_devoluciones_listar(), public.rev_devolucion_recibir(bigint, text, text), public.rev_devolucion_deshacer(bigint) to authenticated;
