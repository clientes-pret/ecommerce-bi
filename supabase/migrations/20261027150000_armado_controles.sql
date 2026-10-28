-- Control de armado de pedidos (reposicion/dashboard/armado.html + Edge Function armado-api).
-- Cada vez que alguien termina de revisar un paquete queda una fila en armado_controles
-- y una por producto en armado_items. Solo escribe la Edge Function (service_role).

create table if not exists armado_controles (
  id            bigint generated always as identity primary key,
  creado_at     timestamptz not null default now(),
  canal         text not null check (canal in ('tn', 'ml')),
  tienda        text not null check (tienda in ('pret', 'lavan')),
  pedido        text not null,          -- número de orden TN, o id de orden/pack ML
  envio         text,                   -- id de envío ML o seguimiento del correo, si lo hay
  codigo_leido  text,                   -- lo que leyó la cámara o se escribió
  armado_por    text,
  resultado     text not null check (resultado in ('completo', 'faltante')),
  unidades      integer not null default 0,
  inicio_at     timestamptz,
  fin_at        timestamptz
);

create index if not exists armado_controles_pedido_idx on armado_controles (tienda, canal, pedido, creado_at desc);
create index if not exists armado_controles_creado_idx on armado_controles (creado_at desc);

create table if not exists armado_items (
  id          bigint generated always as identity primary key,
  control_id  bigint not null references armado_controles (id) on delete cascade,
  sku         text,
  nombre      text,
  variante    text,
  cantidad    integer not null default 1,
  estado      text not null check (estado in ('ok', 'no_hay')),
  confirmado_at timestamptz
);

create index if not exists armado_items_control_idx on armado_items (control_id);

-- Nadie lee estas tablas con la clave pública: todo pasa por la Edge Function.
alter table armado_controles enable row level security;
alter table armado_items enable row level security;
