-- Vincula los proveedores de la cuenta corriente con los pedidos de reposición.
-- repo_pedidos.proveedor es texto libre (el nombre que usa el tablero de reposición);
-- cc_proveedores.repo_nombres guarda con qué nombre(s) se pide cada proveedor.

alter table cc_proveedores add column if not exists repo_nombres text[] not null default '{}';

-- Vínculo automático cuando el nombre coincide exacto (sin mayúsculas/espacios).
update cc_proveedores p
   set repo_nombres = array(
     select distinct r.proveedor from repo_pedidos r
      where upper(trim(r.proveedor)) = upper(trim(p.nombre)))
 where cardinality(p.repo_nombres) = 0
   and exists (select 1 from repo_pedidos r where upper(trim(r.proveedor)) = upper(trim(p.nombre)));

-- Administración puede leer los pedidos. Reposición escribe con service_role (Edge Function y jobs),
-- que ignora RLS, así que activarla no rompe el tablero. Sin policy, anon/depósito no ven nada.
alter table repo_pedidos enable row level security;
alter table repo_pedido_items enable row level security;
drop policy if exists repo_pedidos_admin_read on repo_pedidos;
create policy repo_pedidos_admin_read on repo_pedidos for select to authenticated using (cc_rol() in ('admin','dueno'));
drop policy if exists repo_pedido_items_admin_read on repo_pedido_items;
create policy repo_pedido_items_admin_read on repo_pedido_items for select to authenticated using (cc_rol() in ('admin','dueno'));
