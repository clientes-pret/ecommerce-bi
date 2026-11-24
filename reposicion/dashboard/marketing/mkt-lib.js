// reposicion/dashboard/marketing/mkt-lib.js — helpers compartidos por las pantallas de Marketing.
import { sb, esc } from '../proveedores/app.js';

export const AR = 'America/Argentina/Buenos_Aires';
export const hoyAR = () => new Date().toLocaleDateString('en-CA', { timeZone: AR });
export const sumarDias = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toLocaleDateString('en-CA'); };
export const ddmm = (iso) => iso.slice(8, 10) + '/' + iso.slice(5, 7);
export const hace = (iso) => {
  if (!iso) return '—';
  const m = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  return m < 60 ? `hace ${m} min` : m < 1440 ? `hace ${Math.round(m / 60)} h` : `hace ${Math.round(m / 1440)} d`;
};

export const n0 = (n) => (n == null ? '—' : Number(n).toLocaleString('es-AR'));
export const pct = (n, d = 1) => (n == null ? '—' : Number(n).toFixed(d).replace('.', ',') + ' %');
export const peso = (n) => (n == null ? '—' : '$' + Math.round(Number(n)).toLocaleString('es-AR'));

// Semáforo (los umbrales y la clasificación viven en la base: función mkt_tablero).
export const SEM = {
  killer: ['Killer', '#2F9E5B', 'Buen volumen de visitas y alta conversión'],
  potencial: ['Con potencial', '#E0A100', 'Buen volumen de visitas pero baja conversión'],
  impulsar: ['A impulsar', '#2B8CD9', 'Pocas visitas pero alta conversión'],
  rojo: ['Dormidas', '#D64545', 'Pocas visitas y poca conversión'],     // la clave interna sigue siendo "rojo" (así se guarda y se filtra)
  sinstock: ['Sin stock', '#1B1D21', 'No tiene stock: no se puede comprar, así que no se juzga por visitas ni conversión'],
};
// Un producto sin stock no se puede comprar: sus pocas visitas o ventas no dicen nada de la publicación. Se muestra en negro como
// «Sin stock» y el color que le tocaría queda en semaforo_base (la base lo sigue calculando igual).
export const conSinStock = (r) => (r.stock != null && r.stock <= 0 ? { ...r, semaforo_base: r.semaforo, semaforo: 'sinstock' } : r);
export const dotSem = (s) => (SEM[s] ? `<span class="dot" style="background:${SEM[s][1]}"></span>` : '');
export const chipSem = (s) => (SEM[s]
  ? `<span class="chip" title="${esc(SEM[s][2])}">${dotSem(s)}${SEM[s][0]}</span>`
  : '<span class="chip warn" title="Sin visitas de GA4: falta volver a autorizar la cuenta de Google">Sin visitas</span>');

export const urlTienda = (handle) => `https://www.pretahome.com/productos/${encodeURIComponent(handle)}/`;
// Ícono de link a la página del producto en la tienda: va en todos lados donde aparece un producto.
export const linkTienda = (handle) => (handle
  ? `<a href="${urlTienda(handle)}" target="_blank" rel="noopener" title="Abrir la página del producto en la tienda" onclick="event.stopPropagation()" aria-label="Abrir la página del producto en la tienda">↗</a>` : '');
export const chipHome = (f) => (f.en_home ? '<span class="chip info" title="Está hoy en la homepage de la tienda">🏠 Home</span>' : '');

export const tono = (f, cfg) => (f.ganancia == null ? '' : f.ganancia < 0 ? 'bad' : f.roi < (cfg.roi_piso ?? 15) ? 'warn' : 'ok');
export const chipMargen = (f, cfg) => (f.margen == null
  ? '<span class="chip" title="El producto no tiene costo cargado en Tienda Nube">Sin costo</span>'
  : `<span class="chip ${tono(f, cfg)}" title="Ganancia ${peso(f.ganancia)} · ROI ${pct(f.roi, 0)}">${pct(f.margen, 0)}</span>`);

export async function cargarConfig() {
  const { data } = await sb.from('mkt_config').select('clave,valor');
  return Object.fromEntries((data || []).map((r) => [r.clave, Number(r.valor)]));
}

