-- Publicaciones de Pret a Home en Mercado Libre, por SKU, separadas en TRADICIONALES y de CATÁLOGO (pedido del usuario,
-- 18/11/2026: el revendedor a veces quiere sumarse a un catálogo —el de Pret o el de Sakura— y otras hacer una publicación
-- tradicional, propia). La llena reposicion/rev_pret_publicaciones_sync.py leyendo las publicaciones activas de Pret con la
-- conexión de solo lectura (fila 'pausado' de rev_revendedores). Solo la lee el servicio; el portal la ve por
-- rev_catalogo_publicaciones_pret().
create table if not exists public.rev_pret_publicaciones (
  ml_item_id         text    not null,
  ml_variation_id    bigint  not null default 0,          -- 0 = publicación sin variantes
  sku                text    not null,
  tipo               text    not null check (tipo in ('tradicional', 'catalogo')),
  catalog_product_id text,                                 -- ficha de catálogo de ML (solo tipo 'catalogo')
  marca              text,                                 -- atributo BRAND de la publicación
  titulo             text,
  precio             numeric,
  vendidas           integer,                              -- sold_quantity de la publicación (para elegir la principal)
  permalink          text,
  visto_at           timestamptz not null default now(),
  primary key (ml_item_id, ml_variation_id)
);
create index if not exists rev_pret_publicaciones_sku_idx on public.rev_pret_publicaciones (sku);

alter table public.rev_pret_publicaciones enable row level security;
revoke all on public.rev_pret_publicaciones from anon, authenticated;
grant select, insert, update, delete on public.rev_pret_publicaciones to service_role;

-- Marcas que el revendedor puede reemplazar por la suya (decisión del usuario: solo productos Pret). Se compara sin
-- importar mayúsculas ni espacios de más. "Pret / Portofino" queda afuera hasta que el usuario confirme.
insert into public.rev_config (clave, valor) values ('marcas_propias', '["Pret.", "Pret A Home"]'::jsonb)
on conflict (clave) do nothing;

-- Por SKU del catálogo y tipo, la publicación principal de Pret (la más vendida) y cuántas hay de ese tipo.
-- `marca_propia` = el revendedor puede publicarlo con su marca (solo tiene sentido en tradicional: en catálogo la ficha
-- es de ML y la marca no se cambia).
create or replace function public.rev_catalogo_publicaciones_pret()
returns table (sku text, tipo text, ml_item_id text, ml_variation_id bigint, catalog_product_id text, marca text,
               marca_propia boolean, titulo text, permalink text, cantidad integer)
language sql stable security definer set search_path = public as $$
  with propias as (
    select lower(trim(m)) as m from public.rev_config c, jsonb_array_elements_text(c.valor) m where c.clave = 'marcas_propias'
  ), base as (
    select p.*, count(*) over (partition by p.sku, p.tipo) as n,
           row_number() over (partition by p.sku, p.tipo order by p.vendidas desc nulls last, p.ml_item_id) as rn
    from public.rev_pret_publicaciones p
    join public.rev_catalogo c on c.sku = p.sku and c.habilitado
  )
  select b.sku, b.tipo, b.ml_item_id, nullif(b.ml_variation_id, 0), b.catalog_product_id, b.marca,
         lower(trim(coalesce(b.marca, ''))) in (select m from propias),
         b.titulo, b.permalink, b.n::integer
  from base b
  where b.rn = 1 and (public.rev_mi_id() is not null or public.rev_es_admin())
  order by b.sku, b.tipo
$$;

revoke all on function public.rev_catalogo_publicaciones_pret() from public, anon;
grant execute on function public.rev_catalogo_publicaciones_pret() to authenticated;
