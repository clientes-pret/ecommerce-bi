// reposicion/dashboard/app.js — helpers compartidos por todas las páginas del tablero.

const PASSWORD_KEY = "repo_app_password";
const USER_KEY = "repo_app_user";

function getPassword() {
  return sessionStorage.getItem(PASSWORD_KEY) || "";
}

function getUser() {
  return localStorage.getItem(USER_KEY) || "";
}

// ─── Ingreso ────────────────────────────────────────────────────────────────
// Se entra con el usuario del panel (el mismo de Proveedores; solo dueño y administradores). La contraseña
// compartida sigue sirviendo hasta que se confirme que todos entran bien (27/11/2026); después se saca.
const RM_ROLES = ["admin", "dueno"];
let rmPanelPromise = null;
let rmPerfil = null;     // {id, nombre, email, permitido}
let rmLoginPromise = null;
let rmTokenRechazado = false; // la API no aceptó la sesión: se sigue con la contraseña

const rmPanel = () => (rmPanelPromise ||= import("./proveedores/app.js"));

async function rmSesion() {
  try {
    const { sb } = await rmPanel();
    const { data } = await sb.auth.getSession();
    if (!data.session) return null;
    const u = data.session.user;
    if (!rmPerfil || rmPerfil.id !== u.id) {
      const { data: p } = await sb.from("cc_perfiles").select("nombre,rol").eq("id", u.id).maybeSingle();
      rmPerfil = { id: u.id, nombre: p?.nombre || u.email, email: u.email, permitido: !!p && RM_ROLES.includes(p.rol) };
      if (rmPerfil.permitido) localStorage.setItem(USER_KEY, u.email); // queda como autor de pedidos y cambios
    }
    rmPintarUsuario();
    return { ...rmPerfil, token: data.session.access_token };
  } catch (e) {
    console.warn("No se pudo revisar la sesión del panel", e);
    return null;
  }
}

function rmPintarUsuario() {
  const el = document.getElementById("rmUsuario");
  if (el) el.textContent = rmPerfil?.permitido ? rmPerfil.nombre : getUser();
}

async function rmSalirSesion() {
  sessionStorage.removeItem(PASSWORD_KEY);
  if (rmPerfil) {
    const { salir } = await rmPanel();
    return salir();
  }
  location.reload();
}

function ensureLoginOverlay(sesion) {
  let overlay = document.getElementById("repoLoginOverlay");
  if (overlay) return overlay;
  overlay = document.createElement("div");
  overlay.id = "repoLoginOverlay";
  overlay.className = "rm-login";
  const volver = (location.pathname.split("/").pop() || "index.html") + location.search;
  const aviso = sesion && !sesion.permitido
    ? "Tu usuario no tiene acceso a Reposición. Entrá con uno de administrador."
    : "Entrá con tu usuario del panel, el mismo de Proveedores.";
  overlay.innerHTML = `
    <div class="rm-caja">
      <h2>Reposición</h2>
      <p>${aviso}</p>
      <a class="rm-boton" id="repoEntrarPanel" href="proveedores/index.html?volver=${encodeURIComponent(volver)}">Entrar con mi usuario</a>
      <details id="repoClave"><summary>Usar la contraseña compartida</summary>
        <form id="repoLoginForm">
          <label>Usuario <input id="repoLoginUser" type="text" value="info@pretahome.com" autocomplete="username"></label>
          <label>Contraseña <input id="repoLoginPass" type="password" autocomplete="current-password"></label>
          <div id="repoLoginError" class="rm-error" hidden>Usuario o contraseña incorrectos.</div>
          <button type="submit" class="secondary">Entrar</button>
        </form>
      </details>
    </div>`;
  // Con una sesión sin permiso, primero hay que cerrarla para poder entrar con otro usuario.
  if (sesion && !sesion.permitido) {
    overlay.querySelector("#repoEntrarPanel").addEventListener("click", async (e) => {
      e.preventDefault();
      const { sb } = await rmPanel();
      await sb.auth.signOut({ scope: "local" });
      location.href = e.target.href;
    });
  }
  document.body.appendChild(overlay);
  return overlay;
}

// Una sola espera compartida: dos pedidos a la vez no abren dos formularios ni dejan uno colgado.
function waitForLogin(sesion, conError = false) {
  const overlay = ensureLoginOverlay(sesion);
  overlay.style.display = "flex";
  if (conError) {
    document.getElementById("repoClave").open = true;
    document.getElementById("repoLoginError").hidden = false;
  }
  if (rmLoginPromise) return rmLoginPromise;
  rmLoginPromise = new Promise((resolve) => {
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
      rmLoginPromise = null;
      rmPintarUsuario();
      resolve();
    };
  });
  return rmLoginPromise;
}

