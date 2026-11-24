-- Fecha en que Pret a Home creó cada publicación en Mercado Libre (date_created del item), para que el catálogo de
-- revendedores pueda mostrar y filtrar "lo último que publicó Pret" (pedido del usuario, 24/11/2026).
-- La llena reposicion/rev_pret_publicaciones_sync.py.
alter table public.rev_pret_publicaciones add column if not exists creada_at timestamptz;

-- Misma función que antes, con una columna más: la fecha de la publicación de Pret más nueva de ese SKU
-- (tradicional o catálogo). Cambia lo que devuelve, así que se borra y se vuelve a crear.
drop function if exists public.rev_catalogo_publicaciones_pret();
create function public.rev_catalogo_publicaciones_pret()
returns table (sku text, tipo text, ml_item_id text, ml_variation_id bigint, catalog_product_id text, marca text,
               marca_propia boolean, titulo text, permalink text, cantidad integer, creada_at timestamptz)
language sql stable security definer set search_path = public as $$
  with propias as (
    select lower(trim(m)) as m from public.rev_config c, jsonb_array_elements_text(c.valor) m where c.clave = 'marcas_propias'
  ), base as (
    select p.*, count(*) over (partition by p.sku, p.tipo) as n,
           max(p.creada_at) over (partition by p.sku) as mas_nueva,
           row_number() over (partition by p.sku, p.tipo order by p.vendidas desc nulls last, p.ml_item_id) as rn
    from public.rev_pret_publicaciones p
    join public.rev_catalogo c on c.sku = p.sku and c.habilitado
  )
  select b.sku, b.tipo, b.ml_item_id, nullif(b.ml_variation_id, 0), b.catalog_product_id, b.marca,
         lower(trim(coalesce(b.marca, ''))) in (select m from propias),
         b.titulo, b.permalink, b.n::integer, b.mas_nueva
  from base b
  where b.rn = 1 and (public.rev_mi_id() is not null or public.rev_es_admin())
  order by b.sku, b.tipo
$$;

revoke all on function public.rev_catalogo_publicaciones_pret() from public, anon;
grant execute on function public.rev_catalogo_publicaciones_pret() to authenticated;
