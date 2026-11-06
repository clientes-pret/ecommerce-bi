-- Despacho: Zeta y Ninja se eligen por separado antes de empezar a cargar, para que quede registrado a cuál de
-- los dos se le entregó cada paquete (por si hay que reclamar uno perdido). Lo pendiente de entregar sigue
-- siendo un solo total entre los dos. 'zeta_ninja' queda permitido solo por las filas de prueba anteriores.

alter table armado_despachos drop constraint if exists armado_despachos_mensajeria_check;
alter table armado_despachos add constraint armado_despachos_mensajeria_check
  check (mensajeria in ('zeta', 'ninja', 'zeta_ninja', 'correo', 'andreani'));
