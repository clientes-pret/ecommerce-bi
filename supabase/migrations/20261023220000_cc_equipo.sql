-- Equipo: datos de las personas que trabajan (ingreso, cumpleaños, dirección, sueldo, hora extra, contratación).
-- Datos personales: solo los ven usuarios logueados con rol admin/dueño (RLS vía cc_rol()); nada para anon.
create table if not exists cc_equipo (
  id bigint generated always as identity primary key,
  nombre text not null,
  area text,                               -- Marketing, Depósito, Venta del salón, Administración… (texto libre)
  puesto text,
  fecha_ingreso date,
  cumpleanos date,                         -- se usa día y mes; el año puede ser el real o cualquiera
  direccion text,
  telefono text,
  sueldo numeric(18,2) check (sueldo is null or sueldo >= 0),             -- mensual
  valor_hora_extra numeric(18,2) check (valor_hora_extra is null or valor_hora_extra >= 0),
  contratacion text check (contratacion in ('relacion_dependencia','prestador_servicios')),
  activo boolean not null default true,
  fecha_egreso date,
  nota text,
  creado_por uuid references auth.users(id) default auth.uid(),
  created_at timestamptz not null default now(),
  actualizado_en timestamptz not null default now()
);

alter table cc_equipo enable row level security;
drop policy if exists equipo_all on cc_equipo;
create policy equipo_all on cc_equipo for all to authenticated
  using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));

revoke all on cc_equipo from anon;
grant select, insert, update, delete on cc_equipo to authenticated, service_role;
