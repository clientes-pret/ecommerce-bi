-- Marketing: tráfico y conversión por canal (orgánico, Meta, Google Ads, directo, email), con GA4 como fuente.
-- Lo carga reposicion/mkt_sync.py: por día, fuente y medio, con dos puntos de vista de atribución:
--   · 'sesion'  = último contacto: la fuente de la sesión en la que se compró (lo que GA4 muestra por defecto).
--   · 'primer'  = primer contacto: la fuente por la que ese usuario llegó por primera vez a la tienda.
-- Se guarda lo crudo (fuente y medio tal cual los trae GA4) y el canal se decide al leer con mkt_canal(),
-- así cambiar la agrupación no obliga a volver a cargar nada.

create table if not exists mkt_trafico_dia (
  fecha date not null,
  modelo text not null check (modelo in ('sesion', 'primer')),
  fuente text not null,
  medio text not null,
  sesiones integer not null default 0,
  sesiones_activas integer not null default 0,     -- sesiones con interacción real (GA4 "engaged")
  usuarios_nuevos integer not null default 0,
  carritos integer not null default 0,             -- add_to_cart
  checkouts integer not null default 0,            -- begin_checkout
  compras integer not null default 0,
  ingresos numeric not null default 0,             -- purchaseRevenue de GA4 (incluye envío)
  primary key (fecha, modelo, fuente, medio)
);
create index if not exists mkt_trafico_fecha on mkt_trafico_dia (fecha desc);

alter table mkt_trafico_dia enable row level security;
drop policy if exists mkt_trafico_lectura on mkt_trafico_dia;
create policy mkt_trafico_lectura on mkt_trafico_dia for select to authenticated using (mkt_rol() is not null);
revoke all on mkt_trafico_dia from anon;
grant select on mkt_trafico_dia to authenticated;
grant select, insert, update, delete on mkt_trafico_dia to service_role;

-- Canal de una fuente/medio de GA4. Cinco canales con nombre y "otros" para lo que no se puede clasificar.
--   meta     = anuncios de Instagram/Facebook/Audience Network (medio "paid")
--   google   = Google Ads (google / cpc; incluye Performance Max, que GA4 llama "Cross-network")
--   directo  = (direct) / (none)
--   email    = medio email
--   organico = búsqueda orgánica, redes sociales sin pagar y links desde Instagram/Facebook/YouTube/TikTok…
--   otros    = sin dato ((not set)), pasarelas de pago, reseñas y demás referidos
create or replace function mkt_canal(p_fuente text, p_medio text)
returns text language sql immutable as $$
  select case
    when lower(coalesce(p_medio, '')) = 'paid' and lower(coalesce(p_fuente, '')) in ('ig', 'fb', 'an', 'facebook', 'instagram', 'meta') then 'meta'
    when lower(coalesce(p_fuente, '')) = 'google' and lower(coalesce(p_medio, '')) = 'cpc' then 'google'
    when lower(coalesce(p_fuente, '')) = '(direct)' and lower(coalesce(p_medio, '')) in ('(none)', '(not set)', '') then 'directo'
    when lower(coalesce(p_medio, '')) = 'email' then 'email'
    when lower(coalesce(p_medio, '')) in ('organic', 'social') then 'organico'
    when lower(coalesce(p_medio, '')) = 'referral'
         and lower(coalesce(p_fuente, '')) ~ '(instagram|facebook|youtube|tiktok|pinterest|whatsapp|linktr|t\.co|twitter)' then 'organico'
    else 'otros'
  end
$$;

-- Totales por canal y punto de vista de atribución. La ventana es de p_dias días completos hasta ayer (igual que el resto de Marketing).
create or replace function mkt_trafico_canales(p_dias integer default 30)
returns table (modelo text, canal text, sesiones bigint, sesiones_activas bigint, usuarios_nuevos bigint, carritos bigint, checkouts bigint, compras bigint, ingresos numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select t.modelo, mkt_canal(t.fuente, t.medio), sum(t.sesiones)::bigint, sum(t.sesiones_activas)::bigint, sum(t.usuarios_nuevos)::bigint,
         sum(t.carritos)::bigint, sum(t.checkouts)::bigint, sum(t.compras)::bigint, round(sum(t.ingresos), 0)
  from mkt_trafico_dia t
  where t.fecha between v_hoy - p_dias and v_hoy - 1
  group by 1, 2
  order by 1, 3 desc;
end $$;
revoke execute on function mkt_trafico_canales(integer) from public, anon;
grant execute on function mkt_trafico_canales(integer) to authenticated;

-- Evolución día por día de cada canal (último contacto).
create or replace function mkt_trafico_serie(p_dias integer default 30)
returns table (fecha date, canal text, sesiones bigint, compras bigint, ingresos numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select t.fecha, mkt_canal(t.fuente, t.medio), sum(t.sesiones)::bigint, sum(t.compras)::bigint, round(sum(t.ingresos), 0)
  from mkt_trafico_dia t
  where t.modelo = 'sesion' and t.fecha between v_hoy - p_dias and v_hoy - 1
  group by 1, 2
  order by 1, 2;
end $$;
revoke execute on function mkt_trafico_serie(integer) from public, anon;
grant execute on function mkt_trafico_serie(integer) to authenticated;

-- Detalle por fuente y medio (último contacto), para ver qué hay adentro de cada canal.
create or replace function mkt_trafico_fuentes(p_dias integer default 30)
returns table (fuente text, medio text, canal text, sesiones bigint, carritos bigint, compras bigint, ingresos numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select t.fuente, t.medio, mkt_canal(t.fuente, t.medio), sum(t.sesiones)::bigint, sum(t.carritos)::bigint, sum(t.compras)::bigint, round(sum(t.ingresos), 0)
  from mkt_trafico_dia t
  where t.modelo = 'sesion' and t.fecha between v_hoy - p_dias and v_hoy - 1
  group by 1, 2, 3
  having sum(t.sesiones) > 0
  order by 4 desc, 1, 2
  limit 60;
end $$;
revoke execute on function mkt_trafico_fuentes(integer) from public, anon;
grant execute on function mkt_trafico_fuentes(integer) to authenticated;

-- Control contra la realidad: pedidos pagos de Tienda Nube Pret en el mismo período (del scorecard). Sirve para saber
-- qué parte de las compras ve GA4. Solo devuelve el total, no el detalle del scorecard.
create or replace function mkt_trafico_pedidos(p_dias integer default 30)
returns table (pedidos bigint, ventas numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
declare v_hoy date := (now() at time zone 'America/Argentina/Buenos_Aires')::date;
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select coalesce(sum(s.ordenes), 0)::bigint, round(coalesce(sum(s.ventas), 0), 0)
  from scorecard_canal_dia s
  where s.marca = 'pret' and s.canal = 'tn' and s.fecha between v_hoy - p_dias and v_hoy - 1;
end $$;
revoke execute on function mkt_trafico_pedidos(integer) from public, anon;
grant execute on function mkt_trafico_pedidos(integer) to authenticated;
