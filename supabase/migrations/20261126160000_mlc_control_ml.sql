-- Control diario de Mercado Libre (reclamos, envíos Flex/Colecta, peso y medidas) para Pret a Home y Casa Laván.
-- Pantalla: reposicion/dashboard/operaciones/mercadolibre.html. Los datos los escribe reposicion/ml_control_sync.py.

-- 1) Rol nuevo "mercadolibre": ve solo esta pantalla.
--    cc_rol() lo ignora (igual que a "marketing"): ninguna policy ni función existente le abre acceso, ni siquiera
--    las que solo piden "tener algún rol". Lo de esta pantalla se decide con mlc_rol().
alter table cc_perfiles drop constraint if exists cc_perfiles_rol_check;
alter table cc_perfiles add constraint cc_perfiles_rol_check check (rol in ('deposito','admin','dueno','marketing','mercadolibre'));

create or replace function cc_rol() returns text
language sql stable security definer set search_path = public as $$
  select rol from cc_perfiles where id = auth.uid() and rol not in ('marketing','mercadolibre')
$$;

create or replace function mlc_rol() returns text
language sql stable security definer set search_path = public as $$
  select rol from cc_perfiles where id = auth.uid() and rol in ('admin','dueno','mercadolibre')
$$;
revoke execute on function mlc_rol() from public, anon;
grant execute on function mlc_rol() to authenticated;

-- 2) Límites para los colores (los puede cambiar admin/dueño).
create table if not exists mlc_config (
  clave text primary key,
  valor numeric not null,
  nota text
);
insert into mlc_config (clave, valor, nota) values
  ('limite_reclamos',    1,   'Reclamos (% de ventas, 60 días) desde el que está en rojo'),
  ('limite_cancel',      0.5, 'Cancelaciones (% de ventas, 60 días) desde el que está en rojo'),
  ('limite_demoras',     8,   'Envíos con demora (% de ventas, 60 días) desde el que está en rojo'),
  ('aviso',              75,  'Se pinta amarillo desde este porcentaje del límite'),
  ('exposicion_buena',   97,  'Envíos a tiempo en la semana (%) para exposición excelente (verde)'),
  ('exposicion_mala',    90,  'Envíos a tiempo en la semana (%) por debajo del cual la exposición es muy mala (rojo)')
on conflict (clave) do nothing;

-- 3) La foto de cada día, por cuenta.
create table if not exists mlc_resumen_dia (
  fecha date not null,                    -- día en hora de Argentina
  cuenta text not null check (cuenta in ('pret','lavan')),
  nivel text,
  medalla text,
  proteccion_hasta timestamptz,
  protegida boolean,                      -- true = ML no está contando estos valores (se guardan los reales)
  ventas_60d integer,
  reclamos_60d integer,  reclamos_tasa numeric,     -- tasas en %
  demoras_60d integer,   demoras_tasa numeric,
  cancel_60d integer,    cancel_tasa numeric,
  reclamos_abiertos integer, reclamos_afectan integer, reclamos_responder integer,
  flex_semana integer,    flex_demorados integer,     -- desde el lunes
  colecta_semana integer, colecta_demorados integer,
  alertas_pendientes integer, alertas_30d integer, riesgo_medidas integer,
  actualizado_at timestamptz not null default now(),
  primary key (fecha, cuenta)
);

create table if not exists mlc_reclamos (
  id bigint primary key,
  cuenta text not null,
  tipo text, etapa text, estado text,
  motivo_id text, motivo text,
  recurso text, order_id bigint, item_id text, titulo text, sku text,
  creado timestamptz, actualizado timestamptz,
  vence timestamptz, accion text,         -- respuesta obligatoria nuestra que vence primero
  afecta text, resolucion text,
  visto_at timestamptz not null default now()
);
create index if not exists mlc_reclamos_estado on mlc_reclamos (cuenta, estado, creado desc);

