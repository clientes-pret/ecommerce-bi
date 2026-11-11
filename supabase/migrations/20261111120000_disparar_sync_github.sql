-- Dispara el job de GitHub (cashflow_sync.yml: MercadoPago + scorecard de Tienda Nube y Mercado Libre) desde Supabase.
-- Por qué: el cron propio de GitHub arranca con 4 a 10 horas de retraso (medido 8-10/11/2026), pero un disparo manual
-- (workflow_dispatch) arranca al instante. pg_cron de Supabase sí es puntual.
-- Horarios (hora de Argentina): 05:30 corrida principal (el job dura ~15 min) y 07:30 repaso, por si algo falló o ML
-- terminó de consolidar costos. Las dos son seguras de repetir: el job vuelve a calcular los últimos días y pisa con
-- el mismo método.
-- El token de GitHub NO está acá: se guarda en Vault con vault.create_secret(<token>, 'github_dispatch_token')
-- (token fine-grained solo para clientes-pret/ecommerce-bi, permiso "Actions: Read and write").

create extension if not exists pg_cron;
create extension if not exists pg_net;

create or replace function public.disparar_github_workflow(p_workflow text)
returns bigint
language plpgsql
security definer
set search_path = public, extensions, vault
as $$
declare
  tok text;
  req bigint;
begin
  select decrypted_secret into tok from vault.decrypted_secrets where name = 'github_dispatch_token' limit 1;
  if tok is null then
    raise exception 'Falta el secreto github_dispatch_token en Vault';
  end if;
  select net.http_post(
    url := 'https://api.github.com/repos/clientes-pret/ecommerce-bi/actions/workflows/' || p_workflow || '/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || tok,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'supabase-pg-cron',
      'Content-Type', 'application/json'),
    body := '{"ref":"master"}'::jsonb
  ) into req;
  return req;
end;
$$;

-- Solo la base (pg_cron corre como postgres) puede dispararlo: nadie desde el navegador.
revoke all on function public.disparar_github_workflow(text) from public, anon, authenticated;

-- Reprogramable: si ya existen, se borran y se vuelven a crear.
select cron.unschedule(jobid) from cron.job where jobname in ('sync-scorecard-0530', 'sync-scorecard-0730');
select cron.schedule('sync-scorecard-0530', '30 8 * * *',  $$select public.disparar_github_workflow('cashflow_sync.yml')$$);   -- 08:30 UTC = 05:30 AR
select cron.schedule('sync-scorecard-0730', '30 10 * * *', $$select public.disparar_github_workflow('cashflow_sync.yml')$$);   -- 10:30 UTC = 07:30 AR
