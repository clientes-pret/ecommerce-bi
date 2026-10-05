-- reposicion-api/getProductos() traía TODO repo_stock_snapshot (240.800+ filas,
-- ~241 llamadas paginadas) y TODO repo_calculo_semanal (41.800+ filas, ~42
-- llamadas paginadas) solo para quedarse con la fila más reciente de cada SKU,
-- descartando el resto en Deno. Ambas tablas crecen ~8.500 filas/día (un
-- snapshot/cálculo por SKU por día), así que esa carga se pone peor cada
-- semana. Estas funciones hacen el "quedarme con la última fila por SKU"
-- directo en Postgres, pasando de ~283 llamadas REST paginadas a 2 llamadas
-- RPC.
--
-- Un DISTINCT ON (sku) ... ORDER BY sku, fecha DESC común escanea la tabla
-- entera igual (Postgres no hace "skip scan" nativo sobre un btree) — medido
-- en 2,7s para repo_stock_snapshot. La técnica de abajo (recursive CTE +
-- LATERAL, "loose index scan") en cambio hace UN salto de índice por SKU
-- distinto en vez de leer cada fila: 8.500 saltos en vez de 275.000 filas —
-- medido en ~80ms, ~33x más rápido, y escala con la cantidad de SKUs (estable)
-- en vez de con el historial acumulado (que crece todos los días).
--
-- Requiere un índice (sku, fecha/semana_iso DESC) para poder saltar en ese
-- orden — el de repo_stock_snapshot ya existía (idx_repo_stock_snapshot_sku_fecha);
-- el de repo_calculo_semanal solo tenía ASC (idx_repo_calculo_sku_semana), así
-- que se agrega la versión DESC.

create index if not exists idx_repo_calculo_sku_semana_desc
  on public.repo_calculo_semanal (sku, semana_iso desc);

create or replace function public.repo_stock_snapshot_latest()
returns setof public.repo_stock_snapshot
language sql
stable
as $$
  with recursive t as (
    (select * from public.repo_stock_snapshot order by sku, fecha desc limit 1)
    union all
    select s.* from t,
      lateral (
        select *
        from public.repo_stock_snapshot
        where sku > t.sku
        order by sku, fecha desc
        limit 1
      ) s
  )
  select * from t;
$$;

create or replace function public.repo_calculo_semanal_latest()
returns setof public.repo_calculo_semanal
language sql
stable
as $$
  with recursive t as (
    (select * from public.repo_calculo_semanal order by sku, semana_iso desc limit 1)
    union all
    select s.* from t,
      lateral (
        select *
        from public.repo_calculo_semanal
        where sku > t.sku
        order by sku, semana_iso desc
        limit 1
      ) s
  )
  select * from t;
$$;

-- El linter de seguridad de Supabase marca "search_path mutable" en cualquier
-- función sin search_path fijo (aunque acá los nombres ya están calificados
-- como public.tabla) — se cierra fijándolo explícito, buena práctica estándar.
alter function public.repo_stock_snapshot_latest() set search_path = public, pg_temp;
alter function public.repo_calculo_semanal_latest() set search_path = public, pg_temp;
