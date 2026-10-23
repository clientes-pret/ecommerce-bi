-- Adelantos de sueldo: plata que sale antes y se descuenta del sueldo de un mes.
-- No es un costo extra: en el P&L el sueldo del mes no cambia; en el cashflow la salida pasa a la fecha
-- del adelanto y el sueldo de descuenta_mes sale por ese monto menos.
create table if not exists cc_adelantos_sueldo (
  id bigint generated always as identity primary key,
  fecha date not null,                                                        -- día en que se dio
  empleado text not null,
  monto numeric(18,2) not null check (monto > 0),
  descuenta_mes date not null check (extract(day from descuenta_mes) = 1),    -- sueldo del que se descuenta (día 1)
  nota text,
  creado_por uuid references auth.users(id) default auth.uid(),
  created_at timestamptz not null default now()
);

alter table cc_adelantos_sueldo enable row level security;
drop policy if exists adelantos_all on cc_adelantos_sueldo;
create policy adelantos_all on cc_adelantos_sueldo for all to authenticated
  using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));

revoke all on cc_adelantos_sueldo from anon;
grant select, insert, update, delete on cc_adelantos_sueldo to authenticated, service_role;
