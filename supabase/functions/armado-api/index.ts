// supabase/functions/armado-api/index.ts
//
// API del control de armado de pedidos (reposicion/dashboard/armado.html).
// Quien arma escanea el QR de la etiqueta de envío; esta función averigua a qué
// pedido pertenece, devuelve sus productos (foto, nombre, variante, cantidad) y
// guarda el resultado del control.
//
// Qué trae el QR de cada etiqueta (verificado con etiquetas reales el 27/10/2026):
//   - Mercado Libre Flex: {"id": <envío>, "sender_id": <cuenta ML>} → /shipments/{id}/items.
//   - Correo Argentino (Envío Nube): "<seguimiento>|DOM|…" → el seguimiento está en la orden TN.
//   - Ninja Flex: {"internalID": …} → TN lo guarda como seguimiento solo en parte de las órdenes.
//   - Zeta Llegue (envío en el día): {"did": …} → su página de seguimiento
//     (token = did + ZETA_TOKEN_SUFIJO) muestra el número de venta de TN.
// Si nada de eso encuentra el pedido, la pantalla deja escribir el número a mano.
//
// Misma contraseña compartida que reposicion-api (header X-App-Password, secret
// APP_SHARED_PASSWORD). Credenciales de Tiendanube y Mercado Libre en el secret
// ARMADO_CREDENCIALES (JSON):
//   {"tn": {"pret": {"store_id", "access_token"}, "lavan": {…}},
//    "ml": {"pret": {"user_id", "client_id", "client_secret", "refresh_token", "access_token"?}, "lavan": {…}}}

import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-App-Password",
};

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

const APP_PASSWORD = Deno.env.get("APP_SHARED_PASSWORD") ?? "";

type Tienda = "pret" | "lavan";
const TIENDAS: Tienda[] = ["pret", "lavan"];

type CredTN = { store_id: string | number; access_token: string };
type CredML = {
  user_id: string | number;
  client_id: string;
  client_secret: string;
  refresh_token?: string;
  access_token?: string;
};
const CRED: { tn: Record<Tienda, CredTN>; ml: Record<Tienda, CredML> } = JSON.parse(
  Deno.env.get("ARMADO_CREDENCIALES") ?? "{}",
);

// Sufijo fijo que Zeta agrega al "did" del QR para armar el token de seguimiento
// (igual en todas las órdenes de Pret con seguimiento de Zeta cargado, ej. 1571347d54df4s8a513).
const ZETA_TOKEN_SUFIJO = "d54df4s8a513";
const ZETA_TRACKING_URL = "https://zetaenvios.lightdata.app/tracking.php?token=";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

function checkPassword(req: Request): boolean {
  if (!APP_PASSWORD) return true;
  return req.headers.get("x-app-password") === APP_PASSWORD;
}

// ─── Tipos de respuesta ─────────────────────────────────────────────────────

type Aviso = { nivel: "rojo" | "amarillo"; texto: string };
type Item = {
  sku: string;
  nombre: string;
  variante: string[];
  cantidad: number;
  imagen: string | null;
  grupo: string; // producto padre: dos ítems del mismo grupo son el mismo producto en otra variante
};
type Pedido = {
  canal: "tn" | "ml";
  tienda: Tienda;
  pedido: string;
  envio: string | null;
  envio_tipo: string | null;
  avisos: Aviso[];
  items: Item[];
};

// ─── Qué dice el código leído ───────────────────────────────────────────────

type Codigo =
  | { tipo: "ml_envio"; envio: string; sender?: string }
  | { tipo: "zeta"; token: string }
  | { tipo: "seguimiento"; ref: string }
  | { tipo: "numero"; ref: string };

