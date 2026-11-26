-- Cuentas propias de "solo publicar" (ej. LAVANCASA, la cuenta de Casa Laván): su stock lo maneja Ecomm-App. Este candado
-- impide que queden activas/desactivadas como un revendedor o con "Sincroniza stock" prendido, que es lo que haría que
-- el sistema les mande stock o descuente sus ventas (pedido del usuario, 26/11/2026). Las funciones además las excluyen
-- por código (rev-sync y el descuento de ventas, también por rev_config.cuentas_propias).
alter table rev_revendedores
  add constraint rev_revendedores_cuenta_propia_sin_stock
  check (not coalesce(solo_publicaciones, false) or (estado not in ('activo', 'inactivo') and not coalesce(sincroniza_stock, false)));
