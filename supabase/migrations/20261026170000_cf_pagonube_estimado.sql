-- Pago Nube calculado desde las órdenes de Tienda Nube (cashflow_sync.py), hasta que se importe el reporte.
-- Pago Nube cobra con reglas fijas (tasa por medio de pago, costo por cantidad de cuotas, disponible a 1 día en
-- transferencias y 7 en tarjetas): el job estima cada cobro con los descuentos del último reporte importado.
alter table cf_pagonube_movimientos add column if not exists cuotas int;
alter table cf_pagonube_movimientos add column if not exists estimado boolean not null default false;  -- true = calculado desde la orden
