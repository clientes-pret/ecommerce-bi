-- El aviso "Modo prueba" del portal sale de rev_mi_cuenta().modo, que devolvía el modo general del sistema. Desde que la
-- sincronización es por revendedor (20261119190000), CASAPINAR está en vivo aunque el sistema siga en prueba y seguía
-- viendo el aviso (reportado por el usuario, 19/11/2026). Ahora el modo es el de la cuenta:
--   'solo_publicaciones' → cuenta propia que solo publica (sin aviso: no hay stock ni ventas que simular)
--   'real'               → se le sincroniza el stock (por la cuenta o porque todo el sistema está en real/activo)
--   'sombra'             → en prueba
create or replace function public.rev_mi_cuenta()
returns table (id bigint, nickname text, ml_user_id bigint, estado text, conectado_at timestamptz, conectada boolean, modo text)
language sql stable security definer set search_path = public as $$
  select r.id, r.nickname, r.ml_user_id, r.estado, r.conectado_at, (r.access_token is not null),
         case
           when r.solo_publicaciones then 'solo_publicaciones'
           when r.sincroniza_stock
             or (select c.valor #>> '{}' from public.rev_config c where c.clave = 'modo') in ('real', 'activo') then 'real'
           else 'sombra'
         end
  from public.rev_revendedores r where r.id = public.rev_mi_id()
$$;
