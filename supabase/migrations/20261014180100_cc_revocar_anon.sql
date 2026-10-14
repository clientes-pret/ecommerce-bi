revoke all on cc_perfiles, cc_proveedores, cc_productos, cc_alias_sku, cc_recepciones, cc_recepcion_items, cc_movimientos, cc_cuenta_corriente, cc_saldos, cc_recepciones_resumen from anon;
revoke execute on function cc_rol() from public, anon;
grant execute on function cc_rol() to authenticated;
