// Dashboard de costos (Chart.js): evolución, en qué se va la plata, por área, por marca y canal, y qué cambió contra el mes anterior.
// Lo llama costos.html (vista "Dashboard") con los datos que ya cargó. Tocar una columna del gráfico de meses cambia el mes mirado.
import { $, esc, peso0 } from './app.js';

const Chart = window.Chart;
const AREAS = { ventas: 'Ventas', marketing: 'Marketing', operaciones: 'Operaciones', atencion: 'Atención al cliente' };
const AREA_COL = { ventas: '#3B5BA9', marketing: '#C2547B', operaciones: '#E3A82B', atencion: '#2B6D68' };
const MARCA = { pret: 'Pret a Home', lavan: 'Casa Laván' }, CANAL = { tn: 'Tienda Nube', ml: 'Mercado Libre', local: 'Local' };
const PAL = ['#3B5BA9', '#E3A82B', '#2B6D68', '#C2547B', '#7A3B46', '#7C6BC4', '#9AA0A8'];
const INK = '#1B1D21', GRID = '#EEECE6';
let charts = [];

const corto = (n) => { const a = Math.abs(n), s = n < 0 ? '−' : ''; return a >= 1e6 ? `${s}$${(a / 1e6).toLocaleString('es-AR', { maximumFractionDigits: 1 })} M` : a >= 1e3 ? `${s}$${Math.round(a / 1e3).toLocaleString('es-AR')} mil` : `${s}$${Math.round(a)}`; };
const pct1 = (v) => `${v.toLocaleString('es-AR', { maximumFractionDigits: 1 })}%`;
const chip = (d) => (d == null ? '<span class="chip">Sin base</span>' : `<span class="chip ${d <= 0 ? 'ok' : 'warn'}">${d >= 0 ? '+' : '−'}${Math.abs(d * 100).toLocaleString('es-AR', { maximumFractionDigits: 1 })}%</span>`); // en costos, bajar es bueno

function css() {
  if ($('#cgf-css')) return;
  const s = document.createElement('style'); s.id = 'cgf-css';
  s.textContent = `.cgf-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}.cgf-full{grid-column:1/-1}
  .cgf-card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;min-width:0}
  .cgf-card h3{margin:0;font-size:15px;font-weight:600}.cgf-card .sub{font-size:12.5px;color:var(--muted);margin:2px 0 10px}
  .cgf-box{position:relative;height:300px}.cgf-box.alto{height:360px}.cgf-box.bajo{height:260px}
  @media(max-width:860px){.cgf-grid{grid-template-columns:1fr}.cgf-box{height:260px}.cgf-box.alto{height:320px}}`;
  document.head.appendChild(s);
}

