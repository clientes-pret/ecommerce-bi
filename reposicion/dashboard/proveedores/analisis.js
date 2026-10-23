// Cálculos e indicadores de un proveedor a partir de sus movimientos (ordenados por fecha, id).
import { esc, peso0, fecha } from './app.js';

const DIA = 86400000;
const f2d = (s) => new Date(String(s).slice(0, 10) + 'T12:00:00');
export const diasEntre = (a, b) => Math.round((f2d(b) - f2d(a)) / DIA);
const mesDe = (s) => String(s).slice(0, 7);
const sumar = (arr, f) => arr.reduce((a, x) => a + Number(f(x) || 0), 0);

export function corto(n) {
  const a = Math.abs(n), s = n < 0 ? '-' : '';
  if (a >= 1e6) return `${s}$${(a / 1e6).toLocaleString('es-AR', { maximumFractionDigits: 1 })} M`;
  if (a >= 1e3) return `${s}$${Math.round(a / 1e3).toLocaleString('es-AR')} k`;
  return `${s}$${Math.round(a)}`;
}

export function mesesAtras(hoyStr, n) {
  const out = []; let [y, m] = hoyStr.slice(0, 7).split('-').map(Number);
  for (let i = 0; i < n; i++) { out.unshift(`${y}-${String(m).padStart(2, '0')}`); m--; if (m === 0) { m = 12; y--; } }
  return out;
}
export const MESN = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];
const MES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const etqMes = (ym) => MES[Number(ym.slice(5)) - 1] + (ym.slice(5) === '01' ? ' ' + ym.slice(2, 4) : '');

