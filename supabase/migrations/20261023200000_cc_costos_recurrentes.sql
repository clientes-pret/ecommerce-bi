-- Costos que se repiten (alquiler sin fin, una auditoría por 3 meses, etc.).
-- Una fila por costo recurrente; los meses se materializan en cc_costos (una fila por mes, con recurrente_id)
-- para que el P&L y la pantalla de Costos sigan sumando cc_costos como hasta ahora.
-- Los meses se cargan hasta el mes en curso cuando alguien abre Costos (cc_costos_al_dia), sin cron.
create table if not exists cc_costos_recurrentes (
  id bigint generated always as identity primary key,
  tipo text not null check (tipo in ('fijo','variable')),
  categoria text not null,
  concepto text,
  monto numeric(18,2) not null check (monto >= 0),
  desde date not null check (extract(day from desde) = 1),               -- primer mes (día 1)
  hasta date check (hasta is null or extract(day from hasta) = 1),        -- último mes incluido; null = sin fin
  generado_hasta date,                                                     -- último mes ya cargado en cc_costos
  nota text,
  creado_por uuid references auth.users(id) default auth.uid(),
  created_at timestamptz not null default now(),
  check (hasta is null or hasta >= desde)
);

alter table cc_costos add column if not exists recurrente_id bigint references cc_costos_recurrentes(id) on delete cascade;
create unique index if not exists cc_costos_recurrente_periodo_uq on cc_costos(recurrente_id, periodo) where recurrente_id is not null;

alter table cc_costos_recurrentes enable row level security;
drop policy if exists costos_rec_all on cc_costos_recurrentes;
create policy costos_rec_all on cc_costos_recurrentes for all to authenticated
  using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));

-- Mes en curso en hora de Argentina (día 1).
create or replace function cc_mes_actual() returns date language sql stable as $$
  select date_trunc('month', now() at time zone 'America/Argentina/Buenos_Aires')::date
$$;

-- Carga en cc_costos los meses que falten de cada costo recurrente, hasta el mes en curso.
-- Avanza desde generado_hasta: si alguien borra a mano un mes ya cargado, no vuelve a aparecer.
-- security invoker: corre con los permisos (y el RLS) de quien la llama.
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
      insert into cc_costos (tipo, categoria, concepto, monto, periodo, nota, recurrente_id)
      values (r.tipo, r.categoria, r.concepto, r.monto, m, r.nota, r.id)
      on conflict (recurrente_id, periodo) where recurrente_id is not null do nothing;
      n := n + 1;
      m := (m + interval '1 month')::date;
    end loop;
    update cc_costos_recurrentes set generado_hasta = tope where id = r.id;
  end loop;
  return n;
end $$;

-- Cambiar el monto de un costo recurrente desde un mes: los meses anteriores quedan con el monto viejo.
-- Corta el costo actual el mes anterior y crea uno nuevo desde p_desde con el mismo final;
-- los meses ya cargados desde p_desde pasan al nuevo con el monto nuevo.
create or replace function cc_costo_recurrente_cambiar_monto(p_id bigint, p_desde date, p_monto numeric) returns bigint
language plpgsql security invoker set search_path = public as $$
declare
  r cc_costos_recurrentes%rowtype;
  nuevo bigint;
begin
  p_desde := date_trunc('month', p_desde)::date;
  select * into r from cc_costos_recurrentes where id = p_id for update;
  if not found then raise exception 'No existe el costo recurrente %', p_id; end if;
  if p_monto is null or p_monto < 0 then raise exception 'Monto inválido'; end if;
  if r.hasta is not null and p_desde > r.hasta then raise exception 'Ese costo termina antes de ese mes'; end if;

  if p_desde <= r.desde then
    -- Cambia desde el principio: se corrige el mismo costo y todos sus meses.
    update cc_costos_recurrentes set monto = p_monto where id = p_id;
    update cc_costos set monto = p_monto where recurrente_id = p_id;
    return p_id;
  end if;

  insert into cc_costos_recurrentes (tipo, categoria, concepto, monto, desde, hasta, generado_hasta, nota)
  values (r.tipo, r.categoria, r.concepto, p_monto, p_desde, r.hasta,
          case when r.generado_hasta >= p_desde then r.generado_hasta end, r.nota)
  returning id into nuevo;
  update cc_costos set monto = p_monto, recurrente_id = nuevo where recurrente_id = p_id and periodo >= p_desde;
  update cc_costos_recurrentes
     set hasta = (p_desde - interval '1 month')::date,
         generado_hasta = least(generado_hasta, (p_desde - interval '1 month')::date)
   where id = p_id;
  return nuevo;
end $$;

-- Terminar un costo recurrente: p_ultimo es el último mes que se paga. Borra los meses cargados después.
create or replace function cc_costo_recurrente_terminar(p_id bigint, p_ultimo date) returns void
language plpgsql security invoker set search_path = public as $$
declare
  r cc_costos_recurrentes%rowtype;
begin
  p_ultimo := date_trunc('month', p_ultimo)::date;
  select * into r from cc_costos_recurrentes where id = p_id for update;
  if not found then raise exception 'No existe el costo recurrente %', p_id; end if;
  if p_ultimo < r.desde then raise exception 'El último mes no puede ser anterior al primero'; end if;
  update cc_costos_recurrentes set hasta = p_ultimo, generado_hasta = least(generado_hasta, p_ultimo) where id = p_id;
  delete from cc_costos where recurrente_id = p_id and periodo > p_ultimo;
end $$;

revoke all on cc_costos, cc_costos_recurrentes from anon;
grant select, insert, update, delete on cc_costos, cc_costos_recurrentes to authenticated, service_role;
revoke all on function cc_mes_actual(), cc_costos_al_dia(), cc_costo_recurrente_cambiar_monto(bigint, date, numeric),
  cc_costo_recurrente_terminar(bigint, date) from public, anon;
grant execute on function cc_mes_actual(), cc_costos_al_dia(), cc_costo_recurrente_cambiar_monto(bigint, date, numeric),
  cc_costo_recurrente_terminar(bigint, date) to authenticated, service_role;
