-- Portal del revendedor (Fase 2): login ligado a rev_revendedores, catálogo con precio mayorista, vínculo manual de
-- publicaciones y cuenta corriente (lo que cada revendedor le debe a Pret a Home).
--
-- Principios de seguridad:
--  * Las tablas rev_* NO se abren a la clave pública ni a usuarios logueados: no hay políticas ni permisos de tabla para
--    anon/authenticated. Todo lo que ve el front sale de funciones `security definer` que filtran por el revendedor logueado,
--    así `access_token` / `refresh_token` / costo de los productos nunca llegan al navegador.
--  * Las tablas rev_* venían con los permisos por defecto de Supabase (anon y authenticated con todo; solo RLS sin políticas
--    las bloqueaba). Acá se revocan: un error futuro (una política mal puesta, RLS apagada) ya no las abriría.
--  * El precio mayorista = costo + margen fijo % (rev_config.margen_mayorista, decisión del dueño 17/11/2026). El costo
--    se guarda en rev_catalogo_info (sin acceso desde el front) y solo se devuelve el resultado.

-- ───────────────────────── 0. Cerrar permisos de las tablas existentes ─────────────────────────
revoke all on public.rev_revendedores, public.rev_ml_avisos, public.rev_config, public.rev_catalogo,
              public.rev_publicaciones, public.rev_stock_ofrecido, public.rev_ordenes, public.rev_sync_estado
  from anon, authenticated;

-- ───────────────────────── 1. Tablas nuevas ─────────────────────────
-- Quién es cada usuario del portal (Supabase Auth) → a qué revendedor pertenece.
create table if not exists public.rev_usuarios (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  revendedor_id bigint not null references public.rev_revendedores(id) on delete cascade,
  creado_at     timestamptz not null default now()
);
create index if not exists rev_usuarios_revendedor_idx on public.rev_usuarios (revendedor_id);

-- Contenido para publicar (título, descripción, fotos), costo y precio de lista por SKU. Lo llena
-- reposicion/rev_contenido_sync.py desde Tiendanube. El costo NUNCA sale de acá hacia el front.
create table if not exists public.rev_catalogo_info (
  sku             text primary key,
  titulo          text,
  descripcion     text,
  fotos           jsonb not null default '[]'::jsonb,
  costo           numeric,
  precio_sugerido numeric,
  fuente          text,
  actualizado_at  timestamptz not null default now()
);

-- Pagos que cada revendedor le hizo a Pret a Home (la liquidación se cobra aparte; acá solo se registra).
create table if not exists public.rev_pagos (
  id            bigint generated always as identity primary key,
  revendedor_id bigint not null references public.rev_revendedores(id) on delete cascade,
  fecha         date not null default current_date,
  monto         numeric not null check (monto > 0),
  nota          text,
  creado_por    uuid default auth.uid(),
  creado_at     timestamptz not null default now()
);
create index if not exists rev_pagos_revendedor_idx on public.rev_pagos (revendedor_id, fecha);

alter table public.rev_usuarios      enable row level security;
alter table public.rev_catalogo_info enable row level security;
alter table public.rev_pagos         enable row level security;
revoke all on public.rev_usuarios, public.rev_catalogo_info, public.rev_pagos from anon, authenticated;
grant select, insert, update, delete on public.rev_usuarios, public.rev_catalogo_info, public.rev_pagos to service_role;

-- ───────────────────────── 2. Cambios chicos en tablas del backend (aditivos) ─────────────────────────
-- Publicaciones: un revendedor puede corregir a mano el SKU. `vinculo = 'manual'` le avisa al proceso de vinculación
-- automática que no lo pise. `sku` pasa a aceptar nulo para poder mostrar publicaciones que todavía no se reconocieron.
alter table public.rev_publicaciones add column if not exists vinculo text not null default 'auto'
  check (vinculo in ('auto', 'manual'));
alter table public.rev_publicaciones alter column sku drop not null;

-- Órdenes: precio mayorista congelado al momento de la venta (si el margen cambia después, lo ya vendido no se mueve).
alter table public.rev_ordenes add column if not exists precio_mayorista numeric;

