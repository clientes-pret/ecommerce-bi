// Datos y cálculos compartidos por Scorecard (ventas.html) y Dashboard (ventas-dashboard.html). Lee scorecard_canal_dia /
// scorecard_ads_dia en Supabase al importarse: las páginas lo importan recién después de verificar la sesión.
import { sb, $, esc, peso0, hoy } from './app.js';
import { MESN } from './analisis.js?v=5';

export const DAY = 864e5;
export const PRET = '#7A3B46', LAVAN = '#2B6D68';
export const CANALES = [
  { marca: 'pret', canal: 'tn', nombre: 'Tienda Nube · Pret a Home', ads: ['meta', 'google'] },
  { marca: 'pret', canal: 'local', nombre: 'Local físico · Pret a Home', ads: null },
  { marca: 'pret', canal: 'ml', nombre: 'Mercado Libre · Pret a Home', ads: ['ml'] },
  { marca: 'lavan', canal: 'tn', nombre: 'Tienda Nube · Casa Laván', ads: ['meta', 'google'] },
  { marca: 'lavan', canal: 'ml', nombre: 'Mercado Libre · Casa Laván', ads: ['ml'] },
];
export const PLATAFORMAS = [['meta', 'Meta Ads'], ['google', 'Google Ads'], ['ml', 'Mercado Libre Ads']];
export const DIAS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];

