-- Costos en dólares: cada costo (suelto o que se repite) se carga en pesos o en USD.
-- Un costo en USD se pasa a pesos con el dólar oficial BNA (venta) del día 1 de su mes; cc_costos.monto queda
-- siempre en pesos, así Cashflow, P&L y los resúmenes de Costos siguen sumando igual.
-- La cotización la trae el navegador al abrir Costos (argentinadatos.com, un valor por día, incluidos fines de
-- semana y feriados) y la guarda en cc_cotizaciones_usd; no hay cron.
-- Si el día 1 todavía no llegó (o falta la cotización), se usa la última anterior y el costo queda provisorio
-- (cotizacion_fecha < periodo); cc_costos_al_dia lo recalcula solo cuando aparece la del día 1.
create table if not exists cc_cotizaciones_usd (
  fecha date primary key,
  compra numeric(12,2),
  venta numeric(12,2) not null check (venta > 0),
  fuente text not null default 'argentinadatos.com · oficial BNA',
  actualizado_at timestamptz not null default now()
);
alter table cc_cotizaciones_usd enable row level security;
drop policy if exists cotiz_all on cc_cotizaciones_usd;
create policy cotiz_all on cc_cotizaciones_usd for all to authenticated
  using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));

-- moneda: en qué se cargó. monto_usd: el importe original si es USD. cotizacion/cotizacion_fecha: el dólar usado.
alter table cc_costos
  add column if not exists moneda text not null default 'ARS' check (moneda in ('ARS','USD')),
  add column if not exists monto_usd numeric(18,2) check (monto_usd >= 0),
  add column if not exists cotizacion numeric(12,2),
  add column if not exists cotizacion_fecha date;
alter table cc_costos drop constraint if exists cc_costos_usd_chk;
alter table cc_costos add constraint cc_costos_usd_chk check (moneda = 'ARS' or monto_usd is not null);

-- En los que se repiten, monto (pesos) de un costo en USD es la estimación con el dólar de hoy: lo usa la
-- proyección de Cashflow. Cada mes cargado en cc_costos se convierte con el dólar del día 1 de ese mes.
alter table cc_costos_recurrentes
  add column if not exists moneda text not null default 'ARS' check (moneda in ('ARS','USD')),
  add column if not exists monto_usd numeric(18,2) check (monto_usd >= 0),
  add column if not exists cotizacion numeric(12,2),
  add column if not exists cotizacion_fecha date;
alter table cc_costos_recurrentes drop constraint if exists cc_costos_rec_usd_chk;
alter table cc_costos_recurrentes add constraint cc_costos_rec_usd_chk check (moneda = 'ARS' or monto_usd is not null);

-- Dólar a usar para una fecha: el de ese día o, si no está, el último anterior.
create or replace function cc_dolar_al(p date) returns table (venta numeric, fecha date)
language sql stable set search_path = public as $$
  select c.venta, c.fecha from cc_cotizaciones_usd c where c.fecha <= p order by c.fecha desc limit 1
$$;

-- Convierte a pesos al guardar. Recalcula si es nuevo, si cambió el importe, el mes o la moneda, o si
-- cotizacion viene en null (así se fuerza el recálculo). Un costo en pesos queda sin datos de dólar.
create or replace function cc_costos_convertir() returns trigger
language plpgsql security invoker set search_path = public as $$
declare v numeric; f date;
begin
  if new.moneda = 'USD' then
    if tg_op = 'INSERT' or new.cotizacion is null or new.monto_usd is distinct from old.monto_usd
       or new.periodo is distinct from old.periodo or new.moneda is distinct from old.moneda then
      select d.venta, d.fecha into v, f from cc_dolar_al(new.periodo) d;
      if v is null then raise exception 'No hay cotización del dólar para %. Abrí Costos para que se actualice.', to_char(new.periodo, 'MM/YYYY'); end if;
      new.cotizacion := v; new.cotizacion_fecha := f; new.monto := round(new.monto_usd * v, 2);
    end if;
  else
    new.monto_usd := null; new.cotizacion := null; new.cotizacion_fecha := null;
  end if;
  return new;
end $$;
drop trigger if exists cc_costos_convertir on cc_costos;
create trigger cc_costos_convertir before insert or update on cc_costos for each row execute function cc_costos_convertir();

-- Igual para los que se repiten, pero con el dólar de hoy (es solo la estimación por mes).
create or replace function cc_costos_rec_convertir() returns trigger
language plpgsql security invoker set search_path = public as $$
declare v numeric; f date;
begin
  if new.moneda = 'USD' then
    if tg_op = 'INSERT' or new.cotizacion is null or new.monto_usd is distinct from old.monto_usd
       or new.moneda is distinct from old.moneda then
      select d.venta, d.fecha into v, f from cc_dolar_al((now() at time zone 'America/Argentina/Buenos_Aires')::date) d;
      if v is null then raise exception 'No hay cotización del dólar. Abrí Costos para que se actualice.'; end if;
      new.cotizacion := v; new.cotizacion_fecha := f; new.monto := round(new.monto_usd * v, 2);
    end if;
  else
    new.monto_usd := null; new.cotizacion := null; new.cotizacion_fecha := null;
  end if;
  return new;
