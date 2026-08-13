-- Caché permanente de publicaciones ML ya resueltas por id, incluidas las
-- cerradas/pausadas (que ya no aparecen en el catálogo activo). Se usa para
-- clasificar correctamente ventas históricas como Full/no-Full sin volver a
-- pedir la misma publicación cerrada en cada corrida de weekly_calc.py
-- (una publicación cerrada no vuelve a cambiar de logistic_type).

create table repo_items_ml_cache (
  item_id        text primary key,
  canal          text not null check (canal in ('ml_pret','ml_lavan')),
  sku            text,
  logistic_type  text,
  resuelto_at    timestamptz not null default now()
);
