-- Órdenes de compra (OC).
-- Cada pedido de reposición (repo_pedidos) ES una orden de compra y su número es su id:
-- el id 1 es el primer pedido que se hizo, así que la numeración sigue sin reiniciarse
-- ("OC 124 - Palette"). No hace falta otra columna ni otra secuencia.

-- 1) Relacionar movimientos (facturas, pagos, notas) y recepciones de mercadería con una OC,
--    además del proveedor que ya tienen. Es opcional: lo anterior sigue igual.
alter table cc_movimientos add column if not exists oc_id bigint references repo_pedidos(id) on delete set null;
alter table cc_recepciones add column if not exists oc_id bigint references repo_pedidos(id) on delete set null;
create index if not exists cc_movimientos_oc_idx on cc_movimientos (oc_id) where oc_id is not null;
create index if not exists cc_recepciones_oc_idx on cc_recepciones (oc_id) where oc_id is not null;

-- 2) Checklist de ingreso: lo que se tilda a mano en Gestión gana sobre la estimación
--    automática (reposicion/recepciones.py ignora las líneas con recibido_manual = true).
alter table repo_pedido_items add column if not exists recibido_manual boolean not null default false;

-- Marca cuánto ingresó de una línea de una OC. Solo admin/dueño (el panel entra como usuario
-- logueado y repo_pedido_items solo tiene permiso de lectura para él, por eso va por función).
-- Recalcula el estado de la OC con el mismo criterio que la recepción automática y la API:
--   todas las líneas completas → recibido_completo; alguna con algo → recibido_parcial;
--   ninguna → enviado (acá se puede volver atrás si se tildó por error).
create or replace function oc_marcar_recibido(p_item_id bigint, p_cantidad integer)
returns void
language plpgsql security definer set search_path = public as $$
declare
  v_pedido bigint;
  v_pedida integer;
  v_estado text;
begin
  if coalesce(cc_rol(), '') not in ('admin', 'dueno') then
    raise exception 'Sin permiso para marcar recepciones';
  end if;
  if p_cantidad is null or p_cantidad < 0 then
    raise exception 'La cantidad recibida no puede ser negativa';
  end if;

  select i.pedido_id, i.cantidad_pedida, p.estado
    into v_pedido, v_pedida, v_estado
    from repo_pedido_items i join repo_pedidos p on p.id = i.pedido_id
   where i.id = p_item_id;
  if not found then
    raise exception 'La línea no existe';
  end if;
  if v_estado = 'cancelado' then
    raise exception 'La OC está cancelada';
  end if;

  update repo_pedido_items
     set cantidad_recibida = p_cantidad,
         recibido_completo = (p_cantidad >= v_pedida),
         recibido_at       = case when p_cantidad >= v_pedida then coalesce(recibido_at, now()) else null end,
         recibido_manual   = true
   where id = p_item_id;

  update repo_pedidos
     set estado = case
       when not exists (select 1 from repo_pedido_items where pedido_id = v_pedido and not recibido_completo)
         then 'recibido_completo'
       when exists (select 1 from repo_pedido_items
                     where pedido_id = v_pedido and (recibido_completo or cantidad_recibida > 0))
         then 'recibido_parcial'
       else 'enviado' end
   where id = v_pedido;
end $$;

revoke all on function oc_marcar_recibido(bigint, integer) from public, anon;
grant execute on function oc_marcar_recibido(bigint, integer) to authenticated, service_role;

-- "Marcar todo recibido": cierra de una vez todas las líneas pendientes de una OC.
create or replace function oc_marcar_todo_recibido(p_pedido_id bigint)
returns void
language plpgsql security definer set search_path = public as $$
declare
  v_estado text;
begin
  if coalesce(cc_rol(), '') not in ('admin', 'dueno') then
    raise exception 'Sin permiso para marcar recepciones';
  end if;
  select estado into v_estado from repo_pedidos where id = p_pedido_id;
  if not found then
    raise exception 'La OC no existe';
  end if;
  if v_estado = 'cancelado' then
    raise exception 'La OC está cancelada';
  end if;

  update repo_pedido_items
     set cantidad_recibida = cantidad_pedida,
         recibido_completo = true,
         recibido_at       = coalesce(recibido_at, now()),
         recibido_manual   = true
   where pedido_id = p_pedido_id and not recibido_completo;

  update repo_pedidos set estado = 'recibido_completo' where id = p_pedido_id;
end $$;

revoke all on function oc_marcar_todo_recibido(bigint) from public, anon;
grant execute on function oc_marcar_todo_recibido(bigint) to authenticated, service_role;

-- 3) Texto escrito a mano que se agrega al título de la OC (ej. "OC 124 - Alcoyana - Sábanas 1200 hilos").
--    Sale en el título del PDF, en el historial y en Gestión. Opcional.
alter table repo_pedidos add column if not exists titulo_extra text;
