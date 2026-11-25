-- Marketing: scorecard de Pret a Home · Tienda Nube para el equipo de Marketing (rol `marketing`).
-- Las tablas del scorecard solo las lee admin/dueño (cc_rol). Estas dos funciones dejan leer únicamente Pret + Tienda Nube
-- (ventas, órdenes, neta, costo de productos, envío y la pauta de Meta y Google Ads); nada de Casa Laván, Mercado Libre ni el local.

create or replace function mkt_scorecard_dias(p_desde date)
returns table (fecha date, ventas numeric, ordenes integer, facturacion_neta numeric, costo_productos numeric, costo_envio numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select s.fecha, s.ventas, s.ordenes, s.facturacion_neta, s.costo_productos, s.costo_envio
  from scorecard_canal_dia s
  where s.marca = 'pret' and s.canal = 'tn' and s.fecha >= p_desde
  order by s.fecha;
end $$;
revoke execute on function mkt_scorecard_dias(date) from public, anon;
grant execute on function mkt_scorecard_dias(date) to authenticated;

create or replace function mkt_scorecard_ads(p_desde date)
returns table (fecha date, plataforma text, inversion numeric)
language plpgsql stable security definer set search_path = public as $$
#variable_conflict use_column
begin
  if mkt_rol() is null then raise exception 'Sin permiso'; end if;
  return query
  select a.fecha, a.plataforma, a.inversion
  from scorecard_ads_dia a
  where a.marca = 'pret' and a.plataforma in ('meta', 'google') and a.fecha >= p_desde
  order by a.fecha, a.plataforma;
end $$;
revoke execute on function mkt_scorecard_ads(date) from public, anon;
grant execute on function mkt_scorecard_ads(date) to authenticated;
