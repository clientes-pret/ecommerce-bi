// Correr: node reposicion/tests/js/stock_lib_test.mjs
import assert from 'node:assert/strict';
import * as S from '../../dashboard/proveedores/stock-lib.js';

const f = (o) => ({ dep: 0, full_pret: 0, full_lavan: 0, vel: 0, vendido: 0, costo: 100, plazo: 60, proveedor: 'A', sku: 'x', ...o });
const stock = [
  f({ sku: 'rapido', dep: 10, vel: 1, vendido: 60 }),                    // 10 días
  f({ sku: 'justo', dep: 30, full_pret: 30, vel: 1, vendido: 60 }),      // 60 días → tramo 31-60
  f({ sku: 'lento', dep: 400, vel: 1, vendido: 5 }),                     // 400 días
  f({ sku: 'quieto', dep: 7, vel: 0 }),
  f({ sku: 'vielsinventa', dep: 3, vel: 0.5, vendido: 0 }),              // ritmo viejo pero sin ventas: sin ventas
  f({ sku: 'sincosto', dep: 5, vel: 1, vendido: 3, costo: null }),
];
assert.equal(S.tramoDe(stock[0]), 't30');
assert.equal(S.tramoDe(stock[1]), 't60');
assert.equal(S.tramoDe(stock[2]), 'mas');
assert.equal(S.tramoDe(stock[3]), 'sin');
assert.equal(S.tramoDe(stock[4]), 'sin');
const t = S.porTramo(stock);
assert.equal(S.suma(t, (x) => x.plata), S.suma(stock, S.valor), 'los tramos suman el total');
assert.equal(t.find((x) => x.id === 'sin').unidades, 10);
assert.equal(Math.round(S.diasDeStock([stock[0], stock[1]])), 35);       // 7000 / 200 por día
assert.equal(S.diasDeStock([stock[3]]), null);

const fr = S.frenada(stock);
assert.deepEqual(fr.map((x) => [x.sku, x.sobran]), [['lento', 340], ['quieto', 7], ['vielsinventa', 3]]);

const HOY = '2026-11-27';
const l = (o) => ({ estado: 'detectado', proveedor: 'A', costo: 10, unidades: 100, quedan: 0, quedan_al_pago: 0, plazo: 60, fecha: '2026-09-01', pago: '2026-10-31', agotado_el: null, ...o });
const lotes = [
  l({ agotado_el: '2026-09-11' }),                                       // pagado y vendido en 10 días
  l({ quedan: 40, quedan_al_pago: 50 }),                                 // venció con 50 sin vender
  l({ fecha: '2026-11-01', pago: '2026-12-10', quedan: 80, quedan_al_pago: 30 }),
  l({ fecha: '2026-11-10', pago: '2027-01-20', quedan: 90, quedan_al_pago: 0, agotado_el: null }),
  l({ estado: 'descartado', quedan: 100, quedan_al_pago: 100 }),
  l({ proveedor: 'B', costo: null, fecha: '2026-09-01', agotado_el: '2026-10-01' }),
];
assert.deepEqual(lotes.map((x) => S.estadoLote(x, HOY).id), ['vendido', 'pagado', 'riesgo', 'llega', 'descartado', 'vendido']);
assert.equal(S.estadoLote(l({ quedan: 5, quedan_al_pago: 0 }), HOY).id, 'atiempo');
assert.equal(S.vendidoAlPago(lotes, HOY), 0.75, 'solo vencidos, sin descartados: 1 - 500/2000');
assert.equal(S.vendidoAlPago([lotes[2]], HOY), null);
const p30 = S.proximos(lotes, HOY, 1, 30);
assert.deepEqual([p30.aPagar, p30.sinVender], [1000, 300]);
assert.equal(S.proximos(lotes, HOY, 31, 60).aPagar, 1000);
assert.equal(S.mediana([5, 1, 9]), 5);
assert.equal(S.mediana([2, 4]), 3);
assert.equal(S.mediana([]), null);

const prov = S.porProveedor(lotes, [...stock, f({ proveedor: 'C', dep: 2, vel: 1, vendido: 2, plazo: 90 })], HOY);
const A = prov.find((x) => x.proveedor === 'A'), B = prov.find((x) => x.proveedor === 'B'), C = prov.find((x) => x.proveedor === 'C');
assert.equal(A.ingresado, 4000);
assert.equal(A.diasEnAgotar, 10);
assert.equal(A.sinVender60, 300);
assert.equal(B.diasEnAgotar, 30);
assert.equal(B.plataStock, 0);
assert.equal(C.plazo, 90, 'sin ingresos toma el plazo del stock');
assert.equal(C.pctAlPago, null);
console.log('stock-lib: todo OK');
