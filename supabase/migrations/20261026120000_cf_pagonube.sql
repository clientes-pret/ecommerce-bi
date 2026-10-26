-- Pago Nube no tiene API: el reporte "movimientos financieros" que se descarga del admin de Tiendanube
-- (Pago Nube → exportar) se importa desde el Cashflow. Cada fila es un cobro, devolución, contracargo,
-- nota de crédito/débito o retiro, con la fecha en que queda disponible y el neto ya descontados tasas e impuestos.
create table if not exists cf_pagonube_movimientos (
  tienda text not null check (tienda in ('pret', 'lavan')),
  clave text not null,                     -- descripción|número de venta|fecha de creación|neto|n.º de repetición en el archivo
  descripcion text not null,               -- Venta, Devolución total/parcial, Contracargo, Nota de crédito/débito, Retiro
  numero_venta text,
  medio text,
  fecha_creacion timestamp not null,       -- hora de Argentina, como viene en el reporte
  fecha_disponible timestamp,              -- "Disponible para transferir"; vacío en devoluciones y retiros (salen al crearse)
  monto_venta numeric(18,2),
  neto numeric(18,2) not null,
  importado_por uuid references auth.users(id) default auth.uid(),
  importado_at timestamptz not null default now(),
  primary key (tienda, clave)
);
create index if not exists cf_pagonube_disponible on cf_pagonube_movimientos (tienda, coalesce(fecha_disponible, fecha_creacion));

alter table cf_pagonube_movimientos enable row level security;
drop policy if exists cf_pagonube_all on cf_pagonube_movimientos;
create policy cf_pagonube_all on cf_pagonube_movimientos for all to authenticated
  using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));

revoke all on cf_pagonube_movimientos from anon;
grant select, insert, update, delete on cf_pagonube_movimientos to authenticated, service_role;
