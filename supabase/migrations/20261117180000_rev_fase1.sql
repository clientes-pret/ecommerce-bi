-- Dropshipping con revendedores en ML — Fase 1: configuración, catálogo ofrecido, publicaciones vinculadas,
-- stock ofrecido, órdenes de revendedores y estado de los procesos.
-- Todas las tablas con RLS activada y SIN políticas (solo la clave de servicio): guardan datos comerciales de terceros.
-- La pantalla de administración agregará políticas de lectura para admin/dueño cuando se construya.

create table if not exists public.rev_config (
  clave          text primary key,
  valor          jsonb not null,
  actualizado_at timestamptz not null default now()
);

-- modo 'sombra': los procesos calculan y registran lo que harían, sin escribir en Ecomm-App ni en ML.
-- Se pasa a 'activo' a mano, cuando los números de la sombra cierran.
insert into public.rev_config (clave, valor) values
  ('colchon',      '5'::jsonb),
  ('warehouse_id', '1083'::jsonb),
  ('modo',         '"sombra"'::jsonb)
on conflict (clave) do nothing;

-- SKUs que se ofrecen a revendedores (decisión 11/11/2026: todo lo que Pret a Home tiene en ML, sin combos).
create table if not exists public.rev_catalogo (
  sku             text primary key,
  habilitado      boolean not null default true,
  colchon_override integer check (colchon_override is null or colchon_override >= 0),
  precio_minimo   numeric check (precio_minimo is null or precio_minimo > 0), -- solo Volf y Sakura (lista del proveedor)
  origen          text,
  creado_at       timestamptz not null default now(),
  actualizado_at  timestamptz not null default now()
);

-- Publicaciones de cada revendedor vinculadas a un SKU del catálogo (por seller_custom_field).
create table if not exists public.rev_publicaciones (
  id                      bigint generated always as identity primary key,
  revendedor_id           bigint not null references public.rev_revendedores(id) on delete cascade,
  ml_item_id              text not null,
  ml_variation_id         bigint,
  sku                     text not null,
  estado_ml               text,
  precio                  numeric,
  ultima_cantidad_enviada integer,
  ultimo_envio_at         timestamptz,
  creado_at               timestamptz not null default now(),
  actualizado_at          timestamptz not null default now()
);
create unique index if not exists rev_publicaciones_uq
  on public.rev_publicaciones (revendedor_id, ml_item_id, coalesce(ml_variation_id, 0));
create index if not exists rev_publicaciones_sku_idx on public.rev_publicaciones (sku);

-- Última foto del stock de depósito en Ecomm-App y lo que se ofrece a revendedores por SKU.
create table if not exists public.rev_stock_ofrecido (
  sku          text primary key,
  stock_ecomm  integer not null,
  ofrecido     integer not null,
  consultado_at timestamptz not null default now()
);

-- Ventas de revendedores. Una fila por orden de ML y SKU (clave única): un aviso repetido no duplica nada.
create table if not exists public.rev_ordenes (
  id               bigint generated always as identity primary key,
  revendedor_id    bigint not null references public.rev_revendedores(id) on delete cascade,
  ml_order_id      bigint not null,
  ml_item_id       text,
  sku              text not null,
  cantidad         integer not null check (cantidad > 0),
  precio_unitario  numeric,
  estado           text not null default 'nueva'
                   check (estado in ('nueva', 'sombra', 'descontada', 'sin_stock', 'error', 'cancelada')),
  ecomm_clave      text,            -- Idempotency-Key usada contra Ecomm-App
  ecomm_respuesta  jsonb,
  detalle          text,
  creado_at        timestamptz not null default now(),
  actualizado_at   timestamptz not null default now(),
  unique (ml_order_id, sku)
);
create index if not exists rev_ordenes_estado_idx on public.rev_ordenes (estado);

-- Último resultado de cada proceso (sincronización de stock, vinculación, reconciliación), para verlo en el panel.
create table if not exists public.rev_sync_estado (
  clave      text primary key,
  ultimo_at  timestamptz not null default now(),
  resultado  text,
  detalle    jsonb
);

alter table public.rev_config         enable row level security;
alter table public.rev_catalogo       enable row level security;
alter table public.rev_publicaciones  enable row level security;
alter table public.rev_stock_ofrecido enable row level security;
alter table public.rev_ordenes        enable row level security;
alter table public.rev_sync_estado    enable row level security;
