-- Revendedores de Mercado Libre (dropshipping): cuentas conectadas y avisos recibidos.
-- Fase 0 de la prueba con un revendedor. Tablas solo accesibles con la clave de servicio:
-- RLS activada y SIN políticas, así la clave pública del proyecto no ve nada (guardan tokens de ML de terceros).

create table if not exists public.rev_revendedores (
  id               bigint generated always as identity primary key,
  ml_user_id       bigint not null unique,
  nickname         text,
  access_token     text,
  refresh_token    text,
  token_expires_at timestamptz,
  scopes           text,
  estado           text not null default 'activo' check (estado in ('activo', 'pausado', 'desconectado')),
  conectado_at     timestamptz not null default now(),
  actualizado_at   timestamptz not null default now()
);

create table if not exists public.rev_ml_avisos (
  id             bigint generated always as identity primary key,
  recibido_at    timestamptz not null default now(),
  topic          text,
  ml_user_id     bigint,
  resource       text,
  application_id bigint,
  intentos       int,
  crudo          jsonb
);

create index if not exists rev_ml_avisos_recibido_idx on public.rev_ml_avisos (recibido_at desc);

alter table public.rev_revendedores enable row level security;
alter table public.rev_ml_avisos    enable row level security;