// El tablero viene ordenado desde la base (visitas de 7 días, id): se pagina de a 1000 sin perder filas.
export async function cargarTablero() {
  const out = [];
  for (let i = 0; ; i += 1000) {
    const { data, error } = await sb.rpc('mkt_tablero').range(i, i + 999);
    if (error) throw error;
    out.push(...data.map(conSinStock));
    if (data.length < 1000) return out;
  }
}

export const sinDatos = (e) => `<div class="card"><h2>Todavía no hay datos de Marketing</h2>
  <p class="muted">${esc(e?.message || 'No se pudo leer.')}</p>
  <p class="small muted">Si es la primera vez: falta aplicar la migración de Marketing en la base y correr una vez la carga de datos (<code>mkt_sync</code>).</p></div>`;

// ---- Gráficos ----
// series: [{ d: [valor|null por día], c: color }]; fechas: ['AAAA-MM-DD'] alineado con d. Los null dejan un hueco (no se dibujan como 0).
// Ancho en píxeles disponible para un gráfico: el de la página menos los márgenes de las tarjetas.
export const anchoGrafico = () => Math.max(300, Math.min(1500, (document.querySelector('#app')?.clientWidth || 900) - 150));
// Vuelve a dibujar los gráficos si cambia el ancho de la ventana (así las letras siguen siendo de tamaño normal).
export function alCambiarAncho(fn) {
  let ultimo = anchoGrafico(), t;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(() => { const a = anchoGrafico(); if (Math.abs(a - ultimo) > 40) { ultimo = a; fn(); } }, 200); });
}
export function chart(series, fechas, { bars = false, step = false, t = '', f = (v) => Math.round(v), h = null, refs = [], marks = [] } = {}) {
  const W = anchoGrafico(); h = h || (W > 700 ? 150 : 124);
  const pl = 52, pr = 12, pt = 10, pb = 24, pw = W - pl - pr, ph = h - pt - pb, len = fechas.length;
  const todos = series.flatMap((s) => s.d).filter((v) => v != null);
  if (!todos.length) return '<p class="muted small" style="margin:8px 0">Todavía no hay datos de este período.</p>';
  let mn = Math.min(...todos), mx = Math.max(...todos);
  if (bars) mn = Math.min(0, mn);
  for (const r of refs) { mn = Math.min(mn, r.v); mx = Math.max(mx, r.v); }
  if (!bars) { const pd = (mx - mn) * 0.12 || 1, noNeg = mn >= 0; mn -= pd; mx += pd; if (noNeg && mn < 0) mn = 0; }   // un conteo (visitas, stock) no baja de 0
  if (mx === mn) mx = mn + 1;
  const Y = (v) => pt + ph * (1 - (v - mn) / (mx - mn));
  const X = (i) => pl + (len > 1 ? (i * pw) / (len - 1) : pw / 2);
  const tx = 'font-size:11px;fill:#5B5F66;font-variant-numeric:tabular-nums';
  let s = `<svg viewBox="0 0 ${W} ${h}" width="100%" style="display:block;max-width:${W}px" role="img" aria-label="${esc(t)}"><title>${esc(t)}</title>`;
  for (const k of [0, 0.5, 1]) {
    const v = mn + (mx - mn) * k, y = Y(v);
    s += `<line x1="${pl}" x2="${W - pr}" y1="${y}" y2="${y}" stroke="${k === 0 ? '#C9C6BD' : '#EEEDE8'}"/><text x="${pl - 6}" y="${y + 3.5}" text-anchor="end" style="${tx}">${esc(f(v))}</text>`;
  }
  for (const r of refs) s += `<line x1="${pl}" x2="${W - pr}" y1="${Y(r.v)}" y2="${Y(r.v)}" stroke-dasharray="4 3" stroke="${r.c}"/><text x="${pl + 4}" y="${Y(r.v) - 3}" style="font-size:11px;font-weight:600;fill:${r.c}">${esc(r.t)}</text>`;
  series.forEach((sr) => {
    if (bars) {
      const bw = Math.max(2, Math.min(18, (pw / len) * 0.55));
      sr.d.forEach((v, i) => { if (v != null) s += `<rect x="${X(i) - bw / 2}" y="${Y(v)}" width="${bw}" height="${Math.max(0, Y(Math.max(mn, 0)) - Y(v))}" rx="1.5" fill="${sr.c}"/>`; });
    } else {
      let d = '', prev = null;
      sr.d.forEach((v, i) => {
        if (v == null) { prev = null; return; }
        d += prev == null ? `M${X(i)} ${Y(v)} ` : (step ? `H${X(i)}V${Y(v)} ` : `L${X(i)} ${Y(v)} `);
        prev = v;
      });
      s += `<path d="${d}" fill="none" stroke="${sr.c}" stroke-width="2" stroke-linejoin="round"/>`;
    }
  });
  const ult = series[series.length - 1].d;
  for (const m of marks) {
    const i = fechas.indexOf(m.fecha);
    if (i >= 0 && ult[i] != null) s += `<line x1="${X(i)}" x2="${X(i)}" y1="${pt}" y2="${Y(mn)}" stroke-dasharray="2 3" stroke="#9EA2A8"/><circle cx="${X(i)}" cy="${Y(ult[i])}" r="4" fill="#1B1D21" stroke="#fff" stroke-width="1.5"><title>${esc(ddmm(m.fecha) + ' · ' + m.t)}</title></circle>`;
  }
  for (let k = 0; k < 5; k++) {
    const i = Math.round((k * (len - 1)) / 4);
    s += `<text x="${X(i)}" y="${h - 6}" text-anchor="${k === 0 ? 'start' : k === 4 ? 'end' : 'middle'}" style="${tx}">${ddmm(fechas[i])}</text>`;
  }
  fechas.forEach((fe, i) => {
    s += `<rect x="${X(i) - pw / len / 2}" y="${pt}" width="${pw / len}" height="${ph}" fill="transparent"><title>${esc(ddmm(fe) + ' · ' + series.map((sr) => (sr.d[i] == null ? 'sin dato' : f(sr.d[i]))).join(' / '))}</title></rect>`;
  });
  return s + '</svg>';
}

