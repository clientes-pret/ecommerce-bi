-- Búsqueda profunda: "Lo pedí al proveedor" (30/10/2026, pedido del usuario).
-- En el depósito es una de las opciones de "¿Dónde estaba?" al cerrar como apareció: el pedido se puede
-- despachar, pero esas unidades no aparecieron en el depósito, así que cuentan como Stock perdido (con su
-- costo) y se muestran aparte en el panel. Una misma búsqueda puede terminar mezclada (parte apareció,
-- parte se pidió, parte no está): `perdidas` acumula todo lo que no apareció y `perdido_at` es la última vez
-- que se sumaron pérdidas (el panel agrupa por ese mes).

alter table busqueda_profunda add column if not exists al_proveedor integer not null default 0;  -- de `perdidas`, cuántas se pidieron
alter table busqueda_profunda add column if not exists perdido_at timestamptz;

update busqueda_profunda set perdido_at = cerrado_at where perdidas > 0 and perdido_at is null;
