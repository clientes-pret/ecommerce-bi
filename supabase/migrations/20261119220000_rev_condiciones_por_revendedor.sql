-- Colchón de stock y margen mayorista POR REVENDEDOR (19/11/2026, pedido del usuario: "tanto el colchón de stock como el
-- margen deben ser configurables a nivel cliente"). Cada revendedor puede tener los suyos; si no tiene (null), usa los
-- generales de rev_config ('colchon', 'margen_mayorista'), que quedan como valor por defecto.
--   colchon          → unidades que se guarda Pret de cada producto para ESE revendedor: se le ofrece stock − colchón.
--                      (rev_catalogo.colchon_override, por producto, sigue ganando si alguna vez se carga.)
--   margen_mayorista → % sobre el costo para el precio mayorista de ESE revendedor (lo que le paga a Pret).
-- Las ventas ya registradas no cambian: rev_ordenes guarda el precio mayorista del momento de la venta.
alter table public.rev_revendedores add column if not exists colchon integer check (colchon is null or colchon between 0 and 100);
alter table public.rev_revendedores add column if not exists margen_mayorista numeric check (margen_mayorista is null or margen_mayorista between 0 and 500);

-- Colchón de un revendedor (el suyo o el general; 5 si no hay ninguno, como rev-sync).
create or replace function public.rev_colchon_de(p_rev bigint) returns integer
language sql stable security definer set search_path = public as $$
  select coalesce((select r.colchon from public.rev_revendedores r where r.id = p_rev),
                  (select round((c.valor #>> '{}')::numeric)::integer from public.rev_config c where c.clave = 'colchon'),
                  5)
$$;

-- Stock que se le ofrece a un revendedor: stock de Ecomm-App − colchón (el del producto, el suyo o el general), nunca negativo.
create or replace function public.rev_ofrecido_de(p_stock integer, p_colchon_producto integer, p_rev bigint) returns integer
language sql stable security definer set search_path = public as $$
  select case when p_stock is null then 0
              else greatest(0, p_stock - greatest(0, coalesce(p_colchon_producto, public.rev_colchon_de(p_rev)))) end
$$;

-- Margen mayorista de un revendedor (el suyo o el general).
create or replace function public.rev_margen_de(p_rev bigint) returns numeric
language sql stable security definer set search_path = public as $$
  select coalesce((select r.margen_mayorista from public.rev_revendedores r where r.id = p_rev), public.rev_margen())
$$;

create or replace function public.rev_precio_mayorista_de(p_costo numeric, p_rev bigint) returns numeric
language sql stable security definer set search_path = public as $$
  select case when p_costo is null or p_costo <= 0 or public.rev_margen_de(p_rev) is null then null
              else round(p_costo * (1 + public.rev_margen_de(p_rev) / 100)) end
$$;

-- El precio mayorista "de quien mira": el revendedor logueado ve el suyo; el administrador (sin revendedor), el general.
create or replace function public.rev_precio_mayorista(p_costo numeric) returns numeric
language sql stable security definer set search_path = public as $$
  select public.rev_precio_mayorista_de(p_costo, public.rev_mi_id())
$$;

-- Cada venta guarda el precio mayorista de SU revendedor.
create or replace function public.rev_ordenes_precio_mayorista() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.precio_mayorista is null then
    select public.rev_precio_mayorista_de(i.costo, new.revendedor_id) into new.precio_mayorista from public.rev_catalogo_info i where i.sku = new.sku;
  end if;
  return new;
end $$;

-- Catálogo del portal: el stock ofrecido es el de cada revendedor (con su colchón).
create or replace function public.rev_catalogo_listar()
returns table (sku text, titulo text, color text, medida text, categoria text, foto text, stock_ofrecido integer,
               precio_mayorista numeric, precio_sugerido numeric, precio_minimo numeric, ya_publicado boolean)
language sql stable security definer set search_path = public as $$
  select c.sku,
         coalesce(i.titulo, r.nombre, p.producto),
         p.color, p.medida,
         coalesce(r.categoria_manual, r.categoria_auto, p.categoria),
         i.fotos ->> 0,
         public.rev_ofrecido_de(s.stock_ecomm, c.colchon_override, public.rev_mi_id()),
         public.rev_precio_mayorista(i.costo),
         i.precio_sugerido,
         c.precio_minimo,
         exists (select 1 from public.rev_publicaciones x where x.revendedor_id = public.rev_mi_id() and x.sku = c.sku)
  from public.rev_catalogo c
  left join public.rev_stock_ofrecido s on s.sku = c.sku
  left join public.rev_catalogo_info i on i.sku = c.sku
  left join public.cc_productos p on p.sku = c.sku
  left join public.repo_productos r on r.sku = c.sku
  where c.habilitado and (public.rev_mi_id() is not null or public.rev_es_admin())
  order by c.sku
$$;

create or replace function public.rev_catalogo_detalle(p_skus text[])
returns table (sku text, titulo text, descripcion text, fotos jsonb, color text, medida text, categoria text, stock_ofrecido integer,
               precio_mayorista numeric, precio_sugerido numeric, precio_minimo numeric)
language sql stable security definer set search_path = public as $$
  select c.sku,
         coalesce(i.titulo, r.nombre, p.producto),
         i.descripcion,
         coalesce(i.fotos, '[]'::jsonb),
         p.color, p.medida,
         coalesce(r.categoria_manual, r.categoria_auto, p.categoria),
         public.rev_ofrecido_de(s.stock_ecomm, c.colchon_override, public.rev_mi_id()),
         public.rev_precio_mayorista(i.costo),
         i.precio_sugerido,
         c.precio_minimo
  from public.rev_catalogo c
  left join public.rev_stock_ofrecido s on s.sku = c.sku
  left join public.rev_catalogo_info i on i.sku = c.sku
  left join public.cc_productos p on p.sku = c.sku
  left join public.repo_productos r on r.sku = c.sku
  where c.habilitado and c.sku = any (p_skus) and (public.rev_mi_id() is not null or public.rev_es_admin())
  order by c.sku
  limit 1000
$$;

create or replace function public.rev_mis_publicaciones()
returns table (id bigint, ml_item_id text, ml_variation_id bigint, sku text, titulo text, estado_ml text, precio numeric,
               precio_minimo numeric, bajo_minimo boolean, vinculo text, ofrecido integer,
               ultima_cantidad_enviada integer, ultimo_envio_at timestamptz)
language sql stable security definer set search_path = public as $$
  select b.id, b.ml_item_id, b.ml_variation_id, b.sku,
         coalesce(i.titulo, r.nombre, p.producto),
         b.estado_ml, b.precio, c.precio_minimo,
         (c.precio_minimo is not null and b.precio is not null and b.precio < c.precio_minimo),
         b.vinculo,
         case when s.sku is null then null else public.rev_ofrecido_de(s.stock_ecomm, c.colchon_override, b.revendedor_id) end,
         b.ultima_cantidad_enviada, b.ultimo_envio_at
  from public.rev_publicaciones b
  left join public.rev_catalogo c on c.sku = b.sku
  left join public.rev_stock_ofrecido s on s.sku = b.sku
  left join public.rev_catalogo_info i on i.sku = b.sku
  left join public.cc_productos p on p.sku = b.sku
  left join public.repo_productos r on r.sku = b.sku
  where b.revendedor_id = public.rev_mi_id()
  order by (b.sku is null) desc, b.ml_item_id
$$;

-- Internas: un revendedor logueado no puede llamarlas (deduciría el margen y con eso el costo; ver 20261118130000).
revoke all on function public.rev_colchon_de(bigint), public.rev_ofrecido_de(integer, integer, bigint),
  public.rev_margen_de(bigint), public.rev_precio_mayorista_de(numeric, bigint), public.rev_precio_mayorista(numeric)
  from public, anon, authenticated;
grant execute on function public.rev_precio_mayorista_de(numeric, bigint), public.rev_margen_de(bigint), public.rev_colchon_de(bigint)
  to service_role;
