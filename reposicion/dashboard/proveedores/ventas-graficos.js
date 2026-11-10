// Gráficos interactivos del Dashboard de ventas (Chart.js). Se importa recién después de verificar la sesión, igual que ventas-lib.js.
// Todo sale de scorecard_canal_dia / scorecard_ads_dia (los mismos datos y cálculos del Scorecard). Tocar una barra de un gráfico
// diario selecciona ese día y abre su detalle; el período se elige arriba.
import { $, $$, peso0 } from './app.js';
import { fila, adsDia, ref, masDias, lunes, primeroMes, ddmm, largo, DIAS, sumar, PLATAFORMAS, PRET, LAVAN, pctTxt } from './ventas-lib.js';

const Chart = window.Chart;
const COL = { tn: '#3B5BA9', ml: '#E3A82B', local: '#9AA0A8', ok: '#1F6F43', bad: '#B42318', ink: '#1B1D21', muted: '#5B5F66', grid: '#EEECE6' };
const PLAT_COL = { meta: '#3B5BA9', google: '#D2503F', ml: '#E3A82B' };
const CANAL_NOM = { tn: 'Tienda Nube', ml: 'Mercado Libre', local: 'Local físico' };
const OPCIONES = [['14', '14 días'], ['30', '30 días'], ['60', '60 días'], ['90', '90 días'], ['mes', 'Mes en curso'], ['ml', 'Período ML']];
const est = { per: '30', dia: null, metrica: 'ventas' }; // se recuerda mientras la página está abierta
let charts = [];

const corto = (n) => { const a = Math.abs(n), s = n < 0 ? '−' : ''; return a >= 1e6 ? `${s}$${(a / 1e6).toLocaleString('es-AR', { maximumFractionDigits: 1 })} M` : a >= 1e3 ? `${s}$${Math.round(a / 1e3).toLocaleString('es-AR')} mil` : `${s}$${Math.round(a)}`; };
const dd = (s) => `${DIAS[new Date(s + 'T00:00:00Z').getUTCDay()].slice(0, 3)} ${ddmm(s)}`;
const rango = (a, b) => { const o = []; for (let d = a; d <= b; d = masDias(d, 1)) o.push(d); return o; };
const inicioML = (d) => { let y = +d.slice(0, 4), m = +d.slice(5, 7); if (+d.slice(8, 10) < 11) { m -= 1; if (m === 0) { m = 12; y -= 1; } } return `${y}-${String(m).padStart(2, '0')}-11`; };
const chip = (d) => (d == null ? '<span class="chip">Sin base</span>' : `<span class="chip ${d >= 0 ? 'ok' : 'warn'}">${pctTxt(d)}</span>`);
const delta = (a, b) => (a == null || b == null || b === 0 ? null : (a - b) / Math.abs(b));

function css() {
  if ($('#gfx-css')) return;
  const s = document.createElement('style'); s.id = 'gfx-css';
  s.textContent = `.gfx-bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin:8px 0 4px}
  .gfx-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}.gfx-full{grid-column:1/-1}
  .gfx-card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;min-width:0}
  .gfx-card h3{margin:0;font-size:15px;font-weight:600}.gfx-card .sub{font-size:12.5px;color:var(--muted);margin:2px 0 10px}
  .gfx-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:flex-start}
  .gfx-box{position:relative;height:300px}.gfx-box.alto{height:340px}.gfx-box.bajo{height:260px}
  .gfx-det{margin-bottom:16px}.gfx-det table{min-width:640px}.gfx-nav{display:flex;gap:8px;align-items:center}
  .gfx-chips{display:flex;flex-wrap:wrap;gap:14px 22px;margin:8px 0 14px}.gfx-chips div{font-size:12.5px;color:var(--muted)}.gfx-chips b{display:block;font-size:13px;color:var(--ink);margin-bottom:2px}
  @media(max-width:860px){.gfx-grid{grid-template-columns:1fr}.gfx-box{height:260px}}`;
  document.head.appendChild(s);
}