// d = { filas, ventas, mes, reparto, addMes, nom, onMes }
//   filas: costos [{ ym, tipo, categoria, monto }], ventas: { ym -> ventas }, reparto: v_costos_atribucion de los últimos 12 meses.
export function montarCostos(cont, d) {
  css();
  charts.forEach((c) => c.destroy()); charts = [];
  const { filas, ventas, ventasOk, mes, addMes, nom } = d;
  const meses = Array.from({ length: 12 }, (_, i) => addMes(mes, i - 11)), prev = addMes(mes, -1);
  const suma = (ym, f = () => true) => filas.filter((x) => x.ym === ym && f(x)).reduce((t, x) => t + x.monto, 0);
  const tot = suma(mes), totPrev = suma(prev), fij = suma(mes, (x) => x.tipo === 'fijo'), vari = suma(mes, (x) => x.tipo === 'variable');
  const vtas = ventas[mes] || 0;
  const conDatos = meses.filter((m) => suma(m) > 0), prom = conDatos.length ? conDatos.reduce((t, m) => t + suma(m), 0) / conDatos.length : 0;
  const rubros = (ym) => { const o = {}; for (const x of filas) if (x.ym === ym) o[x.categoria] = (o[x.categoria] || 0) + x.monto; return o; };
  const rMes = rubros(mes), rPrev = rubros(prev);
  const repMes = d.reparto.filter((r) => r.periodo.slice(0, 7) === mes);
  const porArea = {}; for (const r of repMes) porArea[r.area] = (porArea[r.area] || 0) + r.monto;
  const topArea = Object.entries(porArea).sort((a, b) => b[1] - a[1])[0];
  const kpi = (l, v, sub) => `<div class="cc-card"><div class="cc-label">${l}</div><div class="cc-valor num">${v}</div><div class="cc-sub">${sub}</div></div>`;
  const delta = (a, b) => (b > 0 ? (a - b) / b : null);

  cont.innerHTML = `<div class="centro-comando" style="margin-top:0">
      ${kpi('Total del mes', peso0(tot), `${chip(delta(tot, totPrev))} <span class="small muted">vs ${nom(prev)}</span>`)}
      ${kpi('De cada $100 vendidos', vtas > 0 ? `$${(tot / vtas * 100).toLocaleString('es-AR', { maximumFractionDigits: 1 })}` : '—', vtas > 0 ? `se van en costos · ventas ${corto(vtas)}${ventasOk.has(mes) ? '' : ' (parcial: el mes no tiene todas las ventas cargadas)'}` : 'Sin ventas del scorecard este mes')}
      ${kpi('Fijos / Variables', `${tot > 0 ? Math.round(fij / tot * 100) : 0}% / ${tot > 0 ? Math.round(vari / tot * 100) : 0}%`, `${peso0(fij)} fijos · ${peso0(vari)} variables`)}
      ${kpi('Promedio de 12 meses', peso0(prom), `${chip(delta(tot, prom))} <span class="small muted">este mes vs el promedio</span>`)}
    </div>
    <div class="cgf-grid">
      <section class="cgf-card cgf-full"><h3>Costos por mes</h3><div class="sub">Fijos y variables de los últimos 12 meses; la línea es el % sobre las ventas (solo en meses con las ventas completas). Tocá una columna para ver ese mes.</div><div class="cgf-box alto"><canvas id="c-meses"></canvas></div></section>
      <section class="cgf-card"><h3>En qué se va la plata · ${nom(mes)}</h3><div class="sub">Por rubro, contra ${nom(prev)}.</div><div class="cgf-box alto"><canvas id="c-rubros"></canvas></div></section>
      <section class="cgf-card"><h3>Por área · ${nom(mes)}</h3><div class="sub">Lo que es de varias áreas se reparte en partes iguales.${topArea ? ` La que más gasta: <b>${AREAS[topArea[0]] || topArea[0]}</b> (${pct1(topArea[1] / Math.max(1, Object.values(porArea).reduce((a, b) => a + b, 0)) * 100)}).` : ''}</div><div class="cgf-box alto"><canvas id="c-area"></canvas></div></section>
      <section class="cgf-card cgf-full"><h3>Evolución por rubro</h3><div class="sub">Los 6 rubros que más pesaron en los últimos 12 meses; el resto va junto.</div><div class="cgf-box alto"><canvas id="c-evol"></canvas></div></section>
      <section class="cgf-card"><h3>Por marca y canal · ${nom(mes)}</h3><div class="sub">Reparto según las ventas de cada uno ese mes.</div><div class="cgf-box"><canvas id="c-canal"></canvas></div></section>
      <section class="cgf-card"><h3>Qué cambió contra ${nom(prev)}</h3><div class="sub">Los rubros que más subieron y bajaron.</div><div id="c-cambios"></div></section>
    </div>`;

  const base = {
    responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
    plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, font: { size: 12 } } }, tooltip: { backgroundColor: INK, padding: 10 } },
    scales: { x: { grid: { display: false }, ticks: { font: { size: 11 } } }, y: { grid: { color: GRID }, ticks: { callback: (v) => corto(v), font: { size: 11 } } } },
  };
  const esObj = (x) => x && typeof x === 'object' && !Array.isArray(x);
  const merge = (a, b) => { const r = { ...a }; for (const k of Object.keys(b)) r[k] = esObj(a[k]) && esObj(b[k]) ? merge(a[k], b[k]) : b[k]; return r; };
  const nuevo = (id, cfg) => { const c = new Chart($(`#${id}`), cfg); charts.push(c); return c; };
  const etq = (m) => nom(m).slice(0, 3).replace(/^./, (c) => c.toUpperCase()) + ' ' + m.slice(2, 4);
  const sel = (ctx) => (meses[ctx.dataIndex] === mes);

  // 1. Meses: fijos + variables apilados y % sobre ventas
  const pctV = meses.map((m) => (ventasOk.has(m) && ventas[m] > 0 && suma(m) > 0 ? suma(m) / ventas[m] * 100 : null));
  nuevo('c-meses', {
    data: { labels: meses.map(etq), datasets: [
      { type: 'bar', label: 'Fijos', data: meses.map((m) => suma(m, (x) => x.tipo === 'fijo')), backgroundColor: (c) => '#3B5BA9' + (sel(c) ? '' : '99'), stack: 'c', order: 2, maxBarThickness: 44 },
      { type: 'bar', label: 'Variables', data: meses.map((m) => suma(m, (x) => x.tipo === 'variable')), backgroundColor: (c) => '#E3A82B' + (sel(c) ? '' : '99'), stack: 'c', order: 2, maxBarThickness: 44 },
      { type: 'line', label: '% sobre ventas', data: pctV, yAxisID: 'y1', borderColor: INK, borderWidth: 2, pointRadius: 3, tension: 0.3, spanGaps: true, order: 1 }] },
    options: merge(base, {
      interaction: { mode: 'index', intersect: false },
      onClick: (e, _, chart) => { const el = chart.getElementsAtEventForMode(e, 'index', { intersect: false }, true); if (el.length && meses[el[0].index] !== mes) d.onMes(meses[el[0].index]); },
      scales: { x: { ...base.scales.x, stacked: true }, y: { ...base.scales.y, stacked: true }, y1: { position: 'right', min: 0, grid: { display: false }, ticks: { callback: (v) => `${Math.round(v)}%`, font: { size: 11 } } } },
      plugins: { ...base.plugins, tooltip: { ...base.plugins.tooltip, callbacks: { label: (c) => ` ${c.dataset.label}: ${c.dataset.yAxisID === 'y1' ? pct1(c.parsed.y) : peso0(c.parsed.y)}`, footer: (it) => `Total: ${peso0(suma(meses[it[0].dataIndex]))}` } } } }),
  });

  // 2. Rubros del mes contra el mes anterior (horizontal)
  const orden = Object.entries(rMes).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([k]) => k);
  nuevo('c-rubros', {
    type: 'bar',
    data: { labels: orden, datasets: [
      { label: nom(mes), data: orden.map((k) => rMes[k]), backgroundColor: '#3B5BA9', maxBarThickness: 18 },
      { label: nom(prev), data: orden.map((k) => rPrev[k] || 0), backgroundColor: '#C7CBD3', maxBarThickness: 18 }] },
    options: merge(base, { indexAxis: 'y', interaction: { mode: 'index', intersect: false }, scales: { x: { grid: { color: GRID }, ticks: { callback: (v) => corto(v), font: { size: 11 } } }, y: { grid: { display: false }, ticks: { font: { size: 11 }, callback(v) { const t = this.getLabelForValue(v); return t.length > 26 ? t.slice(0, 25) + '…' : t; } } } },
      plugins: { ...base.plugins, tooltip: { ...base.plugins.tooltip, callbacks: { label: (c) => ` ${c.dataset.label}: ${peso0(c.parsed.x)}` } } } }),
  });

  // 3. Por área (dona)
  const areas = Object.keys(AREAS).filter((k) => porArea[k] > 0), totArea = areas.reduce((t, k) => t + porArea[k], 0);
  nuevo('c-area', {
    type: 'doughnut',
    data: { labels: areas.map((k) => AREAS[k]), datasets: [{ data: areas.map((k) => porArea[k]), backgroundColor: areas.map((k) => AREA_COL[k]), borderColor: '#fff', borderWidth: 2, hoverOffset: 8 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: '58%', plugins: { legend: { position: 'right', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, font: { size: 12 } } },
      tooltip: { backgroundColor: INK, padding: 10, callbacks: { label: (c) => ` ${peso0(c.parsed)} · ${pct1(c.parsed / totArea * 100)}` } } } },
  });

  // 4. Evolución por rubro (6 principales + otros)
  const acum = {}; for (const m of meses) for (const [k, v] of Object.entries(rubros(m))) acum[k] = (acum[k] || 0) + v;
  const top = Object.entries(acum).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k]) => k);
  const serieR = (k) => meses.map((m) => rubros(m)[k] || 0);
  nuevo('c-evol', {
    type: 'bar',
    data: { labels: meses.map(etq), datasets: [
      ...top.map((k, i) => ({ label: k, data: serieR(k), backgroundColor: PAL[i], stack: 'r', maxBarThickness: 44 })),
      { label: 'Otros rubros', data: meses.map((m) => Object.entries(rubros(m)).filter(([k]) => !top.includes(k)).reduce((t, [, v]) => t + v, 0)), backgroundColor: PAL[6], stack: 'r', maxBarThickness: 44 }] },
    options: merge(base, { interaction: { mode: 'index', intersect: false }, scales: { x: { ...base.scales.x, stacked: true }, y: { ...base.scales.y, stacked: true } },
      plugins: { ...base.plugins, tooltip: { ...base.plugins.tooltip, callbacks: { label: (c) => ` ${c.dataset.label}: ${peso0(c.parsed.y)}`, footer: (it) => `Total: ${peso0(suma(meses[it[0].dataIndex]))}` } } } }),
  });

  // 5. Marca y canal del mes (horizontal)
  const porCanal = {}; for (const r of repMes) { const k = `${r.marca}|${r.canal}`; porCanal[k] = (porCanal[k] || 0) + r.monto; }
  const cs = Object.entries(porCanal).filter(([, v]) => v >= 0.5).sort((a, b) => b[1] - a[1]), totCanal = cs.reduce((t, [, v]) => t + v, 0);
  nuevo('c-canal', {
    type: 'bar',
    data: { labels: cs.map(([k]) => { const [m, c] = k.split('|'); return `${CANAL[c] || c} · ${MARCA[m] || m}`; }), datasets: [{ label: 'Costos', data: cs.map(([, v]) => v), backgroundColor: cs.map(([k]) => (k.startsWith('pret') ? '#7A3B46' : '#2B6D68')), maxBarThickness: 24 }] },
    options: merge(base, { indexAxis: 'y', plugins: { legend: { display: false }, tooltip: { ...base.plugins.tooltip, callbacks: { label: (c) => ` ${peso0(c.parsed.x)} · ${pct1(c.parsed.x / totCanal * 100)}` } } },
      scales: { x: { grid: { color: GRID }, ticks: { callback: (v) => corto(v), font: { size: 11 } } }, y: { grid: { display: false }, ticks: { font: { size: 11 } } } } }),
  });

  // 6. Qué cambió contra el mes anterior
  const difs = [...new Set([...Object.keys(rMes), ...Object.keys(rPrev)])].map((k) => ({ k, a: rMes[k] || 0, b: rPrev[k] || 0, dif: (rMes[k] || 0) - (rPrev[k] || 0) })).filter((x) => Math.abs(x.dif) >= 1);
  const suben = difs.filter((x) => x.dif > 0).sort((a, b) => b.dif - a.dif).slice(0, 5), bajan = difs.filter((x) => x.dif < 0).sort((a, b) => a.dif - b.dif).slice(0, 5);
  const fila = (x) => `<tr><th scope="row">${esc(x.k)}</th><td class="r num">${peso0(x.a)}</td><td class="r num"><b style="color:${x.dif > 0 ? 'var(--warn)' : 'var(--ok)'}">${x.dif > 0 ? '+' : '−'}${peso0(Math.abs(x.dif))}</b></td><td class="r num muted">${x.b > 0 ? `${x.dif > 0 ? '+' : '−'}${Math.abs(x.dif / x.b * 100).toLocaleString('es-AR', { maximumFractionDigits: 0 })}%` : 'nuevo'}</td></tr>`;
  const cab = '<thead><tr><th scope="col">Rubro</th><th scope="col" class="r">Este mes</th><th scope="col" class="r">Diferencia</th><th scope="col" class="r"></th></tr></thead>';
  $('#c-cambios').innerHTML = !totPrev ? '<p class="muted">No hay costos cargados el mes anterior para comparar.</p>'
    : `<div class="scroll"><table>${cab}<tbody>${suben.length ? `<tr><td colspan="4" class="small muted" style="padding-top:12px">Subieron</td></tr>${suben.map(fila).join('')}` : ''}${bajan.length ? `<tr><td colspan="4" class="small muted" style="padding-top:12px">Bajaron</td></tr>${bajan.map(fila).join('')}` : ''}${!suben.length && !bajan.length ? '<tr><td colspan="4" class="muted">Sin cambios.</td></tr>' : ''}</tbody></table></div>`;
}
