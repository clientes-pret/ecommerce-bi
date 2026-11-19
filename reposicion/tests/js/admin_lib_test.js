// Pruebas de las cuentas del portal de administrador de revendedores.
// Correr con: deno test reposicion/tests/js/admin_lib_test.js
import { assertEquals } from 'jsr:@std/assert@1';
import { agrupar, diaAR, etiquetaMes, mesDe, saldo, semanaDe, totales, ultimasSemanas } from '../../dashboard/revendedores/admin-lib.js';

const L = (o, fecha, q, pu, pm, estado = 'sombra') => ({ ml_order_id: o, creado_at: fecha, cantidad: q, precio_unitario: pu, precio_mayorista: pm, estado });

Deno.test('día y semana en hora de Argentina', () => {
  assertEquals(diaAR('2026-11-20T02:00:00Z'), '2026-11-19');         // 23 h del 19 en Argentina
  assertEquals(semanaDe('2026-11-19T15:00:00Z'), '2026-11-16');      // jueves → lunes 16
  assertEquals(semanaDe('2026-11-16T03:30:00Z'), '2026-11-16');      // lunes 00:30
  assertEquals(semanaDe('2026-11-16T02:30:00Z'), '2026-11-09');      // domingo 23:30 → semana anterior
  assertEquals(mesDe('2026-12-01T02:00:00Z'), '2026-11');            // 30/11 23 h
  assertEquals(etiquetaMes('2026-11'), 'nov 2026');
});

Deno.test('agrupar por semana: ventas, unidades, vendido y costo; canceladas aparte', () => {
  const lineas = [
    L(1, '2026-11-17T15:00:00Z', 2, 100, 60),
    L(1, '2026-11-17T15:00:00Z', 1, 50, 30),        // misma venta, otro SKU
    L(2, '2026-11-18T15:00:00Z', 1, 100, null),     // sin costo cargado
    L(3, '2026-11-10T15:00:00Z', 1, 100, 60),       // semana anterior
    L(4, '2026-11-18T15:00:00Z', 5, 100, 60, 'cancelada'),
    L(5, '2026-11-18T15:00:00Z', 1, 100, 60, 'error'), // no cuenta
  ];
  const g = agrupar(lineas, (l) => semanaDe(l.creado_at));
  assertEquals(g.map((x) => x.clave), ['2026-11-16', '2026-11-09']);
  assertEquals(g[0], { clave: '2026-11-16', ventas: 2, unidades: 4, vendido: 350, costo: 150, sinCosto: 1, canceladas: 1 });
  assertEquals(totales(lineas).vendido, 450);
});

Deno.test('saldo = costo mayorista − pagos', () => {
  const s = saldo([L(1, '2026-11-17T15:00:00Z', 2, 100, 60)], [{ monto: 50 }, { monto: '20' }]);
  assertEquals(s, { costo: 120, pagado: 70, saldo: 50 });
});

Deno.test('últimas semanas terminan en la semana actual', () => {
  assertEquals(ultimasSemanas(3, new Date('2026-11-19T15:00:00Z')), ['2026-11-02', '2026-11-09', '2026-11-16']);
});
