-- Cashflow con datos reales de MercadoPago (cuentas de Pret a Home y Casa Laván).
-- Lo carga reposicion/cashflow_sync.py todos los días desde el "reporte de dinero liberado" de MercadoPago
-- (cada movimiento de la cuenta con su saldo) y desde /v1/payments/search (lo que falta liberar).
-- Pago Nube no tiene API: sus saldos se cargan a mano en cf_saldos (fuente 'manual').

-- Movimientos de la cuenta MercadoPago sumados por día y categoría:
--   ventas_ml     ventas de Mercado Libre ya liberadas, neto de comisiones, impuestos, envíos, reclamos y devoluciones
--   otros_cobros  otros cobros que entran a la cuenta (Tienda Nube pagado con MP, transferencias recibidas, QR, rendimientos)
--   credito       cuotas de Mercado Crédito
--   deudas_ml     deudas que Mercado Libre cobra del saldo (factura de ML: publicidad, cargos no retenidos en la venta)
--   pagos_mp      pagos y transferencias hechos desde la cuenta
--   retiros       retiros a bancos (plata que se mueve entre cuentas propias, no es un gasto)
create table if not exists cf_liberaciones (
  fecha date not null,
  cuenta text not null check (cuenta in ('mp_pret', 'mp_lavan')),
  categoria text not null check (categoria in ('ventas_ml', 'otros_cobros', 'credito', 'deudas_ml', 'pagos_mp', 'retiros')),
  credito numeric(18,2) not null default 0,
  debito numeric(18,2) not null default 0,
  cantidad int not null default 0,
  actualizado_at timestamptz not null default now(),
  primary key (fecha, cuenta, categoria)
);

-- Salidas individuales desde la cuenta (pagos_mp, credito y deudas_ml), para mostrar qué fue cada una.
create table if not exists cf_mp_egresos (
  cuenta text not null check (cuenta in ('mp_pret', 'mp_lavan')),
  movimiento text not null,                -- SOURCE_ID del reporte | día (neto de las filas de ese pago en el día)
  fecha date not null,
  categoria text not null check (categoria in ('credito', 'deudas_ml', 'pagos_mp')),
  monto numeric(18,2) not null,            -- positivo = salió plata
  descripcion text,
  actualizado_at timestamptz not null default now(),
  primary key (cuenta, movimiento)
);

-- Lo que MercadoPago ya tiene agendado para liberar (pagos aprobados con fecha de liberación futura).
-- Es una foto: cada corrida reemplaza las filas de la cuenta.
create table if not exists cf_liberaciones_pendientes (
  fecha date not null,
  cuenta text not null check (cuenta in ('mp_pret', 'mp_lavan')),
  monto numeric(18,2) not null,
  cantidad int not null,
  actualizado_at timestamptz not null default now(),
  primary key (fecha, cuenta)
);

-- Saldo disponible al cierre de cada día. MercadoPago lo carga el job (fuente 'mp_reporte');
-- Pago Nube y bancos se cargan a mano desde el Cashflow (fuente 'manual').
create table if not exists cf_saldos (
  fecha date not null,
  cuenta text not null check (cuenta ~ '^[a-z0-9_]{2,40}$'),
  saldo numeric(18,2) not null,
  fuente text not null default 'manual' check (fuente in ('mp_reporte', 'manual')),
  hora timestamptz,                        -- último movimiento del día que fija el saldo (MercadoPago)
  creado_por uuid references auth.users(id) default auth.uid(),
  actualizado_at timestamptz not null default now(),
  primary key (fecha, cuenta)
);

-- Cobros de Tienda Nube por medio de pago: los pagados con Mercado Pago ya entran en la cuenta MP, así que el
-- estimado de Tienda Nube (Pago Nube y otros) los descuenta para no contarlos dos veces.
create table if not exists cf_tn_medios (
  fecha date not null,
  tienda text not null check (tienda in ('pret', 'lavan')),
  medio text not null,                     -- gateway de Tiendanube: pago-nube, mercado-pago, gocuotas, ...
  total numeric(18,2) not null,
  cantidad int not null,
  actualizado_at timestamptz not null default now(),
  primary key (fecha, tienda, medio)
);

alter table cf_liberaciones enable row level security;
alter table cf_mp_egresos enable row level security;
alter table cf_liberaciones_pendientes enable row level security;
alter table cf_saldos enable row level security;
alter table cf_tn_medios enable row level security;

drop policy if exists cf_liberaciones_leer on cf_liberaciones;
create policy cf_liberaciones_leer on cf_liberaciones for select to authenticated using (cc_rol() in ('admin','dueno'));
drop policy if exists cf_mp_egresos_leer on cf_mp_egresos;
create policy cf_mp_egresos_leer on cf_mp_egresos for select to authenticated using (cc_rol() in ('admin','dueno'));
drop policy if exists cf_pendientes_leer on cf_liberaciones_pendientes;
create policy cf_pendientes_leer on cf_liberaciones_pendientes for select to authenticated using (cc_rol() in ('admin','dueno'));
drop policy if exists cf_tn_medios_leer on cf_tn_medios;
create policy cf_tn_medios_leer on cf_tn_medios for select to authenticated using (cc_rol() in ('admin','dueno'));
-- Saldos: se leen todos; a mano solo se cargan/borran los manuales (los de MercadoPago los escribe el job).
drop policy if exists cf_saldos_leer on cf_saldos;
create policy cf_saldos_leer on cf_saldos for select to authenticated using (cc_rol() in ('admin','dueno'));
drop policy if exists cf_saldos_manual on cf_saldos;
create policy cf_saldos_manual on cf_saldos for all to authenticated
  using (cc_rol() in ('admin','dueno') and fuente = 'manual')
  with check (cc_rol() in ('admin','dueno') and fuente = 'manual' and cuenta not like 'mp\_%');

revoke all on cf_liberaciones, cf_mp_egresos, cf_liberaciones_pendientes, cf_saldos, cf_tn_medios from anon;
grant select on cf_liberaciones, cf_mp_egresos, cf_liberaciones_pendientes, cf_tn_medios to authenticated;
grant select, insert, update, delete on cf_saldos to authenticated;
grant select, insert, update, delete on cf_liberaciones, cf_mp_egresos, cf_liberaciones_pendientes, cf_saldos, cf_tn_medios to service_role;