-- ───────────────────────── 3. Funciones internas (sin permiso para el front) ─────────────────────────
create or replace function public.rev_mi_id() returns bigint
language sql stable security definer set search_path = public as $$
  select revendedor_id from public.rev_usuarios where user_id = auth.uid()
$$;

create or replace function public.rev_es_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(public.cc_rol() in ('admin', 'dueno'), false)
$$;

create or replace function public.rev_margen() returns numeric
language sql stable security definer set search_path = public as $$
  select (valor #>> '{}')::numeric from public.rev_config where clave = 'margen_mayorista'
$$;

create or replace function public.rev_precio_mayorista(p_costo numeric) returns numeric
language sql stable security definer set search_path = public as $$
  select case when p_costo is null or p_costo <= 0 or public.rev_margen() is null then null
              else round(p_costo * (1 + public.rev_margen() / 100)) end
$$;

-- Al insertar una orden sin precio mayorista, se congela el vigente (el backend no tiene que saber nada de esto).
create or replace function public.rev_ordenes_precio_mayorista() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.precio_mayorista is null then
    select public.rev_precio_mayorista(i.costo) into new.precio_mayorista from public.rev_catalogo_info i where i.sku = new.sku;
  end if;
  return new;
end $$;
drop trigger if exists rev_ordenes_precio_mayorista_t on public.rev_ordenes;
create trigger rev_ordenes_precio_mayorista_t before insert on public.rev_ordenes
  for each row execute function public.rev_ordenes_precio_mayorista();

-- ───────────────────────── 4. Funciones del revendedor ─────────────────────────
-- Su cuenta (sin tokens). Vacío si el usuario no es un revendedor.
create or replace function public.rev_mi_cuenta()
returns table (id bigint, nickname text, ml_user_id bigint, estado text, conectado_at timestamptz, conectada boolean, modo text)
language sql stable security definer set search_path = public as $$
  select r.id, r.nickname, r.ml_user_id, r.estado, r.conectado_at, (r.access_token is not null),
         (select c.valor #>> '{}' from public.rev_config c where c.clave = 'modo')
  from public.rev_revendedores r where r.id = public.rev_mi_id()
$$;

-- Catálogo habilitado con el stock que se ofrece y el precio mayorista. Lo ven revendedores y admin.
create or replace function public.rev_catalogo_listar()
returns table (sku text, titulo text, color text, medida text, categoria text, foto text, stock_ofrecido integer,
               precio_mayorista numeric, precio_sugerido numeric, precio_minimo numeric, ya_publicado boolean)
language sql stable security definer set search_path = public as $$
  select c.sku,
         coalesce(i.titulo, r.nombre, p.producto),
         p.color, p.medida,
         coalesce(r.categoria_manual, r.categoria_auto, p.categoria),
         i.fotos ->> 0,
         coalesce(s.ofrecido, 0),
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

-- Detalle completo (descripción, todas las fotos) de los SKUs elegidos, para armar la planilla de carga masiva.
create or replace function public.rev_catalogo_detalle(p_skus text[])
returns table (sku text, titulo text, descripcion text, fotos jsonb, color text, medida text, categoria text,
               stock_ofrecido integer, precio_mayorista numeric, precio_sugerido numeric, precio_minimo numeric)
language sql stable security definer set search_path = public as $$
  select c.sku,
         coalesce(i.titulo, r.nombre, p.producto),
         i.descripcion,
         coalesce(i.fotos, '[]'::jsonb),
         p.color, p.medida,
         coalesce(r.categoria_manual, r.categoria_auto, p.categoria),
         coalesce(s.ofrecido, 0),
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

-- Mis publicaciones (vinculadas a un SKU o sin reconocer) con el stock que se les está ofreciendo.
create or replace function public.rev_mis_publicaciones()
returns table (id bigint, ml_item_id text, ml_variation_id bigint, sku text, titulo text, estado_ml text, precio numeric,
               precio_minimo numeric, bajo_minimo boolean, vinculo text, ofrecido integer,
               ultima_cantidad_enviada integer, ultimo_envio_at timestamptz)
language sql stable security definer set search_path = public as $$
  select b.id, b.ml_item_id, b.ml_variation_id, b.sku,
         coalesce(i.titulo, r.nombre, p.producto),
         b.estado_ml, b.precio, c.precio_minimo,
         (c.precio_minimo is not null and b.precio is not null and b.precio < c.precio_minimo),
         b.vinculo, s.ofrecido, b.ultima_cantidad_enviada, b.ultimo_envio_at
  from public.rev_publicaciones b
  left join public.rev_catalogo c on c.sku = b.sku
  left join public.rev_stock_ofrecido s on s.sku = b.sku
  left join public.rev_catalogo_info i on i.sku = b.sku
  left join public.cc_productos p on p.sku = b.sku
  left join public.repo_productos r on r.sku = b.sku
  where b.revendedor_id = public.rev_mi_id()
  order by (b.sku is null) desc, b.ml_item_id
$$;

-- Corregir el SKU de una publicación mía (queda marcada como vínculo manual).
create or replace function public.rev_vincular_publicacion(p_id bigint, p_sku text) returns void
language plpgsql security definer set search_path = public as $$
declare v_sku text := nullif(trim(p_sku), '');
begin
  if public.rev_mi_id() is null then raise exception 'No sos un revendedor.'; end if;
  if v_sku is null or not exists (select 1 from public.rev_catalogo c where c.sku = v_sku and c.habilitado) then
    raise exception 'Ese SKU no está en el catálogo habilitado.';
  end if;
  update public.rev_publicaciones set sku = v_sku, vinculo = 'manual', actualizado_at = now()
   where id = p_id and revendedor_id = public.rev_mi_id();
  if not found then raise exception 'No encontré esa publicación.'; end if;
end $$;

-- Agregar a mano una publicación mía (por número o link de Mercado Libre) vinculada a un SKU.
create or replace function public.rev_agregar_publicacion(p_item text, p_variation bigint, p_sku text) returns bigint
language plpgsql security definer set search_path = public as $$
declare v_num text; v_item text; v_sku text := nullif(trim(p_sku), ''); v_id bigint;
begin
  if public.rev_mi_id() is null then raise exception 'No sos un revendedor.'; end if;
  v_num := substring(upper(coalesce(p_item, '')) from 'MLA-?([0-9]{6,})');
  if v_num is null then raise exception 'No reconocí el número de publicación (tiene que ser tipo MLA1234567890).'; end if;
  v_item := 'MLA' || v_num;
  if v_sku is null or not exists (select 1 from public.rev_catalogo c where c.sku = v_sku and c.habilitado) then
    raise exception 'Ese SKU no está en el catálogo habilitado.';
  end if;
  insert into public.rev_publicaciones (revendedor_id, ml_item_id, ml_variation_id, sku, vinculo)
  values (public.rev_mi_id(), v_item, p_variation, v_sku, 'manual')
  on conflict (revendedor_id, ml_item_id, coalesce(ml_variation_id, 0))
  do update set sku = excluded.sku, vinculo = 'manual', actualizado_at = now()
  returning id into v_id;
  return v_id;
end $$;

-- Quitar una publicación que agregué a mano (las que detecta el sistema no se pueden quitar).
create or replace function public.rev_quitar_publicacion(p_id bigint) returns void
language plpgsql security definer set search_path = public as $$
begin
  delete from public.rev_publicaciones where id = p_id and revendedor_id = public.rev_mi_id() and vinculo = 'manual';
  if not found then raise exception 'Solo se pueden quitar las publicaciones agregadas a mano.'; end if;
end $$;

-- Ventas (a precio mayorista). Revendedor: las suyas. Admin: las de todos, o de uno si pasa p_revendedor.
create or replace function public.rev_ventas(p_revendedor bigint default null)
returns table (id bigint, revendedor_id bigint, nickname text, ml_order_id bigint, sku text, titulo text, cantidad integer,
               precio_unitario numeric, precio_mayorista numeric, estado text, creado_at timestamptz)
language sql stable security definer set search_path = public as $$
  select o.id, o.revendedor_id, r.nickname, o.ml_order_id, o.sku,
         coalesce(i.titulo, pr.nombre, p.producto), o.cantidad, o.precio_unitario, o.precio_mayorista, o.estado, o.creado_at
  from public.rev_ordenes o
  join public.rev_revendedores r on r.id = o.revendedor_id
  left join public.rev_catalogo_info i on i.sku = o.sku
  left join public.cc_productos p on p.sku = o.sku
  left join public.repo_productos pr on pr.sku = o.sku
  where o.revendedor_id = public.rev_mi_id()
     or (public.rev_es_admin() and (p_revendedor is null or o.revendedor_id = p_revendedor))
  order by o.creado_at desc
  limit 5000
$$;

-- Pagos registrados (mismos criterios de acceso que rev_ventas).
create or replace function public.rev_pagos_listar(p_revendedor bigint default null)
returns table (id bigint, revendedor_id bigint, nickname text, fecha date, monto numeric, nota text)
language sql stable security definer set search_path = public as $$
  select g.id, g.revendedor_id, r.nickname, g.fecha, g.monto, g.nota
  from public.rev_pagos g join public.rev_revendedores r on r.id = g.revendedor_id
  where g.revendedor_id = public.rev_mi_id()
     or (public.rev_es_admin() and (p_revendedor is null or g.revendedor_id = p_revendedor))
  order by g.fecha desc, g.id desc
$$;

-- Cuenta corriente: lo vendido a precio mayorista (órdenes ya descontadas del stock), lo pagado y el saldo.
-- `ventas_simuladas`: órdenes del modo prueba ('sombra'), que todavía no cuentan para cobrar.
create or replace function public.rev_cuenta_resumen(p_revendedor bigint default null)
returns table (revendedor_id bigint, nickname text, ordenes integer, ventas_mayorista numeric, ventas_simuladas numeric,
               sin_precio integer, pagado numeric, saldo numeric)
language sql stable security definer set search_path = public as $$
  select r.id, r.nickname,
         (select count(*)::int from public.rev_ordenes o where o.revendedor_id = r.id and o.estado = 'descontada'),
         coalesce((select sum(o.cantidad * o.precio_mayorista) from public.rev_ordenes o where o.revendedor_id = r.id and o.estado = 'descontada'), 0),
         coalesce((select sum(o.cantidad * o.precio_mayorista) from public.rev_ordenes o where o.revendedor_id = r.id and o.estado = 'sombra'), 0),
         (select count(*)::int from public.rev_ordenes o where o.revendedor_id = r.id and o.estado in ('descontada', 'sombra') and o.precio_mayorista is null),
         coalesce((select sum(g.monto) from public.rev_pagos g where g.revendedor_id = r.id), 0),
         coalesce((select sum(o.cantidad * o.precio_mayorista) from public.rev_ordenes o where o.revendedor_id = r.id and o.estado = 'descontada'), 0)
           - coalesce((select sum(g.monto) from public.rev_pagos g where g.revendedor_id = r.id), 0)
  from public.rev_revendedores r
  where r.id = public.rev_mi_id()
     or (public.rev_es_admin() and (p_revendedor is null or r.id = p_revendedor))
  order by r.nickname
$$;

-- ───────────────────────── 5. Funciones de administración (admin / dueño) ─────────────────────────
create or replace function public.rev_admin_revendedores()
returns table (id bigint, nickname text, ml_user_id bigint, estado text, conectado_at timestamptz, conectada boolean, usuarios text)
language sql stable security definer set search_path = public as $$
  select r.id, r.nickname, r.ml_user_id, r.estado, r.conectado_at, (r.access_token is not null),
         (select string_agg(u.email, ', ' order by u.email) from public.rev_usuarios x join auth.users u on u.id = x.user_id where x.revendedor_id = r.id)
  from public.rev_revendedores r
  where public.rev_es_admin()
  order by r.nickname
$$;

create or replace function public.rev_vincular_usuario(p_email text, p_revendedor bigint) returns void
language plpgsql security definer set search_path = public, auth as $$
declare v_uid uuid;
begin
  if not public.rev_es_admin() then raise exception 'Sin permiso.'; end if;
  select u.id into v_uid from auth.users u where lower(u.email) = lower(trim(p_email));
  if v_uid is null then raise exception 'No hay ningún usuario con ese email. Primero invitalo desde Supabase → Authentication → Users.'; end if;
  if not exists (select 1 from public.rev_revendedores r where r.id = p_revendedor) then raise exception 'Revendedor inexistente.'; end if;
  if exists (select 1 from public.cc_perfiles where id = v_uid) then raise exception 'Ese usuario es del equipo (panel de control), no puede ser un revendedor.'; end if;
  insert into public.rev_usuarios (user_id, revendedor_id) values (v_uid, p_revendedor)
  on conflict (user_id) do update set revendedor_id = excluded.revendedor_id;
end $$;

create or replace function public.rev_desvincular_usuario(p_email text) returns void
language plpgsql security definer set search_path = public, auth as $$
begin
  if not public.rev_es_admin() then raise exception 'Sin permiso.'; end if;
  delete from public.rev_usuarios where user_id in (select u.id from auth.users u where lower(u.email) = lower(trim(p_email)));
end $$;

create or replace function public.rev_registrar_pago(p_revendedor bigint, p_fecha date, p_monto numeric, p_nota text) returns bigint
language plpgsql security definer set search_path = public as $$
declare v_id bigint;
begin
  if not public.rev_es_admin() then raise exception 'Sin permiso.'; end if;
  insert into public.rev_pagos (revendedor_id, fecha, monto, nota) values (p_revendedor, coalesce(p_fecha, current_date), p_monto, nullif(trim(p_nota), ''))
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.rev_borrar_pago(p_id bigint) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.rev_es_admin() then raise exception 'Sin permiso.'; end if;
  delete from public.rev_pagos where id = p_id;
end $$;

-- Margen mayorista (%) que el dueño suma al costo. NULL = todavía no definido (el catálogo muestra "a definir").
create or replace function public.rev_admin_margen() returns numeric
language sql stable security definer set search_path = public as $$
  select case when public.rev_es_admin() then public.rev_margen() end
$$;

create or replace function public.rev_set_margen(p numeric) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.rev_es_admin() then raise exception 'Sin permiso.'; end if;
  if p is null or p < 0 or p > 500 then raise exception 'El margen tiene que estar entre 0 y 500 %%.'; end if;
  insert into public.rev_config (clave, valor, actualizado_at) values ('margen_mayorista', to_jsonb(p), now())
  on conflict (clave) do update set valor = excluded.valor, actualizado_at = now();
end $$;

-- ───────────────────────── 6. Permisos de ejecución ─────────────────────────
-- Las funciones nacen ejecutables por PUBLIC (incluye anon): se cierra y se abre solo para usuarios logueados.
-- Las internas (rev_margen, rev_precio_mayorista, triggers) quedan sin permiso: las usan las funciones de arriba como dueño.
revoke all on function
  public.rev_mi_id(), public.rev_es_admin(), public.rev_margen(), public.rev_precio_mayorista(numeric), public.rev_ordenes_precio_mayorista(),
  public.rev_mi_cuenta(), public.rev_catalogo_listar(), public.rev_catalogo_detalle(text[]), public.rev_mis_publicaciones(),
  public.rev_vincular_publicacion(bigint, text), public.rev_agregar_publicacion(text, bigint, text), public.rev_quitar_publicacion(bigint),
  public.rev_ventas(bigint), public.rev_pagos_listar(bigint), public.rev_cuenta_resumen(bigint),
  public.rev_admin_revendedores(), public.rev_vincular_usuario(text, bigint), public.rev_desvincular_usuario(text),
  public.rev_registrar_pago(bigint, date, numeric, text), public.rev_borrar_pago(bigint), public.rev_admin_margen(), public.rev_set_margen(numeric)
  from public, anon;

grant execute on function
  public.rev_mi_cuenta(), public.rev_catalogo_listar(), public.rev_catalogo_detalle(text[]), public.rev_mis_publicaciones(),
  public.rev_vincular_publicacion(bigint, text), public.rev_agregar_publicacion(text, bigint, text), public.rev_quitar_publicacion(bigint),
  public.rev_ventas(bigint), public.rev_pagos_listar(bigint), public.rev_cuenta_resumen(bigint),
  public.rev_admin_revendedores(), public.rev_vincular_usuario(text, bigint), public.rev_desvincular_usuario(text),
  public.rev_registrar_pago(bigint, date, numeric, text), public.rev_borrar_pago(bigint), public.rev_admin_margen(), public.rev_set_margen(numeric)
  to authenticated;
