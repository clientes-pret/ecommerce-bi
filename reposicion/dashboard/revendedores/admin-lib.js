// reposicion/dashboard/revendedores/admin-lib.js — cuentas del portal de ADMINISTRADOR de revendedores (sin red ni
// pantalla, para poder probarlas: reposicion/tests/js/admin_lib_test.js). Las usan dashboard.html y admin.html.
//
// Una "línea" es una fila de rev_ordenes (una venta de ML × SKU): cantidad, precio_unitario (al que vendió el
// revendedor), precio_mayorista (lo que le paga a Pret a Home por unidad; queda fijo al registrar la venta) y estado.
// Cuentan como venta: 'sombra' (modo prueba) y 'descontada' (modo real). Las canceladas se cuentan aparte.

export const CUENTAN = new Set(['sombra', 'descontada']);

/** Día de Argentina (YYYY-MM-DD) de una fecha ISO. */
export const diaAR = (iso) => new Date(Date.parse(iso) - 3 * 3600_000).toISOString().slice(0, 10);

/** Lunes (YYYY-MM-DD) de la semana de una fecha (semanas de lunes a domingo, hora de Argentina). */
export function semanaDe(iso) {
  const d = new Date(diaAR(iso) + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}

export const mesDe = (iso) => diaAR(iso).slice(0, 7);

const vacio = () => ({ ventas: new Set(), unidades: 0, vendido: 0, costo: 0, sinCosto: 0, canceladas: new Set() });

/** Agrupa líneas por `clave(línea)`. Devuelve [{clave, ventas, unidades, vendido, costo, sinCosto, canceladas}],
 *  de la clave más nueva a la más vieja. `vendido` = Σ cantidad × precio de venta; `costo` = Σ cantidad × precio
 *  mayorista (lo que el revendedor le tiene que pagar a Pret); `sinCosto` = líneas sin precio mayorista cargado. */
export function agrupar(lineas, clave) {
  const g = new Map();
  for (const l of lineas) {
    const k = clave(l);
    if (!g.has(k)) g.set(k, vacio());
    const a = g.get(k);
    if (l.estado === 'cancelada') { a.canceladas.add(l.ml_order_id); continue; }
    if (!CUENTAN.has(l.estado)) continue;
    const q = Number(l.cantidad) || 0;
    a.ventas.add(l.ml_order_id);
    a.unidades += q;
    a.vendido += q * (Number(l.precio_unitario) || 0);
    if (l.precio_mayorista == null) a.sinCosto++;
    else a.costo += q * Number(l.precio_mayorista);
  }
  return [...g.entries()].map(([k, a]) => ({ clave: k, ventas: a.ventas.size, unidades: a.unidades, vendido: a.vendido, costo: a.costo, sinCosto: a.sinCosto, canceladas: a.canceladas.size }))
    .sort((x, y) => String(y.clave).localeCompare(String(x.clave)));
}

/** Totales de un conjunto de líneas (mismos criterios que `agrupar`). */
export function totales(lineas) {
  return agrupar(lineas, () => 'todo')[0] ?? { clave: 'todo', ventas: 0, unidades: 0, vendido: 0, costo: 0, sinCosto: 0, canceladas: 0 };
}

/** Lo que debe cada revendedor: costo mayorista de sus ventas − pagos registrados. */
export function saldo(lineas, pagos) {
  const t = totales(lineas);
  const pagado = pagos.reduce((s, p) => s + (Number(p.monto) || 0), 0);
  return { costo: t.costo, pagado, saldo: t.costo - pagado };
}

/** Últimas `n` semanas (lunes YYYY-MM-DD), de la más vieja a la más nueva, terminando en la semana de `hoy`. */
export function ultimasSemanas(n, hoy = new Date()) {
  const fin = semanaDe(hoy.toISOString());
  return Array.from({ length: n }, (_, i) => {
    const d = new Date(fin + 'T12:00:00Z');
    d.setUTCDate(d.getUTCDate() - (n - 1 - i) * 7);
    return d.toISOString().slice(0, 10);
  });
}

/** "17/11" para un lunes; "nov 2026" para un mes. */
export const etiquetaSemana = (lunes) => lunes.slice(8, 10) + '/' + lunes.slice(5, 7);
const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
export const etiquetaMes = (ym) => `${MESES[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}`;