export function analizar(movs, hoyStr, meses = 18) {
  const r = { n: movs.length };
  r.saldo = sumar(movs, (m) => Number(m.ingreso) - Number(m.pago));
  const pagos = movs.filter((m) => m.tipo === 'pago' && Number(m.pago) > 0);
  const facts = movs.filter((m) => m.tipo === 'comprobante' && Number(m.ingreso) > 0);
  const up = pagos[pagos.length - 1], ui = facts[facts.length - 1];
  r.ultimoPago = up ? { fecha: up.fecha, monto: Number(up.pago), medio: up.medio_pago, dias: diasEntre(up.fecha, hoyStr) } : null;
  r.ultimoIngreso = ui ? { fecha: ui.fecha, monto: Number(ui.ingreso), detalle: ui.detalle, dias: diasEntre(ui.fecha, hoyStr) } : null;
  r.desde = movs[0]?.fecha || null;

  // Antigüedad de la deuda: lo que se debe hoy corresponde a las facturas más nuevas (los pagos cancelan las más viejas).
  const b = { d0_30: 0, d31_60: 0, d61_90: 0, d90: 0 };
  let resto = Math.max(r.saldo, 0), masVieja = null;
  for (let i = movs.length - 1; i >= 0 && resto > 0.5; i--) {
    const m = movs[i]; const ing = Number(m.ingreso);
    if (ing <= 0) continue;
    const t = Math.min(resto, ing), d = diasEntre(m.fecha, hoyStr);
    resto -= t; masVieja = m.fecha;
    if (d <= 30) b.d0_30 += t; else if (d <= 60) b.d31_60 += t; else if (d <= 90) b.d61_90 += t; else b.d90 += t;
  }
  r.aging = b; r.deudaMasViejaDias = masVieja ? diasEntre(masVieja, hoyStr) : null;

  // Plazo medio de pago: los pagos cancelan las facturas más viejas primero (FIFO), solo pagos de los últimos 12 meses.
  const lim = mesesAtras(hoyStr, 12)[0] + '-01';
  const cola = []; let pesoD = 0, pesoT = 0;
  for (const m of movs) {
    if (Number(m.ingreso) > 0) cola.push({ f: m.fecha, rest: Number(m.ingreso) });
    let p = Number(m.pago);
    while (p > 0.5 && cola.length) {
      const q = cola[0], t = Math.min(p, q.rest);
      if (m.tipo === 'pago' && m.fecha >= lim) { pesoD += t * Math.max(0, diasEntre(q.f, m.fecha)); pesoT += t; }
      q.rest -= t; p -= t; if (q.rest <= 0.5) cola.shift();
    }
  }
  r.plazoMedio = pesoT > 0 ? Math.round(pesoD / pesoT) : null;

  // Series mensuales
  const ms = Array.isArray(meses) ? meses : mesesAtras(hoyStr, meses), idx = Object.fromEntries(ms.map((m, i) => [m, i]));
  r.mensual = ms.map((m) => ({ mes: m, etq: etqMes(m), compras: 0, pagos: 0, saldo: null }));
  let corrido = 0, j = 0;
  for (const m of movs) {
    const k = idx[mesDe(m.fecha)];
    if (mesDe(m.fecha) < ms[0]) { corrido += Number(m.ingreso) - Number(m.pago); continue; }
    if (k !== undefined) {
      if (m.tipo !== 'saldo_inicial') r.mensual[k].compras += Number(m.ingreso);
      if (m.tipo === 'pago') r.mensual[k].pagos += Number(m.pago);
    }
  }
  corrido = 0; j = 0;
  for (let i = 0; i < ms.length; i++) {
    while (j < movs.length && mesDe(movs[j].fecha) <= ms[i]) { corrido += Number(movs[j].ingreso) - Number(movs[j].pago); j++; }
    r.mensual[i].saldo = corrido;
  }

  const m12 = mesesAtras(hoyStr, 12), mPrev = mesesAtras(hoyStr, 24).slice(0, 12);
  const enMes = (set, f) => sumar(movs.filter((m) => set.includes(mesDe(m.fecha)) && m.tipo !== 'saldo_inicial'), f);
  r.compras12 = enMes(m12, (m) => m.ingreso); r.comprasPrev = enMes(mPrev, (m) => m.ingreso);
  r.pagos12 = sumar(movs.filter((m) => m.tipo === 'pago' && m12.includes(mesDe(m.fecha))), (m) => m.pago);
  r.tendencia = r.comprasPrev > 0 ? (r.compras12 / r.comprasPrev - 1) * 100 : null;
  const f12 = facts.filter((m) => m12.includes(mesDe(m.fecha)));
  r.facturas12 = f12.length; r.ticket = f12.length ? sumar(f12, (m) => m.ingreso) / f12.length : null;
  r.cadenciaDias = f12.length > 1 ? Math.round(diasEntre(f12[0].fecha, f12[f12.length - 1].fecha) / (f12.length - 1)) : null;
  r.pagos12n = pagos.filter((m) => m12.includes(mesDe(m.fecha))).length;

  r.cheques = movs.filter((m) => m.medio_pago === 'cheque' && m.cheque_cobrado === false)
    .sort((a, b) => String(a.cheque_fecha_cobro || '9').localeCompare(String(b.cheque_fecha_cobro || '9')));
  r.chequesTotal = sumar(r.cheques, (m) => m.pago);
  return r;
}

export function lecturas(a, hoyStr) {
  const L = [];
  const mal = (t) => L.push(['bad', t]), ojo = (t) => L.push(['warn', t]), bien = (t) => L.push(['ok', t]), info = (t) => L.push(['', t]);
  if (!a.n) return [['', 'Todavía no tiene movimientos.']];
  if (a.saldo > 1) {
    if (a.ultimoPago) {
      const t = `Última vez que se le pagó: hace ${a.ultimoPago.dias} días (${peso0(a.ultimoPago.monto)}${a.ultimoPago.medio ? ' por ' + a.ultimoPago.medio : ''}).`;
      a.ultimoPago.dias > 60 ? mal(t) : a.ultimoPago.dias > 30 ? ojo(t) : info(t);
    } else mal('Nunca se le registró un pago.');
    if (a.deudaMasViejaDias != null) {
      const t = `La deuda más antigua que sigue abierta tiene ${a.deudaMasViejaDias} días.`;
      a.deudaMasViejaDias > 90 ? mal(t) : a.deudaMasViejaDias > 60 ? ojo(t) : info(t);
    }
    const viejo = a.aging.d61_90 + a.aging.d90;
    if (viejo > 0) ojo(`${peso0(viejo)} (${Math.round((viejo / a.saldo) * 100)}% del saldo) tiene más de 60 días.`);
  } else if (a.saldo < -1) bien(`Tenemos ${peso0(-a.saldo)} a favor con este proveedor.`);
  else bien('Cuenta al día, sin deuda.');
  if (a.plazoMedio != null) info(`Se le paga en promedio a ${a.plazoMedio} días de la factura (últimos 12 meses).`);
  if (a.tendencia != null && Math.abs(a.tendencia) >= 10) info(`Las compras de los últimos 12 meses son ${a.tendencia > 0 ? 'un ' + Math.round(a.tendencia) + '% más' : 'un ' + Math.round(-a.tendencia) + '% menos'} que los 12 anteriores.`);
  if (a.ultimoIngreso && a.ultimoIngreso.dias > 180) ojo(`Hace ${a.ultimoIngreso.dias} días que no nos factura: ¿sigue siendo proveedor activo?`);
  if (a.cheques.length) {
    const p = a.cheques[0];
    info(`${a.cheques.length} cheque${a.cheques.length > 1 ? 's' : ''} sin cobrar por ${peso0(a.chequesTotal)}${p.cheque_fecha_cobro ? '; el próximo vence el ' + fecha(p.cheque_fecha_cobro) : ''}.`);
  }
  return L;
}

