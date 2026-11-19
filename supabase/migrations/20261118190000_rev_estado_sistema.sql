-- Pantalla "Estado del sistema" de revendedores (solo admin/dueño): junta en un solo JSON el estado de los procesos
-- programados, el stock que se ofrece, cada revendedor, las ventas registradas, los avisos recibidos y las alertas de precio.
-- Solo lee. Si quien llama no es admin/dueño devuelve error (misma regla que el resto de las funciones de administración).
create or replace function public.rev_estado_sistema() returns jsonb
language plpgsql stable security definer set search_path = public, cron as $$
begin
  if not public.rev_es_admin() then raise exception 'Sin permiso.'; end if;
  return jsonb_build_object(
    'ahora', now(),
    'modo', (select c.valor #>> '{}' from public.rev_config c where c.clave = 'modo'),
    'colchon', (select c.valor #>> '{}' from public.rev_config c where c.clave = 'colchon'),
    'margen', (select c.valor #>> '{}' from public.rev_config c where c.clave = 'margen_mayorista'),
    'trabajos', coalesce((
      select jsonb_agg(jsonb_build_object('nombre', j.jobname, 'frecuencia', j.schedule, 'activo', j.active,
                                          'ultima_vez', d.start_time, 'estado', d.status) order by j.jobname)
        from cron.job j
        left join lateral (select x.start_time, x.status from cron.job_run_details x where x.jobid = j.jobid order by x.start_time desc limit 1) d on true
       where j.jobname like 'rev-%'), '[]'::jsonb),
    'procesos', coalesce((
      select jsonb_object_agg(s.clave, jsonb_build_object('ultimo_at', s.ultimo_at, 'resultado', s.resultado, 'detalle', s.detalle))
        from public.rev_sync_estado s where s.clave in ('stock', 'vinculacion', 'ordenes', 'conciliacion')), '{}'::jsonb),
    'oferta', (select jsonb_build_object('skus', count(*), 'con_oferta', count(*) filter (where o.ofrecido > 0),
                                         'unidades', coalesce(sum(o.ofrecido), 0), 'ultima_lectura', max(o.consultado_at))
                 from public.rev_stock_ofrecido o),
    'catalogo', (select count(*) from public.rev_catalogo c where c.habilitado),
    'revendedores', coalesce((
      select jsonb_agg(jsonb_build_object(
          'id', r.id, 'nickname', r.nickname, 'estado', r.estado, 'conectado_at', r.conectado_at, 'token_vence', r.token_expires_at,
          'publicaciones', (select count(*) from public.rev_publicaciones p where p.revendedor_id = r.id),
          'vinculadas', (select count(*) from public.rev_publicaciones p where p.revendedor_id = r.id and p.sku is not null),
          'activas', (select count(*) from public.rev_publicaciones p where p.revendedor_id = r.id and p.estado_ml = 'active'),
          'activas_sin_reconocer', (select count(*) from public.rev_publicaciones p where p.revendedor_id = r.id and p.estado_ml = 'active' and p.sku is null),
          'ventas', (select count(*) from public.rev_ordenes o where o.revendedor_id = r.id and o.estado in ('sombra', 'descontada')),
          'ultima_venta', (select max(o.creado_at) from public.rev_ordenes o where o.revendedor_id = r.id)
        ) order by r.id)
        from public.rev_revendedores r), '[]'::jsonb),
    'ventas', jsonb_build_object(
      'por_estado', coalesce((select jsonb_object_agg(e.estado, e.n) from (select o.estado, count(*) as n from public.rev_ordenes o group by o.estado) e), '{}'::jsonb),
      'no_reconocidas', (select count(*) from public.rev_ordenes o where o.detalle like 'SKU no reconocido%' and o.estado <> 'cancelada'),
      'ultimas_24h', (select count(*) from public.rev_ordenes o where o.creado_at > now() - interval '24 hours')),
    'avisos_24h', coalesce((
      select jsonb_object_agg(a.topic, a.n)
        from (select a0.topic, count(*) as n from public.rev_ml_avisos a0
               where a0.recibido_at > now() - interval '24 hours'
                 and a0.ml_user_id in (select r.ml_user_id from public.rev_revendedores r)
               group by a0.topic) a), '{}'::jsonb),
    'minimos_cargados', (select count(*) from public.rev_catalogo c where c.precio_minimo is not null),
    'bajo_minimo', coalesce((
      select jsonb_agg(jsonb_build_object('revendedor', x.nickname, 'item', x.ml_item_id, 'sku', x.sku, 'precio', x.precio, 'minimo', x.precio_minimo))
        from (select r.nickname, p.ml_item_id, p.sku, p.precio, c.precio_minimo
                from public.rev_publicaciones p
                join public.rev_revendedores r on r.id = p.revendedor_id
                join public.rev_catalogo c on c.sku = p.sku
               where p.estado_ml = 'active' and c.precio_minimo is not null and p.precio < c.precio_minimo
               order by r.nickname, p.sku limit 50) x), '[]'::jsonb)
  );
end $$;
revoke all on function public.rev_estado_sistema() from public, anon;
grant execute on function public.rev_estado_sistema() to authenticated;
