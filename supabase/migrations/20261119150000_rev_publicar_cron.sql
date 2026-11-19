-- Publicación masiva: la base dispara rev-publicar cada minuto para que publique lo pendiente de rev_publicar_cola.
-- Aplicar DESPUÉS de desplegar la función (supabase functions deploy rev-publicar).
create or replace function public.disparar_rev_funcion(p_nombre text) returns bigint
language plpgsql security definer set search_path = public, extensions, vault as $$
declare tok text; req bigint;
begin
  if p_nombre not in ('rev-sync', 'rev-vincular', 'rev-ordenes', 'rev-conciliar', 'rev-publicar') then raise exception 'Función no permitida: %', p_nombre; end if;
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

-- Solo dispara si hay algo pendiente (o trabado), para no llamar a la función cada minuto sin motivo.
select cron.schedule('rev-publicar-minuto', '* * * * *', $$
  select public.disparar_rev_funcion('rev-publicar')
  where exists (select 1 from public.rev_publicar_cola where estado in ('pendiente', 'procesando'))
$$);
