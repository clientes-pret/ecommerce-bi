-- "Fuera de temporada": marca por producto (ej. todo lo de invierno en verano)
-- para que no aparezca en el tablero salvo que se elija verlo en el filtro.
-- A diferencia de "descontinuado" no saca al producto del cálculo de reposición
-- (sigue calculándose y se puede volver a la temporada con un click).

alter table repo_productos
  add column if not exists fuera_de_temporada     boolean not null default false,
  add column if not exists fuera_de_temporada_at  timestamptz,
  add column if not exists fuera_de_temporada_por text;
