-- Cierra el acceso con la clave pública a las tablas de Reposición que habían quedado sin RLS: cualquiera con la
-- anon key (visible en el sitio) podía leerlas, modificarlas y borrarlas.
-- Las usan solo los jobs de Python y las Edge Functions (clave de servicio, que no pasa por RLS) y funciones
-- "security definer" de la base. Las pantallas no las leen directo. Por eso no hace falta ninguna policy.

alter table repo_productos         enable row level security;
alter table repo_stock_snapshot    enable row level security;
alter table repo_calculo_semanal   enable row level security;
alter table repo_quiebre_historial enable row level security;
alter table repo_settings          enable row level security;
alter table repo_sync_estado       enable row level security;
alter table repo_ventas_items      enable row level security;
alter table repo_items_ml_cache    enable row level security;

revoke all on
  repo_productos, repo_stock_snapshot, repo_calculo_semanal, repo_quiebre_historial,
  repo_settings, repo_sync_estado, repo_ventas_items, repo_items_ml_cache
from anon, authenticated;

revoke all on sequence
  repo_stock_snapshot_id_seq, repo_calculo_semanal_id_seq, repo_quiebre_historial_id_seq, repo_ventas_items_id_seq
from anon, authenticated;
