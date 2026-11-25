-- Medidas del paquete por SKU que cargan los revendedores cuando la publicación de Pret no las tiene (25/11/2026).
-- Mercado Libre exige SELLER_PACKAGE_HEIGHT/WIDTH/LENGTH/WEIGHT para crear publicaciones nuevas en varias categorías y
-- muchas publicaciones viejas de Pret no las tienen (no hay de dónde copiarlas). Se cargan una vez por producto y sirven
-- para todos los revendedores. Solo las lee y escribe la función rev-publicar (servicio): sin políticas.
create table if not exists public.rev_medidas_paquete (
  sku            text primary key,
  alto_cm        numeric not null check (alto_cm > 0 and alto_cm <= 300),
  ancho_cm       numeric not null check (ancho_cm > 0 and ancho_cm <= 300),
  largo_cm       numeric not null check (largo_cm > 0 and largo_cm <= 300),
  peso_g         integer not null check (peso_g > 0 and peso_g <= 100000),
  revendedor_id  bigint references public.rev_revendedores(id) on delete set null,
  actualizado_at timestamptz not null default now()
);
alter table public.rev_medidas_paquete enable row level security;
revoke all on table public.rev_medidas_paquete from anon, authenticated;
