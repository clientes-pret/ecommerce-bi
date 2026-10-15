-- En qué están expresadas las cantidades de un pedido: unidades sueltas o
-- bultos (cajas/packs del proveedor). Es un dato del pedido entero, no de
-- cada línea — un pedido va todo en unidades o todo en bultos. Los pedidos
-- ya existentes quedan como 'unidades', que es como se armaron siempre.

alter table repo_pedidos
  add column unidad_medida text not null default 'unidades'
    check (unidad_medida in ('unidades', 'bultos'));
