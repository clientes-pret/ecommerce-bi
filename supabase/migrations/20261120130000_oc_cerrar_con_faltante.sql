-- "Cerrar con faltante": dar por terminada una OC que recibió casi todo y cuyo resto ya no va a llegar.
--
-- Por qué así: lo que falta de una OC no se puede marcar "recibido" (inventaría unidades que no llegaron) ni
-- cancelar (una OC que ya recibió mercadería figuraría como si nunca hubiera llegado nada). Lo que se hace es dar
-- por completas las líneas que quedaron abiertas SIN tocar cuánto se pidió ni cuánto se recibió, y dejar anotado que
-- se cerró con faltante. Como esas líneas pasan a recibido_completo = true, el resto del sistema las deja de contar
-- solo: ya no son "pedido en camino" (punto rojo / "Ocultar ya solicitados"), la recepción automática
-- (recepciones.py) no las toca y la pantalla de recepción del depósito no las ofrece. Es reversible (oc_reabrir).

alter table repo_pedidos
  add column if not exists cerrada_con_faltante_at  timestamptz,
  add column if not exists cerrada_con_faltante_por uuid references auth.users(id);
alter table repo_pedido_items
  add column if not exists cerrado_con_faltante boolean not null default false;   -- línea que se cerró sin recibirse completa

create or replace function oc_cerrar_con_faltante(p_pedido_id bigint)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  v_estado text; v_cerrada timestamptz; v_lineas integer; v_unidades integer;
begin
  if coalesce(cc_rol(), '') not in ('admin', 'dueno') then
    raise exception 'Sin permiso para cerrar una OC';
  end if;
  select estado, cerrada_con_faltante_at into v_estado, v_cerrada from repo_pedidos where id = p_pedido_id for update;
  if not found then raise exception 'La OC no existe'; end if;
  if v_estado = 'cancelado' then raise exception 'La OC está cancelada'; end if;
  if v_cerrada is not null and v_estado = 'recibido_completo' then raise exception 'La OC ya está cerrada con faltante'; end if;

  select count(*), coalesce(sum(greatest(cantidad_pedida - cantidad_recibida, 0)), 0)
    into v_lineas, v_unidades
    from repo_pedido_items where pedido_id = p_pedido_id and not recibido_completo;
  if v_lineas = 0 then raise exception 'Esta OC no tiene nada pendiente'; end if;
  if not exists (select 1 from repo_pedido_items where pedido_id = p_pedido_id and cantidad_recibida > 0) then
    raise exception 'No llegó nada de esta OC: si no va a llegar, cancelala en vez de cerrarla';
  end if;

  update repo_pedido_items
     set recibido_completo = true,
         recibido_at = coalesce(recibido_at, now()),
         cerrado_con_faltante = true,
         recibido_manual = true
   where pedido_id = p_pedido_id and not recibido_completo;

  update repo_pedidos
     set estado = 'recibido_completo', cerrada_con_faltante_at = now(), cerrada_con_faltante_por = auth.uid()
   where id = p_pedido_id;

  return jsonb_build_object('lineas', v_lineas, 'unidades', v_unidades);
end $$;

create or replace function oc_reabrir(p_pedido_id bigint)
returns void
language plpgsql security definer set search_path = public as $$
declare v_cerrada timestamptz;
begin
  if coalesce(cc_rol(), '') not in ('admin', 'dueno') then
    raise exception 'Sin permiso para reabrir una OC';
  end if;
  select cerrada_con_faltante_at into v_cerrada from repo_pedidos where id = p_pedido_id for update;
  if not found then raise exception 'La OC no existe'; end if;
  if v_cerrada is null then raise exception 'Esta OC no está cerrada con faltante'; end if;

  update repo_pedido_items
     set recibido_completo = (cantidad_recibida >= cantidad_pedida),
         recibido_at = case when cantidad_recibida >= cantidad_pedida then recibido_at else null end,
         cerrado_con_faltante = false
   where pedido_id = p_pedido_id and cerrado_con_faltante;

  update repo_pedidos set
    cerrada_con_faltante_at = null, cerrada_con_faltante_por = null,
    estado = case
      when not exists (select 1 from repo_pedido_items where pedido_id = p_pedido_id and not recibido_completo) then 'recibido_completo'
      when exists (select 1 from repo_pedido_items where pedido_id = p_pedido_id and (recibido_completo or cantidad_recibida > 0)) then 'recibido_parcial'
      else 'enviado' end
  where id = p_pedido_id and estado <> 'cancelado';
end $$;

-- Si alguien corrige a mano una línea que se había cerrado con faltante, esa línea deja de estar "cerrada":
-- misma función de antes (20261117150000) más apagar la marca de la línea.
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
         recibido_manual   = true,
         cerrado_con_faltante = false
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

revoke all on function oc_cerrar_con_faltante(bigint) from public, anon;
grant execute on function oc_cerrar_con_faltante(bigint) to authenticated, service_role;
revoke all on function oc_reabrir(bigint) from public, anon;
grant execute on function oc_reabrir(bigint) to authenticated, service_role;
revoke all on function oc_marcar_recibido(bigint, integer) from public, anon;
grant execute on function oc_marcar_recibido(bigint, integer) to authenticated, service_role;
