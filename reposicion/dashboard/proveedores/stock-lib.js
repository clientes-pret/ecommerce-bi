// Cuentas del análisis de stock y de ingresos. Sin dependencias: las usan las pantallas y las pruebas
// (node reposicion/tests/js/stock_lib_test.mjs).
//   lotes: lo que devuelve repo_lotes()           (un ingreso por fila: unidades, quedan, quedan_al_pago, pago, costo…)
//   stock: lo que devuelve repo_stock_valorizado() (un producto por fila: dep, full_pret, full_lavan, vel, costo…)
// La plata es siempre a costo de hoy (Ecomm-App); un producto sin costo cuenta en unidades pero no en pesos.

export const PLAZO_DEFECTO = 60;
const n = (v) => Number(v) || 0;
export const suma = (filas, f) => filas.reduce((s, x) => s + n(f(x)), 0);

// Importes grandes en millones ("$ 766 M"), para que entren en las tarjetas del celular.
export const millones = (v) => (Math.abs(v) >= 1e6 ? `$ ${(v / 1e6).toLocaleString('es-AR', { maximumFractionDigits: v >= 1e8 ? 0 : 1 })} M`
  : `$ ${Math.round(v).toLocaleString('es-AR')}`);

export const diasEntre = (desde, hasta) => Math.round((Date.parse(hasta + 'T12:00:00Z') - Date.parse(desde + 'T12:00:00Z')) / 864e5);

// ───────────── Stock de hoy ─────────────
export const unidades = (f) => n(f.dep) + n(f.full_pret) + n(f.full_lavan);
export const valor = (f) => unidades(f) * n(f.costo);
export const sinVentas = (f) => !(n(f.vel) > 0) || n(f.vendido) <= 0;
export const cobertura = (f) => (sinVentas(f) ? null : unidades(f) / n(f.vel));

export const TRAMOS = [
  { id: 't30', nombre: 'Hasta 30 días', hasta: 30 },
  { id: 't60', nombre: '31 a 60 días', hasta: 60 },
  { id: 't90', nombre: '61 a 90 días', hasta: 90 },
  { id: 't180', nombre: '91 a 180 días', hasta: 180 },
  { id: 'mas', nombre: 'Más de 180 días', hasta: Infinity },
  { id: 'sin', nombre: 'Sin ventas en 60 días', hasta: null },
];
export function tramoDe(f) {
  const c = cobertura(f);
  return c === null ? 'sin' : TRAMOS.find((t) => t.hasta !== null && c <= t.hasta).id;
}
export function porTramo(stock) {
  return TRAMOS.map((t) => {
    const filas = stock.filter((f) => tramoDe(f) === t.id);
    return { ...t, filas, plata: suma(filas, valor), unidades: suma(filas, unidades), productos: filas.length };
  });
}

// Días que tarda en venderse la plata que hay en stock, al ritmo actual. null = no se vende nada.
export function diasDeStock(filas) {
  const porDia = suma(filas, (f) => (sinVentas(f) ? 0 : n(f.vel) * n(f.costo)));
  return porDia > 0 ? suma(filas, valor) / porDia : null;
}

// Plata frenada: lo que no se va a vender dentro del plazo de pago del proveedor.
export function frenada(stock) {
  return stock.map((f) => {
    const sobran = Math.max(0, Math.round(unidades(f) - (sinVentas(f) ? 0 : n(f.vel) * n(f.plazo ?? PLAZO_DEFECTO))));
    return { ...f, sobran, plataSobra: sobran * n(f.costo), dias: cobertura(f) };
  }).filter((f) => f.sobran > 0).sort((a, b) => b.plataSobra - a.plataSobra || b.sobran - a.sobran);
}

// ───────────── Ingresos (lotes) ─────────────
export const vigentes = (lotes) => lotes.filter((l) => l.estado !== 'descartado');
export const ingresado = (l) => n(l.unidades) * n(l.costo);
export const queda = (l) => n(l.quedan) * n(l.costo);
export const quedaAlPago = (l) => n(l.quedan_al_pago) * n(l.costo);
export const vencido = (l, hoy) => l.pago <= hoy;

export function estadoLote(l, hoy) {
  if (l.estado === 'descartado') return { id: 'descartado', texto: 'No fue compra', clase: '' };
  if (n(l.quedan) === 0) return { id: 'vendido', texto: 'Vendido', clase: 'ok' };
  if (vencido(l, hoy)) {
    return n(l.quedan_al_pago) > 0 ? { id: 'pagado', texto: 'Venció el pago sin venderse', clase: 'bad' }
      : { id: 'atiempo', texto: 'Vendido antes del pago', clase: 'ok' };
  }
  return n(l.quedan_al_pago) > 0 ? { id: 'riesgo', texto: 'No llega a venderse', clase: 'warn' }
    : { id: 'llega', texto: 'Se vende antes de pagar', clase: 'ok' };
}

// Fracción (0 a 1) vendida al vencer el pago, en plata. Solo ingresos cuyo pago ya venció. null = todavía ninguno.
export function vendidoAlPago(lotes, hoy) {
  const v = vigentes(lotes).filter((l) => vencido(l, hoy));
  const total = suma(v, ingresado);
  return total > 0 ? 1 - suma(v, quedaAlPago) / total : null;
}

// Pagos que vencen entre `desde` y `hasta` días desde hoy: cuánto es y cuánto va a estar sin vender.
export function proximos(lotes, hoy, desde, hasta) {
  const filas = vigentes(lotes).filter((l) => { const d = diasEntre(hoy, l.pago); return d >= desde && d <= hasta; });
  return { filas, aPagar: suma(filas, ingresado), sinVender: suma(filas, quedaAlPago) };
}

export function mediana(xs) {
  if (!xs.length) return null;
  const o = [...xs].sort((a, b) => a - b), m = Math.floor(o.length / 2);
  return o.length % 2 ? o[m] : (o[m - 1] + o[m]) / 2;
}

export function porProveedor(lotes, stock, hoy) {
  const mapa = new Map();
  const de = (p) => { if (!mapa.has(p)) mapa.set(p, { proveedor: p, lotes: [], stock: [] }); return mapa.get(p); };
  for (const l of vigentes(lotes)) de(l.proveedor).lotes.push(l);
  for (const f of stock) de(f.proveedor).stock.push(f);
  return [...mapa.values()].map((g) => {
    const agotados = g.lotes.filter((l) => l.agotado_el);
    return {
      ...g,
      plazo: g.lotes[0]?.plazo ?? g.stock[0]?.plazo ?? PLAZO_DEFECTO,
      ingresado: suma(g.lotes, ingresado),
      pctAlPago: vendidoAlPago(g.lotes, hoy),
      sinVender60: proximos(g.lotes, hoy, 1, 60).sinVender,
      // Mediana de días en agotarse, solo de lo que ya se agotó.
      diasEnAgotar: mediana(agotados.map((l) => diasEntre(l.fecha, l.agotado_el))),
      agotados: agotados.length,
      plataStock: suma(g.stock, valor),
      unidadesStock: suma(g.stock, unidades),
      diasStock: diasDeStock(g.stock),
    };
  }).sort((a, b) => b.plataStock - a.plataStock || b.ingresado - a.ingresado);
}
