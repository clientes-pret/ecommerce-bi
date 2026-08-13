-- Persiste la categoría de producto (Cortinas / Sábanas / Acolchados y
-- Edredones / Toallas y Toallones / Almohadas / Bazar) — clasificada por
-- classify_product() en core.py, que ya existía pero no se usaba en ningún
-- lado. Mismo patrón que proveedor_auto/proveedor_manual: el override
-- manual gana si no es null.

alter table repo_productos
  add column if not exists categoria_auto   text,
  add column if not exists categoria_manual text;
