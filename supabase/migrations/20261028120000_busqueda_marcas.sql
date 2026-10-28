-- Búsqueda de productos (reposicion/dashboard/armado.html → Búsqueda + Edge Function armado-api).
-- Varias personas buscan a la vez: lo que marca cada una se guarda acá y lo ven todos los celulares.
-- Una marca por SKU por día (hora de Argentina): la última marca gana.

create table if not exists busqueda_marcas (
  fecha        date not null,
  sku          text not null,
  estado       text not null check (estado in ('ok', 'parcial', 'no_hay')),
  requeridas   integer not null,   -- unidades que había que buscar al marcar
  encontradas  integer not null,
  por          text,
  marcado_at   timestamptz not null default now(),
  primary key (fecha, sku)
);

-- Quién está buscando en qué tipo de producto (se considera activo si se vio en los últimos 2 minutos).
create table if not exists busqueda_presencia (
  por         text primary key,
  categoria   text,
  visto_at    timestamptz not null default now()
);

alter table busqueda_marcas enable row level security;
alter table busqueda_presencia enable row level security;
