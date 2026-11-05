-- Despacho (reposicion/dashboard/armado.html + Edge Function armado-api, 5/11/2026): qué paquetes se le entregan
-- a cada mensajería cuando pasa a retirar. Quien entrega escanea el QR de cada paquete; una fila por paquete.
-- Zeta y Ninja son una sola opción ('zeta_ninja'): el reparto entre ellos lo define su equipo al retirar.
-- Solo escribe la Edge Function (service_role); el panel lo lee con su login (admin / dueño).

create table if not exists armado_despachos (
  id            bigint generated always as identity primary key,
  creado_at     timestamptz not null default now(),
  fecha         date not null,                    -- día en Argentina
  mensajeria    text not null check (mensajeria in ('zeta_ninja', 'correo', 'andreani')),
  tipo          text not null check (tipo in ('flex_ml', 'tn', 'otro')),
  por           text,
  codigo_leido  text,
  clave         text not null,                    -- identifica el paquete (envío ML, did de Zeta, seguimiento…) para no contarlo dos veces
  canal         text check (canal in ('tn', 'ml')),
  tienda        text check (tienda in ('pret', 'lavan')),
  pedido        text,                             -- se completa en segundo plano; null = paquete sin identificar
  envio         text,
  pedido_clave  text,                             -- 'tn|tienda|numero' o 'ml|envio': para restarlo de lo pendiente
  estado        text not null default 'ok' check (estado in ('ok', 'anulado')),
  anulado_at    timestamptz,
  anulado_por   text
);

-- Un paquete no se cuenta dos veces (los anulados dejan libre la clave)
create unique index if not exists armado_despachos_clave_uq on armado_despachos (clave) where estado = 'ok';
create index if not exists armado_despachos_fecha_idx on armado_despachos (fecha desc, mensajeria);
create index if not exists armado_despachos_pedido_idx on armado_despachos (pedido_clave) where estado = 'ok';

alter table armado_despachos enable row level security;

drop policy if exists armado_despachos_lectura on armado_despachos;
create policy armado_despachos_lectura on armado_despachos for select to authenticated
  using (cc_rol() in ('admin', 'dueno'));

revoke all on armado_despachos from anon;
grant select on armado_despachos to authenticated;
