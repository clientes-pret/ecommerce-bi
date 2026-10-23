// reposicion/dashboard/proveedores/app.js — helpers compartidos por todas las pantallas de Proveedores.
import { createClient } from 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/+esm';
import { SUPABASE_URL, SUPABASE_ANON_KEY, LECTURA_IA } from './config.js';

export { SUPABASE_URL, SUPABASE_ANON_KEY, LECTURA_IA };
export const sb = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const qs = (k) => new URLSearchParams(location.search).get(k);

const m2 = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const m0 = new Intl.NumberFormat('es-AR', { style: 'currency', currency: 'ARS', maximumFractionDigits: 0 });
export const peso = (n) => (n == null ? '—' : m2.format(Number(n)));
export const peso0 = (n) => (n == null ? '—' : m0.format(Number(n)));
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
  variable: ['Comisiones de marketplace', 'Envíos y logística', 'Packaging', 'Publicidad', 'Impuestos sobre ventas', 'Costos de cobro / financieros', 'Mercadería dañada o devuelta', 'Otros'],
};

// Barra superior de las pantallas de administración.
export function nav(perfil, activa) {
  const it = (href, txt, k) => `<a href="${href}" class="${activa === k ? 'on' : ''}">${txt}</a>`;
  const el = document.createElement('nav');
  el.className = 'nav';
  el.innerHTML = '<img src="../assets/logo-white.png" alt="" onerror="this.remove()">' + it('admin.html', 'Inicio', 'inicio') + it('proveedores.html', 'Proveedores', 'prov') +
    it('costos.html?tipo=fijo', 'Costos fijos', 'cfijo') + it('costos.html?tipo=variable', 'Costos variables', 'cvariable') +
    `<span class="sp"></span><span class="muted small">${esc(perfil.nombre)}</span><a href="#" id="salir">Salir</a><a href="nuevo.html" class="reg ${activa === 'nuevo' ? 'on' : ''}">＋ Registrar</a>`;
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
