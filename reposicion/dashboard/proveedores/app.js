// reposicion/dashboard/proveedores/app.js — helpers compartidos por todas las pantallas de Proveedores.
import { createClient } from 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/+esm';
import { SUPABASE_URL, SUPABASE_ANON_KEY, LECTURA_IA } from './config.js';

export { SUPABASE_URL, SUPABASE_ANON_KEY, LECTURA_IA };
// El link del mail "olvidé mi contraseña" llega con #...type=recovery (o #error=... si venció).
// Se lee antes de crear el cliente porque el cliente limpia el # al tomar la sesión.
export const LINK_RECUPERO = new URLSearchParams(location.hash.slice(1));
export const sb = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const qs = (k) => new URLSearchParams(location.search).get(k);

const m2 = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const m0 = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 });
export const peso = (n) => (n == null ? '—' : m2.format(Number(n)));
export const peso0 = (n) => (n == null ? '—' : m0.format(Number(n)));
const u2 = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 2 });
export const dolar = (n) => (n == null ? '—' : u2.format(Number(n)));
export const fecha = (d) => (d ? new Date(d.length > 10 ? d : d + 'T12:00:00').toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—');
export const fechaHora = (d) => (d ? new Date(d).toLocaleString('es-AR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');
export const hoy = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Argentina/Buenos_Aires' });
export const parseMonto = (s) => parseFloat(String(s).replace(/\./g, '').replace(',', '.'));

export const ESTADO = {
  pendiente_control: ['En revisión', 'warn'], controlada: ['Controlada', 'ok'], con_diferencia: ['Con diferencia', 'bad'], anulada: ['Anulada', ''], borrador: ['Borrador', ''],
};
export const chipEstado = (e) => `<span class="chip ${ESTADO[e]?.[1] || ''}">${esc(ESTADO[e]?.[0] || e)}</span>`;

export const rutaPara = (rol) => (rol === 'deposito' ? 'recibir.html' : 'admin.html');

// Verifica sesión y rol. Si no corresponde, redirige. Devuelve el perfil.
export async function guard(roles) {
  if (SUPABASE_ANON_KEY.startsWith('PEGAR')) {
    document.body.innerHTML = '<div class="narrow"><h1>Falta configurar</h1><p>Pegá la anon key de Supabase en <code>config.js</code>.</p></div>';
    throw new Error('anon key sin configurar');
  }
  const { data } = await sb.auth.getSession();
  if (!data.session) { location.replace('index.html'); throw new Error('sin sesión'); }
  const { data: p } = await sb.from('cc_perfiles').select('id,nombre,rol').eq('id', data.session.user.id).maybeSingle();
  if (!p) { await sb.auth.signOut(); location.replace('index.html'); throw new Error('sin perfil'); }
  if (!roles.includes(p.rol)) { location.replace(rutaPara(p.rol)); throw new Error('rol incorrecto'); }
  return p;
}

export async function salir() { await sb.auth.signOut(); location.href = 'index.html'; }

// Rubros sugeridos para cargar costos (se puede escribir uno nuevo).
export const RUBROS_COSTO = {
  fijo: ['Alquiler', 'Sueldos y cargas sociales', 'Servicios (luz, gas, internet)', 'Contador y administración', 'Software y suscripciones', 'Seguros', 'Impuestos fijos', 'Otros'],
  variable: ['Horas extra', 'Comisiones de marketplace', 'Envíos y logística', 'Packaging', 'Publicidad', 'Impuestos sobre ventas', 'Costos de cobro / financieros', 'Mercadería dañada o devuelta', 'Otros'],
};

// Lectura paginada con orden fijo (sin orden, las páginas pueden repetir o perder filas).
// `armar(q)` agrega los filtros; `orden` es la lista de columnas para ordenar.
export async function leerTodo(tabla, cols, orden, armar = (q) => q) {
  const out = [];
  for (let i = 0; ; i += 1000) {
    let q = armar(sb.from(tabla).select(cols));
    for (const o of orden) q = q.order(o);
    const { data, error } = await q.range(i, i + 999);
    if (error) throw error;
    out.push(...data);
    if (data.length < 1000) return out;
  }
}
export const sumarMes = (ym, n) => { let [y, m] = ym.split('-').map(Number); m += n; y += Math.floor((m - 1) / 12); m = ((m - 1) % 12 + 12) % 12 + 1; return `${y}-${String(m).padStart(2, '0')}`; };

// ---- Costos que se repiten ----
// Campo "Se repite" del formulario de costos. Fijos arrancan en "todos los meses", variables en "solo este mes".
export function camposRepite(tipo, mes) {
  const def = tipo === 'fijo' ? 'siempre' : 'no';
  return `<div><label for="rep">Se repite</label><select id="rep">
    <option value="no" ${def === 'no' ? 'selected' : ''}>Solo este mes</option>
    <option value="siempre" ${def === 'siempre' ? 'selected' : ''}>Todos los meses (sin fin)</option>
    <option value="hasta">Todos los meses hasta…</option></select></div>
  <div id="repHastaBox" hidden><label for="repHasta">Último mes</label><input type="month" id="repHasta" value="${mes}"></div>`;
}
export function activarRepite() {
  const sync = () => ($('#repHastaBox').hidden = $('#rep').value !== 'hasta');
  $('#rep').onchange = sync; sync();
}
// ---- Costos en dólares ----
// Selector de moneda del importe (por defecto, pesos).
export const camposMoneda = (moneda = 'ARS', id = 'moneda') => `<select id="${id}" aria-label="Moneda" style="width:auto">
  <option value="ARS" ${moneda === 'ARS' ? 'selected' : ''}>Pesos</option><option value="USD" ${moneda === 'USD' ? 'selected' : ''}>USD</option></select>`;
// Columnas de moneda para guardar un costo. En USD la base lo pasa a pesos con el dólar oficial (venta) del día 1
// del mes (trigger cc_costos_convertir); cotizacion: null fuerza ese cálculo.
export const montoEn = (moneda, monto) => (moneda === 'USD'
  ? { moneda, monto: 0, monto_usd: monto, cotizacion: null }
  : { moneda: 'ARS', monto, monto_usd: null, cotizacion: null });
// Trae el dólar oficial BNA (argentinadatos.com, un valor por día) y guarda en cc_cotizaciones_usd lo que falte
// (y los últimos 10 días, por si se corrigieron). Si falla, la pantalla sigue con lo que ya hay guardado.
export async function actualizarDolar() {
  try {
    const { data: ult } = await sb.from('cc_cotizaciones_usd').select('fecha').order('fecha', { ascending: false }).limit(1);
    const r = await fetch('https://api.argentinadatos.com/v1/cotizaciones/dolares/oficial');
    if (!r.ok) return;
    const d = ult?.[0]?.fecha ? new Date(ult[0].fecha + 'T12:00:00') : null;
    if (d) d.setDate(d.getDate() - 10);
    const desde = d ? d.toLocaleDateString('en-CA') : '';
    const xs = (await r.json()).filter((x) => x.fecha >= desde && Number(x.venta) > 0)
      .map((x) => ({ fecha: x.fecha, compra: x.compra, venta: x.venta, actualizado_at: new Date().toISOString() }));
    for (let i = 0; i < xs.length; i += 1000) await sb.from('cc_cotizaciones_usd').upsert(xs.slice(i, i + 1000), { onConflict: 'fecha' });
  } catch (e) { console.warn('No se pudo actualizar el dólar', e); }
}
// Debajo del importe en pesos: "US$ 50 × $1.540 (dólar del 01/10)", o provisorio si todavía no está el del día 1.
export function detalleDolar(c, provisorio = c.cotizacion_fecha < c.periodo) {
  if (c.moneda !== 'USD') return '';
  const f = c.cotizacion_fecha ? c.cotizacion_fecha.slice(8, 10) + '/' + c.cotizacion_fecha.slice(5, 7) : '';
  return `<div class="small muted">${dolar(c.monto_usd)} × ${peso(c.cotizacion)}${provisorio
    ? ` <span class="chip warn" title="Todavía no está el dólar del día 1 de este mes: se usó el del ${f} y se corrige solo cuando aparezca">provisorio</span>` : ` (dólar del ${f})`}</div>`;
}

// Guarda un costo: si se repite, crea el costo recurrente y carga sus meses hasta el mes en curso.
export async function guardarCosto({ tipo, categoria, concepto, monto, mes, nota, moneda = 'ARS' }) {
  const rep = $('#rep').value, hasta = $('#repHasta').value, m = montoEn(moneda, monto);
  if (rep === 'no') return sb.from('cc_costos').insert({ tipo, categoria, concepto, ...m, periodo: mes + '-01', nota });
  if (rep === 'hasta' && (!hasta || hasta < mes)) return { error: { message: 'El último mes tiene que ser igual o posterior al mes de inicio.' } };
  const { error } = await sb.from('cc_costos_recurrentes').insert({ tipo, categoria, concepto, ...m, desde: mes + '-01', hasta: rep === 'hasta' ? hasta + '-01' : null, nota });
  if (error) return { error };
  return sb.rpc('cc_costos_al_dia');
}

// Menú lateral del panel de control (pantallas de administración).
export function nav(perfil, activa) {
  const it = (href, txt, k, cls = '') => `<a href="${href}" class="${[cls, activa === k ? 'on' : ''].filter(Boolean).join(' ')}"${activa === k ? ' aria-current="page"' : ''}>${txt}</a>`;
  const el = document.createElement('nav');
  el.className = 'side';
  el.setAttribute('aria-label', 'Panel de control');
  el.innerHTML = '<div class="marca"><img src="../assets/logo-white.png" alt="Pret a Home" onerror="this.remove()"><div class="titulo">Panel de control</div></div>' +
    it('admin.html', 'Inicio', 'inicio') + it('ventas.html', 'Ventas', 'ventas') +
    '<div class="grupo">Gestión</div>' + it('proveedores.html', 'Proveedores', 'prov') + it('costos.html', 'Costos', 'costos') +
    [['software', 'Software'], ['sueldos', 'Sueldos'], ['servicios', 'Servicios'], ['publicidad', 'Publicidad']]
      .map(([k, t]) => it(`costos.html?rubro=${k}`, t, 'costos-' + k, 'sub')).join('') +
    it('equipo.html', 'Equipo', 'equipo') + it('cashflow.html', 'Cashflow', 'cashflow') + it('pyl.html', 'P&amp;L', 'pyl') +
    `<span class="sp"></span><a href="nuevo.html" class="reg ${activa === 'nuevo' ? 'on' : ''}">＋ Registrar</a>` +
    `<div class="usuario"><span>${esc(perfil.nombre)}</span><a href="#" id="salir">Salir</a></div>`;
  document.body.classList.add('panel');
  document.body.prepend(el);
  $('#salir').onclick = (e) => { e.preventDefault(); salir(); };
}

// ---- Buscador de SKU (hoja a pantalla completa) ----
export const etiquetaProducto = (p) => [p.producto, p.color, p.medida].filter(Boolean).join(' · ');

export function pickSku({ inicial = '', onPick }) {
  const sheet = document.createElement('div');
  sheet.className = 'sheet';
  sheet.innerHTML = `<div class="row" style="margin-bottom:12px"><input id="q" placeholder="Buscar SKU o producto…" autocomplete="off"><button class="btn" id="cerrar">Cerrar</button></div><div id="res"></div>`;
  document.body.appendChild(sheet);
  const q = $('#q', sheet), res = $('#res', sheet);
  q.value = inicial; q.focus();
  $('#cerrar', sheet).onclick = () => sheet.remove();
  let t, lista = [];
  const buscar = async () => {
    const tokens = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    if (!tokens.length) { res.innerHTML = ''; return; }
    res.innerHTML = '<p class="muted"><span class="spin"></span> Buscando…</p>';
    let query = sb.from('cc_productos').select('sku,producto,color,medida').limit(30);
    for (const tk of tokens) query = query.ilike('busqueda', `%${tk.replace(/[%_,()]/g, '')}%`);
    const { data } = await query;
    lista = data || [];
    res.innerHTML = lista.length
      ? lista.map((p, i) => `<button class="pick" data-i="${i}"><b>${esc(p.sku)}</b><br><span class="muted small">${esc(etiquetaProducto(p))}</span></button>`).join('')
      : '<p class="muted">No encontré nada. Probá con menos palabras.</p>';
  };
  q.oninput = () => { clearTimeout(t); t = setTimeout(buscar, 250); };
  res.onclick = (e) => { const b = e.target.closest('[data-i]'); if (b) { const p = lista[+b.dataset.i]; sheet.remove(); onPick(p); } };
  if (inicial) buscar();
}

// ---- Fotos: achicar a ~1600px JPEG ----
export async function achicar(file, max = 1600, calidad = 0.82) {
  const bmp = await createImageBitmap(file);
  const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const c = document.createElement('canvas');
  c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
  c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
  return new Promise((ok, no) => c.toBlob((b) => (b ? ok(b) : no(new Error('No pude procesar la foto'))), 'image/jpeg', calidad));
}
export const aBase64 = (blob) => new Promise((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(',')[1]); r.onerror = () => no(r.error); r.readAsDataURL(blob); });
