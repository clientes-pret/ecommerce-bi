-- Descuento real en Ecomm-App de las ventas de revendedores (19/11/2026). Solo corre en modo 'real' (rev_config.modo).
--   descontado_at       → cuándo Ecomm-App descontó la venta (clave única rev-venta-<orden>-<sku>).
--   repuesto_at         → cuándo se devolvió el stock (cancelada antes de despachar, o devolución recibida en buen estado).
--   ecomm_reposicion    → respuesta de Ecomm-App a esa devolución de stock.
-- (ecomm_clave / ecomm_respuesta ya existían para el descuento.)
alter table public.rev_ordenes add column if not exists descontado_at    timestamptz;
alter table public.rev_ordenes add column if not exists repuesto_at      timestamptz;
alter table public.rev_ordenes add column if not exists ecomm_reposicion jsonb;
