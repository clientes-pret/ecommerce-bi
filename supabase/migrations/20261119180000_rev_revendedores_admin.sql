-- Administración de revendedores (19/11/2026, pedido del usuario):
--   - Activar / desactivar cada revendedor. Estado nuevo 'inactivo' (lo pone el administrador; distinto de 'pausado',
--     que son las cuentas propias de Pret/Lavan conectadas solo para leer). Al desactivar se elige:
--       desactivado_modo 'stock_cero' → el sistema les pone stock 0 a sus publicaciones vinculadas (en modo real);
--       desactivado_modo 'congelar'   → sus publicaciones quedan con el último stock y se dejan de actualizar.
--     Sus ventas se siguen registrando igual.
--   - descuento_auto: si es false, sus ventas NO se descuentan solas en Ecomm-App; el administrador las descuenta a mano
--     desde Ventas ("Descontar stock").
--   - Ficha: contacto, empresa, CUIT, email, teléfono, WhatsApp, dirección, notas y logo (bucket público rev-logos).
alter table public.rev_revendedores drop constraint if exists rev_revendedores_estado_check;
alter table public.rev_revendedores add constraint rev_revendedores_estado_check check (estado in ('activo', 'pausado', 'desconectado', 'inactivo'));
alter table public.rev_revendedores add column if not exists desactivado_modo text check (desactivado_modo in ('stock_cero', 'congelar'));
alter table public.rev_revendedores add column if not exists desactivado_at   timestamptz;
alter table public.rev_revendedores add column if not exists descuento_auto   boolean not null default true;
alter table public.rev_revendedores add column if not exists contacto_nombre  text;
alter table public.rev_revendedores add column if not exists empresa          text;
alter table public.rev_revendedores add column if not exists cuit             text;
alter table public.rev_revendedores add column if not exists email            text;
alter table public.rev_revendedores add column if not exists telefono         text;
alter table public.rev_revendedores add column if not exists whatsapp         text;
alter table public.rev_revendedores add column if not exists direccion        text;
alter table public.rev_revendedores add column if not exists notas            text;
alter table public.rev_revendedores add column if not exists logo_url         text;

-- Logos de revendedores: archivos públicos (se ven en el portal), los sube solo el servicio (rev-publicar, admin).
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('rev-logos', 'rev-logos', true, 2097152, array['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'])
on conflict (id) do nothing;