export const iso = (d) => d.toISOString().slice(0, 10);
export const masDias = (s, n) => iso(new Date(Date.parse(s + 'T00:00:00Z') + n * DAY));
export const lunes = (s) => { const dow = new Date(s + 'T00:00:00Z').getUTCDay(); return masDias(s, dow === 0 ? -6 : 1 - dow); };
export const primeroMes = (s) => s.slice(0, 7) + '-01';
export const ddmm = (s) => s.slice(8, 10) + '/' + s.slice(5, 7);
export const largo = (s) => `${DIAS[new Date(s + 'T00:00:00Z').getUTCDay()]} ${Number(s.slice(8, 10))} de ${MESN[Number(s.slice(5, 7)) - 1]} de ${s.slice(0, 4)}`;
export const pctTxt = (v) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toLocaleString('es-AR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%';
export const num = (v) => (v == null ? null : Number(v));

// Lectura paginada y con orden fijo (sin orden, las páginas pueden repetir o perder filas).
export async function todo(tabla, cols, desde, orden) {
  const out = [];
  for (let i = 0; ; i += 1000) {
    let q = sb.from(tabla).select(cols).gte('fecha', desde);
    for (const o of orden) q = q.order(o);
    const { data, error } = await q.range(i, i + 999);
    if (error) throw error;
    out.push(...data);
    if (data.length < 1000) return out;
  }
}

// 8 meses de historia alcanzan para el promedio (8 semanas / 8 meses previos).
export const desde = masDias(hoy(), -300);
export let filas, ads;
try {
  [filas, ads] = await Promise.all([
    todo('scorecard_canal_dia', 'fecha,marca,canal,ventas,ordenes,facturacion_neta,costo_productos,costo_envio', desde, ['fecha', 'marca', 'canal']),
    todo('scorecard_ads_dia', 'fecha,marca,plataforma,inversion', desde, ['fecha', 'marca', 'plataforma']),
  ]);
} catch (e) {
  $('#app').innerHTML = `<h1>Ventas</h1><div class="card"><p class="err">No pude leer el scorecard: ${esc(e.message)}</p></div>`;
  throw e;
}
export const fila = {}; // `${marca}_${canal}` -> { fecha -> fila }
for (const f of filas) ((fila[`${f.marca}_${f.canal}`] ??= {})[f.fecha] = f);
export const adsDia = {}; // `${marca}_${plataforma}` -> { fecha -> inversion }
for (const a of ads) if (a.inversion != null) ((adsDia[`${a.marca}_${a.plataforma}`] ??= {})[a.fecha] = Number(a.inversion));

// "Hoy" del tablero = último día con ventas cargadas en Tienda Nube o Mercado Libre (la carga va con atraso; no inventamos
// el día de hoy). El local no cuenta: su caja se carga el mismo día y adelantaba la fecha, dejando el "último día" vacío.
const ultimoDia = (soloOnline) => filas.filter((f) => f.ventas != null && (!soloOnline || f.canal === 'tn' || f.canal === 'ml')).reduce((m, f) => (f.fecha > m ? f.fecha : m), '');
export const ref = ultimoDia(true) || ultimoDia(false);

// Serie fecha -> ventas de un canal (solo días con dato).
export const serie = (c) => Object.fromEntries(Object.entries(fila[`${c.marca}_${c.canal}`] || {}).filter(([, f]) => f.ventas != null).map(([d, f]) => [d, Number(f.ventas)]));
export const sumaSeries = (ss) => { const o = {}; for (const s of ss) for (const [d, v] of Object.entries(s)) o[d] = (o[d] || 0) + v; return o; };
export const sumaRango = (s, a, b) => { let t = 0, hay = false; for (const [d, v] of Object.entries(s)) if (d >= a && d <= b) { t += v; hay = true; } return hay ? t : null; };

// El promedio histórico solo usa períodos completos: desde el día en que TODOS los canales sumados ya tienen datos
// (antes de julio solo estaba el local) y sin la semana/mes en que arrancó la serie a la mitad.
// `desde` = primer día con datos del canal (o el mayor de esos días si se suman varios canales).
export const inicio = (ss) => ss.map((s) => Object.keys(s).sort()[0] || '9999').reduce((m, x) => (x > m ? x : m), '');
export function evalDia(s, d, desde = '') {
  if (s[d] === undefined) return { actual: null, delta: null, sinDato: true };
  const dow = new Date(d + 'T00:00:00Z').getUTCDay();
  const m = Object.keys(s).filter((x) => x >= desde && x < d && new Date(x + 'T00:00:00Z').getUTCDay() === dow).sort().reverse().slice(0, 8).map((x) => s[x]);
  if (!m.length) return { actual: s[d], delta: null };
  const avg = m.reduce((a, b) => a + b, 0) / m.length;
  return { actual: s[d], delta: avg > 0 ? (s[d] - avg) / avg : null };
}
export function evalPeriodo(s, d, clave, fraccion, desde) {
  const b = {}; for (const [x, v] of Object.entries(s)) b[clave(x)] = (b[clave(x)] || 0) + v;
  const k = clave(d), actual = b[k] || 0;
  const prev = Object.keys(b).filter((x) => x >= desde && x < k).sort().reverse().slice(0, 8);
  if (!prev.length) return { actual, delta: null };
  const base = (prev.reduce((a, x) => a + b[x], 0) / prev.length) * fraccion;
  return { actual, delta: base > 0 ? (actual - base) / base : null };
}
export const evalSemana = (s, d, desde = '') => evalPeriodo(s, d, lunes, (Math.round((Date.parse(d) - Date.parse(lunes(d))) / DAY) + 1) / 7, desde);
export const evalMes = (s, d, desde = '') => { const y = +d.slice(0, 4), m = +d.slice(5, 7); return evalPeriodo(s, d, primeroMes, +d.slice(8, 10) / new Date(Date.UTC(y, m, 0)).getUTCDate(), desde); };
export const badge = (ev) => ev.sinDato ? '<span class="chip">Sin dato</span>'
  : ev.delta == null ? '<span class="chip">Sin base histórica</span>'
  : `<span class="chip ${ev.delta >= 0 ? 'ok' : 'warn'}">${ev.delta >= 0 ? '+' : '−'}${Math.abs(Math.round(ev.delta * 100))}% vs promedio</span>`;

// Rentabilidad del mes de un canal: neta − costo de productos − envío (solo TN; en ML ya viene en la neta) − ads del canal,
// sumando solo días con neta y costo cargados. Local físico no aplica (no trackea costo).
export function rentabilidad(c) {
  if (!c.ads) return null;
  let neta = 0, rev = 0, n = 0;
  for (const [d, f] of Object.entries(fila[`${c.marca}_${c.canal}`] || {})) {
    if (d < primeroMes(ref) || d > ref || f.facturacion_neta == null || f.costo_productos == null) continue;
    if (c.canal === 'tn' && f.costo_envio == null) continue;
    let r = num(f.facturacion_neta) - num(f.costo_productos) - (c.canal === 'tn' ? num(f.costo_envio) : 0);
    for (const p of c.ads) r -= adsDia[`${c.marca}_${p}`]?.[d] || 0;
    neta += r; n += 1; if (f.ventas != null) rev += Number(f.ventas);
  }
  return n ? { neta, prom: neta / n, n, rev } : { n: 0 };
}

// ---- Scorecard por canal (igual que el artifact): una fila por día o por semana, sumando las marcas elegidas ----
// Suma solo los datos reales: un campo queda null ("Pendiente") si ningún día/marca lo tiene; si alguno lo tiene, suma esos.
export const POR_PAGINA = 10;
export const ADS_DE = { tn: [['meta', 'Meta Ads'], ['google', 'Google Ads']], ml: [['ml', 'ML Ads']] };
export function sumar(dias, marcas, canal) {
  const t = { ventas: null, ordenes: null, neta: null, costo: null, envio: null, ads: {} };
  const mas = (k, v) => { if (v != null) t[k] = (t[k] || 0) + Number(v); };
  for (const d of dias) for (const m of marcas) {
    const f = fila[`${m}_${canal}`]?.[d];
    if (f) { mas('ventas', f.ventas); mas('ordenes', f.ordenes); mas('neta', f.facturacion_neta); mas('costo', f.costo_productos); mas('envio', f.costo_envio); }
    for (const [p] of ADS_DE[canal]) { const v = adsDia[`${m}_${p}`]?.[d]; if (v != null) t.ads[p] = (t.ads[p] || 0) + v; }
  }
  // Rentabilidad sin ads = neta − costo de productos − envío (solo TN: en ML el envío ya viene dentro de la neta).
  t.rentS = t.neta == null || t.costo == null || (canal === 'tn' && t.envio == null) ? null : t.neta - t.costo - (canal === 'tn' ? t.envio : 0);
  const adsVals = Object.values(t.ads);
  t.inversion = adsVals.length ? adsVals.reduce((a, b) => a + b, 0) : null;
  t.rentC = t.rentS == null || t.inversion == null ? null : t.rentS - t.inversion;
  t.roas = t.ventas != null && t.inversion > 0 ? t.ventas / t.inversion : null;
  return t;
}
export const PEND = '<span class="chip">Pendiente</span>', NA = '<span class="chip">No aplica</span>';
export const conPct = (v, base) => (v == null ? PEND : `${peso0(v)}${base > 0 ? ` <span class="small" style="font-weight:700;color:${v >= 0 ? 'var(--ok)' : 'var(--warn)'}">${pctTxt(v / base)}</span>` : ''}`);
export const celdasSc = (t, canal, adsJuntos = false) => {
  const c = (x) => `<td class="r num" style="white-space:nowrap">${x}</td>`;
  return c(t.ventas == null ? PEND : peso0(t.ventas)) + c(t.neta == null ? PEND : peso0(t.neta)) + c(t.ordenes == null ? PEND : t.ordenes.toLocaleString('es-AR'))
    + c(t.costo == null ? PEND : peso0(t.costo)) + c(canal === 'ml' ? NA : t.envio == null ? PEND : peso0(t.envio)) + c(conPct(t.rentS, t.ventas))
    + (adsJuntos ? c(t.inversion == null ? PEND : peso0(t.inversion)) : ADS_DE[canal].map(([p]) => c(t.ads[p] == null ? PEND : peso0(t.ads[p]))).join(''))
    + c(t.roas == null ? (t.inversion > 0 ? PEND : '<span class="chip">Sin inversión</span>') : t.roas.toFixed(2).replace('.', ',') + 'x')
    + c(t.rentS == null ? PEND : t.inversion == null ? '<span class="chip">Sin inversión</span>' : conPct(t.rentC, t.ventas));
};
export const cabSc = (canal, primera, adsJuntos = false) => `<tr><th scope="col">${primera}</th><th scope="col" class="r">Ventas</th><th scope="col" class="r">Facturación neta</th><th scope="col" class="r">Órdenes</th>
  <th scope="col" class="r">Costo de productos</th><th scope="col" class="r">Costo de envío</th><th scope="col" class="r">Rentab. s/ads</th>
  ${adsJuntos ? '<th scope="col" class="r">Inversión en ads</th>' : ADS_DE[canal].map(([, n]) => `<th scope="col" class="r">${n}</th>`).join('')}<th scope="col" class="r">ROAS</th><th scope="col" class="r">Rentab. c/ads</th></tr>`;
export const ddmmaa = (d) => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(2, 4)}`;
export function periodosSc(marcas, canal, gran) {
  const dias = [...new Set(marcas.flatMap((m) => Object.keys(fila[`${m}_${canal}`] || {})))].filter((d) => d <= ref).sort().reverse();
  if (gran === 'dia') return dias.map((d) => ({ et: ddmmaa(d), dias: [d] }));
  if (gran === 'mes') {
    const mm = {}; for (const d of dias) (mm[primeroMes(d)] ??= []).push(d);
    // Solo se cargan ~10 meses de historia: el más viejo puede venir cortado, y el último está en curso.
    return Object.keys(mm).sort().reverse().map((p) => ({ et: `${MESN[+p.slice(5, 7) - 1]} ${p.slice(0, 4)}${p === primeroMes(ref) ? ' (en curso)' : p < desde ? ' (parcial)' : ''}`, dias: mm[p] }));
  }
  if (gran === 'ml') {
    // Período de facturación de Mercado Libre: del 11 de un mes al 10 del siguiente (la factura llega el 11).
    const inicioML = (d) => { let y = +d.slice(0, 4), m = +d.slice(5, 7); if (+d.slice(8, 10) < 11) { m -= 1; if (m === 0) { m = 12; y -= 1; } } return `${y}-${String(m).padStart(2, '0')}-11`; };
    const finML = (p) => { let y = +p.slice(0, 4), m = +p.slice(5, 7) + 1; if (m === 13) { m = 1; y += 1; } return `${y}-${String(m).padStart(2, '0')}-10`; };
    const pp = {}; for (const d of dias) (pp[inicioML(d)] ??= []).push(d);
    return Object.keys(pp).sort().reverse().map((p) => ({ et: `${ddmmaa(p)} al ${ddmmaa(finML(p))}${finML(p) > ref ? ' (en curso)' : p < desde ? ' (parcial)' : ''}`, dias: pp[p] }));
  }
  const sem = {}; for (const d of dias) (sem[lunes(d)] ??= []).push(d);
  return Object.keys(sem).sort().reverse().map((l) => ({ et: `${ddmmaa(l)} al ${ddmmaa(masDias(l, 6))}`, dias: sem[l] }));
}

