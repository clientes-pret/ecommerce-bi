// reposicion/dashboard/app.js — helpers compartidos por todas las páginas del tablero.

const PASSWORD_KEY = "repo_app_password";
const USER_KEY = "repo_app_user";

function getPassword() {
  return sessionStorage.getItem(PASSWORD_KEY) || "";
}

function getUser() {
  return localStorage.getItem(USER_KEY) || "";
}

function ensureLoginOverlay() {
  if (document.getElementById("repoLoginOverlay")) return;
  const overlay = document.createElement("div");
  overlay.id = "repoLoginOverlay";
  overlay.style.cssText =
    "position:fixed;inset:0;background:rgba(0,0,0,0.75);display:flex;" +
    "align-items:center;justify-content:center;z-index:9999;font-family:inherit;";
  overlay.innerHTML = `
    <form id="repoLoginForm" style="background:#171a21;border:1px solid #2a2f3a;border-radius:10px;
        padding:28px;min-width:280px;display:flex;flex-direction:column;gap:12px;color:#e8eaed;">
      <div style="font-size:16px;font-weight:600;">📦 Reposición</div>
      <label style="font-size:13px;color:#8b93a1;">Usuario
        <input id="repoLoginUser" type="text" value="info@pretahome.com" autocomplete="username"
          style="width:100%;margin-top:4px;padding:8px;border-radius:6px;background:#0f1115;
              color:#e8eaed;border:1px solid #2a2f3a;box-sizing:border-box;">
      </label>
      <label style="font-size:13px;color:#8b93a1;">Contraseña
        <input id="repoLoginPass" type="password" autocomplete="current-password"
          style="width:100%;margin-top:4px;padding:8px;border-radius:6px;background:#0f1115;
              color:#e8eaed;border:1px solid #2a2f3a;box-sizing:border-box;">
      </label>
      <div id="repoLoginError" style="color:#e57373;font-size:13px;display:none;">Usuario o contraseña incorrectos.</div>
      <button type="submit" style="background:#4f8cff;color:white;border:none;border-radius:6px;
          padding:10px;font-size:14px;cursor:pointer;">Entrar</button>
    </form>`;
  document.body.appendChild(overlay);
}

function waitForLogin() {
  return new Promise((resolve) => {
    ensureLoginOverlay();
    const overlay = document.getElementById("repoLoginOverlay");
    overlay.style.display = "flex";
    const form = document.getElementById("repoLoginForm");
    const userInput = document.getElementById("repoLoginUser");
    const passInput = document.getElementById("repoLoginPass");
    if (getUser()) userInput.value = getUser();
    form.onsubmit = (e) => {
      e.preventDefault();
      const user = userInput.value.trim();
      if (!user || !passInput.value) return;
      localStorage.setItem(USER_KEY, user);
      sessionStorage.setItem(PASSWORD_KEY, passInput.value);
      overlay.style.display = "none";
      resolve();
    };
    passInput.focus();
  });
}

function showLoginError() {
  ensureLoginOverlay();
  document.getElementById("repoLoginOverlay").style.display = "flex";
  document.getElementById("repoLoginError").style.display = "block";
}

async function apiFetch(path, options = {}) {
  if (!getPassword() || !getUser()) await waitForLogin();

  const doFetch = () =>
    fetch(`${window.REPOSICION_API_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-App-Password": getPassword(),
        ...(options.headers || {}),
      },
    });

  let res = await doFetch();
  if (res.status === 401) {
    sessionStorage.removeItem(PASSWORD_KEY);
    showLoginError();
    await waitForLogin();
    res = await doFetch();
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${path} -> ${res.status}: ${body}`);
  }
  return res.status === 204 ? null : res.json();
}

// ─── PDF de pedido ──────────────────────────────────────────────────────────
// Compartido entre pedido.html (armado del carrito) e historial_pedidos.html
// (re-descarga de un pedido ya confirmado) — requiere que la página incluya
// el CDN de jsPDF y assets/logo-black-base64.js antes de este script.

const DESTINO_LABEL = { deposito: "Depósito", full_pret: "Full Pret a Home", full_lavan: "Full Casa Lavan" };

/** Agrupa líneas planas (sku+destino+cantidad) en una fila por SKU con
 * columnas Depósito/Full/Total — Full Pret y Full Lavan se suman juntos
 * (Casa Lavan hoy no maneja stock Full, así que en la práctica esto es
 * Full Pret). Ordena por nombre de producto A-Z. */
function agruparLineasPorSku(lineas) {
  const bySku = new Map();
  for (const l of lineas) {
    if (!bySku.has(l.sku)) bySku.set(l.sku, { sku: l.sku, nombre: l.nombre, deposito: 0, full: 0 });
    const g = bySku.get(l.sku);
    if (l.destino === "deposito") g.deposito += l.cantidad_pedida;
    else g.full += l.cantidad_pedida;
  }
  return Array.from(bySku.values()).sort((a, b) =>
    (a.nombre || "").localeCompare(b.nombre || "", "es", { sensitivity: "base" })
  );
}

