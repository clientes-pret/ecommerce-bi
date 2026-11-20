-- Mis publicaciones: devuelve también cuándo se leyó por última vez el stock del SKU en Ecomm-App
-- (rev_stock_ofrecido.consultado_at), para que el portal muestre "Última revisión" con fecha y hora (20/11/2026).
-- Misma lógica que la versión vigente (stock con rev_ofrecido_de); solo suma la columna stock_consultado_at.
drop function if exists public.rev_mis_publicaciones();

create function public.rev_mis_publicaciones()
returns table (id bigint, ml_item_id text, ml_variation_id bigint, sku text, titulo text, estado_ml text, precio numeric,
               precio_minimo numeric, bajo_minimo boolean, vinculo text, ofrecido integer, ultima_cantidad_enviada integer,
               ultimo_envio_at timestamptz, stock_consultado_at timestamptz)
language sql stable security definer set search_path = public as $$
  select b.id, b.ml_item_id, b.ml_variation_id, b.sku,
         coalesce(i.titulo, r.nombre, p.producto),
         b.estado_ml, b.precio, c.precio_minimo,
         (c.precio_minimo is not null and b.precio is not null and b.precio < c.precio_minimo),
         b.vinculo,
         case when s.sku is null then null else public.rev_ofrecido_de(s.stock_ecomm, c.colchon_override, b.revendedor_id) end,
         b.ultima_cantidad_enviada, b.ultimo_envio_at,
         s.consultado_at
  from public.rev_publicaciones b
  left join public.rev_catalogo c on c.sku = b.sku
  left join public.rev_stock_ofrecido s on s.sku = b.sku
  left join public.rev_catalogo_info i on i.sku = b.sku
  left join public.cc_productos p on p.sku = b.sku
  left join public.repo_productos r on r.sku = b.sku
  where b.revendedor_id = public.rev_mi_id()
  order by (b.sku is null) desc, b.ml_item_id
$$;

revoke execute on function public.rev_mis_publicaciones() from public, anon;
grant execute on function public.rev_mis_publicaciones() to authenticated, service_role;