function interpretarCodigo(raw: string): Codigo | null {
  const txt = raw.trim();
  if (!txt) return null;

  if (txt.startsWith("{")) {
    try {
      const o = JSON.parse(txt);
      if (o.id && o.sender_id) return { tipo: "ml_envio", envio: String(o.id), sender: String(o.sender_id) };
      if (o.did) return { tipo: "zeta", token: `${o.did}${ZETA_TOKEN_SUFIJO}` };
      if (o.internalID) return { tipo: "seguimiento", ref: String(o.internalID) };
    } catch { /* no era JSON: sigue abajo */ }
  }
  const tokenZeta = txt.match(/lightdata\.app\/tracking\.php\?token=([\w]+)/);
  if (tokenZeta) return { tipo: "zeta", token: tokenZeta[1] };
  if (txt.includes("|")) return { tipo: "seguimiento", ref: txt.split("|")[0].trim() };

  const limpio = txt.replace(/^#/, "").replace(/\s+/g, "");
  if (/^\d+$/.test(limpio)) {
    // Los envíos de ML tienen 11 dígitos; los números de venta de TN, hasta 6.
    if (limpio.length >= 10) return { tipo: "ml_envio", envio: limpio };
    return { tipo: "numero", ref: limpio };
  }
  if (/^[\w-]{8,}$/.test(limpio)) return { tipo: "seguimiento", ref: limpio };
  return null;
}

// ─── Tiendanube ─────────────────────────────────────────────────────────────

// deno-lint-ignore no-explicit-any
type Json = any;

async function tnGet(tienda: Tienda, path: string, params: Record<string, string>): Promise<Json> {
  const c = CRED.tn?.[tienda];
  if (!c) throw new Error(`Faltan credenciales de Tiendanube (${tienda})`);
  const url = new URL(`https://api.tiendanube.com/v1/${c.store_id}${path}`);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  for (let intento = 0; intento < 3; intento++) {
    const r = await fetch(url, {
      headers: { Authentication: `bearer ${c.access_token}`, "User-Agent": "ArmadoPedidos (info@pretahome.com)" },
    });
    if (r.status === 404) return [];
    if (r.status === 429 || r.status >= 500) {
      await new Promise((res) => setTimeout(res, 1500 * (intento + 1)));
      continue;
    }
    if (!r.ok) throw new Error(`Tiendanube ${tienda} ${path} -> ${r.status}`);
    return await r.json();
  }
  throw new Error(`Tiendanube ${tienda} no responde`);
}

/** Órdenes de una tienda cuyo número o seguimiento es exactamente `ref`. */
async function tnBuscar(tienda: Tienda, ref: string): Promise<Json[]> {
  if (!CRED.tn?.[tienda]) return [];
  const ordenes: Json[] = await tnGet(tienda, "/orders", { q: ref, per_page: "50" });
  return ordenes.filter((o) =>
    String(o.number) === ref ||
    o.shipping_tracking_number === ref ||
    (o.fulfillments ?? []).some((f: Json) => f?.tracking_info?.code === ref)
  );
}

function pedidoDesdeTN(tienda: Tienda, o: Json): Pedido {
  const avisos: Aviso[] = [];
  if (o.status === "cancelled") {
    avisos.push({ nivel: "rojo", texto: "Este pedido está CANCELADO en Tiendanube. No lo despaches." });
  } else if (o.payment_status && !["paid", "authorized"].includes(o.payment_status)) {
    avisos.push({ nivel: "rojo", texto: "Este pedido no figura pagado en Tiendanube. Consultá antes de despacharlo." });
  }
  const items: Item[] = (o.products ?? []).map((p: Json) => ({
    sku: String(p.sku ?? ""),
    nombre: String(p.name_without_variants || p.name || ""),
    variante: (p.variant_values ?? []).map(String),
    cantidad: Number(p.quantity) || 1,
    imagen: p.image?.src ?? null,
    grupo: `tn-${p.product_id}`,
  }));
  return {
    canal: "tn",
    tienda,
    pedido: String(o.number),
    envio: o.shipping_tracking_number ?? null,
    envio_tipo: o.shipping_option ?? null,
    avisos,
    items,
  };
}

// ─── Mercado Libre ──────────────────────────────────────────────────────────

const mlTokens: Partial<Record<Tienda, { token: string; vence: number }>> = {};

async function mlToken(tienda: Tienda): Promise<string> {
  const cache = mlTokens[tienda];
  if (cache && cache.vence > Date.now()) return cache.token;
  const c = CRED.ml?.[tienda];
  if (!c) throw new Error(`Faltan credenciales de Mercado Libre (${tienda})`);
  if (c.refresh_token) {
    const r = await fetch("https://api.mercadolibre.com/oauth/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: c.client_id,
        client_secret: c.client_secret,
        refresh_token: c.refresh_token,
      }),
    });
    if (r.ok) {
      const t = await r.json();
      // Se renueva 10 minutos antes de que venza.
      mlTokens[tienda] = { token: t.access_token, vence: Date.now() + (Number(t.expires_in) - 600) * 1000 };
      return t.access_token;
    }
    console.error(`No se pudo renovar el token ML ${tienda}: ${r.status}`);
  }
  if (c.access_token) return c.access_token;
  throw new Error(`Sin token de Mercado Libre (${tienda})`);
}

async function mlGet(tienda: Tienda, path: string): Promise<{ status: number; body: Json }> {
  const token = await mlToken(tienda);
  for (let intento = 0; intento < 3; intento++) {
    const r = await fetch(`https://api.mercadolibre.com${path}`, { headers: { Authorization: `Bearer ${token}` } });
    if (r.status === 429 || r.status >= 500) {
      await new Promise((res) => setTimeout(res, 1500 * (intento + 1)));
      continue;
    }
    return { status: r.status, body: r.ok ? await r.json() : null };
  }
  return { status: 503, body: null };
}