export function montarGraficos(cont, marca) {
  css();
  charts.forEach((c) => c.destroy()); charts = [];
  const marcas = marca === 'all' ? ['pret', 'lavan'] : [marca];
  const canales = ['tn', 'ml', 'local'].filter((c) => marcas.some((m) => fila[`${m}_${c}`]));
  const ventasCanal = (c, d) => marcas.reduce((t, m) => t + Number(fila[`${m}_${c}`]?.[d]?.ventas || 0), 0);
  const tieneVenta = (d) => canales.some((c) => marcas.some((m) => fila[`${m}_${c}`]?.[d]?.ventas != null));
  const total = (d) => canales.reduce((t, c) => t + ventasCanal(c, d), 0);
  // Rentabilidad del día (online: TN + ML) = neta − costo − envío (TN) − ads; null si ese día no tiene los datos.
  const rent = (d) => {
    let r = null, base = 0;
    for (const c of ['tn', 'ml']) { const t = sumar([d], marcas, c); if (t.rentC != null) { r = (r || 0) + t.rentC; base += t.ventas || 0; } }
    return r == null ? null : { r, base };
  };
  const adsPlat = (p, d) => marcas.reduce((t, m) => t + (adsDia[`${m}_${p}`]?.[d] || 0), 0);
  const adsTotal = (d) => PLATAFORMAS.reduce((t, [p]) => t + adsPlat(p, d), 0);
  const ventasOnline = (d) => ventasCanal('tn', d) + ventasCanal('ml', d);

  // ----- período elegido -----
  const ini = est.per === 'mes' ? primeroMes(ref) : est.per === 'ml' ? inicioML(ref) : masDias(ref, -(Number(est.per) - 1));
  const dias = rango(ini, ref), n = dias.length;
  const prevDias = rango(masDias(ini, -n), masDias(ini, -1));
  if (!est.dia || est.dia < ini || est.dia > ref) est.dia = ref;
  const sel = () => dias.indexOf(est.dia);

  const sumV = (ds) => ds.reduce((t, d) => t + total(d), 0);
  const sumR = (ds) => { let r = null, b = 0; for (const d of ds) { const x = rent(d); if (x) { r = (r || 0) + x.r; b += x.base; } } return r == null ? null : { r, base: b }; };
  const vP = sumV(dias), vPrev = sumV(prevDias), rP = sumR(dias), rPrev = sumR(prevDias);
  const conDatos = dias.filter(tieneVenta);
  const mejor = conDatos.reduce((m, d) => (m == null || total(d) > total(m) ? d : m), null);
  const peor = conDatos.reduce((m, d) => (m == null || total(d) < total(m) ? d : m), null);
  const kpi = (l, v, sub) => `<div class="cc-card"><div class="cc-label">${l}</div><div class="cc-valor num">${v}</div><div class="cc-sub">${sub}</div></div>`;
  const kpis = [
    kpi('Ventas del período', peso0(vP), `${chip(delta(vP, vPrev))} <span class="small muted">vs ${n} días antes</span>`),
    kpi('Rentabilidad c/ads (online)', rP ? peso0(rP.r) : '—', rP ? `${rP.base > 0 ? `<b>${(rP.r / rP.base * 100).toLocaleString('es-AR', { maximumFractionDigits: 1 })}%</b> de las ventas · ` : ''}${chip(delta(rP?.r, rPrev?.r))}` : 'Sin datos'),
    kpi('Mejor día', mejor ? peso0(total(mejor)) : '—', mejor ? largo(mejor).replace(/ de \d{4}$/, '') : ''),
    kpi('Promedio diario', conDatos.length ? peso0(vP / conDatos.length) : '—', peor ? `Día más flojo: ${dd(peor)} · ${corto(total(peor))}` : ''),
  ].join('');

  cont.innerHTML = `<div class="gfx-bar"><h2 style="margin:0">Gráficos</h2>
      <div class="seg" role="group" aria-label="Período">${OPCIONES.map(([k, t]) => `<button type="button" data-per="${k}" aria-pressed="${est.per === k}">${t}</button>`).join('')}</div></div>
    <div class="centro-comando" style="margin-top:12px">${kpis}</div>
    <div class="gfx-grid">
      <section class="gfx-card gfx-full"><div class="gfx-head"><div><h3>Ventas por día y canal</h3><div class="sub">Tocá una barra para ver el detalle de ese día. La línea es el promedio de los 7 días anteriores.</div></div></div><div class="gfx-box alto"><canvas id="g-ventas"></canvas></div></section>
    </div>
    <section class="gfx-card gfx-det" id="g-detalle"></section>
    <div class="gfx-grid">
      <section class="gfx-card"><h3>Rentabilidad diaria (c/ads)</h3><div class="sub">Neta − costo de productos − envío − ads, en Tienda Nube y Mercado Libre. La línea es el % sobre las ventas.</div><div class="gfx-box"><canvas id="g-rent"></canvas></div></section>
      <section class="gfx-card"><h3>Inversión en ads y ROAS</h3><div class="sub">Gasto por plataforma (ML con IVA). La línea es el ROAS: ventas online ÷ inversión.</div><div class="gfx-box"><canvas id="g-ads"></canvas></div></section>
      <section class="gfx-card"><div class="gfx-head"><div><h3>Progresión del mes</h3><div class="sub">Acumulado día a día contra el mes anterior.</div></div>
        <div class="seg" role="group" aria-label="Métrica">${[['ventas', 'Ventas'], ['rent', 'Rentabilidad']].map(([k, t]) => `<button type="button" data-met="${k}" aria-pressed="${est.metrica === k}">${t}</button>`).join('')}</div></div><div class="gfx-box"><canvas id="g-mes"></canvas></div></section>
      <section class="gfx-card"><h3>Comparativa por día de la semana</h3><div class="sub">Promedio de las últimas 8 semanas contra los últimos 7 días.</div><div class="gfx-box"><canvas id="g-dow"></canvas></div></section>
      <section class="gfx-card gfx-full"><h3>Cómo se reparten las ventas del período</h3><div class="sub">Por canal y marca.</div><div class="gfx-box bajo"><canvas id="g-mix"></canvas></div></section>
    </div>
    <p class="muted small" style="line-height:1.6;margin:0 0 8px">Los días sin neta o costo cargados no cuentan en la rentabilidad. Las ventas del local suman al total pero no tienen costo, así que no entran en la rentabilidad.</p>`;

  const base = {
    responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, font: { size: 12 } } }, tooltip: { backgroundColor: '#1B1D21', padding: 10, titleFont: { weight: '600' } } },
    scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12, font: { size: 11 } } }, y: { grid: { color: COL.grid }, ticks: { callback: (v) => corto(v), font: { size: 11 } } } },
  };
  const esObj = (x) => x && typeof x === 'object' && !Array.isArray(x);
  const merge = (a, b) => { const r = { ...a }; for (const k of Object.keys(b)) r[k] = esObj(a[k]) && esObj(b[k]) ? merge(a[k], b[k]) : b[k]; return r; };
  const peso = (ctx) => ` ${ctx.dataset.label}: ${ctx.dataset.yAxisID === 'y1' && ctx.dataset.pct ? `${ctx.parsed.y.toLocaleString('es-AR', { maximumFractionDigits: 1 })}%` : ctx.dataset.yAxisID === 'y1' ? `${ctx.parsed.y.toLocaleString('es-AR', { maximumFractionDigits: 2 })}x` : peso0(ctx.parsed.y)}`;
  // Barras más claras para los días no seleccionados.
  const resalt = (c) => (ctx) => (ctx.dataIndex === sel() ? c : c + '88');
  const alClick = (e, _, chart) => { const el = chart.getElementsAtEventForMode(e, 'index', { intersect: false }, true); if (el.length) { est.dia = dias[el[0].index]; refrescar(); } };
  const labels = dias.map(dd);
  const nuevo = (id, cfg) => { const c = new Chart($(`#${id}`), cfg); charts.push(c); return c; };

  // 1. Ventas por día y canal (apiladas) + promedio de los 7 días anteriores
  const prom7 = dias.map((d) => { let t = 0; for (let i = 1; i <= 7; i++) t += total(masDias(d, -i)); return t / 7; });
  nuevo('g-ventas', {
    data: { labels, datasets: [
      ...canales.map((c) => ({ type: 'bar', label: CANAL_NOM[c], data: dias.map((d) => ventasCanal(c, d)), backgroundColor: resalt(COL[c]), stack: 'v', order: 2, maxBarThickness: 36 })),
      { type: 'line', label: 'Promedio 7 días anteriores', data: prom7, borderColor: COL.ink, borderWidth: 2, borderDash: [5, 4], pointRadius: 0, tension: 0.3, order: 1 }] },
    options: merge(base, { onClick: alClick, scales: { x: { ...base.scales.x, stacked: true }, y: { ...base.scales.y, stacked: true } },
      plugins: { ...base.plugins, tooltip: { ...base.plugins.tooltip, callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${peso0(ctx.parsed.y)}`, footer: (it) => `Total del día: ${peso0(total(dias[it[0].dataIndex]))}` } } } }),
  });

  // 2. Rentabilidad diaria + margen
  const rentV = dias.map((d) => rent(d));
  nuevo('g-rent', {
    data: { labels, datasets: [
      { type: 'bar', label: 'Rentabilidad c/ads', data: rentV.map((x) => (x ? x.r : null)), backgroundColor: (ctx) => { const v = rentV[ctx.dataIndex]?.r; return (v != null && v < 0 ? COL.bad : COL.ok) + (ctx.dataIndex === sel() ? '' : '88'); }, order: 2, maxBarThickness: 30 },
      { type: 'line', label: '% sobre ventas', data: rentV.map((x) => (x && x.base > 0 ? x.r / x.base * 100 : null)), yAxisID: 'y1', pct: true, borderColor: COL.ink, borderWidth: 2, pointRadius: 2, tension: 0.3, order: 1, spanGaps: true }] },
    options: merge(base, { onClick: alClick, scales: { ...base.scales, y1: { position: 'right', grid: { display: false }, ticks: { callback: (v) => `${v}%`, font: { size: 11 } } } },
      plugins: { ...base.plugins, tooltip: { ...base.plugins.tooltip, callbacks: { label: peso } } } }),
  });

  // 3. Ads por plataforma + ROAS
  const roas = dias.map((d) => { const a = adsTotal(d); return a > 0 ? ventasOnline(d) / a : null; });
  nuevo('g-ads', {
    data: { labels, datasets: [
      ...PLATAFORMAS.filter(([p]) => dias.some((d) => adsPlat(p, d) > 0)).map(([p, nom]) => ({ type: 'bar', label: nom, data: dias.map((d) => adsPlat(p, d)), backgroundColor: resalt(PLAT_COL[p]), stack: 'a', order: 2, maxBarThickness: 30 })),
      { type: 'line', label: 'ROAS', data: roas, yAxisID: 'y1', borderColor: COL.ink, borderWidth: 2, pointRadius: 2, tension: 0.3, order: 1, spanGaps: true }] },
    options: merge(base, { onClick: alClick, scales: { x: { ...base.scales.x, stacked: true }, y: { ...base.scales.y, stacked: true }, y1: { position: 'right', grid: { display: false }, ticks: { callback: (v) => `${v}x`, font: { size: 11 } } } },
      plugins: { ...base.plugins, tooltip: { ...base.plugins.tooltip, callbacks: { label: peso, footer: (it) => `Inversión total: ${peso0(adsTotal(dias[it[0].dataIndex]))}` } } } }),
  });

  // 4. Progresión del mes contra el mes anterior (acumulado)
  const mesAct = primeroMes(ref), y = +mesAct.slice(0, 4), m = +mesAct.slice(5, 7);
  const mesPrev = `${m === 1 ? y - 1 : y}-${String(m === 1 ? 12 : m - 1).padStart(2, '0')}-01`;
  const diasMes = (p) => rango(p, `${p.slice(0, 7)}-${String(new Date(Date.UTC(+p.slice(0, 4), +p.slice(5, 7), 0)).getUTCDate()).padStart(2, '0')}`);
  const valorMet = (d) => (est.metrica === 'ventas' ? total(d) : rent(d)?.r || 0);
  const acum = (p, hasta) => { let t = 0; return diasMes(p).map((d) => (hasta && d > hasta ? null : (t += valorMet(d)))); };
  const largoMax = Math.max(diasMes(mesAct).length, diasMes(mesPrev).length);
  const etiq = Array.from({ length: largoMax }, (_, i) => i + 1);
  const nomMes = (p) => ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'][+p.slice(5, 7) - 1];
  const aAct = acum(mesAct, ref), aPrev = acum(mesPrev);
  nuevo('g-mes', {
    type: 'line',
    data: { labels: etiq, datasets: [
      { label: `${nomMes(mesAct)} (en curso)`, data: aAct, borderColor: COL.ink, backgroundColor: COL.ink + '14', fill: true, borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 5, tension: 0.25 },
      { label: nomMes(mesPrev), data: aPrev, borderColor: '#9AA0A8', borderWidth: 2, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 4, tension: 0.25 }] },
    options: merge(base, { plugins: { ...base.plugins, tooltip: { ...base.plugins.tooltip, callbacks: { title: (it) => `Día ${it[0].label} del mes`, label: (ctx) => ` ${ctx.dataset.label}: ${peso0(ctx.parsed.y)}`,
      footer: (it) => { const a = it.find((x) => x.datasetIndex === 0), b = it.find((x) => x.datasetIndex === 1); return a && b && b.parsed.y ? `Diferencia: ${pctTxt((a.parsed.y - b.parsed.y) / Math.abs(b.parsed.y))}` : ''; } } } } }),
  });

  // 5. Día de la semana: promedio 8 semanas vs últimos 7 días (lunes primero)
  const orden = [1, 2, 3, 4, 5, 6, 0], dow = (d) => new Date(d + 'T00:00:00Z').getUTCDay();
  const ult56 = rango(masDias(ref, -55), ref), ult7 = rango(masDias(ref, -6), ref);
  const promDow = orden.map((k) => { const ds = ult56.filter((d) => dow(d) === k && tieneVenta(d)); return ds.length ? ds.reduce((t, d) => t + total(d), 0) / ds.length : null; });
  const ultDow = orden.map((k) => { const d = ult7.find((x) => dow(x) === k); return d && tieneVenta(d) ? total(d) : null; });
  nuevo('g-dow', {
    data: { labels: orden.map((k) => DIAS[k][0].toUpperCase() + DIAS[k].slice(1, 3)), datasets: [
      { type: 'bar', label: 'Promedio 8 semanas', data: promDow, backgroundColor: '#C7CBD3', maxBarThickness: 44, order: 2 },
      { type: 'line', label: 'Últimos 7 días', data: ultDow, borderColor: COL.ink, backgroundColor: COL.ink, borderWidth: 2, pointRadius: 5, pointHoverRadius: 7, showLine: true, order: 1 }] },
    options: merge(base, { plugins: { ...base.plugins, tooltip: { ...base.plugins.tooltip, callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${peso0(ctx.parsed.y)}`,
      footer: (it) => { const a = it.find((x) => x.datasetIndex === 1), b = it.find((x) => x.datasetIndex === 0); return a && b && b.parsed.y ? `Hoy vs promedio: ${pctTxt((a.parsed.y - b.parsed.y) / b.parsed.y)}` : ''; } } } } }),
  });

  // 6. Mix de ventas del período
  const combos = [];
  for (const mm of marcas) for (const c of ['tn', 'ml', 'local']) { if (!fila[`${mm}_${c}`]) continue; const v = dias.reduce((t, d) => t + Number(fila[`${mm}_${c}`][d]?.ventas || 0), 0); if (v > 0) combos.push({ mm, c, v }); }
  const sumaMix = combos.reduce((t, x) => t + x.v, 0);
  const tono = (mm, c) => ({ pret: { tn: '#7A3B46', ml: '#B8707C', local: '#DDB1B8' }, lavan: { tn: '#2B6D68', ml: '#5FA39D', local: '#A8CFCB' } })[mm][c];
  nuevo('g-mix', {
    type: 'doughnut',
    data: { labels: combos.map((x) => `${CANAL_NOM[x.c]} · ${x.mm === 'pret' ? 'Pret a Home' : 'Casa Laván'}`), datasets: [{ data: combos.map((x) => x.v), backgroundColor: combos.map((x) => tono(x.mm, x.c)), borderColor: '#fff', borderWidth: 2, hoverOffset: 8 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: '58%', plugins: { legend: { position: 'right', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, font: { size: 12 } } },
      tooltip: { backgroundColor: '#1B1D21', padding: 10, callbacks: { label: (ctx) => ` ${peso0(ctx.parsed)} · ${(ctx.parsed / sumaMix * 100).toLocaleString('es-AR', { maximumFractionDigits: 1 })}%` } } } },
  });

  // ----- detalle del día seleccionado -----
  function detalle() {
    const d = est.dia, i = dias.indexOf(d);
    const ant = masDias(d, -1), sem = masDias(d, -7);
    const dw = dow(d);
    const prom = (() => { const ds = rango(masDias(d, -56), masDias(d, -1)).filter((x) => dow(x) === dw && tieneVenta(x)); return ds.length ? ds.reduce((t, x) => t + total(x), 0) / ds.length : null; })();
    const tv = tieneVenta(d) ? total(d) : null, rr = rent(d);
    const filas = ['tn', 'ml'].filter((c) => canales.includes(c)).map((c) => {
      const t = sumar([d], marcas, c), p = (v) => (v == null ? '<span class="chip">—</span>' : peso0(v));
      return `<tr><th scope="row" style="white-space:nowrap">${CANAL_NOM[c]}</th><td class="r num">${p(t.ventas)}</td><td class="r num">${p(t.neta)}</td><td class="r num">${t.ordenes ?? '—'}</td><td class="r num">${p(t.costo)}</td><td class="r num">${c === 'ml' ? '<span class="chip">No aplica</span>' : p(t.envio)}</td><td class="r num">${p(t.inversion)}</td><td class="r num">${t.roas == null ? '—' : t.roas.toFixed(2).replace('.', ',') + 'x'}</td><td class="r num"><b>${p(t.rentC)}</b></td></tr>`;
    }).join('');
    const loc = canales.includes('local') ? `<tr><th scope="row" style="white-space:nowrap">${CANAL_NOM.local}</th><td class="r num">${ventasCanal('local', d) ? peso0(ventasCanal('local', d)) : '<span class="chip">Sin carga</span>'}</td><td colspan="7" class="muted small">Sin costos cargados</td></tr>` : '';
    $('#g-detalle').innerHTML = `<div class="gfx-head"><div><h3>Detalle · ${largo(d)}</h3><div class="sub">${tv == null ? 'Ese día todavía no tiene ventas cargadas.' : `Ventas totales ${peso0(tv)}${rr ? ` · Rentabilidad c/ads ${peso0(rr.r)}` : ''}`}</div></div>
      <div class="gfx-nav"><button class="btn sm" type="button" id="g-prev" ${i <= 0 ? 'disabled' : ''}>← Día anterior</button><button class="btn sm" type="button" id="g-next" ${i >= n - 1 ? 'disabled' : ''}>Día siguiente →</button></div></div>
      <div class="gfx-chips"><div><b>Contra el día anterior</b>${chip(delta(tv, tieneVenta(ant) ? total(ant) : null))}</div><div><b>Contra el mismo día de la semana pasada</b>${chip(delta(tv, tieneVenta(sem) ? total(sem) : null))}</div>
        <div><b>Contra el promedio de ese día (8 semanas)</b>${chip(delta(tv, prom))}</div><div><b>Rentabilidad vs día anterior</b>${chip(delta(rr?.r, rent(ant)?.r))}</div></div>
      <div class="scroll"><table><thead><tr><th scope="col">Canal</th><th scope="col" class="r">Ventas</th><th scope="col" class="r">Facturación neta</th><th scope="col" class="r">Órdenes</th><th scope="col" class="r">Costo de productos</th><th scope="col" class="r">Costo de envío</th><th scope="col" class="r">Inversión en ads</th><th scope="col" class="r">ROAS</th><th scope="col" class="r">Rentab. c/ads</th></tr></thead><tbody>${filas}${loc}</tbody></table></div>`;
    $('#g-prev').onclick = () => { est.dia = dias[i - 1]; refrescar(); };
    $('#g-next').onclick = () => { est.dia = dias[i + 1]; refrescar(); };
  }
  function refrescar() { charts.slice(0, 3).forEach((c) => c.update('none')); detalle(); }
  detalle();

  $$('[data-per]', cont).forEach((b) => (b.onclick = () => { est.per = b.dataset.per; montarGraficos(cont, marca); }));
  $$('[data-met]', cont).forEach((b) => (b.onclick = () => { est.metrica = b.dataset.met; montarGraficos(cont, marca); }));
}