// Panel de un gráfico: título, leyenda y valor actual a la derecha.
export const panelGrafico = (titulo, valor, leyenda, svg) => `<div class="mk-cc"><div class="mk-cch"><span class="t">${titulo}</span><span class="lg">${leyenda || ''}</span><span class="vv">${valor}</span></div>${svg}</div>`;

// Días [desde, hasta] en orden, para alinear series con huecos.
export const rangoDias = (hasta, n) => Array.from({ length: n }, (_, i) => sumarDias(hasta, i - (n - 1)));
export const sumar = (a) => a.reduce((x, y) => x + (y ?? 0), 0);

// ---- Tareas del equipo (ficha del producto y tablero Kanban) ----
export const TIPOS = { fotos: 'Cambio de fotos', precio: 'Mejora de precio', publicidad: 'Armado de publicidad', titulo: 'Título y descripción', otro: 'Otro' };
// Columnas del tablero, en orden: [clave en la base, título, clase del chip].
export const ESTADOS = [['pendiente', 'Por hacer', ''], ['en_curso', 'En curso', 'info'], ['hecho', 'Hecho', 'ok']];
export const nombreEstado = (e) => (ESTADOS.find((x) => x[0] === e) || [e, e])[1];

// ---- Anuncios (Meta y Google Ads), medidos en GA4 por la fuente de la sesión y la página de destino ----
export const FUENTES = { meta: 'Meta (Instagram y Facebook)', google: 'Google Ads' };
// {product_id → {vm, cm, vg, cg}} = visitas y compras desde Meta y desde Google Ads en los últimos `dias` días completos.
// Devuelve null si todavía no está la migración de anuncios (las pantallas siguen andando sin esa parte).
export async function cargarAdsProducto(dias = 7) {
  const { data, error } = await sb.rpc('mkt_ads_producto', { p_dias: dias });
  if (error) return null;
  return new Map(data.map((r) => [r.product_id, { vm: Number(r.visitas_meta), cm: Number(r.compras_meta), vg: Number(r.visitas_google), cg: Number(r.compras_google) }]));
}
// Qué es una página de destino: un producto (/productos/<handle>), el inicio o una categoría/colección.
export function tipoPagina(ruta) {
  if (ruta === '/') return ['inicio', 'Inicio', 'info'];
  if (/^\/productos\/[^/]+$/.test(ruta)) return ['producto', 'Producto', 'ok'];
  if (ruta === '/productos') return ['categoria', 'Listado', ''];
  return ['categoria', 'Categoría', ''];
}
export const urlSitio = (ruta) => `https://www.pretahome.com${ruta}`;