function mlSku(cuerpo: Json): string {
  const sku = String(cuerpo?.seller_custom_field ?? cuerpo?.seller_sku ?? "").trim();
  if (sku) return sku;
  for (const a of cuerpo?.attributes ?? []) {
    if (a.id === "SELLER_SKU") return String(a.value_name ?? "").trim();
  }
  return "";
}

async function mlPedidoPorEnvio(envio: string, sender?: string): Promise<Pedido | null> {
  // Si el QR dice de qué cuenta es, se usa esa; si se escribió el número a mano, se prueban las dos.
  const tiendas = TIENDAS.filter((t) => CRED.ml?.[t] && (!sender || String(CRED.ml[t].user_id) === sender));
  for (const tienda of tiendas) {
    const envioRes = await mlGet(tienda, `/shipments/${envio}`);
    if (envioRes.status !== 200) continue;
    const itemsRes = await mlGet(tienda, `/shipments/${envio}/items`);
    const lineas: Json[] = itemsRes.body ?? [];

    // Datos de cada publicación (hasta 20 por llamada)
    const ids = [...new Set(lineas.map((l) => String(l.item_id)))];
    const publicaciones = new Map<string, Json>();
    for (let i = 0; i < ids.length; i += 20) {
      const r = await mlGet(tienda, `/items?ids=${ids.slice(i, i + 20).join(",")}&include_attributes=all`);
      for (const x of r.body ?? []) if (x.code === 200) publicaciones.set(String(x.body.id), x.body);
    }

    const items: Item[] = lineas.map((l) => {
      const pub = publicaciones.get(String(l.item_id));
      const variacion = (pub?.variations ?? []).find((v: Json) => String(v.id) === String(l.variation_id));
      const fotoId = variacion?.picture_ids?.[0];
      const foto = (pub?.pictures ?? []).find((p: Json) => p.id === fotoId) ?? pub?.pictures?.[0];
      return {
        sku: mlSku(variacion) || mlSku(pub),
        nombre: String(pub?.title ?? l.description ?? ""),
        variante: (variacion?.attribute_combinations ?? []).map((a: Json) => String(a.value_name)),
        cantidad: Number(l.quantity) || 1,
        imagen: foto?.secure_url ?? foto?.url ?? null,
        grupo: `ml-${l.item_id}`,
      };
    });

    const avisos: Aviso[] = [];
    const estado = envioRes.body?.status;
    if (estado === "cancelled") {
      avisos.push({ nivel: "rojo", texto: "Este envío está CANCELADO en Mercado Libre. No lo despaches." });
    } else if (estado === "shipped" || estado === "delivered") {
      avisos.push({ nivel: "amarillo", texto: "Mercado Libre ya lo figura como enviado." });
    }
    const ordenes = [...new Set(lineas.map((l) => String(l.order_id)).filter(Boolean))];
    return {
      canal: "ml",
      tienda,
      pedido: ordenes.join(", ") || envio,
      envio,
      envio_tipo: envioRes.body?.logistic_type === "self_service" ? "Mercado Libre Flex" : "Mercado Libre",
      avisos,
      items,
    };
  }
  return null;
}

// ─── Zeta Llegue ────────────────────────────────────────────────────────────

async function zetaNumeroVenta(token: string): Promise<string | null> {
  const r = await fetch(ZETA_TRACKING_URL + encodeURIComponent(token), { headers: { "User-Agent": "Mozilla/5.0" } });
  if (!r.ok) return null;
  const html = await r.text();
  const m = html.replace(/<[^>]+>/g, " ").match(/Seguimiento del env[ií]o\s+(\d+)/i);
  return m ? m[1] : null;
}

// ─── Rutas ──────────────────────────────────────────────────────────────────

