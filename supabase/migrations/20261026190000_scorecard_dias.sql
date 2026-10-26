-- Scorecard directo en Supabase (deja de usar la base del artifact "Scorecard Pret & Laván").
-- La rutina diaria "Scorecard diario Pret & Laván" guarda cada día con scorecard_guardar_dia(fecha, campos): el mismo
-- documento que guardaba en el artifact (claves tn_pret_home, facturacion_neta_ml_pret_home, meta_ads_pret_home_ars, ...;
-- "pending" = sin dato todavía). La función mezcla los campos con lo que ya había ese día y vuelve a armar las filas de
-- scorecard_canal_dia / scorecard_ads_dia / scorecard_notas, que es lo que leen Ventas, P&L y Cashflow
-- (misma conversión que scorecard/migrar_desde_artifact.py).

create table if not exists scorecard_dias (
  fecha          date primary key,
  data           jsonb not null,
  version        int not null default 1,
  actualizado_en timestamptz not null default now()
);

alter table scorecard_dias enable row level security;
drop policy if exists scorecard_dias_leer on scorecard_dias;
create policy scorecard_dias_leer on scorecard_dias for select to authenticated using (cc_rol() in ('admin','dueno'));
revoke all on scorecard_dias from anon;
grant select on scorecard_dias to authenticated;
grant select, insert, update, delete on scorecard_dias to service_role;

-- Número real o null ("pending", textos, vacíos).
create or replace function scorecard_num(v jsonb) returns numeric language sql immutable as $$
  select case when jsonb_typeof(v) = 'number' then v::text::numeric end
$$;

create or replace function scorecard_aplicar_dia(p_fecha date, d jsonb) returns void
language plpgsql security definer set search_path = public as $$
declare
  c record;
  a record;
  venta jsonb;
  loc jsonb;
  v numeric;
begin
  for c in select * from (values ('tn_pret_home','pret','tn'), ('ml_pret_home','pret','ml'),
                                 ('tn_casa_lavan','lavan','tn'), ('ml_casa_lavan','lavan','ml')) x(clave, marca, canal) loop
    venta := d -> c.clave;
    if jsonb_typeof(venta) = 'object' or scorecard_num(d -> ('facturacion_neta_' || c.clave)) is not null
       or scorecard_num(d -> ('costo_productos_' || c.clave)) is not null or scorecard_num(d -> ('costo_envio_' || c.clave)) is not null then
      insert into scorecard_canal_dia (fecha, marca, canal, ventas, ordenes, facturacion_neta, costo_productos, costo_envio, fuente, actualizado_en)
      values (p_fecha, c.marca, c.canal,
              case when jsonb_typeof(venta) = 'object' then scorecard_num(venta -> 'revenue') end,
              case when jsonb_typeof(venta) = 'object' then scorecard_num(venta -> 'orders')::int end,
              scorecard_num(d -> ('facturacion_neta_' || c.clave)),
              scorecard_num(d -> ('costo_productos_' || c.clave)),
              scorecard_num(d -> ('costo_envio_' || c.clave)), 'rutina', now())
      on conflict (fecha, marca, canal) do update set
        ventas = excluded.ventas, ordenes = excluded.ordenes, facturacion_neta = excluded.facturacion_neta,
        costo_productos = excluded.costo_productos, costo_envio = excluded.costo_envio, fuente = 'rutina', actualizado_en = now();
    end if;
  end loop;

  loc := d -> 'local_fisico_pret';
  if jsonb_typeof(loc) = 'object' then
    insert into scorecard_canal_dia (fecha, marca, canal, ventas, local_efectivo, local_otros_medios, local_gastado, fuente, actualizado_en)
    values (p_fecha, 'pret', 'local', coalesce(scorecard_num(loc -> 'total_recibido'), scorecard_num(loc -> 'revenue')),
            scorecard_num(loc -> 'efectivo'), scorecard_num(loc -> 'otros_medios'), scorecard_num(loc -> 'gastado'), 'rutina', now())
    on conflict (fecha, marca, canal) do update set
      ventas = excluded.ventas, local_efectivo = excluded.local_efectivo, local_otros_medios = excluded.local_otros_medios,
      local_gastado = excluded.local_gastado, fuente = 'rutina', actualizado_en = now();
  end if;

  for a in select * from (values ('meta_ads_pret_home_ars','pret','meta'), ('meta_ads_casa_lavan_ars','lavan','meta'),
                                 ('google_ads_pret_home_ars','pret','google'), ('google_ads_casa_lavan_ars','lavan','google'),
                                 ('ml_ads_pret','pret','ml'), ('ml_ads_lavan','lavan','ml')) x(clave, marca, plataforma) loop
    v := scorecard_num(d -> a.clave);
    if v is not null then
      insert into scorecard_ads_dia (fecha, marca, plataforma, inversion, fuente, actualizado_en)
      values (p_fecha, a.marca, a.plataforma, v, 'rutina', now())
      on conflict (fecha, marca, plataforma) do update set inversion = excluded.inversion, fuente = 'rutina', actualizado_en = now();
    end if;
  end loop;

  if jsonb_typeof(d -> 'error') = 'string' and btrim(d ->> 'error') <> '' then
    insert into scorecard_notas (fecha, nota, actualizado_en) values (p_fecha, d ->> 'error', now())
    on conflict (fecha) do update set nota = excluded.nota, actualizado_en = now();
  end if;
end $$;

-- Guarda (mezcla) los campos de un día y actualiza las tablas del panel. Devuelve el documento completo del día.
create or replace function scorecard_guardar_dia(p_fecha date, p_campos jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare d jsonb;
begin
  if jsonb_typeof(p_campos) <> 'object' then
    raise exception 'p_campos tiene que ser un objeto JSON';
  end if;
  insert into scorecard_dias (fecha, data) values (p_fecha, p_campos || jsonb_build_object('date', p_fecha::text))
  on conflict (fecha) do update set data = scorecard_dias.data || excluded.data,
    version = scorecard_dias.version + 1, actualizado_en = now()
  returning data into d;
  perform scorecard_aplicar_dia(p_fecha, d);
  return d;
end $$;

revoke all on function scorecard_aplicar_dia(date, jsonb) from public, anon, authenticated;
revoke all on function scorecard_guardar_dia(date, jsonb) from public, anon, authenticated;
grant execute on function scorecard_aplicar_dia(date, jsonb) to service_role;
grant execute on function scorecard_guardar_dia(date, jsonb) to service_role;
