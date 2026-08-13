-- App de Reposición de Mercadería — schema inicial
-- Correr una sola vez en el SQL Editor de Supabase (mismo proyecto que ya
-- usa semaforo_ml.py: ver config.json -> supabase.url).
-- Prefijo repo_ para no chocar con semaforo_historial / cambios_precio_historial.

-- ─── Productos (identidad por SKU, cross-canal/cross-marca) ──────────────────

create table repo_productos (
  sku               text primary key,
  nombre            text,
  proveedor_auto    text,              -- último valor calculado por detect_supplier()
  proveedor_manual  text,              -- override manual desde la UI; si no es null, gana sobre proveedor_auto
  descontinuado     boolean not null default false,
  descontinuado_at  timestamptz,
  descontinuado_por text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- ─── Snapshot diario de stock ─────────────────────────────────────────────────
-- stock_deposito viene únicamente de TN Pret (fuente única de depósito,
-- generar_reporte.py:755/786 — TN Lavan vende contra el mismo stock).
-- stock_full_pret / stock_full_lavan se mantienen separados: para armar un
-- pedido de reposición a Full hace falta saber a qué cuenta ML reponer.

create table repo_stock_snapshot (
  id                bigint generated always as identity primary key,
  fecha             date not null,
  sku               text not null references repo_productos(sku),
  stock_deposito    integer not null default 0,
  stock_full_pret   integer not null default 0,
  stock_full_lavan  integer not null default 0,
  captured_at       timestamptz not null default now(),
  unique (fecha, sku)
);
create index idx_repo_stock_snapshot_sku_fecha on repo_stock_snapshot (sku, fecha desc);

-- ─── Cálculo semanal (ventas, velocidad, reposición, sobrestock) ──────────────
-- Reemplaza lo que hoy solo vive en reporte_reposicion_*.xlsx / reporte_sobrestock_*.xlsx

create table repo_calculo_semanal (
  id                      bigint generated always as identity primary key,
  semana_iso              text not null,             -- '2026-W33'
  fecha_corrida           timestamptz not null default now(),
  sku                     text not null references repo_productos(sku),
  stock_deposito          integer,
  vel_diaria_deposito     numeric,
  vel_semanal_deposito    numeric,
  dias_activo             integer,
  confianza               text,                      -- alta/media/baja
  tendencia_pct           numeric,
  dias_quiebre_deposito   integer,
  fecha_quiebre_deposito  date,
  alerta_deposito         text,
  sobrestock_categoria    text,
  sobrestock_accion       text,
  a_reponer_deposito      integer,
  -- Full Pret y Full Lavan son pools de stock separados en Mercado Libre
  -- (no se comparten entre marcas, a diferencia del depósito) — por eso van
  -- en columnas propias, cada una con su propia velocidad/quiebre/alerta.
  stock_full_pret         integer,
  vel_diaria_full_pret    numeric,
  vel_semanal_full_pret   numeric,
  dias_quiebre_full_pret  integer,
  fecha_quiebre_full_pret date,
  alerta_full_pret        text,
  a_reponer_full_pret     integer,
  stock_full_lavan         integer,
  vel_diaria_full_lavan    numeric,
  vel_semanal_full_lavan   numeric,
  dias_quiebre_full_lavan  integer,
  fecha_quiebre_full_lavan date,
  alerta_full_lavan        text,
  a_reponer_full_lavan     integer,
  coverage_days_usado     integer,                   -- auditoría: qué target de cobertura se usó en esta corrida
  unique (semana_iso, sku)
);
create index idx_repo_calculo_sku_semana on repo_calculo_semanal (sku, semana_iso);
create index idx_repo_calculo_semana on repo_calculo_semanal (semana_iso);

-- ─── Historial de quiebres ─────────────────────────────────────────────────────
-- Una fila cada vez que un SKU/destino pasa de stock>0 a stock=0.
-- El índice único parcial garantiza un solo quiebre "abierto" por SKU/destino.

create table repo_quiebre_historial (
  id                      bigint generated always as identity primary key,
  sku                     text not null references repo_productos(sku),
  destino                 text not null check (destino in ('deposito','full_pret','full_lavan')),
  fecha_quiebre           date not null,
  stock_previo            integer,
  vel_semanal_al_quebrar  numeric,
  fecha_reposicion        date,              -- se completa cuando vuelve a haber stock
  created_at              timestamptz not null default now()
);
create unique index uq_quiebre_abierto on repo_quiebre_historial (sku, destino) where fecha_reposicion is null;
create index idx_repo_quiebre_sku on repo_quiebre_historial (sku);

-- ─── Pedidos de reposición ─────────────────────────────────────────────────────

create table repo_pedidos (
  id             bigint generated always as identity primary key,
  proveedor      text not null,
  creado_por     text not null,         -- Martín / Mati / dueño
  creado_at      timestamptz not null default now(),
  estado         text not null default 'enviado'
                   check (estado in ('enviado','recibido_parcial','recibido_completo','cancelado')),
  notas          text
);

create table repo_pedido_items (
  id                  bigint generated always as identity primary key,
  pedido_id           bigint not null references repo_pedidos(id) on delete cascade,
  sku                 text not null references repo_productos(sku),
  destino             text not null check (destino in ('deposito','full_pret','full_lavan')),
  cantidad_pedida     integer not null,
  cantidad_sugerida   integer,           -- lo que calculó el sistema, para trazabilidad de qué editó el usuario
  stock_al_pedir      integer,           -- snapshot del stock del SKU/destino al armar el pedido (base de reconciliación)
  cantidad_recibida   integer not null default 0,
  recibido_completo   boolean not null default false,
  recibido_at         timestamptz
);
create index idx_repo_pedido_items_pedido on repo_pedido_items (pedido_id);
create index idx_repo_pedido_items_sku on repo_pedido_items (sku);
create index idx_repo_pedido_items_pendientes on repo_pedido_items (sku, destino) where not recibido_completo;

-- ─── Settings (clave/valor) ─────────────────────────────────────────────────────

create table repo_settings (
  clave  text primary key,
  valor  jsonb not null
);
insert into repo_settings (clave, valor) values ('coverage_days', '60'::jsonb);
