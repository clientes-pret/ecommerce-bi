-- Empaquetado en el panel de control (proveedores/empaquetado.html, 5/11/2026): quién empaquetó cada pedido.
-- Las tablas las sigue escribiendo solo la Edge Function armado-api (service_role); el panel las lee con su
-- login (admin / dueño), igual que busqueda_profunda. La clave pública sigue sin acceso.

drop policy if exists armado_controles_lectura on armado_controles;
create policy armado_controles_lectura on armado_controles for select to authenticated
  using (cc_rol() in ('admin', 'dueno'));

drop policy if exists armado_items_lectura on armado_items;
create policy armado_items_lectura on armado_items for select to authenticated
  using (cc_rol() in ('admin', 'dueno'));

revoke all on armado_controles, armado_items from anon;
grant select on armado_controles, armado_items to authenticated;
