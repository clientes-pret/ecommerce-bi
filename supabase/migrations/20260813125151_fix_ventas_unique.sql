-- Corrige 005: el índice único con coalesce() no es compatible con el
-- parámetro on_conflict de la REST de Supabase (necesita nombres de columna
-- simples, no expresiones). Sin datos todavía en la tabla — se recrea limpia
-- con item_id/sku NOT NULL DEFAULT '' (sentinel de "no aplica" en vez de
-- NULL, que en un unique constraint normal se trata como distinto de sí
-- mismo y dejaría pasar duplicados en TN).

drop table if exists repo_ventas_items;

create table repo_ventas_items (
  id           bigint generated always as identity primary key,
  canal        text not null check (canal in ('tn_pret','tn_lavan','ml_pret','ml_lavan')),
  order_id     text not null,
  item_id      text not null default '', -- id de publicación ML; '' = no aplica (TN)
  sku          text not null default '',
  cantidad     integer not null,
  fecha        date not null,
  creado_at    timestamptz not null,
  estado       text not null default 'activa' check (estado in ('activa','cancelada')),
  ingested_at  timestamptz not null default now(),
  unique (canal, order_id, item_id, sku)
);
create index idx_repo_ventas_items_sku_fecha on repo_ventas_items (sku, fecha);
create index idx_repo_ventas_items_canal_fecha on repo_ventas_items (canal, fecha);
