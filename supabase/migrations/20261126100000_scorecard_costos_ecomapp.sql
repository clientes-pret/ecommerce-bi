-- Lista de costos (con IVA) por SKU de Ecomm-App: es la que usa el dueño para calcular la rentabilidad de Mercado
-- Libre. El scorecard de ML (reposicion/scorecard_ml_sync.py) la usa primero; Tiendanube y scorecard_costos_extra
-- quedan de respaldo para los SKU que no figuren. Se carga con reposicion/scorecard_costos_ecomapp.py desde el archivo
-- "Actualización de productos" de Ecomm-App. Sin políticas: solo la lee el servicio (jobs).
create table if not exists scorecard_costos_ecomapp (
  sku            text primary key,
  costo          numeric(18,2) not null check (costo > 0),
  actualizado_en timestamptz not null default now()
);
alter table scorecard_costos_ecomapp enable row level security;
revoke all on scorecard_costos_ecomapp from anon, authenticated;
grant select, insert, update, delete on scorecard_costos_ecomapp to service_role;
