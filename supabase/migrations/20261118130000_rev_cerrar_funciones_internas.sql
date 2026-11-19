-- Cierra a los usuarios logueados las funciones INTERNAS del portal de revendedores.
--
-- La migración 20261118120000 hizo `revoke ... from public, anon` y `grant ... to authenticated` solo para las funciones
-- del front, pero en Supabase `authenticated` recibe permiso de ejecución por defecto sobre toda función nueva del esquema
-- public, así que las internas quedaron ejecutables por cualquier usuario logueado. Sin este arreglo, un revendedor podría
-- llamar `rev_margen()` o `rev_precio_mayorista(100)` por la API y deducir el margen del dueño, y con eso el costo de los
-- productos (costo = precio mayorista / (1 + margen)).
--
-- Estas funciones las siguen usando las funciones del front: son `security definer`, se ejecutan con los permisos de su
-- dueño, no con los del revendedor. Verificado: el front (reposicion/dashboard/revendedores) no las llama directamente.
revoke all on function
  public.rev_mi_id(),
  public.rev_es_admin(),
  public.rev_margen(),
  public.rev_precio_mayorista(numeric),
  public.rev_ordenes_precio_mayorista()
  from public, anon, authenticated;
