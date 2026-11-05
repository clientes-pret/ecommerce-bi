-- Último evento de webhook recibido por canal (ventas-webhook). Sirve para detectar que el webhook
-- dejó de llegar: si ultimo_evento_at es viejo en horario comercial, algo se cayó (el sync diario
-- igual repara las ventas). La Edge Function escribe con service_role; nadie más necesita acceso.

create table if not exists repo_webhook_estado (
  canal            text primary key check (canal in ('tn_pret','tn_lavan','ml_pret','ml_lavan')),
  ultimo_evento    text,
  ultimo_order_id  text,
  ultimo_resultado text,
  ultimo_evento_at timestamptz
);

alter table repo_webhook_estado enable row level security;
revoke all on repo_webhook_estado from anon, authenticated;
