-- Dataset persistente de ventas — cargado incrementalmente día a día en vez
-- de re-pedir la ventana completa (60d, ~22000 órdenes de ML Pret) a las
-- APIs en vivo en cada corrida semanal.

create table repo_ventas_items (
  id           bigint generated always as identity primary key,
  canal        text not null check (canal in ('tn_pret','tn_lavan','ml_pret','ml_lavan')),
  order_id     text not null,
  item_id      text,                 -- id de publicación ML (null en TN) — clave para full-split
  sku          text,
  cantidad     integer not null,
  fecha        date not null,        -- fecha de la orden, para ventanas por día
  creado_at    timestamptz not null, -- date_created exacto (half-window de tendencia)
  estado       text not null default 'activa' check (estado in ('activa','cancelada')),
  ingested_at  timestamptz not null default now()
);
-- item_id es null en TN — un unique constraint normal trataría cada NULL
-- como distinto (dos líneas del mismo pedido/SKU no chocarían), por eso va
-- como índice de expresión con coalesce en vez de "unique(...)" en la tabla.
create unique index uq_repo_ventas_items on repo_ventas_items (canal, order_id, coalesce(item_id, ''), coalesce(sku, ''));
create index idx_repo_ventas_items_sku_fecha on repo_ventas_items (sku, fecha);
create index idx_repo_ventas_items_canal_fecha on repo_ventas_items (canal, fecha);

-- Cursor de sincronización por canal — tabla propia (no repo_settings)
-- porque son 4 filas con forma fija y semántica de auditoría, no config de
-- negocio editable desde la UI.
create table repo_sync_estado (
  canal           text primary key check (canal in ('tn_pret','tn_lavan','ml_pret','ml_lavan')),
  cursor_desde    timestamptz,
  ultima_corrida  timestamptz,
  ordenes_sync    integer,
  notas           text
);
