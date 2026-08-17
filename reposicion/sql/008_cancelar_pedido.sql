-- Soporte para cancelar un pedido (borrado lógico, no físico — mantiene
-- historial) y trazabilidad de quién/cuándo lo canceló, mismo patrón que
-- descontinuado_at/descontinuado_por en repo_productos.

alter table repo_pedidos
  add column cancelado_at  timestamptz,
  add column cancelado_por text;
