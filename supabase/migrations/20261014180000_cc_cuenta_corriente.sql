create extension if not exists pg_trgm with schema extensions;
-- Cuenta corriente de proveedores + recepciones de depósito
-- Prefijo cc_ para no chocar con otras tablas del proyecto.

create table if not exists cc_perfiles (
  id uuid primary key references auth.users(id) on delete cascade,
  nombre text not null,
  rol text not null check (rol in ('deposito','admin','dueno')),
  created_at timestamptz not null default now()
);

create or replace function cc_rol() returns text
language sql stable security definer set search_path = public as $$
  select rol from cc_perfiles where id = auth.uid()
$$;

create table if not exists cc_proveedores (
  id bigint generated always as identity primary key,
  nombre text not null unique,
  grupo text,                       -- para agrupar variantes (M/A/Z TEXTIL FEDERAL) sin perder la cuenta original
  activo boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists cc_productos (
  sku text primary key,
  producto text,
  color text,
  medida text,
  categoria text,
  busqueda text generated always as (lower(coalesce(sku,'')||' '||coalesce(producto,'')||' '||coalesce(color,'')||' '||coalesce(medida,''))) stored
);
create index if not exists cc_productos_busqueda_idx on cc_productos using gin (busqueda extensions.gin_trgm_ops);

-- códigos que usa cada proveedor en sus remitos -> SKU propio (se aprende solo)
create table if not exists cc_alias_sku (
  proveedor_id bigint references cc_proveedores(id) on delete cascade,
  codigo_proveedor text not null,
  sku text not null references cc_productos(sku) on delete cascade,
  primary key (proveedor_id, codigo_proveedor)
);

create table if not exists cc_recepciones (
  id uuid primary key default gen_random_uuid(),
  proveedor_id bigint not null references cc_proveedores(id),
  fecha timestamptz not null default now(),
  nro_remito text,
  bultos int,
  recibido_completo boolean not null default true,
  notas text,
  estado text not null default 'pendiente_control'
    check (estado in ('borrador','pendiente_control','controlada','con_diferencia','anulada')),
  remito_paths text[] not null default '{}',
  ocr_estado text not null default 'sin_leer' check (ocr_estado in ('sin_leer','leyendo','ok','error')),
  ocr_json jsonb,
  cargado_por uuid references auth.users(id) default auth.uid(),
  controlado_por uuid references auth.users(id),
  controlado_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists cc_recepciones_prov_idx on cc_recepciones(proveedor_id, fecha desc);

create table if not exists cc_recepcion_items (
  id bigint generated always as identity primary key,
  recepcion_id uuid not null references cc_recepciones(id) on delete cascade,
  sku text references cc_productos(sku),
  descripcion text,                -- texto tal cual del remito si no hay SKU resuelto
  codigo_proveedor text,
  cant_remito int,                 -- lo que dice el remito (leído por IA)
  cant_contada int not null,       -- lo que Mati contó
  created_at timestamptz not null default now()
);
create index if not exists cc_items_recepcion_idx on cc_recepcion_items(recepcion_id);

-- Libro de cuenta corriente (lo que hoy es la Hoja1 de Jackie)
create table if not exists cc_movimientos (
  id bigint generated always as identity primary key,
  proveedor_id bigint not null references cc_proveedores(id),
  fecha date not null,
  tipo text not null check (tipo in ('comprobante','nota_credito','nota_debito','pago','ajuste','saldo_inicial','otro')),
  detalle text,                    -- texto original (FC-0067 00057466, PAGO, etc.)
  numero text,
  ingreso numeric(18,2) not null default 0,   -- aumenta la deuda
  pago numeric(18,2) not null default 0,      -- baja la deuda
  recepcion_id uuid references cc_recepciones(id),
  control_estado text not null default 'sin_control'
    check (control_estado in ('sin_control','ok','con_diferencia')),
  medio_pago text check (medio_pago in ('transferencia','cheque','efectivo','otro')),
  cheque_nro text,
  cheque_fecha_cobro date,
  cheque_cobrado boolean,
  nota text,
  origen text not null default 'app' check (origen in ('planilla','app')),
  orden_original int,
  creado_por uuid references auth.users(id) default auth.uid(),
  created_at timestamptz not null default now()
);
create index if not exists cc_mov_prov_idx on cc_movimientos(proveedor_id, fecha, id);

create or replace view cc_cuenta_corriente with (security_invoker = true) as
select m.*,
  sum(m.ingreso - m.pago) over (partition by m.proveedor_id order by m.fecha, m.id) as saldo
from cc_movimientos m;

create or replace view cc_saldos with (security_invoker = true) as
select p.id as proveedor_id, p.nombre, p.grupo, p.activo,
  coalesce(sum(m.ingreso),0) as ingresos,
  coalesce(sum(m.pago),0) as pagos,
  coalesce(sum(m.ingreso - m.pago),0) as saldo,
  coalesce(sum(m.pago) filter (where m.medio_pago='cheque' and m.cheque_cobrado is false),0) as cheques_no_cobrados,
  max(m.fecha) filter (where m.tipo='pago') as ultimo_pago,
  max(m.fecha) filter (where m.tipo<>'pago') as ultimo_comprobante
from cc_proveedores p left join cc_movimientos m on m.proveedor_id = p.id
group by p.id;

-- Vista segura para depósito: nunca ve importes
create or replace view cc_recepciones_resumen with (security_invoker = true) as
select r.*, p.nombre as proveedor,
  (select coalesce(sum(i.cant_contada),0) from cc_recepcion_items i where i.recepcion_id=r.id) as unidades_contadas
from cc_recepciones r join cc_proveedores p on p.id=r.proveedor_id;

-- RLS
alter table cc_perfiles enable row level security;
alter table cc_proveedores enable row level security;
alter table cc_productos enable row level security;
alter table cc_alias_sku enable row level security;
alter table cc_recepciones enable row level security;
alter table cc_recepcion_items enable row level security;
alter table cc_movimientos enable row level security;

create policy perfiles_self on cc_perfiles for select to authenticated using (id = auth.uid() or cc_rol() in ('admin','dueno'));
create policy prov_read on cc_proveedores for select to authenticated using (cc_rol() is not null);
create policy prov_write on cc_proveedores for all to authenticated using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));
create policy prod_read on cc_productos for select to authenticated using (cc_rol() is not null);
create policy prod_write on cc_productos for all to authenticated using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));
create policy alias_rw on cc_alias_sku for all to authenticated using (cc_rol() is not null) with check (cc_rol() is not null);

