-- Disparo programado de la sincronización de stock de revendedores (Edge Function rev-sync).
-- Mismo patrón que `disparar_github_workflow`: el token vive en Vault, nadie lo ve en claro, y la función de la base lo
-- usa para llamar a rev-sync. El token se genera ACÁ ADENTRO (no pasa por ningún archivo ni chat) y rev-sync lo valida
-- contra la base con `rev_sync_token_valido`, así no hace falta guardarlo también como secret de la función.
--
-- El agendado (cada 5 minutos) NO está en esta migración: se programa aparte, después de probar un disparo manual.

create extension if not exists pg_net;

do $$
begin
  if not exists (select 1 from vault.secrets where name = 'rev_sync_token') then
    perform vault.create_secret(encode(extensions.gen_random_bytes(24), 'hex'), 'rev_sync_token', 'Token de disparo de rev-sync');
  end if;
end $$;

create or replace function public.rev_sync_token_valido(p text) returns boolean
language sql stable security definer set search_path = public, vault as $$
  select coalesce(p, '') <> '' and exists (select 1 from vault.decrypted_secrets where name = 'rev_sync_token' and decrypted_secret = p)
$$;
revoke all on function public.rev_sync_token_valido(text) from public, anon, authenticated;
grant execute on function public.rev_sync_token_valido(text) to service_role;

create or replace function public.disparar_rev_sync() returns bigint
language plpgsql security definer set search_path = public, extensions, vault as $$
declare tok text; req bigint;
begin
  select decrypted_secret into tok from vault.decrypted_secrets where name = 'rev_sync_token' limit 1;
  if tok is null then raise exception 'Falta el secreto rev_sync_token en Vault'; end if;
  select net.http_post(
    url := 'https://fvkbclkofsggbvwcxipa.supabase.co/functions/v1/rev-sync?t=' || tok,
    headers := jsonb_build_object('Content-Type', 'application/json'),
    body := '{}'::jsonb,
    timeout_milliseconds := 120000
  ) into req;
  return req;
end $$;
revoke all on function public.disparar_rev_sync() from public, anon, authenticated;
