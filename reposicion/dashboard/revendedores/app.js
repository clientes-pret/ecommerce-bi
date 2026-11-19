// reposicion/dashboard/revendedores/app.js — helpers del portal de revendedores.
// Misma base que el panel de control (cliente de Supabase, estilos, formatos): se importa de ../proveedores/.
// Lo que ve cada revendedor sale SOLO de funciones de la base (rpc) que filtran por el usuario logueado:
// nunca se lee una tabla rev_* directo (guardan tokens de Mercado Libre y costos).
import { sb, $, $$, esc, qs, peso, peso0, fecha, fechaHora, hoy, parseMonto, LINK_RECUPERO, SUPABASE_ANON_KEY } from '../proveedores/app.js';

export { sb, $, $$, esc, qs, peso, peso0, fecha, fechaHora, hoy, parseMonto, LINK_RECUPERO, SUPABASE_ANON_KEY };
import { SUPABASE_URL } from '../proveedores/app.js';

// Función "Publicar en mi cuenta" (Edge Function rev-publicar). Para probar con la función corriendo en la compu:
// agregar ?api=http://localhost:8000 a la dirección de la página.
const API_PUBLICAR = (new URLSearchParams(location.search).get('api') || `${SUPABASE_URL}/functions/v1/rev-publicar`).replace(/\/+$/, '');
export async function apiPublicar(ruta, body) {
  const { data } = await sb.auth.getSession();
  const r = await fetch(`${API_PUBLICAR}/${ruta}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${data.session?.access_token ?? ''}` },
    body: JSON.stringify(body),
  });
  let j = null;
  try { j = await r.json(); } catch { /* sin cuerpo */ }
  if (!r.ok) throw new Error(j?.error || `No se pudo (HTTP ${r.status}).`);
  return j;
}

