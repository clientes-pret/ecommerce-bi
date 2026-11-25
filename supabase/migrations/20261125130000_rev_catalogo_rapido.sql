-- El catálogo del portal de revendedores tardaba ~45 s en cargar (25/11/2026): con el catálogo ampliado a 8.264 SKU,
-- rev_catalogo_listar llamaba por CADA fila a rev_mi_id(), rev_colchon_de(), rev_margen_de() (funciones security
-- definer que la base no puede acelerar): ~3-5 s por página de 1.000, y el portal pide 9 páginas.
-- Ahora quién es, su colchón y su margen se calculan UNA vez por consulta. Mismas fórmulas que rev_ofrecido_de y
-- rev_precio_mayorista_de (no cambia ningún número), mismas columnas y mismo control de acceso.

create or replace function public.rev_catalogo_listar()
returns table (sku text, titulo text, color text, medida text, categoria text, foto text, stock_ofrecido integer,
               precio_mayorista numeric, precio_sugerido numeric, precio_minimo numeric, ya_publicado boolean)
language sql stable security definer set search_path = public as $$
  with yo as materialized (
    select q.id, q.admin, public.rev_colchon_de(q.id) as colchon, public.rev_margen_de(q.id) as margen
    from (select public.rev_mi_id() as id, public.rev_es_admin() as admin) q
  ), mias as materialized (
    select distinct x.sku from public.rev_publicaciones x, yo where x.revendedor_id = yo.id and x.sku is not null
  )
  select c.sku,
         coalesce(i.titulo, r.nombre, p.producto),
         p.color, p.medida,
         coalesce(r.categoria_manual, r.categoria_auto, p.categoria),
         i.fotos ->> 0,
         case when s.stock_ecomm is null then 0
              else greatest(0, s.stock_ecomm - greatest(0, coalesce(c.colchon_override, yo.colchon))) end,
         case when i.costo is null or i.costo <= 0 or yo.margen is null then null
              else round(i.costo * (1 + yo.margen / 100)) end,
         i.precio_sugerido,
         c.precio_minimo,
         m.sku is not null
  from public.rev_catalogo c
  cross join yo
  left join public.rev_stock_ofrecido s on s.sku = c.sku
  left join public.rev_catalogo_info i on i.sku = c.sku
  left join public.cc_productos p on p.sku = c.sku
  left join public.repo_productos r on r.sku = c.sku
  left join mias m on m.sku = c.sku
  where c.habilitado and (yo.id is not null or yo.admin)
  order by c.sku
$$;

-- Misma idea para las publicaciones de Pret que muestra el catálogo: el control de acceso se evalúa una vez.
create or replace function public.rev_catalogo_publicaciones_pret()
returns table (sku text, tipo text, ml_item_id text, ml_variation_id bigint, catalog_product_id text, marca text,
               marca_propia boolean, titulo text, permalink text, cantidad integer, creada_at timestamptz)
language sql stable security definer set search_path = public as $$
  with acceso as materialized (
    select (public.rev_mi_id() is not null or public.rev_es_admin()) as ok
  ), propias as (
    select lower(trim(m)) as m from public.rev_config c, jsonb_array_elements_text(c.valor) m where c.clave = 'marcas_propias'
  ), base as (
    select p.*, count(*) over (partition by p.sku, p.tipo) as n,
           max(p.creada_at) over (partition by p.sku) as mas_nueva,
           row_number() over (partition by p.sku, p.tipo order by p.vendidas desc nulls last, p.ml_item_id) as rn
    from public.rev_pret_publicaciones p
    join public.rev_catalogo c on c.sku = p.sku and c.habilitado
    where (select ok from acceso)
  )
  select b.sku, b.tipo, b.ml_item_id, nullif(b.ml_variation_id, 0), b.catalog_product_id, b.marca,
         lower(trim(coalesce(b.marca, ''))) in (select m from propias),
         b.titulo, b.permalink, b.n::integer, b.mas_nueva
  from base b
  where b.rn = 1
  order by b.sku, b.tipo
$$;

revoke all on function public.rev_catalogo_listar() from public, anon;
grant execute on function public.rev_catalogo_listar() to authenticated;
revoke all on function public.rev_catalogo_publicaciones_pret() from public, anon;
grant execute on function public.rev_catalogo_publicaciones_pret() to authenticated;
