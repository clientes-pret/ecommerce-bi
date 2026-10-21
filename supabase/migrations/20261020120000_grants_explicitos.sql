-- Desde el 2026-10-30 Supabase deja de dar GRANTs automáticos a tablas nuevas de "public".
-- Esta migración deja explícitos los permisos que la app necesita, así un `db reset`,
-- una branch o un proyecto nuevo quedan funcionando igual que producción.
-- Sólo agrega permisos (no revoca nada): en producción es un no-op.

-- Jobs de Python y Edge Functions (reposicion-api) usan service_role.
grant select, insert, update, delete on
  repo_productos, repo_stock_snapshot, repo_calculo_semanal, repo_quiebre_historial,
  repo_pedidos, repo_pedido_items, repo_settings, repo_ventas_items, repo_sync_estado,
  repo_items_ml_cache,
  cc_perfiles, cc_proveedores, cc_productos, cc_alias_sku, cc_recepciones,
  cc_recepcion_items, cc_movimientos
to service_role;

grant select on cc_cuenta_corriente, cc_saldos, cc_recepciones_resumen to service_role;

-- Cuenta corriente: el front entra como usuario logueado (authenticated) y el acceso
-- fino lo deciden las policies de RLS. anon NO tiene acceso (ver cc_revocar_anon).
grant select, insert, update, delete on
  cc_perfiles, cc_proveedores, cc_productos, cc_alias_sku, cc_recepciones,
  cc_recepcion_items, cc_movimientos
to authenticated;

grant select on cc_cuenta_corriente, cc_saldos, cc_recepciones_resumen to authenticated;

-- Pedidos de reposición: lectura para admins desde cuenta corriente (policy repo_*_admin_read).
grant select on repo_pedidos, repo_pedido_items to authenticated;
