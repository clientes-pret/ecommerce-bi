-- Publicación masiva del portal de revendedores (19/11/2026): el revendedor elige muchos productos y el sistema los publica
-- de a poco por detrás. Cada fila = un producto de Pret a Home (una publicación tradicional de Pret con los colores
-- elegidos, o un SKU que solo tiene catálogo). La llena rev-publicar /encolar y la procesa rev-publicar (cron cada minuto).
-- Solo la usa el servicio: el portal ve el avance por la función.
create table if not exists public.rev_publicar_cola (
  id            bigint generated always as identity primary key,
  revendedor_id bigint not null references public.rev_revendedores(id) on delete cascade,
  lote          uuid not null,                     -- un "Publicar seleccionados"
  item_id       text,                              -- publicación tradicional de Pret de donde se copia (null = solo catálogo)
  skus          text[] not null,                   -- colores elegidos
  todos_colores boolean not null default false,    -- además, todos los colores con stock de esa publicación
  opciones      jsonb not null,                    -- tradicional, catalogo, marca, tipos_venta, pausada, regla de precio
  estado        text not null default 'pendiente'
                check (estado in ('pendiente', 'procesando', 'listo', 'parcial', 'error', 'cancelado')),
  resultados    jsonb not null default '[]'::jsonb, -- una entrada por publicación intentada (se guarda a medida que avanza)
  error         text,
  intentos      integer not null default 0,
  creado_at     timestamptz not null default now(),
  tomado_at     timestamptz,
  terminado_at  timestamptz
);
create index if not exists rev_publicar_cola_pend_idx on public.rev_publicar_cola (estado, id);
create index if not exists rev_publicar_cola_rev_idx on public.rev_publicar_cola (revendedor_id, lote);

alter table public.rev_publicar_cola enable row level security;
revoke all on public.rev_publicar_cola from anon, authenticated;
grant select, insert, update, delete on public.rev_publicar_cola to service_role;
