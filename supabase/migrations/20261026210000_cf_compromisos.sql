-- Agenda de pagos: compromisos con fecha de débito (cheques y e-cheqs emitidos, cuotas de préstamos, alquiler,
-- impuestos, transferencias programadas). El Cashflow los usa para proyectar las salidas; cuando se debitan se
-- marcan como pagados (o anulados). Los cheques emitidos se cargaron de la planilla "CASH FLOW PH" (origen 'planilla').
create table if not exists cf_compromisos (
  id bigint generated always as identity primary key,
  fecha date not null,                                   -- día en que sale la plata (vencimiento del cheque)
  proveedor_id bigint references cc_proveedores(id),
  beneficiario text not null,                            -- proveedor o a quién se le paga
  imputacion text not null default 'mercaderia'
    check (imputacion in ('mercaderia', 'prestamo', 'impuestos', 'alquiler', 'sueldos', 'servicios', 'otro')),
  medio text not null default 'transferencia'
    check (medio in ('echeq', 'cheque', 'transferencia', 'debito', 'efectivo', 'otro')),
  banco text,
  cheque_nro text,
  monto numeric(18,2) not null check (monto > 0),
  estado text not null default 'pendiente' check (estado in ('pendiente', 'pagado', 'anulado')),
  estado_fecha date,                                      -- cuándo se marcó pagado / anulado
  nota text,
  origen text not null default 'manual' check (origen in ('manual', 'planilla')),
  clave_origen text unique,                              -- para que una importación repetida no duplique
  creado_por uuid references auth.users(id) default auth.uid(),
  created_at timestamptz not null default now()
);
create index if not exists cf_compromisos_fecha on cf_compromisos (estado, fecha);

alter table cf_compromisos enable row level security;
drop policy if exists cf_compromisos_all on cf_compromisos;
create policy cf_compromisos_all on cf_compromisos for all to authenticated
  using (cc_rol() in ('admin','dueno')) with check (cc_rol() in ('admin','dueno'));

revoke all on cf_compromisos from anon;
grant select, insert, update, delete on cf_compromisos to authenticated, service_role;
