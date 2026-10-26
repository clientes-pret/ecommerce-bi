-- Atribución de costos: cada costo dice de qué marcas, canales y áreas es (una o varias de cada una).
-- Si es de varias marcas/canales ("mix"), se reparte según las ventas de cada marca+canal en el mismo mes
-- (v_scorecard_mensual); si ninguna de las elegidas vendió ese mes (ej. un mes futuro), en partes iguales entre
-- las que alguna vez vendieron (Casa Laván no tiene local), o entre todas si ninguna vendió nunca. Si es de varias áreas,
-- en partes iguales. Los costos ya cargados quedan en todas (mix) y se reparten por ventas.
-- El reparto no se guarda: lo calcula la vista v_costos_atribucion, así siempre usa las ventas al día.
alter table cc_costos
  add column if not exists marcas text[] not null default array['pret','lavan'],
  add column if not exists canales text[] not null default array['tn','ml','local'],
  add column if not exists areas text[] not null default array['ventas','marketing','operaciones','atencion'];
alter table cc_costos_recurrentes
  add column if not exists marcas text[] not null default array['pret','lavan'],
  add column if not exists canales text[] not null default array['tn','ml','local'],
  add column if not exists areas text[] not null default array['ventas','marketing','operaciones','atencion'];

alter table cc_costos drop constraint if exists cc_costos_atrib_chk;
alter table cc_costos add constraint cc_costos_atrib_chk check (
  cardinality(marcas) > 0 and marcas <@ array['pret','lavan']
  and cardinality(canales) > 0 and canales <@ array['tn','ml','local']
  and cardinality(areas) > 0 and areas <@ array['ventas','marketing','operaciones','atencion']);
alter table cc_costos_recurrentes drop constraint if exists cc_costos_rec_atrib_chk;
alter table cc_costos_recurrentes add constraint cc_costos_rec_atrib_chk check (
  cardinality(marcas) > 0 and marcas <@ array['pret','lavan']
  and cardinality(canales) > 0 and canales <@ array['tn','ml','local']
  and cardinality(areas) > 0 and areas <@ array['ventas','marketing','operaciones','atencion']);

-- Una fila por costo × marca × canal × área con la parte del monto (en pesos) que le toca.
-- por_ventas = false cuando no hubo ventas de las combinaciones elegidas ese mes y se repartió en partes iguales.
create or replace view v_costos_atribucion with (security_invoker = true) as
with combos as (
  select c.id, c.periodo, m.marca, k.canal
  from cc_costos c cross join unnest(c.marcas) m(marca) cross join unnest(c.canales) k(canal)
), vtas as (
  select periodo, marca, canal, sum(ventas) as ventas from v_scorecard_mensual group by 1, 2, 3
), activos as (
  select marca, canal from vtas group by 1, 2 having sum(ventas) > 0
), pesos as (
  select x.id, x.marca, x.canal, greatest(coalesce(v.ventas, 0), 0) as ventas,
         sum(greatest(coalesce(v.ventas, 0), 0)) over (partition by x.id) as tot,
         a.marca is not null as activo,
         count(a.marca) over (partition by x.id) as nact,
         count(*) over (partition by x.id) as n
  from combos x
  left join vtas v on v.periodo = x.periodo and v.marca = x.marca and v.canal = x.canal
  left join activos a on a.marca = x.marca and a.canal = x.canal
)
select c.id as costo_id, c.periodo, c.tipo, c.categoria, p.marca, p.canal, a.area,
       c.monto * (case when p.tot > 0 then p.ventas / p.tot
                       when p.nact > 0 then case when p.activo then 1.0 / p.nact else 0 end
                       else 1.0 / p.n end) / cardinality(c.areas) as monto,
       p.tot > 0 as por_ventas
from cc_costos c
join pesos p on p.id = c.id
cross join unnest(c.areas) a(area);

-- Igual que en 20261025120000, más la atribución: cada mes cargado copia marcas/canales/áreas del costo que se repite.
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
      insert into cc_costos (tipo, categoria, concepto, monto, periodo, nota, recurrente_id, moneda, monto_usd, marcas, canales, areas)
      values (r.tipo, r.categoria, r.concepto, r.monto, m, r.nota, r.id, r.moneda, r.monto_usd, r.marcas, r.canales, r.areas)
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

-- Igual que en 20261025120000; el costo nuevo que se crea al cambiar el monto conserva la atribución.
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

  insert into cc_costos_recurrentes (tipo, categoria, concepto, monto, desde, hasta, generado_hasta, nota, moneda, monto_usd, marcas, canales, areas)
  values (r.tipo, r.categoria, r.concepto, ars, p_desde, r.hasta,
          case when r.generado_hasta >= p_desde then r.generado_hasta end, r.nota, mon, usd, r.marcas, r.canales, r.areas)
  returning id into nuevo;
  update cc_costos set monto = ars, moneda = mon, monto_usd = usd, cotizacion = null, recurrente_id = nuevo
   where recurrente_id = p_id and periodo >= p_desde;
  update cc_costos_recurrentes
     set hasta = (p_desde - interval '1 month')::date,
         generado_hasta = least(generado_hasta, (p_desde - interval '1 month')::date)
   where id = p_id;
  return nuevo;
end $$;

revoke all on v_costos_atribucion from anon;
grant select on v_costos_atribucion to authenticated, service_role;