// ---- Gráficos SVG ----
const nombreMes = (ym) => `${MESN[Number(ym.slice(5)) - 1]} ${ym.slice(0, 4)}`;
// Tooltip: aparece al pasar el mouse o al tocar/clickear una columna o punto.
export function activarTooltips(root = document) {
  let tip = document.getElementById('tip');
  if (!tip) { tip = document.createElement('div'); tip.id = 'tip'; document.body.appendChild(tip); }
  const mostrar = (e) => {
    const t = e.target.closest('[data-t]'); if (!t) { tip.style.display = 'none'; return; }
    const [h, ...l] = t.dataset.t.split('|');
    tip.innerHTML = `<b>${h}</b>${l.map((x) => '<div>' + x + '</div>').join('')}`; tip.style.display = 'block';
    const w = tip.offsetWidth; tip.style.left = Math.max(4, Math.min(innerWidth - w - 4, e.clientX + 14)) + 'px'; tip.style.top = Math.max(4, e.clientY - tip.offsetHeight - 10) + 'px';
  };
  root.addEventListener('mousemove', mostrar); root.addEventListener('click', mostrar);
  root.addEventListener('mouseleave', () => (tip.style.display = 'none'));
}
export function graficoBarras(mensual) {
  const W = 720, H = 220, pl = 52, pr = 8, pt = 10, pb = 26;
  const max = Math.max(1, ...mensual.flatMap((m) => [m.compras, m.pagos]));
  const y = (v) => pt + (H - pt - pb) * (1 - v / max), bw = (W - pl - pr) / mensual.length;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Facturas y pagos por mes">`;
  for (let i = 0; i <= 3; i++) { const v = (max * i) / 3; s += `<line class="ax" x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}"/><text x="${pl - 6}" y="${y(v) + 4}" text-anchor="end">${corto(v)}</text>`; }
  mensual.forEach((m, i) => {
    const x = pl + i * bw, w = Math.max(2, bw * 0.38);
    if (m.compras > 0) s += `<rect x="${x + bw * 0.08}" y="${y(m.compras)}" width="${w}" height="${H - pb - y(m.compras)}" rx="2" fill="#4f8cff"></rect>`;
    if (m.pagos > 0) s += `<rect x="${x + bw * 0.08 + w + 1}" y="${y(m.pagos)}" width="${w}" height="${H - pb - y(m.pagos)}" rx="2" fill="#6fcf97"></rect>`;
    if (i % Math.ceil(mensual.length / 12) === 0 || mensual.length <= 12) s += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle">${m.etq}</text>`;
    s += `<rect class="hit" x="${x}" y="${pt}" width="${bw}" height="${H - pt - pb}" data-t="${nombreMes(m.mes)}|Facturado: ${peso0(m.compras)}|Pagado: ${peso0(m.pagos)}"/>`;
  });
  return s + '</svg>';
}

