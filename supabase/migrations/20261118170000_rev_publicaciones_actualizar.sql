-- Actualización en lote de publicaciones de revendedores (la usa rev-vincular).
-- `rev_publicaciones.id` es "generated always": no se puede reescribir con un upsert, así que la actualización va por esta función.
create or replace function public.rev_publicaciones_actualizar(p jsonb) returns integer
language plpgsql security definer set search_path = public as $$
declare n integer;
begin
  update public.rev_publicaciones t
     set sku = x.sku, estado_ml = x.estado_ml, precio = x.precio, actualizado_at = now()
    from jsonb_to_recordset(p) as x(id bigint, sku text, estado_ml text, precio numeric)
   where t.id = x.id;
  get diagnostics n = row_count;
  return n;
end $$;
revoke all on function public.rev_publicaciones_actualizar(jsonb) from public, anon, authenticated;
grant execute on function public.rev_publicaciones_actualizar(jsonb) to service_role;
