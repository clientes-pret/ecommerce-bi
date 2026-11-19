-- Disparador genérico de las funciones de revendedores (rev-sync, rev-vincular), con el mismo token de Vault.
-- Solo funciona con nombres de una lista fija: no se puede usar para llamar a cualquier otra función.
create or replace function public.disparar_rev_funcion(p_nombre text) returns bigint
language plpgsql security definer set search_path = public, extensions, vault as $$
declare tok text; req bigint;
begin
  if p_nombre not in ('rev-sync', 'rev-vincular') then raise exception 'Función no permitida: %', p_nombre; end if;
  select decrypted_secret into tok from vault.decrypted_secrets where name = 'rev_sync_token' limit 1;
  if tok is null then raise exception 'Falta el secreto rev_sync_token en Vault'; end if;
  select net.http_post(
    url := 'https://fvkbclkofsggbvwcxipa.supabase.co/functions/v1/' || p_nombre || '?t=' || tok,
    headers := jsonb_build_object('Content-Type', 'application/json'),
    body := '{}'::jsonb,
    timeout_milliseconds := 120000
  ) into req;
  return req;
end $$;
revoke all on function public.disparar_rev_funcion(text) from public, anon, authenticated;
