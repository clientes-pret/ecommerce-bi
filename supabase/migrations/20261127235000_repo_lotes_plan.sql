-- Estas funciones se llaman muchas veces seguidas sobre la misma conexión. Sin esto, a partir de la sexta Postgres puede
-- pasar a un plan genérico que lee toda la tabla de fotos de stock y supera el tiempo permitido por pedido.
alter function repo_calcular_lotes(text[])   set plan_cache_mode = force_custom_plan;
alter function repo_detectar_ingresos(date)  set plan_cache_mode = force_custom_plan;
alter function repo_lotes(date)              set plan_cache_mode = force_custom_plan;