function generarPDF(proveedor, lineas) {
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF();
  const hoy = new Date().toLocaleDateString("es-AR");
  const grupos = agruparLineasPorSku(lineas);

  doc.addImage(LOGO_PDF_BASE64, "PNG", 14, 8, 50, 8.63);
  doc.setFontSize(14);
  doc.text(`Orden de pedido — ${proveedor}`, 14, 32);
  doc.setFontSize(10);
  doc.text(`Fecha: ${hoy}  |  Pedido por: ${getUser()}`, 14, 39);

  // Columnas: SKU tiene prioridad — nunca se corta, así que necesita ancho
  // de sobra (algunos SKU pasan los 20 caracteres). Producto se banca hasta
  // 2 líneas si no entra en una sola; la altura de cada fila de la tabla se
  // ajusta según cuántas líneas terminó ocupando el producto.
  const colSku = 14, colProducto = 60, colDeposito = 140, colFull = 162, colTotal = 184;
  const anchoProducto = colDeposito - colProducto - 4;
  const lineHeight = 4.2;

  let y = 52;
  doc.setFontSize(11);
  doc.text("SKU", colSku, y);
  doc.text("Producto", colProducto, y);
  doc.text("Depósito", colDeposito, y);
  doc.text("Full", colFull, y);
  doc.text("Total", colTotal, y);
  y += 4;
  doc.line(14, y, 196, y);
  y += 6;

  doc.setFontSize(9);
  let totalDeposito = 0, totalFull = 0;
  for (const g of grupos) {
    const nombreLineas = doc.splitTextToSize(String(g.nombre || ""), anchoProducto).slice(0, 2);
    const filaAlto = Math.max(1, nombreLineas.length) * lineHeight;
    if (y + filaAlto > 280) { doc.addPage(); y = 20; }
    doc.text(String(g.sku), colSku, y);
    doc.text(nombreLineas, colProducto, y);
    doc.text(String(g.deposito), colDeposito, y);
    doc.text(String(g.full), colFull, y);
    doc.text(String(g.deposito + g.full), colTotal, y);
    totalDeposito += g.deposito;
    totalFull += g.full;
    y += filaAlto + 3;
  }

  y += 4;
  doc.setFontSize(11);
  doc.text(
    `Total: ${grupos.length} productos — Depósito: ${totalDeposito}, Full: ${totalFull}, General: ${totalDeposito + totalFull}`,
    14, y
  );

  doc.save(`pedido_${proveedor.replace(/\s+/g, "_")}_${hoy.replace(/\//g, "-")}.pdf`);
}

const NAV_LINKS = [
  { href: "index.html", label: "📦 Tablero", key: "tablero" },
  { href: "pedido.html", label: "🛒 Carrito", key: "pedido" },
  { href: "historial_pedidos.html", label: "📋 Pedidos", key: "pedidos" },
  { href: "historial_quiebres.html", label: "🕳️ Quiebres", key: "quiebres" },
  { href: "analisis_stock.html", label: "📊 Análisis de stock", key: "analisis" },
];

function renderNav(activeKey) {
  const nav = document.createElement("nav");
  nav.style.cssText =
    "display:flex;align-items:center;gap:4px;padding:10px 20px;border-bottom:1px solid #2a2f3a;" +
    "background:#0f1115;flex-wrap:wrap;";
  const logo = document.createElement("img");
  logo.src = "assets/logo-white.png";
  logo.alt = "Pret a Home";
  logo.style.cssText = "height:20px;margin-right:16px;display:block;";
  nav.appendChild(logo);
  for (const link of NAV_LINKS) {
    const a = document.createElement("a");
    a.href = link.href;
    const carritoLen = link.key === "pedido" ? getCarrito().length : 0;
    a.textContent = carritoLen ? `${link.label} (${carritoLen})` : link.label;
    const isActive = link.key === activeKey;
    a.style.cssText =
      "color:#e8eaed;text-decoration:none;font-size:13px;padding:6px 12px;border-radius:6px;" +
      (isActive ? "background:#4f8cff;" : "background:transparent;");
    nav.appendChild(a);
  }
  document.body.insertBefore(nav, document.body.firstChild);
}

// ─── Carrito ────────────────────────────────────────────────────────────────
// Reemplaza la selección ciega por checkbox: cada línea ya trae destino y
// cantidad decididos en el tablero (ver reposicion/dashboard/index.html),
// persiste en localStorage entre búsquedas/filtros/recargas.

const CARRITO_KEY = "repo_carrito";

function getCarrito() {
  try {
    return JSON.parse(localStorage.getItem(CARRITO_KEY) || "[]");
  } catch {
    return [];
  }
}

function setCarrito(lineas) {
  localStorage.setItem(CARRITO_KEY, JSON.stringify(lineas));
}

function addLineaCarrito(linea) {
  const carrito = getCarrito();
  const idx = carrito.findIndex((l) => l.sku === linea.sku && l.destino === linea.destino);
  if (idx >= 0) carrito[idx] = linea; else carrito.push(linea);
  setCarrito(carrito);
  return carrito;
}

function quitarLineaCarrito(sku, destino) {
  const carrito = getCarrito().filter((l) => !(l.sku === sku && l.destino === destino));
  setCarrito(carrito);
  return carrito;
}

function totalUnidadesCarrito() {
  return getCarrito().reduce((s, l) => s + (l.cantidad || 0), 0);
}

function fmtNum(n) {
  if (n === null || n === undefined) return "—";
  return typeof n === "number" ? n.toLocaleString("es-AR") : String(n);
}

function fmtDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

function fmtFechaHora(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("es-AR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
