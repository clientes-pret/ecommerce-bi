-- El rubro dice QUÉ es el gasto (sueldos, alquiler…) y el área dice EN QUÉ SE USA (marketing, ventas…).
-- Las 4 personas del equipo de marketing eran un rubro "Marketing (equipo y agencias)": pasan a "Sueldos y cargas sociales"
-- y siguen con el área Marketing (ya la tenían). Los totales por mes del historial de la planilla (origen 'planilla_historial')
-- mezclan sueldos y agencias en un solo número y no se pueden separar: quedan como estaban.
update public.cc_costos_recurrentes set categoria = 'Sueldos y cargas sociales'
 where categoria = 'Marketing (equipo y agencias)';
update public.cc_costos set categoria = 'Sueldos y cargas sociales'
 where categoria = 'Marketing (equipo y agencias)' and origen <> 'planilla_historial';