create policy rec_read on cc_recepciones for select to authenticated
  using (cc_rol() in ('admin','dueno') or (cc_rol()='deposito' and created_at > now() - interval '14 days'));
create policy rec_insert on cc_recepciones for insert to authenticated with check (cc_rol() is not null and cargado_por = auth.uid());
create policy rec_update on cc_recepciones for update to authenticated
  using (cc_rol() in ('admin','dueno') or (cc_rol()='deposito' and cargado_por = auth.uid() and estado in ('borrador','pendiente_control')))
  with check (cc_rol() in ('admin','dueno') or (cc_rol()='deposito' and cargado_por = auth.uid()));

create policy items_read on cc_recepcion_items for select to authenticated
  using (exists (select 1 from cc_recepciones r where r.id = recepcion_id));
create policy items_write on cc_recepcion_items for all to authenticated
  using (exists (select 1 from cc_recepciones r where r.id = recepcion_id and (cc_rol() in ('admin','dueno') or (r.cargado_por = auth.uid() and r.estado in ('borrador','pendiente_control')))))
  with check (exists (select 1 from cc_recepciones r where r.id = recepcion_id and (cc_rol() in ('admin','dueno') or (r.cargado_por = auth.uid() and r.estado in ('borrador','pendiente_control')))));

create policy mov_all on cc_movimientos for all to authenticated using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));

-- Storage privado para fotos de remitos
insert into storage.buckets (id, name, public) values ('remitos','remitos',false) on conflict do nothing;
create policy remitos_insert on storage.objects for insert to authenticated with check (bucket_id='remitos' and cc_rol() is not null);
create policy remitos_read on storage.objects for select to authenticated using (bucket_id='remitos' and (cc_rol() in ('admin','dueno') or owner = auth.uid()));
