// Segmentación grupo → medida. Correr: node reposicion/tests/js/grupos_test.mjs
import assert from 'node:assert/strict';
import { grupoYMedida, segmentar } from '../../dashboard/proveedores/grupos.js';

const casos = [
  ['Sábanas Hoteleras 1200 H Queen - Pret', 'Azul', 'Sábanas Hoteleras 1200 H', 'Queen', 'Azul'],
  ['Sábanas Hoteleras 1200 H Twin - Pret', 'Azul Marino', 'Sábanas Hoteleras 1200 H', 'Twin', 'Azul Marino'],
  ['Cortinas 100% Black Out 2 Paños 140x210 Cm - Pret', 'Gris', 'Cortinas 100% Black Out 2 Paños', '140x210', 'Gris'],
  ['Sábanas 700 H con Festón Bordado Twin - Pret', 'Twin / Azul', 'Sábanas 700 H con Festón Bordado', 'Twin', 'Azul'],
  ['Almohada Linea Básica 70x40 Cm - Pret', '70 x 40 cm', 'Almohada Linea Básica', '70x40', ''],
  ['Sábanas Fieldcrest 100% Algodón Full 2 Plazas - Cannon', 'Blue', 'Sábanas Fieldcrest 100% Algodón - Cannon', 'Full', 'Blue'],
  ['Acolchado River Plate 1 1/2- City Blanco', 'Monumental', 'Acolchado River Plate - City Blanco', 'Twin', 'Monumental'],
  ['Set X2 Fundas de Almohada Dobby 500 H', 'Azul', 'Set X2 Fundas de Almohada Dobby 500 H', '', 'Azul'],
];
for (const [nombre, color, grupo, medida, limpio] of casos) assert.deepEqual(grupoYMedida(nombre, color), { grupo, medida, color: limpio }, nombre);

const arbol = segmentar([
  { sku: 'b', categoria: 'Sábanas', nombre: 'Sábanas X King - Pret', color: 'Azul' },
  { sku: 'a', categoria: 'Sábanas', nombre: 'Sábanas X Twin - Pret', color: 'Rosa' },
]);
assert.equal(arbol[0].grupos.length, 1);
assert.deepEqual(arbol[0].grupos[0].medidas.map((m) => m.medida), ['Twin', 'King']);
console.log(`${casos.length + 2} comprobaciones ok`);
