-- Actualización diaria del catálogo de revendedores con los SKU de Ecomm-App (pedido del usuario, 24/11/2026).
-- La función rev-catalogo solo suma SKU simples nuevos (nunca cambia ni borra). Corre 07:02 AR (10:02 UTC): entre dos
-- corridas de rev-sync, que también lee Ecomm-App, para no pasar entre las dos el límite de lecturas por minuto.

create or replace function public.disparar_rev_funcion(p_nombre text) returns bigint
language plpgsql security definer set search_path to 'public', 'extensions', 'vault' as $function$
declare tok text; req bigint;
begin
  if p_nombre not in ('rev-sync', 'rev-vincular', 'rev-ordenes', 'rev-conciliar', 'rev-publicar', 'rev-catalogo') then
    raise exception 'Función no permitida: %', p_nombre;
  end if;
  select decrypted_secret into tok from vault.decrypted_secrets where name = 'rev_sync_token' limit 1;
  if tok is null then raise exception 'Falta el secreto rev_sync_token en Vault'; end if;
  select net.http_post(
    url := 'https://fvkbclkofsggbvwcxipa.supabase.co/functions/v1/' || p_nombre || '?t=' || tok,
    headers := jsonb_build_object('Content-Type', 'application/json'),
    body := '{}'::jsonb,
    timeout_milliseconds := 120000
  ) into req;
  return req;
end $function$;

revoke all on function public.disparar_rev_funcion(text) from public, anon, authenticated;

select cron.unschedule('rev-catalogo-dia') where exists (select 1 from cron.job where jobname = 'rev-catalogo-dia');
select cron.schedule('rev-catalogo-dia', '2 10 * * *', $$select public.disparar_rev_funcion('rev-catalogo')$$);