export function graficoSaldo(mensual) {
  const W = 720, H = 180, pl = 52, pr = 12, pt = 10, pb = 26;
  const vals = mensual.map((m) => m.saldo ?? 0), mn = Math.min(0, ...vals), mx = Math.max(1, ...vals), rng = mx - mn || 1;
  const x = (i) => pl + ((W - pl - pr) * i) / Math.max(1, mensual.length - 1), y = (v) => pt + (H - pt - pb) * (1 - (v - mn) / rng);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Evolución del saldo">`;
  for (let i = 0; i <= 3; i++) { const v = mn + (rng * i) / 3; s += `<line class="ax" x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}"/><text x="${pl - 6}" y="${y(v) + 4}" text-anchor="end">${corto(v)}</text>`; }
  const pts = vals.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  s += `<polygon points="${pl},${y(mn)} ${pts} ${x(vals.length - 1)},${y(mn)}" fill="rgba(79,140,255,.15)"/><polyline points="${pts}" fill="none" stroke="#4f8cff" stroke-width="2"/>`;
  vals.forEach((v, i) => { s += `<circle cx="${x(i)}" cy="${y(v)}" r="3" fill="#4f8cff"></circle>`; if (i % Math.ceil(vals.length / 12) === 0 || vals.length <= 12) s += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${mensual[i].etq}</text>`; s += `<rect class="hit" x="${x(i) - (W - pl - pr) / vals.length / 2}" y="${pt}" width="${(W - pl - pr) / vals.length}" height="${H - pt - pb}" data-t="${nombreMes(mensual[i].mes)}|Saldo a fin de mes: ${peso0(v)}"/>`; });
  return s + '</svg>';
}

export function barraAging(a) {
  const t = a.aging.d0_30 + a.aging.d31_60 + a.aging.d61_90 + a.aging.d90;
  if (t <= 0) return '<p class="muted small">Sin deuda abierta.</p>';
  const seg = [['0–30 días', a.aging.d0_30, '#6fcf97'], ['31–60', a.aging.d31_60, '#e5b567'], ['61–90', a.aging.d61_90, '#f08a5d'], ['+90', a.aging.d90, '#f0707d']];
  return `<div class="aging">${seg.map(([n, v, c]) => `<span style="width:${(v / t) * 100}%;background:${c}" title="${n}: ${peso0(v)}"></span>`).join('')}</div>` +
    `<div class="row wrapRow small" style="gap:14px">${seg.map(([n, v, c]) => `<span><i style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${c};margin-right:5px"></i>${n}: <b class="num">${peso0(v)}</b></span>`).join('')}</div>`;
}

// semáforo para listados
export function semaforo(saldo, diasSinPagar) {
  if (saldo < -1) return ['A favor', 'ok'];
  if (saldo <= 1) return ['Al día', 'ok'];
  if (diasSinPagar == null) return ['Sin pagos', 'bad'];
  return diasSinPagar > 60 ? [`${diasSinPagar} d sin pagar`, 'bad'] : diasSinPagar > 30 ? [`${diasSinPagar} d sin pagar`, 'warn'] : [`Pagado hace ${diasSinPagar} d`, 'ok'];
}
export { esc };

// Barras de un solo valor por mes (costos). Cada columna lleva data-mes para poder elegirla con un click.
export function graficoSimple(serie, color = '#4f8cff', activo = null) {
  const W = 720, H = 200, pl = 52, pr = 8, pt = 10, pb = 26, max = Math.max(1, ...serie.map((m) => m.valor)), bw = (W - pl - pr) / serie.length;
  const y = (v) => pt + (H - pt - pb) * (1 - v / max);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Total por mes">`;
  for (let i = 0; i <= 3; i++) { const v = (max * i) / 3; s += `<line class="ax" x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}"/><text x="${pl - 6}" y="${y(v) + 4}" text-anchor="end">${corto(v)}</text>`; }
  serie.forEach((m, i) => {
    const x = pl + i * bw, w = bw * 0.6;
    if (m.valor > 0) s += `<rect x="${x + bw * 0.2}" y="${y(m.valor)}" width="${w}" height="${H - pb - y(m.valor)}" rx="2" fill="${color}" opacity="${activo && m.mes !== activo ? 0.45 : 1}"/>`;
    s += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle">${m.etq}</text><rect class="hit" data-mes="${m.mes}" x="${x}" y="${pt}" width="${bw}" height="${H - pt - pb}" data-t="${nombreMes(m.mes)}|Total: ${peso0(m.valor)}"/>`;
  });
  return s + '</svg>';
}
export { etqMes };