async function apiFetch(path, options = {}) {
  const sesion = await rmSesion();
  let token = sesion?.permitido && !rmTokenRechazado ? sesion.token : null;
  const pedirClave = async () => { if (!getPassword() || !getUser()) await waitForLogin(sesion); };
  if (!token) await pedirClave();

  const doFetch = () =>
    fetch(`${window.REPOSICION_API_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : { "X-App-Password": getPassword() }),
        ...(options.headers || {}),
      },
    });
  const sinSesion = async () => { rmTokenRechazado = true; token = null; await pedirClave(); };

  let res;
  try {
    res = await doFetch();
  } catch (e) {
    // Una versión anterior de la API no acepta la sesión del panel y el navegador corta el pedido.
    if (!token) throw e;
    await sinSesion();
    res = await doFetch();
  }
  if (res.status === 401 && token) {
    await sinSesion();
    res = await doFetch();
  }
  if (res.status === 401) {
    sessionStorage.removeItem(PASSWORD_KEY);
    await waitForLogin(sesion, true);
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

/** Parte un texto sin espacios (un SKU) en líneas que entren en `ancho` —
 * splitTextToSize solo corta en espacios, así que un SKU largo quedaba en
 * una sola línea pisando la columna de al lado. */
function partirSinEspacios(doc, texto, ancho) {
  const lineas = [];
  let actual = "";
  for (const ch of texto) {
    if (actual && doc.getTextWidth(actual + ch) > ancho) {
      lineas.push(actual);
      actual = ch;
    } else {
      actual += ch;
    }
  }
  if (actual) lineas.push(actual);
  return lineas.length ? lineas : [""];
}

const UNIDAD_LABEL = { unidades: "Unidades", bultos: "Bultos" };

/** Selector "Cantidades en: Unidades / Bultos" — es un dato del pedido
 * entero (no de cada línea). Lo usan pedido.html y historial_pedidos.html. */
function selectorUnidad(valor = "unidades", disabled = false) {
  return `<label class="unidad-medida">Cantidades en:
    <select class="sel-unidad"${disabled ? " disabled" : ""}>
      ${Object.entries(UNIDAD_LABEL).map(([v, l]) =>
        `<option value="${v}"${v === valor ? " selected" : ""}>${l}</option>`).join("")}
    </select></label>`;
}

// Título de una orden de compra: "OC 124 - Palette" y, si se escribió a mano, "OC 124 - Palette - Sábanas 1200 hilos".
function tituloOC(id, proveedor, extra = "") {
  return [`OC ${id} - ${proveedor}`, String(extra || "").trim()].filter(Boolean).join(" - ");
}

// `oc` = número de la orden de compra (id del pedido guardado); sin número es un borrador.
function generarPDF(proveedor, lineas, unidad = "unidades", oc = null, extra = "") {
  const { jsPDF } = window.jspdf;
  const doc = new jsPDF();
  const hoy = new Date().toLocaleDateString("es-AR");
  const grupos = agruparLineasPorSku(lineas);

  doc.addImage(LOGO_PDF_BASE64, "PNG", 14, 8, 50, 8.63);
  doc.setFontSize(14);
  const tituloPDF = oc ? tituloOC(oc, proveedor, extra) : [`Orden de pedido — ${proveedor}`, String(extra || "").trim()].filter(Boolean).join(" - ");
  // Una sola línea: si el texto a mano lo alarga, se achica la letra (hasta 9 pt) para que entre.
  let tamTitulo = 14;
  while (tamTitulo > 9 && doc.getTextWidth(tituloPDF) > 182) doc.setFontSize(tamTitulo -= 0.5);
  doc.text(doc.splitTextToSize(tituloPDF, 182)[0], 14, 32);
  doc.setFontSize(10);
  doc.text(`Fecha: ${hoy}  |  Pedido por: ${getUser()}`, 14, 39);
  const unidadTxt = (UNIDAD_LABEL[unidad] || UNIDAD_LABEL.unidades).toUpperCase();
  doc.setFont(undefined, "bold");
  doc.text(`Cantidades expresadas en: ${unidadTxt}`, 14, 45);
  doc.setFont(undefined, "normal");

  // Cada texto se acomoda dentro del ancho de SU columna: un SKU largo (hay
  // de más de 30 caracteres, ej. MAN-RECT-3D150X200TRIANGULOSGRISOSCURO) se
  // parte en varias líneas en vez de pisar el nombre del producto, y el
  // producto se banca hasta 3 líneas. La altura de cada fila se ajusta a la
  // columna que más líneas ocupó. Las cantidades van alineadas a la derecha.
  const colSku = 14, colProducto = 78, colDeposito = 162, colFull = 180, colTotal = 196;
  const anchoSku = colProducto - colSku - 4;
  const anchoProducto = colDeposito - 16 - colProducto;
  const lineHeight = 4.2;

  // Pedido "todo a depósito" (ninguna línea a Full): una sola columna
  // Cantidad en vez de Depósito/Full/Total en cero.
  const soloDeposito = grupos.every((g) => g.full === 0);

  const encabezado = (yy) => {
    doc.setFontSize(10);
    doc.setFont(undefined, "bold");
    doc.text("SKU", colSku, yy);
    doc.text("Producto", colProducto, yy);
    if (soloDeposito) {
      doc.text("Cantidad", colTotal, yy, { align: "right" });
    } else {
      doc.text("Depósito", colDeposito, yy, { align: "right" });
      doc.text("Full", colFull, yy, { align: "right" });
      doc.text("Total", colTotal, yy, { align: "right" });
    }
    doc.setFont(undefined, "normal");
    doc.line(14, yy + 3, 196, yy + 3);
    doc.setFontSize(9);
    return yy + 9;
  };

  let y = encabezado(52);
  let totalDeposito = 0, totalFull = 0;
  for (const g of grupos) {
    // El SKU primero se achica (hasta 7 pt) para entrar entero en una línea;
    // solo si ni así entra se parte en dos.
    let skuSize = 9;
    doc.setFontSize(skuSize);
    while (skuSize > 7 && doc.getTextWidth(String(g.sku)) > anchoSku) doc.setFontSize(skuSize -= 0.5);
    const skuLineas = partirSinEspacios(doc, String(g.sku), anchoSku);
    doc.setFontSize(9);
    const nombreLineas = doc.splitTextToSize(String(g.nombre || ""), anchoProducto).slice(0, 3);
    const filaAlto = Math.max(skuLineas.length, nombreLineas.length, 1) * lineHeight;
    if (y + filaAlto > 280) { doc.addPage(); y = encabezado(20); }
    doc.setFontSize(skuSize);
    doc.text(skuLineas, colSku, y);
    doc.setFontSize(9);
    doc.text(nombreLineas, colProducto, y);
    if (!soloDeposito) {
      doc.text(String(g.deposito), colDeposito, y, { align: "right" });
      doc.text(String(g.full), colFull, y, { align: "right" });
    }
    doc.text(String(g.deposito + g.full), colTotal, y, { align: "right" });
    totalDeposito += g.deposito;
    totalFull += g.full;
    y += filaAlto + 3;
  }

  y += 4;
  doc.setFontSize(11);
  doc.text(
    soloDeposito
      ? `Total: ${grupos.length} productos — ${totalDeposito} ${unidadTxt.toLowerCase()} (todo a depósito)`
      : `Total: ${grupos.length} productos — Depósito: ${totalDeposito}, Full: ${totalFull}, General: ${totalDeposito + totalFull} ${unidadTxt.toLowerCase()}`,
    14, y
  );

  // El archivo se llama igual que el título ("OC 131 - City Blanco - Funda para sillón.pdf"), así cada OC baja con su
  // propio nombre. Sin número (borrador, antes de confirmar) arranca con "Borrador". Se sacan los caracteres que
  // los sistemas de archivos no admiten.
  const nombreArchivo = (oc ? tituloPDF : ["Borrador", proveedor, String(extra || "").trim()].filter(Boolean).join(" - "))
    .replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, " ").trim();
  doc.save(`${nombreArchivo}.pdf`);
}

const NAV_LINKS = [
  { href: "index.html", label: "Tablero", key: "tablero" },
  { href: "index.html?vista=estrella", label: "Productos estrella", key: "estrella" },
  { href: "pedido.html", label: "Carrito", key: "pedido" },
  { href: "historial_pedidos.html", label: "Pedidos", key: "pedidos" },
  { href: "historial_quiebres.html", label: "Quiebres", key: "quiebres" },
  { href: "analisis_stock.html", label: "Stock por categoría", key: "analisis" },
  { href: "armado.html", label: "Armado de pedidos", key: "armado" },
];

// Menú lateral, igual al del panel (estilos en tema.css; clases propias para no chocar con las de cada pantalla).
function renderNav(activeKey) {
  const nav = document.createElement("nav");
  nav.className = "rm-side";
  nav.setAttribute("aria-label", "Reposición");
  const carritoLen = getCarrito().length;
  const link = (l) => {
    const texto = l.key === "pedido" && carritoLen ? `${l.label} (${carritoLen})` : l.label;
    return `<a href="${l.href}"${l.key === activeKey ? ' class="on" aria-current="page"' : ""}>${texto}</a>`;
  };
  nav.innerHTML =
    '<div class="rm-marca"><img src="assets/logo-white.png" alt="Pret a Home"><div class="rm-titulo">Reposición</div></div>' +
    '<div class="rm-links"><a href="inicio.html">← Inicio</a><div class="rm-sec">Reposición</div>' + NAV_LINKS.map(link).join("") +
    '<span class="rm-sp"></span></div>' +
    '<div class="rm-usuario"><span id="rmUsuario"></span><a href="#" id="rmSalir">Salir</a></div>';
  document.body.classList.add("con-menu");
  document.body.insertBefore(nav, document.body.firstChild);
  nav.querySelector("#rmSalir").addEventListener("click", (e) => { e.preventDefault(); rmSalirSesion(); });
  // En el celular los links se deslizan: deja a la vista el de la pantalla actual.
  const links = nav.querySelector(".rm-links"), on = nav.querySelector("a.on");
  if (on && links.scrollWidth > links.clientWidth) links.scrollLeft = Math.max(0, on.offsetLeft - 24);
  rmPintarUsuario();
  rmSesion();
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
