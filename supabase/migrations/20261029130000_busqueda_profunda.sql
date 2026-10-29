-- Búsqueda profunda (reposicion/dashboard/armado.html + Edge Function armado-api) y Stock perdido (panel de control).
-- Lo que alguien del depósito no encuentra (Búsqueda: "no la tengo" / encontré menos; Empaquetado: "No hay en el
-- depósito") abre un caso por SKU. Una persona del equipo lo busca a fondo y lo cierra:
--   - aparecio: se encontró todo lo que faltaba (puede aparecer de a partes: `encontradas` suma);
--   - perdido: confirmó que no está → Stock perdido, valuado al costo de Tiendanube de ese momento.
-- Un SKU tiene a lo sumo un caso pendiente. Cada aviso se guarda en `reportes` (clave por origen, así volver a
-- marcar o desmarcar en Búsqueda reemplaza el aviso en vez de sumarlo dos veces).

create table if not exists busqueda_profunda (
  id              bigint generated always as identity primary key,
  sku             text not null,
  nombre          text,
  variante        text,
  imagen          text,
  estado          text not null default 'pendiente' check (estado in ('pendiente', 'aparecio', 'perdido')),
  reportadas      integer not null default 0,   -- unidades que faltaban según los avisos
  unidades        integer not null default 0,   -- las que todavía faltan (reportadas − encontradas)
  encontradas     integer not null default 0,
  perdidas        integer not null default 0,
  pedidos         jsonb not null default '[]',  -- [{canal, tienda, pedido, cantidad}]
  reportes        jsonb not null default '[]',  -- [{clave, origen, por, at, faltan, pedidos}]
  donde           text,                         -- dónde estaba, si apareció
  creado_at       timestamptz not null default now(),
  actualizado_at  timestamptz not null default now(),
  tomado_por      text,
  tomado_at       timestamptz,
  cerrado_por     text,
  cerrado_at      timestamptz,
  stock_tn        integer,                      -- stock que Tiendanube creía tener al confirmar la pérdida
  costo_unitario  numeric(18,2),
  costo_total     numeric(18,2)
);

create unique index if not exists busqueda_profunda_pendiente_sku on busqueda_profunda (sku) where estado = 'pendiente';
create index if not exists busqueda_profunda_cerrado on busqueda_profunda (estado, cerrado_at desc);

-- Escribe solo la Edge Function (service_role). El panel de control lee con su login (admin / dueño).
alter table busqueda_profunda enable row level security;
drop policy if exists busqueda_profunda_lectura on busqueda_profunda;
create policy busqueda_profunda_lectura on busqueda_profunda for select to authenticated
  using (cc_rol() in ('admin', 'dueno'));
revoke all on busqueda_profunda from anon;
grant select on busqueda_profunda to authenticated;
grant all on busqueda_profunda to service_role;