create table if not exists mlc_envios (
  id bigint primary key,
  cuenta text not null,
  logistica text,
  order_id bigint, item_id text, titulo text, sku text,
  vendido timestamptz, limite timestamptz,
  sla text,                               -- on_time / delayed / early (según Mercado Libre)
  demoras text[] not null default '{}',
  estado text, subestado text,
  final boolean not null default false,
  actualizado_at timestamptz not null default now()
);
create index if not exists mlc_envios_limite on mlc_envios (limite desc);
create index if not exists mlc_envios_pend on mlc_envios (cuenta, vendido) where not final;

create table if not exists mlc_alertas (
  id text primary key,                    -- id de la moderación en Mercado Libre
  cuenta text not null,
  item_id text not null,
  fecha timestamptz not null,
  motivo text, reincidencia boolean not null default false,
  titulo text, sku text,
  estado_item text, subestado_item text[] not null default '{}',
  pendiente boolean not null default false,
  declarado text, paquete text, link text,
  actualizado_at timestamptz not null default now()
);
create index if not exists mlc_alertas_fecha on mlc_alertas (fecha desc);

create table if not exists mlc_riesgo (
  item_id text primary key,
  cuenta text not null,
  titulo text, sku text,
  tipo text not null,                     -- ml_midio_mas / datos_distintos
  declarado text, otro text,
  dif_peso numeric, dif_volumen numeric,
  vendidos integer, logistica text, link text,
  actualizado_at timestamptz not null default now()
);

-- 4) Lo que Mati marca como revisado (queda el historial).
create table if not exists mlc_revisiones (
  id bigint generated always as identity primary key,
  item_id text not null,
  cuenta text not null,
  nota text,
  autor uuid default auth.uid() references auth.users(id) on delete set null,
  autor_nombre text,
  created_at timestamptz not null default now()
);
create index if not exists mlc_revisiones_item on mlc_revisiones (item_id, created_at desc);

-- El nombre y la fecha los pone la base: no se pueden cargar a mano desde la pantalla.
create or replace function mlc_revisiones_antes() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is not null then
    new.autor := auth.uid();
    new.autor_nombre := (select nombre from cc_perfiles where id = auth.uid());
    new.created_at := now();
  end if;
  return new;
end $$;
drop trigger if exists mlc_revisiones_antes on mlc_revisiones;
create trigger mlc_revisiones_antes before insert on mlc_revisiones for each row execute function mlc_revisiones_antes();

-- 5) Permisos: leen admin, dueño y mercadolibre; los datos los escribe solo el job (service_role).
do $$
declare t text;
begin
  foreach t in array array['mlc_config','mlc_resumen_dia','mlc_reclamos','mlc_envios','mlc_alertas','mlc_riesgo','mlc_revisiones'] loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists %I on %I', t || '_lectura', t);
    execute format('create policy %I on %I for select to authenticated using (mlc_rol() is not null)', t || '_lectura', t);
    execute format('revoke all on %I from anon, authenticated', t);
    execute format('grant select on %I to authenticated', t);
    execute format('grant select, insert, update, delete on %I to service_role', t);
  end loop;
end $$;

drop policy if exists mlc_config_edicion on mlc_config;
create policy mlc_config_edicion on mlc_config for update to authenticated
  using (mlc_rol() in ('admin','dueno')) with check (mlc_rol() in ('admin','dueno'));
grant update (valor) on mlc_config to authenticated;

drop policy if exists mlc_revisiones_alta on mlc_revisiones;
create policy mlc_revisiones_alta on mlc_revisiones for insert to authenticated
  with check (mlc_rol() is not null and autor = auth.uid());
drop policy if exists mlc_revisiones_baja on mlc_revisiones;
create policy mlc_revisiones_baja on mlc_revisiones for delete to authenticated
  using (mlc_rol() in ('admin','dueno') or autor = auth.uid());
grant insert, delete on mlc_revisiones to authenticated;
