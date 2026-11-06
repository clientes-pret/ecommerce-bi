-- Costo con IVA de SKUs vendidos en Mercado Libre que ya no están en el catálogo de Tiendanube (packs, productos dados
-- de baja). El scorecard de ML (reposicion/scorecard_ml_sync.py) los usa solo si el SKU no está en ninguna de las dos tiendas.
-- Se cargó de la planilla de rentabilidad de ML. Sin políticas: solo lo lee el servicio (jobs).
create table if not exists scorecard_costos_extra (
  sku            text primary key,
  costo          numeric(18,2) not null check (costo > 0),
  fuente         text not null default 'planilla_ml',
  actualizado_en timestamptz not null default now()
);
alter table scorecard_costos_extra enable row level security;
revoke all on scorecard_costos_extra from anon, authenticated;
grant select, insert, update, delete on scorecard_costos_extra to service_role;