export const num = new Intl.NumberFormat('es-AR');
export const quitarAcentos = (s) => String(s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

// Lee todas las filas de una función que devuelve una tabla (se pagina de a 1000; las funciones ya traen su orden).
export async function leerRpc(fn, args = {}) {
  const out = [];
  for (let i = 0; ; i += 1000) {
    const { data, error } = await sb.rpc(fn, args).range(i, i + 999);
    if (error) throw error;
    out.push(...data);
    if (data.length < 1000) return out;
  }
}

// Usuarios sin email (ej. "casalavan"): en Supabase Auth se guardan como <usuario>@DOMINIO_USUARIOS, una dirección que
// no recibe correos (por eso a esos usuarios no se les ofrece recuperar la contraseña por mail).
export const DOMINIO_USUARIOS = 'usuarios.revendedores.pretahome.com.ar';
export const esUsuarioSinEmail = (v) => !String(v ?? '').includes('@');
export const emailDeUsuario = (v) => {
  const t = String(v ?? '').trim().toLowerCase();
  return esUsuarioSinEmail(t) ? `${t}@${DOMINIO_USUARIOS}` : t;
};

export async function salir() { await sb.auth.signOut(); location.href = 'index.html'; }

// Verifica la sesión y quién es: un revendedor (tiene cuenta en rev_usuarios) o alguien del equipo (admin/dueño).
// `permitidos`: ['rev', 'admin']. Si no corresponde, redirige. Devuelve { tipo, nombre, cuenta? }.
export async function guardRev(permitidos = ['rev', 'admin']) {
  if (SUPABASE_ANON_KEY.startsWith('PEGAR')) throw new Error('anon key sin configurar');
  const { data } = await sb.auth.getSession();
  if (!data.session) { location.replace('index.html'); throw new Error('sin sesión'); }
  const { data: cu } = await sb.rpc('rev_mi_cuenta');
  let s = null;
  if (cu?.length) s = { tipo: 'rev', nombre: cu[0].nickname || data.session.user.email, cuenta: cu[0] };
  else {
    const { data: p } = await sb.from('cc_perfiles').select('nombre,rol').eq('id', data.session.user.id).maybeSingle();
    if (p && ['admin', 'dueno'].includes(p.rol)) s = { tipo: 'admin', nombre: p.nombre };
  }
  if (!s) { await sb.auth.signOut(); location.replace('index.html?sinacceso=1'); throw new Error('sin acceso'); }
  if (!permitidos.includes(s.tipo)) { location.replace(s.tipo === 'admin' ? 'admin.html' : 'catalogo.html'); throw new Error('rol incorrecto'); }
  return s;
}

// Menú lateral (en el celular se vuelve una fila que se desliza, igual que el panel de control).
export function navRev(sesion, activa) {
  const it = (href, txt, k) => `<a href="${href}" class="${activa === k ? 'on' : ''}"${activa === k ? ' aria-current="page"' : ''}>${txt}</a>`;
  const el = document.createElement('nav');
  el.className = 'side';
  el.setAttribute('aria-label', 'Portal de revendedores');
  const admin = sesion.tipo === 'admin';
  el.innerHTML = '<div class="marca"><img src="../assets/logo-white.png" alt="Pret a Home" onerror="this.remove()"><div class="titulo">Revendedores</div></div>' +
    '<div class="links">' + it('catalogo.html', 'Catálogo', 'catalogo') + it('publicaciones.html', 'Mis publicaciones', 'publicaciones') + it('cuenta.html', 'Mi cuenta', 'cuenta') +
    (admin ? '<div class="grupo">Administración</div>' + it('admin.html', 'Revendedores y pagos', 'admin') + it('estado.html', 'Estado del sistema', 'estado') + '<div class="grupo">Otras plataformas</div>' + it('../proveedores/admin.html', 'Panel de control ↗', 'panel') : '') +
    '<span class="sp"></span></div>' +
    `<div class="usuario"><span>${esc(sesion.nombre)}</span><a href="#" id="salir">Salir</a></div>`;
  document.body.classList.add('panel');
  document.body.prepend(el);
  const links = $('.links', el), on = $('a.on', el);
  if (links && on) links.scrollLeft = Math.max(0, on.offsetLeft - 24);
  $('#salir').onclick = (e) => { e.preventDefault(); salir(); };
  if (sesion.cuenta?.modo === 'sombra') {
    const b = document.createElement('div');
    b.className = 'wrap'; b.style.cssText = 'flex:0 0 100%;padding:0;max-width:none;order:-1';
    b.innerHTML = '<div style="background:#FFF1C2;color:#6B4E00;padding:8px 16px;font-size:13px;font-weight:600;text-align:center">Modo prueba: todavía no se descuenta stock ni se cobran ventas reales.</div>';
    document.body.prepend(b);
  }
}

// ---- Estados de las órdenes ----
export const ESTADO_ORDEN = {
  nueva: ['En proceso', 'warn'], sombra: ['Simulada (prueba)', 'ejemplo'], descontada: ['Confirmada', 'ok'],
  sin_stock: ['Sin stock', 'bad'], error: ['Con error', 'bad'], cancelada: ['Cancelada', ''],
};
export const chipOrden = (e) => { const [t, c] = ESTADO_ORDEN[e] || [e, '']; return `<span class="chip ${c}">${esc(t)}</span>`; };

// Fecha (AAAA-MM-DD) en hora de Argentina de un timestamp, y el lunes de esa semana.
export const diaAR = (ts) => new Date(ts).toLocaleDateString('en-CA', { timeZone: 'America/Argentina/Buenos_Aires' });
export function lunesDe(dia) {
  const d = new Date(dia + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}
export const sumarDias = (dia, n) => { const d = new Date(dia + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

// Selección de productos para la planilla (se recuerda en este navegador).
const CLAVE_SEL = 'rev_seleccion';
export function leerSeleccion() { try { return new Set(JSON.parse(localStorage.getItem(CLAVE_SEL) || '[]')); } catch { return new Set(); } }
export function guardarSeleccion(set) { try { localStorage.setItem(CLAVE_SEL, JSON.stringify([...set])); } catch { /* sin almacenamiento: la selección dura solo esta página */ } }
