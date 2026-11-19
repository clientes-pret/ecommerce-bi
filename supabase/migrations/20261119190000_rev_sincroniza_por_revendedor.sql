-- Sincronización por revendedor (19/11/2026, pedido del usuario: "probar la sincronización SOLAMENTE con CASAPINAR").
--   sincroniza_stock   → el sistema le pone el stock ofrecido a sus publicaciones vinculadas y descuenta sus ventas en
--                        Ecomm-App (si además tiene descuento_auto), aunque el resto siga en modo prueba. Arranca apagado.
--   solo_publicaciones → cuenta propia (ej. LAVANCASA, Casa Laván) que usa el portal SOLO para publicar: nunca se le toca
--                        el stock ni se registran/descuentan sus ventas (su stock ya lo maneja Ecomm-App).
alter table public.rev_revendedores add column if not exists sincroniza_stock   boolean not null default false;
alter table public.rev_revendedores add column if not exists solo_publicaciones boolean not null default false;