end $$;
drop trigger if exists cc_costos_rec_convertir on cc_costos_recurrentes;
create trigger cc_costos_rec_convertir before insert or update on cc_costos_recurrentes for each row execute function cc_costos_rec_convertir();

-- Igual que antes, más: cada mes se carga con la moneda del costo (el trigger lo convierte con el dólar de ese
-- mes) y al final se recalculan los costos en USD cuyo dólar ya no es el que corresponde (provisorios que ya
-- tienen el del día 1, o cotizaciones corregidas) y la estimación de hoy de los que se repiten.
create or replace function cc_costos_al_dia() returns integer
language plpgsql security invoker set search_path = public as $$
declare
  r cc_costos_recurrentes%rowtype;
  tope date;
  m date;
  n integer := 0;
begin
  for r in
    select * from cc_costos_recurrentes
    where desde <= cc_mes_actual()
      and (generado_hasta is null or generado_hasta < least(coalesce(hasta, cc_mes_actual()), cc_mes_actual()))
    for update
  loop
    tope := least(coalesce(r.hasta, cc_mes_actual()), cc_mes_actual());
    m := coalesce((r.generado_hasta + interval '1 month')::date, r.desde);
    while m <= tope loop
      insert into cc_costos (tipo, categoria, concepto, monto, periodo, nota, recurrente_id, moneda, monto_usd)
      values (r.tipo, r.categoria, r.concepto, r.monto, m, r.nota, r.id, r.moneda, r.monto_usd)
      on conflict (recurrente_id, periodo) where recurrente_id is not null do nothing;
      n := n + 1;
      m := (m + interval '1 month')::date;
    end loop;
    update cc_costos_recurrentes set generado_hasta = tope where id = r.id;
  end loop;

  update cc_costos c set cotizacion = null
   where c.moneda = 'USD'
     and exists (select 1 from cc_dolar_al(c.periodo) d where d.venta is distinct from c.cotizacion or d.fecha is distinct from c.cotizacion_fecha);
  update cc_costos_recurrentes c set cotizacion = null
   where c.moneda = 'USD'
     and exists (select 1 from cc_dolar_al((now() at time zone 'America/Argentina/Buenos_Aires')::date) d
                 where d.venta is distinct from c.cotizacion or d.fecha is distinct from c.cotizacion_fecha);
  return n;
end $$;

-- Cambiar el monto desde un mes, ahora con moneda (null = la que ya tenía). p_monto está en esa moneda.
drop function if exists cc_costo_recurrente_cambiar_monto(bigint, date, numeric);
create or replace function cc_costo_recurrente_cambiar_monto(p_id bigint, p_desde date, p_monto numeric, p_moneda text default null) returns bigint
language plpgsql security invoker set search_path = public as $$
declare
  r cc_costos_recurrentes%rowtype;
  nuevo bigint;
  mon text;
  ars numeric;
  usd numeric;
begin
  p_desde := date_trunc('month', p_desde)::date;
  select * into r from cc_costos_recurrentes where id = p_id for update;
  if not found then raise exception 'No existe el costo recurrente %', p_id; end if;
  if p_monto is null or p_monto < 0 then raise exception 'Monto inválido'; end if;
  if r.hasta is not null and p_desde > r.hasta then raise exception 'Ese costo termina antes de ese mes'; end if;
  mon := coalesce(p_moneda, r.moneda);
  if mon not in ('ARS','USD') then raise exception 'Moneda inválida'; end if;
  ars := case when mon = 'ARS' then p_monto else 0 end;   -- en USD el trigger pisa el monto en pesos
  usd := case when mon = 'USD' then p_monto end;

  if p_desde <= r.desde then
    -- Cambia desde el principio: se corrige el mismo costo y todos sus meses.
    update cc_costos_recurrentes set monto = ars, moneda = mon, monto_usd = usd, cotizacion = null where id = p_id;
    update cc_costos set monto = ars, moneda = mon, monto_usd = usd, cotizacion = null where recurrente_id = p_id;
    return p_id;
  end if;

  insert into cc_costos_recurrentes (tipo, categoria, concepto, monto, desde, hasta, generado_hasta, nota, moneda, monto_usd)
  values (r.tipo, r.categoria, r.concepto, ars, p_desde, r.hasta,
          case when r.generado_hasta >= p_desde then r.generado_hasta end, r.nota, mon, usd)
  returning id into nuevo;
  update cc_costos set monto = ars, moneda = mon, monto_usd = usd, cotizacion = null, recurrente_id = nuevo
   where recurrente_id = p_id and periodo >= p_desde;
  update cc_costos_recurrentes
     set hasta = (p_desde - interval '1 month')::date,
         generado_hasta = least(generado_hasta, (p_desde - interval '1 month')::date)
   where id = p_id;
  return nuevo;
end $$;

revoke all on cc_cotizaciones_usd from anon;
grant select, insert, update, delete on cc_cotizaciones_usd to authenticated, service_role;
revoke all on function cc_dolar_al(date), cc_costos_al_dia(), cc_costo_recurrente_cambiar_monto(bigint, date, numeric, text) from public, anon;
grant execute on function cc_dolar_al(date), cc_costos_al_dia(), cc_costo_recurrente_cambiar_monto(bigint, date, numeric, text) to authenticated, service_role;
