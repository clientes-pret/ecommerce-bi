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
    "display:flex;gap:4px;padding:10px 20px;border-bottom:1px solid #2a2f3a;" +
    "background:#0f1115;flex-wrap:wrap;";
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
