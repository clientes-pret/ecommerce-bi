// Pruebas de la lógica de alertas de "Estado del sistema".
// Correr con: deno test reposicion/tests/js/estado_lib_test.js
import { assertEquals } from 'jsr:@std/assert@1';
import { construirAlertas, hace, minutosEntre } from '../../dashboard/revendedores/estado-lib.js';

const AHORA = '2026-11-18T14:00:00Z';
const haceMin = (m) => new Date(Date.parse(AHORA) - m * 60000).toISOString();

// Un estado sano: todos los procesos al día, sin problemas.
const sano = () => ({
  ahora: AHORA,
  catalogo: 2444,
  oferta: { ultima_lectura: haceMin(2) },
  trabajos: [
    { nombre: 'rev-sync-5min', activo: true, ultima_vez: haceMin(1), estado: 'succeeded' },
    { nombre: 'rev-vincular-hora', activo: true, ultima_vez: haceMin(40), estado: 'succeeded' },
    { nombre: 'rev-ordenes-minuto', activo: true, ultima_vez: haceMin(0.5), estado: 'succeeded' },
    { nombre: 'rev-conciliar-6h', activo: true, ultima_vez: haceMin(100), estado: 'succeeded' },
  ],
  procesos: { stock: { resultado: 'sombra_ok' }, vinculacion: { resultado: 'ok' }, conciliacion: { resultado: 'ok' } },
  revendedores: [{ id: 1, nickname: 'CASAPINAR', estado: 'activo', token_vence: haceMin(-200), activas_sin_reconocer: 0 }],
  ventas: { no_reconocidas: 0 },
  bajo_minimo: [],
});

Deno.test('un sistema sano no genera alertas', () => {
  assertEquals(construirAlertas(sano()), []);
});

Deno.test('hace(): minutos, horas y días en castellano', () => {
  assertEquals(hace(0.2), 'hace menos de 1 minuto');
  assertEquals(hace(1), 'hace 1 minuto');
  assertEquals(hace(7), 'hace 7 minutos');
  assertEquals(hace(60), 'hace 1 hora');
  assertEquals(hace(180), 'hace 3 horas');
  assertEquals(hace(60 * 24 * 3), 'hace 3 días');
  assertEquals(hace(NaN), 'nunca');
});

Deno.test('minutosEntre: diferencia en minutos', () => {
  assertEquals(minutosEntre('2026-11-18T13:50:00Z', AHORA), 10);
});

Deno.test('un proceso atrasado da alerta roja con cuánto atraso tiene', () => {
  const d = sano();
  d.trabajos[0].ultima_vez = haceMin(45); // el de stock debería correr cada 5 min; tolerancia 20
  const a = construirAlertas(d);
  assertEquals(a.length, 1);
  assertEquals(a[0].nivel, 'rojo');
  assertEquals(a[0].titulo, 'Stock hacia revendedores está atrasado');
});

Deno.test('cada proceso tolera su propio atraso (la conciliación de 6 h no alerta a las 3 h)', () => {
  const d = sano();
  d.trabajos[3].ultima_vez = haceMin(180);
  assertEquals(construirAlertas(d), []);
  d.trabajos[3].ultima_vez = haceMin(600);
  assertEquals(construirAlertas(d)[0].titulo, 'Conciliación de ventas está atrasado');
});

Deno.test('un trabajo apagado o que falló da alerta roja', () => {
  const d = sano();
  d.trabajos[1].activo = false;
  d.trabajos[2].estado = 'failed';
  const t = construirAlertas(d).map((x) => x.titulo);
  assertEquals(t.includes('Vinculación de publicaciones está apagado'), true);
  assertEquals(t.includes('Registro de ventas falló en su última corrida'), true);
});

Deno.test('un trabajo que todavía no corrió es solo un aviso amarillo', () => {
  const d = sano();
  d.trabajos[3].ultima_vez = null;
  const a = construirAlertas(d);
  assertEquals([a.length, a[0].nivel], [1, 'amarillo']);
});

Deno.test('un proceso que terminó con problemas se avisa, pero "sombra_ok" y "activo_ok" no', () => {
  const d = sano();
  d.procesos.stock.resultado = 'activo_ok';
  assertEquals(construirAlertas(d), []);
  d.procesos.stock.resultado = 'lectura_incompleta';
  d.procesos.vinculacion.resultado = 'con_fallas';
  const t = construirAlertas(d).map((x) => x.titulo);
  assertEquals(t, ['La sincronización de stock terminó con problemas', 'La vinculación de publicaciones terminó con problemas']);
});

Deno.test('lectura de stock vieja', () => {
  const d = sano();
  d.oferta.ultima_lectura = haceMin(30);
  assertEquals(construirAlertas(d)[0].titulo, 'El stock de Ecomm-App está desactualizado');
});

Deno.test('sin catálogo no se alerta por lectura de stock', () => {
  const d = sano();
  d.catalogo = 0;
  d.oferta.ultima_lectura = haceMin(500);
  assertEquals(construirAlertas(d), []);
});

Deno.test('revendedor desconectado: alerta roja; pausado: amarilla', () => {
  const d = sano();
  d.revendedores = [{ id: 1, nickname: 'A', estado: 'desconectado' }, { id: 2, nickname: 'B', estado: 'pausado' }];
  const a = construirAlertas(d);
  assertEquals(a.map((x) => [x.nivel, x.titulo]), [['rojo', 'A se desconectó'], ['amarillo', 'B está pausado']]);
});

Deno.test('credencial vencida hace mucho = no se renueva; recién vencida no alerta', () => {
  const d = sano();
  d.revendedores[0].token_vence = haceMin(30); // venció hace 30 min: todavía dentro de la tolerancia
  assertEquals(construirAlertas(d), []);
  d.revendedores[0].token_vence = haceMin(120);
  assertEquals(construirAlertas(d)[0].titulo, 'La credencial de CASAPINAR venció y no se renueva');
});

Deno.test('publicaciones activas sin reconocer: singular y plural', () => {
  const d = sano();
  d.revendedores[0].activas_sin_reconocer = 1;
  assertEquals(construirAlertas(d)[0].titulo, 'CASAPINAR: 1 publicación activa sin SKU reconocido');
  d.revendedores[0].activas_sin_reconocer = 5;
  assertEquals(construirAlertas(d)[0].titulo, 'CASAPINAR: 5 publicaciones activas sin SKU reconocido');
});

Deno.test('ventas con SKU no reconocido', () => {
  const d = sano();
  d.ventas.no_reconocidas = 2;
  assertEquals(construirAlertas(d)[0].titulo, '2 ventas con SKU no reconocido');
});

Deno.test('publicaciones por debajo del mínimo: alerta roja solo de aviso', () => {
  const d = sano();
  d.bajo_minimo = [{ revendedor: 'X', item: 'MLA1', sku: 'S', precio: 10, minimo: 20 }];
  const a = construirAlertas(d);
  assertEquals([a[0].nivel, a[0].titulo], ['rojo', '1 publicación por debajo del precio mínimo']);
});

Deno.test('las alertas rojas van antes que las amarillas', () => {
  const d = sano();
  d.ventas.no_reconocidas = 1; // amarilla
  d.trabajos[0].activo = false; // roja
  assertEquals(construirAlertas(d).map((x) => x.nivel), ['rojo', 'amarillo']);
});

Deno.test('datos incompletos no rompen la función', () => {
  assertEquals(construirAlertas({ ahora: AHORA }), []);
});
