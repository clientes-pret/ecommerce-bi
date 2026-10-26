-- De dónde salió cada costo: 'manual' (cargado en el panel, o mes de un costo recurrente) o 'planilla_historial'
-- (historial de gastos dic 2024 - oct 2026 importado de la planilla de pagos, un total por rubro y mes).
-- Costos y el P&L suman todo; el Cashflow ignora 'planilla_historial' porque esos pagos ya los ve por MercadoPago
-- o los proyecta con los costos recurrentes, y sumarlos los contaría dos veces.
alter table cc_costos add column if not exists origen text not null default 'manual';
alter table cc_costos drop constraint if exists cc_costos_origen_chk;
alter table cc_costos add constraint cc_costos_origen_chk check (origen in ('manual', 'planilla_historial'));
create index if not exists cc_costos_origen_idx on cc_costos(origen) where origen <> 'manual';