async function controlesPrevios(p: Pedido): Promise<Aviso[]> {
  const { data } = await supabase
    .from("armado_controles")
    .select("creado_at, armado_por, resultado")
    .eq("tienda", p.tienda).eq("canal", p.canal).eq("pedido", p.pedido)
    .order("creado_at", { ascending: false }).limit(1);
  const c = data?.[0];
  if (!c) return [];
  const cuando = new Date(c.creado_at).toLocaleString("es-AR", {
    timeZone: "America/Argentina/Buenos_Aires", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
  const como = c.resultado === "completo" ? "completo" : "con faltante";
  return [{ nivel: "amarillo", texto: `Ya lo controló ${c.armado_por || "alguien"} el ${cuando} (${como}).` }];
}

async function postBuscar(req: Request) {
  const { codigo } = await req.json();
  const c = interpretarCodigo(String(codigo ?? ""));
  if (!c) return json({ pedidos: [], motivo: "No reconozco ese código." });

  let pedidos: Pedido[] = [];
  let motivo = "";

  if (c.tipo === "ml_envio") {
    const p = await mlPedidoPorEnvio(c.envio, c.sender);
    if (p) pedidos = [p];
    else motivo = "Mercado Libre no encontró ese envío.";
  } else {
    let ref = c.tipo === "zeta" ? "" : c.ref;
    if (c.tipo === "zeta") {
      const venta = await zetaNumeroVenta(c.token);
      if (!venta) return json({ pedidos: [], motivo: "La etiqueta de Zeta no dijo el número de venta. Escribilo a mano." });
      ref = venta;
    }
    const porTienda = await Promise.all(TIENDAS.map(async (t) => (await tnBuscar(t, ref)).map((o) => pedidoDesdeTN(t, o))));
    pedidos = porTienda.flat();
    if (!pedidos.length) {
      motivo = c.tipo === "seguimiento"
        ? "Tiendanube no tiene ese código de envío cargado. Escribí el número de venta."
        : `No encontré el pedido ${ref}.`;
    }
  }

  for (const p of pedidos) p.avisos.push(...(await controlesPrevios(p)));
  return json({ pedidos, motivo });
}

async function postControl(req: Request) {
  const b = await req.json();
  const items: Json[] = Array.isArray(b.items) ? b.items : [];
  if (!["tn", "ml"].includes(b.canal) || !TIENDAS.includes(b.tienda) || !b.pedido || !items.length) {
    return json({ error: "Faltan datos del control" }, 400);
  }
  if (items.some((i) => !["ok", "no_hay"].includes(i.estado))) {
    return json({ error: "Hay productos sin confirmar" }, 400);
  }
  const resultado = items.some((i) => i.estado === "no_hay") ? "faltante" : "completo";
  const { data, error } = await supabase.from("armado_controles").insert({
    canal: b.canal,
    tienda: b.tienda,
    pedido: String(b.pedido),
    envio: b.envio ?? null,
    codigo_leido: b.codigo_leido ? String(b.codigo_leido).slice(0, 500) : null,
    armado_por: b.armado_por ? String(b.armado_por).slice(0, 80) : null,
    resultado,
    unidades: items.reduce((s, i) => s + (Number(i.cantidad) || 0), 0),
    inicio_at: b.inicio_at ?? null,
    fin_at: new Date().toISOString(),
  }).select("id").single();
  if (error) throw error;
  const { error: e2 } = await supabase.from("armado_items").insert(items.map((i) => ({
    control_id: data.id,
    sku: i.sku ?? null,
    nombre: i.nombre ?? null,
    variante: Array.isArray(i.variante) ? i.variante.join(" · ") : (i.variante ?? null),
    cantidad: Number(i.cantidad) || 1,
    estado: i.estado,
    confirmado_at: i.confirmado_at ?? null,
  })));
  if (e2) throw e2;
  return json({ id: data.id, resultado });
}

/** Pedidos de los últimos 14 días cuyo último control quedó con faltante. */
async function getFaltantes() {
  const desde = new Date(Date.now() - 14 * 86400_000).toISOString();
  const { data, error } = await supabase
    .from("armado_controles")
    .select("id, creado_at, canal, tienda, pedido, armado_por, resultado, armado_items(sku, nombre, variante, cantidad, estado)")
    .gte("creado_at", desde)
    .order("creado_at", { ascending: false });
  if (error) throw error;
  const vistos = new Set<string>();
  const faltantes = [];
  for (const c of data ?? []) {
    const clave = `${c.tienda}|${c.canal}|${c.pedido}`;
    if (vistos.has(clave)) continue; // solo cuenta el control más reciente de cada pedido
    vistos.add(clave);
    if (c.resultado !== "faltante") continue;
    faltantes.push({
      ...c,
      armado_items: (c.armado_items ?? []).filter((i: Json) => i.estado === "no_hay"),
    });
  }
  return json({ faltantes });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS_HEADERS });
  if (!checkPassword(req)) return json({ error: "Contraseña inválida" }, 401);

  const url = new URL(req.url);
  const path = url.pathname.replace(/^\/armado-api/, "") || "/";

  try {
    if (req.method === "POST" && path === "/buscar") return await postBuscar(req);
    if (req.method === "POST" && path === "/controles") return await postControl(req);
    if (req.method === "GET" && path === "/faltantes") return await getFaltantes();
    return json({ error: "Ruta no encontrada" }, 404);
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
