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
// Lo que nadie encuentra (Búsqueda o Empaquetado) pasa a la búsqueda profunda (tabla busqueda_profunda,
// rutas /profunda): una persona lo busca a fondo y lo cierra como "apareció" o "perdido" (Stock perdido,
// que se ve en el panel de control, no en la app del depósito).
//
// Misma contraseña compartida que reposicion-api (header X-App-Password, secret
// APP_SHARED_PASSWORD). Credenciales de Tiendanube y Mercado Libre en el secret
// ARMADO_CREDENCIALES (JSON):
//   {"tn": {"pret": {"store_id", "access_token"}, "lavan": {…}},
//    "ml": {"pret": {"user_id", "client_id", "client_secret", "refresh_token", "access_token"?}, "lavan": {…}}}

import { createClient } from "jsr:@supabase/supabase-js@2";
import {
  detectarPaquete, esParaManana, fechaHoraAR, grupoDeEnvioTN, MENSAJERIAS, MIEMBROS, NOMBRE_MENSAJERIA, NOMBRE_POOL, poolDe,
  ZETA_TOKEN_SUFIJO, type Mensajeria, type Paquete, type Pool, type TipoPaquete,
} from "./despacho.ts";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-App-Password",
};

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

// La gente del depósito entra con su propia contraseña (secret ARMADO_PASSWORD) y su nombre;
// la del tablero (APP_SHARED_PASSWORD) también sirve, para el dueño.
const APP_PASSWORD = Deno.env.get("APP_SHARED_PASSWORD") ?? "";
const DEPOSITO_PASSWORD = Deno.env.get("ARMADO_PASSWORD") ?? "";

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

// El sufijo fijo que Zeta agrega al "did" del QR para armar el token de seguimiento (ZETA_TOKEN_SUFIJO,
// ej. 1571347d54df4s8a513) vive en despacho.ts, que también interpreta los QR del modo Despacho.
const ZETA_TRACKING_URL = "https://zetaenvios.lightdata.app/tracking.php?token=";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

function checkPassword(req: Request): boolean {
  if (!APP_PASSWORD && !DEPOSITO_PASSWORD) return true;
  const p = req.headers.get("x-app-password");
  return !!p && (p === APP_PASSWORD || p === DEPOSITO_PASSWORD);
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
  | { tipo: "ml_venta"; venta: string }
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
      // Colecta: {"id": envío, "t": "lm"}, sin la cuenta → se prueban las dos
      if (o.id && /^\d{10,}$/.test(String(o.id))) return { tipo: "ml_envio", envio: String(o.id) };
      if (o.did) return { tipo: "zeta", token: `${o.did}${ZETA_TOKEN_SUFIJO}` };
      if (o.internalID) return { tipo: "seguimiento", ref: String(o.internalID) };
    } catch { /* no era JSON: sigue abajo */ }
  }
  const tokenZeta = txt.match(/lightdata\.app\/tracking\.php\?token=([\w]+)/);
  if (tokenZeta) return { tipo: "zeta", token: tokenZeta[1] };
  if (txt.includes("|")) return { tipo: "seguimiento", ref: txt.split("|")[0].trim() };

  const limpio = txt.replace(/^#/, "").replace(/\s+/g, "");
  if (/^\d+$/.test(limpio)) {
    // Ventas de ML (orden o carrito): 16 dígitos que empiezan con 2000, impresas como "Venta:" en la etiqueta.
    // Envíos de ML: 11 dígitos (el número bajo el código de barras). Números de venta de TN: hasta 6.
    if (limpio.length >= 15 && limpio.startsWith("2000")) return { tipo: "ml_venta", venta: limpio };
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

/**
 * Marca el pedido como empaquetado en Tiendanube (queda "por enviar"), como si se hiciera a mano en el admin
 * (5/11/2026, pedido del usuario). Pasa a PACKED cada orden de despacho que esté UNPACKED / IN_PREPARATION; las
 * que ya están empaquetadas, despachadas o entregadas no se tocan. Nunca tira: el control ya quedó guardado.
 */
async function marcarEmpaquetadoTN(tienda: Tienda, numero: string): Promise<{ estado: "marcado" | "ya_estaba" | "error"; retiro?: boolean; texto?: string }> {
  const c = CRED.tn?.[tienda];
  try {
    if (!c) throw new Error("sin credenciales");
    const o = (await tnBuscar(tienda, numero)).find((x) => String(x.number) === numero);
    if (!o) throw new Error("no encontré el pedido");
    const pendientes = (o.fulfillments ?? []).filter((f: Json) => ["UNPACKED", "IN_PREPARATION"].includes(f?.status));
    if (!pendientes.length) return { estado: "ya_estaba" };
    let retiro = false;
    for (const f of pendientes) {
      // "Retira en el local" (pickup sin envío): Tiendanube no acepta PACKED; pasa directo a "listo para retirar",
      // igual que cuando se marca a mano en el admin (5/11/2026, pedido del usuario).
      const sinEnvio = f?.shipping?.extras?.shippable === false;
      if (sinEnvio) retiro = true;
      const r = await fetch(`https://api.tiendanube.com/v1/${c.store_id}/orders/${o.id}/fulfillment-orders/${f.id}`, {
        method: "PATCH",
        headers: { Authentication: `bearer ${c.access_token}`, "User-Agent": "ArmadoPedidos (info@pretahome.com)", "Content-Type": "application/json" },
        body: JSON.stringify({ status: sinEnvio ? "READY_FOR_PICKUP" : "PACKED" }),
      });
      if (!r.ok) throw new Error(`Tiendanube respondió ${r.status}: ${(await r.text()).slice(0, 200)}`);
    }
    return { estado: "marcado", retiro };
  } catch (err) {
    console.error("marcar empaquetado TN", tienda, numero, err);
    return { estado: "error", texto: String((err as Error)?.message ?? err) };
  }
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
    // Se muestra el número de venta que imprime la etiqueta: el del carrito (pack) si lo hay, si no el de la orden.
    const ordenIds = [...new Set(lineas.map((l) => String(l.order_id)).filter(Boolean))];
    const ordenes = [...new Set(await Promise.all(ordenIds.map(async (id) => {
      const o = await mlGet(tienda, `/orders/${id}`);
      return String(o.body?.pack_id ?? id);
    })))];
    return {
      canal: "ml",
      tienda,
      pedido: ordenes.join(", ") || envio,
      envio,
      envio_tipo: `Mercado Libre ${LOGISTICA_ML[envioRes.body?.logistic_type] ?? ""}`.trim(),
      avisos,
      items,
    };
  }
  return null;
}

/** Número de venta de ML (orden suelta o carrito) → su envío. */
async function mlEnvioDeVenta(venta: string): Promise<string | null> {
  for (const tienda of TIENDAS.filter((t) => CRED.ml?.[t])) {
    const orden = await mlGet(tienda, `/orders/${venta}`);
    if (orden.status === 200 && orden.body?.shipping?.id) return String(orden.body.shipping.id);
    const pack = await mlGet(tienda, `/packs/${venta}`);
    if (pack.status === 200 && pack.body?.shipment?.id) return String(pack.body.shipment.id);
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

  if (c.tipo === "ml_envio" || c.tipo === "ml_venta") {
    const envio = c.tipo === "ml_envio" ? c.envio : await mlEnvioDeVenta(c.venta);
    const p = envio ? await mlPedidoPorEnvio(envio, c.tipo === "ml_envio" ? c.sender : undefined) : null;
    if (p) pedidos = [p];
    else motivo = c.tipo === "ml_venta" ? "Mercado Libre no encontró esa venta." : "Mercado Libre no encontró ese envío.";
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

  // Lo que no hay va a la búsqueda profunda; si este pedido ya había avisado un faltante y ahora está, se retira.
  const pedidoRef = { canal: b.canal, tienda: b.tienda, pedido: String(b.pedido) };
  for (const i of items) {
    if (!i.sku) continue;
    const clave = `e|${b.tienda}|${b.canal}|${b.pedido}|${i.sku}`;
    try {
      if (i.estado === "no_hay") {
        const cantidad = Number(i.cantidad) || 1;
        await reportarProfunda(String(i.sku), datosProducto(i), {
          clave, origen: "empaquetado", faltan: cantidad, pedidos: [{ ...pedidoRef, cantidad }],
          por: b.armado_por ? String(b.armado_por).slice(0, 80) : null, at: new Date().toISOString(),
        });
      } else {
        await retirarReporte(String(i.sku), clave);
      }
    } catch (err) {
      console.error("búsqueda profunda", err); // el control ya quedó guardado
    }
  }
  // Pedido de Tiendanube completo → empaquetado en la tienda. Con faltante no se marca.
  const tiendanube = b.canal === "tn" && resultado === "completo"
    ? await marcarEmpaquetadoTN(b.tienda, String(b.pedido))
    : null;
  return json({ id: data.id, resultado, tiendanube });
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

// ─── Búsqueda de productos ──────────────────────────────────────────────────
// Lo pendiente de despachar desde el depósito, por línea de pedido:
//   - Tiendanube: órdenes abiertas, pagas y sin empaquetar.
//   - Mercado Libre: envíos listos para despachar que no son Full.
// Lo que marca cada persona va a busqueda_marcas (una fila por SKU por día; vale hasta que se empaqueta, ver marcasVigentes).

type Linea = {
  canal: "tn" | "ml";
  tienda: Tienda;
  pedido: string;
  logistica: string | null;
  sku: string;
  nombre: string;
  variante: string[];
  cantidad: number;
  imagen: string | null;
  categoria: string | null;
  marca: string | null;
  fecha: string | null;    // creación de la orden (para repartir lo encontrado del más viejo al más nuevo)
  cliente: string | null;  // ML: usuario del comprador; TN: nombre
};

const LOGISTICA_ML: Record<string, string> = {
  self_service: "Flex",
  cross_docking: "Colecta",
  xd_drop_off: "Drop off",
  drop_off: "Drop off",
};

function hoyAR(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Argentina/Buenos_Aires" });
}

// Lo encontrado en Búsqueda sigue valiendo hasta que se empaqueta, aunque cambie el día (6/11/2026: Jair buscó
// a la tarde, no se llegó a empaquetar todo y a la mañana la lista arrancó de cero). Se miran las marcas de los
// últimos días (la más nueva de cada SKU gana) y se descuenta lo que se llevaron los paquetes cerrados después.
// Antes del 5/11 casi no se empaquetaba con la app (el 4/11, 20 paquetes): sus marcas no se pueden descontar.
const DIAS_MARCAS = 4, MARCAS_DESDE = "2026-11-05";
function desdeMarcasAR(): string {
  const d = new Date(Date.now() - (DIAS_MARCAS - 1) * 86400_000).toLocaleDateString("en-CA", { timeZone: "America/Argentina/Buenos_Aires" });
  return d > MARCAS_DESDE ? d : MARCAS_DESDE;
}

/** Paquetes cerrados completos desde el inicio de la ventana de marcas. */
async function controlesCompletos(): Promise<Json[]> {
  const { data, error } = await supabase.from("armado_controles")
    .select("canal, tienda, pedido, creado_at, armado_items(sku, cantidad, estado)")
    .eq("resultado", "completo").gte("creado_at", `${desdeMarcasAR()}T00:00:00-03:00`).order("id");
  if (error) throw error;
  return data ?? [];
}

/** Marca vigente por SKU: la más nueva de la ventana, menos lo que ya se empaquetó después de marcarla. */
async function marcasVigentes(controles: Json[]): Promise<Json[]> {
  const { data, error } = await supabase.from("busqueda_marcas")
    .select("fecha, sku, estado, requeridas, encontradas, por, marcado_at")
    .gte("fecha", desdeMarcasAR()).order("marcado_at");
  if (error) throw error;
  const ultima = new Map<string, Json>();
  for (const m of data ?? []) ultima.set(m.sku, m);
  const out: Json[] = [];
  for (const m of ultima.values()) {
    const marcadoMs = Date.parse(m.marcado_at);
    let usadas = 0;
    for (const c of controles) {
      if (Date.parse(c.creado_at) <= marcadoMs) continue;
      for (const i of c.armado_items ?? []) if (i.estado === "ok" && i.sku === m.sku) usadas += Number(i.cantidad) || 0;
    }
    const requeridas = Math.max(0, m.requeridas - usadas);
    if (!requeridas) continue; // ya se empaquetó todo lo que esperaba esta marca
    out.push({ ...m, requeridas, encontradas: Math.max(0, m.encontradas - usadas) });
  }
  return out;
}

async function enParalelo<T, R>(items: T[], n: number, fn: (x: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (i < items.length) {
      const k = i++;
      out[k] = await fn(items[k]);
    }
  }));
  return out;
}

async function pendientesTN(tienda: Tienda): Promise<Linea[]> {
  if (!CRED.tn?.[tienda]) return [];
  const desde = new Date(Date.now() - 45 * 86400_000).toISOString();
  const ordenes: Json[] = [];
  for (let page = 1; page < 20; page++) {
    const r: Json[] = await tnGet(tienda, "/orders", {
      status: "open", payment_status: "paid", shipping_status: "unpacked",
      created_at_min: desde, per_page: "200", page: String(page),
    });
    ordenes.push(...r);
    if (r.length < 200) break;
  }
  return ordenes.flatMap((o) => (o.products ?? []).map((p: Json) => ({
    canal: "tn" as const,
    tienda,
    pedido: String(o.number),
    logistica: o.shipping_option ?? null,
    sku: String(p.sku ?? ""),
    nombre: String(p.name_without_variants || p.name || ""),
    variante: (p.variant_values ?? []).map(String),
    cantidad: Number(p.quantity) || 1,
    imagen: p.image?.src ?? null,
    categoria: null,
    marca: null,
    fecha: o.created_at ?? null,
    cliente: o.customer?.name || o.contact_name || null,
  })));
}

async function pendientesML(tienda: Tienda): Promise<Linea[]> {
  const c = CRED.ml?.[tienda];
  if (!c) return [];
  const ordenes: Json[] = [];
  for (let offset = 0; offset < 1000; offset += 50) {
    const r = await mlGet(tienda,
      `/orders/search?seller=${c.user_id}&order.status=paid&shipping.status=ready_to_ship&sort=date_desc&limit=50&offset=${offset}`);
    const res: Json[] = r.body?.results ?? [];
    ordenes.push(...res);
    if (res.length < 50) break;
  }
  // El filtro de logística no existe en la búsqueda de órdenes: se mira cada envío para sacar los de Full.
  const envioIds = [...new Set(ordenes.map((o) => o.shipping?.id).filter(Boolean).map(String))];
  const envios = new Map<string, Json>();
  await enParalelo(envioIds, 12, async (id) => {
    const r = await mlGet(tienda, `/shipments/${id}`);
    if (r.body) envios.set(id, r.body);
  });

  const lineas: (Linea & { _item: string; _var: string | null })[] = [];
  for (const o of ordenes) {
    const e = envios.get(String(o.shipping?.id));
    if (!e || e.status !== "ready_to_ship" || e.logistic_type === "fulfillment") continue;
    for (const it of o.order_items ?? []) {
      lineas.push({
        canal: "ml",
        tienda,
        pedido: String(o.pack_id ?? o.id),
        logistica: LOGISTICA_ML[e.logistic_type] ?? "Mercado Libre",
        sku: String(it.item?.seller_sku ?? it.item?.seller_custom_field ?? ""),
        nombre: String(it.item?.title ?? ""),
        variante: (it.item?.variation_attributes ?? []).map((a: Json) => String(a.value_name)),
        cantidad: Number(it.quantity) || 1,
        imagen: null,
        categoria: null,
        marca: null,
        fecha: o.date_created ?? null,
        cliente: o.buyer?.nickname ?? null,
        _item: String(it.item?.id),
        _var: it.item?.variation_id ? String(it.item.variation_id) : null,
      });
    }
  }

  // Fotos: de la variación si tiene, si no la primera de la publicación
  const ids = [...new Set(lineas.map((l) => l._item))];
  const pubs = new Map<string, Json>();
  for (let i = 0; i < ids.length; i += 20) {
    const r = await mlGet(tienda, `/items?ids=${ids.slice(i, i + 20).join(",")}&attributes=id,pictures,variations`);
    for (const x of r.body ?? []) if (x.code === 200) pubs.set(String(x.body.id), x.body);
  }
  return lineas.map(({ _item, _var, ...l }) => {
    const pub = pubs.get(_item);
    const v = (pub?.variations ?? []).find((x: Json) => String(x.id) === _var);
    const foto = (pub?.pictures ?? []).find((p: Json) => p.id === v?.picture_ids?.[0]) ?? pub?.pictures?.[0];
    return { ...l, imagen: foto?.secure_url ?? null };
  });
}

let cachePendientes: { at: number; lineas: Linea[] } | null = null;

async function getPendientes(url: URL) {
  const refrescar = url.searchParams.get("refrescar") === "1";
  // Varias personas abren la búsqueda a la vez: se reusa lo calculado en el último minuto.
  if (!cachePendientes || refrescar || Date.now() - cachePendientes.at > 60_000) {
    const partes = await Promise.all([
      ...TIENDAS.map((t) => pendientesTN(t)),
      ...TIENDAS.map((t) => pendientesML(t)),
    ]);
    const lineas = partes.flat();

    // Tipo de producto y marca (proveedor) del tablero de reposición
    const skus = [...new Set(lineas.map((l) => l.sku).filter(Boolean))];
    const info = new Map<string, Json>();
    for (let i = 0; i < skus.length; i += 200) {
      const { data, error } = await supabase.from("repo_productos")
        .select("sku, nombre, categoria_auto, categoria_manual, proveedor_auto, proveedor_manual")
        .in("sku", skus.slice(i, i + 200));
      if (error) throw error;
      for (const r of data ?? []) info.set(r.sku, r);
    }
    for (const l of lineas) {
      const r = info.get(l.sku);
      l.categoria = r?.categoria_manual || r?.categoria_auto || null;
      l.marca = r?.proveedor_manual || r?.proveedor_auto || null;
      if (r?.nombre && l.canal === "ml") l.nombre = r.nombre; // los títulos de ML son largos
    }
    cachePendientes = { at: Date.now(), lineas };
  }
  // Los de ML siguen "listos para despachar" hasta que salen: lo ya empaquetado completo no se vuelve a buscar.
  const hechos = new Set((await controlesCompletos()).map((c) => `${c.tienda}|${c.canal}|${c.pedido}`));
  const lineas = cachePendientes.lineas.filter((l) => !hechos.has(`${l.tienda}|${l.canal}|${l.pedido}`));
  return json({ actualizado_at: new Date(cachePendientes.at).toISOString(), fecha: hoyAR(), lineas });
}

/** Marcas del día y quién está buscando en qué. Si viene `por`, registra dónde está esa persona. */
async function getEstado(url: URL) {
  const por = url.searchParams.get("por");
  if (por) {
    await supabase.from("busqueda_presencia").upsert({
      por: por.slice(0, 80),
      categoria: url.searchParams.get("categoria") || null,
      visto_at: new Date().toISOString(),
    });
  }
  const [controles, presencia] = await Promise.all([
    controlesCompletos(),
    supabase.from("busqueda_presencia").select("por, categoria")
      .gte("visto_at", new Date(Date.now() - 120_000).toISOString()).not("categoria", "is", null),
  ]);
  const marcas = await marcasVigentes(controles);
  const inicioHoy = Date.parse(`${hoyAR()}T00:00:00-03:00`);
  return json({
    marcas, presencia: presencia.data ?? [],
    // Paquetes cerrados completos hoy (para el avance del día en el inicio de la app).
    empaquetados: controles.filter((c) => Date.parse(c.creado_at) >= inicioHoy).map((c) => ({
      canal: c.canal, tienda: c.tienda, pedido: c.pedido, at: c.creado_at,
      items: (c.armado_items ?? []).filter((i: Json) => i.estado === "ok" && i.sku).map((i: Json) => ({ sku: i.sku, cantidad: i.cantidad })),
    })),
  });
}

async function postMarcar(req: Request) {
  const b = await req.json();
  const sku = String(b.sku ?? "");
  if (!sku) return json({ error: "Falta el SKU" }, 400);
  // Marcas de días anteriores de este SKU (siguen vigentes hasta que se empaqueta): desmarcar las quita, y una
  // marca nueva las reemplaza, así que sus avisos a la búsqueda profunda se retiran.
  const { data: viejas } = await supabase.from("busqueda_marcas").select("fecha")
    .eq("sku", sku).gte("fecha", desdeMarcasAR()).neq("fecha", hoyAR());
  const retirarViejas = async () => {
    for (const v of viejas ?? []) await retirarReporte(sku, `b|${v.fecha}|${sku}`).catch((e) => console.error("búsqueda profunda", e));
  };
  if (b.estado === "borrar") {
    const { error } = await supabase.from("busqueda_marcas").delete().eq("sku", sku).gte("fecha", desdeMarcasAR());
    if (error) throw error;
    await retirarReporte(sku, `b|${hoyAR()}|${sku}`);
    await retirarViejas();
    return json({ ok: true });
  }
  if (!["ok", "parcial", "no_hay"].includes(b.estado)) return json({ error: "Estado inválido" }, 400);
  const requeridas = Math.max(0, Number(b.requeridas) || 0);
  const encontradas = Math.max(0, Math.min(requeridas, Number(b.encontradas) || 0));
  const { error } = await supabase.from("busqueda_marcas").upsert({
    fecha: hoyAR(), sku, estado: b.estado, requeridas, encontradas,
    por: b.por ? String(b.por).slice(0, 80) : null,
    marcado_at: new Date().toISOString(),
  });
  if (error) throw error;
  await retirarViejas();
  // Lo que falta va a la búsqueda profunda (si falla, la marca ya quedó guardada)
  const faltan = requeridas - encontradas;
  try {
    if (faltan > 0) {
      await reportarProfunda(sku, datosProducto(b), {
        clave: `b|${hoyAR()}|${sku}`, origen: "busqueda", faltan, pedidos: pedidosDe(b.pedidos),
        por: b.por ? String(b.por).slice(0, 80) : null, at: new Date().toISOString(),
      });
    } else {
      await retirarReporte(sku, `b|${hoyAR()}|${sku}`);
    }
  } catch (err) {
    console.error("búsqueda profunda", err);
  }
  return json({ ok: true });
}

// ─── Búsqueda profunda ──────────────────────────────────────────────────────
// Lo que nadie encontró abre un caso por SKU (busqueda_profunda). Cada aviso queda en `reportes` con una clave
// por origen: volver a marcar en Búsqueda reemplaza el aviso del día, y un pedido que después se empaqueta
// completo retira el suyo. Así lo mismo reportado dos veces (en Búsqueda y al empaquetar) no se cuenta doble.

type PedidoRef = { canal: string; tienda: string; pedido: string; cantidad: number; cliente?: string | null };
type Reporte = { clave: string; origen: "busqueda" | "empaquetado"; faltan: number; pedidos: PedidoRef[]; por: string | null; at: string };

function pedidosDe(x: unknown): PedidoRef[] {
  if (!Array.isArray(x)) return [];
  return x.slice(0, 100).map((p: Json) => ({
    canal: String(p.canal ?? ""), tienda: String(p.tienda ?? ""), pedido: String(p.pedido ?? ""),
    cantidad: Math.max(1, Number(p.cantidad) || 1),
  })).filter((p) => p.pedido);
}

function datosProducto(b: Json) {
  return {
    nombre: b.nombre ? String(b.nombre).slice(0, 300) : null,
    variante: Array.isArray(b.variante) ? b.variante.join(" · ") : (b.variante ? String(b.variante) : null),
    imagen: b.imagen ? String(b.imagen).slice(0, 1000) : null,
  };
}

const clavePedido = (p: PedidoRef) => `${p.canal}|${p.tienda}|${p.pedido}`;

/** Unidades que faltan según los avisos: Búsqueda ya cuenta todos los pedidos del SKU; un faltante al
 *  empaquetar suma solo si su pedido no estaba en lo que contó Búsqueda. */
function sumarReportes(reportes: Reporte[]) {
  const deBusqueda = reportes.filter((r) => r.origen === "busqueda");
  const enBusqueda = new Set(deBusqueda.flatMap((r) => r.pedidos.map(clavePedido)));
  const reportadas = Math.max(0, ...deBusqueda.map((r) => r.faltan)) +
    reportes.filter((r) => r.origen === "empaquetado" && !r.pedidos.some((p) => enBusqueda.has(clavePedido(p))))
      .reduce((s, r) => s + r.faltan, 0);
  const pedidos = new Map<string, PedidoRef>();
  for (const p of reportes.flatMap((r) => r.pedidos)) {
    const k = clavePedido(p);
    const ya = pedidos.get(k);
    if (!ya || p.cantidad > ya.cantidad) pedidos.set(k, p);
  }
  return { reportadas, pedidos: [...pedidos.values()] };
}

async function casoPendiente(sku: string) {
  const { data, error } = await supabase.from("busqueda_profunda").select("*")
    .eq("sku", sku).eq("estado", "pendiente").maybeSingle();
  if (error) throw error;
  return data;
}

async function guardarReportes(caso: Json, reportes: Reporte[], extra: Json = {}) {
  const { reportadas, pedidos } = sumarReportes(reportes);
  const nombres = new Map((caso.pedidos as PedidoRef[]).filter((p) => "cliente" in p).map((p) => [clavePedido(p), p.cliente]));
  for (const p of pedidos) if (nombres.has(clavePedido(p))) p.cliente = nombres.get(clavePedido(p));
  // Lo que ya apareció o ya se dio por perdido (no está / se pidió al proveedor) no se vuelve a buscar
  const unidades = Math.max(0, reportadas - caso.encontradas - caso.perdidas);
  const { error } = await supabase.from("busqueda_profunda").update({
    ...extra, reportes, reportadas, unidades, pedidos,
    estado: unidades > 0 ? "pendiente" : caso.encontradas > 0 || !caso.perdidas ? "aparecio" : "perdido",
    ...(unidades > 0 ? {} : { cerrado_at: new Date().toISOString(), cerrado_por: caso.cerrado_por ?? caso.tomado_por }),
    actualizado_at: new Date().toISOString(),
  }).eq("id", caso.id);
  if (error) throw error;
}

async function reportarProfunda(sku: string, producto: Json, rep: Reporte) {
  for (let intento = 0; intento < 2; intento++) {
    const caso = await casoPendiente(sku);
    if (caso) {
      const reportes = [...(caso.reportes as Reporte[]).filter((r) => r.clave !== rep.clave), rep];
      return guardarReportes(caso, reportes, {
        nombre: caso.nombre ?? producto.nombre, variante: caso.variante ?? producto.variante, imagen: caso.imagen ?? producto.imagen,
      });
    }
    const { reportadas, pedidos } = sumarReportes([rep]);
    const { error } = await supabase.from("busqueda_profunda").insert({
      sku, ...producto, reportes: [rep], reportadas, unidades: reportadas, pedidos,
    });
    if (!error) return;
    if (error.code !== "23505") throw error; // otro celular abrió el caso al mismo tiempo: se suma a ese
  }
}

/** Ya no falta (se volvió a marcar, se desmarcó, o el pedido se empaquetó completo): se saca ese aviso. */
async function retirarReporte(sku: string, clave: string) {
  const caso = await casoPendiente(sku);
  if (!caso || !(caso.reportes as Reporte[]).some((r) => r.clave === clave)) return;
  const reportes = (caso.reportes as Reporte[]).filter((r) => r.clave !== clave);
  if (!reportes.length && !caso.encontradas && !caso.perdidas && !caso.tomado_por) {
    const { error } = await supabase.from("busqueda_profunda").delete().eq("id", caso.id);
    if (error) throw error;
    return;
  }
  await guardarReportes(caso, reportes);
}

/** Variante de Tiendanube por SKU (stock y costo). El depósito es el stock de Pret; si no está, el de Laván. */
async function varianteTN(sku: string): Promise<{ stock: number | null; costo: number | null } | null> {
  for (const t of TIENDAS) {
    if (!CRED.tn?.[t]) continue;
    const prod: Json = await tnGet(t, `/products/sku/${encodeURIComponent(sku)}`, {});
    const v = (prod?.variants ?? []).find((x: Json) => x.sku === sku);
    if (v) {
      return {
        stock: v.stock === null || v.stock === undefined ? null : Number(v.stock),
        costo: v.cost ? Number(v.cost) : null,
      };
    }
  }
  return null;
}

let cacheStockTN: { at: number; stock: Map<string, number | null> } = { at: 0, stock: new Map() };

/** Quién compró, para buscar el paquete por la etiqueta: en ML el usuario (apodo) del comprador; Tiendanube no
 *  tiene usuario, así que ahí va el nombre. */
async function nombreCliente(p: PedidoRef): Promise<string | null> {
  const tienda = p.tienda as Tienda;
  const num = p.pedido.split(",")[0].trim();
  if (p.canal === "tn") {
    const o = (await tnBuscar(tienda, num))[0];
    return o ? (o.customer?.name || o.contact_name || o.shipping_address?.name || null) : null;
  }
  // ML: el número es el del carrito (pack) o el de la orden
  const pack = await mlGet(tienda, `/packs/${num}`);
  const ordenId = pack.status === 200 ? pack.body?.orders?.[0]?.id : num;
  if (!ordenId) return null;
  const b = (await mlGet(tienda, `/orders/${ordenId}`)).body?.buyer ?? {};
  return b.nickname || [b.first_name, b.last_name].filter(Boolean).join(" ") || null;
}

async function getProfunda() {
  const { data, error } = await supabase.from("busqueda_profunda").select("*")
    .eq("estado", "pendiente").order("creado_at");
  if (error) throw error;
  // Quién compró cada pedido: se averigua una vez y queda guardado en el caso
  const sinNombre = (data ?? []).filter((c) => (c.pedidos as PedidoRef[]).some((p) => !("cliente" in p)));
  await enParalelo(sinNombre, 3, async (c) => {
    const pedidos = await Promise.all((c.pedidos as PedidoRef[]).map(async (p) => {
      if ("cliente" in p) return p;
      try { return { ...p, cliente: await nombreCliente(p) }; } catch (err) { console.error(err); return p; }
    }));
    c.pedidos = pedidos;
    await supabase.from("busqueda_profunda").update({ pedidos }).eq("id", c.id).eq("estado", "pendiente");
  });
  // Cuánto cree Tiendanube que hay: si dice que hay y nadie lo encuentra, el stock está mal cargado.
  if (Date.now() - cacheStockTN.at > 60_000) cacheStockTN = { at: Date.now(), stock: new Map() };
  const falta = (data ?? []).map((c) => c.sku).filter((s) => !cacheStockTN.stock.has(s));
  await enParalelo(falta, 6, async (sku) => {
    try { cacheStockTN.stock.set(sku, (await varianteTN(sku))?.stock ?? null); } catch { /* sin dato */ }
  });
  return json({ casos: (data ?? []).map((c) => ({ ...c, stock_tn: cacheStockTN.stock.get(c.sku) ?? null })) });
}

async function postProfundaTomar(req: Request) {
  const b = await req.json();
  const por = b.por ? String(b.por).slice(0, 80) : null;
  const { error } = await supabase.from("busqueda_profunda")
    .update({ tomado_por: b.soltar ? null : por, tomado_at: b.soltar ? null : new Date().toISOString() })
    .eq("id", Number(b.id)).eq("estado", "pendiente");
  if (error) throw error;
  return json({ ok: true });
}

async function postProfundaCerrar(req: Request) {
  const b = await req.json();
  const { data: caso, error } = await supabase.from("busqueda_profunda").select("*").eq("id", Number(b.id)).maybeSingle();
  if (error) throw error;
  if (!caso || caso.estado !== "pendiente") return json({ error: "Este producto ya no está en la búsqueda profunda." }, 409);
  const por = b.por ? String(b.por).slice(0, 80) : null;
  const ahora = new Date().toISOString();

  // Lo que no apareció en el depósito (no está, o se pidió al proveedor) es Stock perdido: se valúa al
  // costo de Tiendanube de hoy y se guarda el stock que la tienda creía tener.
  const sumarPerdidas = async (n: number) => {
    let tn = null;
    try { tn = await varianteTN(caso.sku); } catch (err) { console.error(err); }
    const costo = tn?.costo ?? (caso.costo_unitario === null ? null : Number(caso.costo_unitario));
    const perdidas = caso.perdidas + n;
    return {
      perdidas, perdido_at: ahora, stock_tn: tn?.stock ?? caso.stock_tn,
      costo_unitario: costo, costo_total: costo === null ? null : costo * perdidas,
    };
  };
  const cerrar = (quedan: number, encontradas: number) => ({
    unidades: quedan,
    estado: quedan > 0 ? "pendiente" : encontradas > 0 ? "aparecio" : "perdido",
    ...(quedan > 0 ? {} : { cerrado_por: por, cerrado_at: ahora }),
    actualizado_at: ahora,
  });

  let cambios: Json;
  if (b.resultado === "aparecio" || b.resultado === "proveedor") {
    const n = Math.max(1, Math.min(caso.unidades, Number(b.encontradas) || 0));
    const quedan = caso.unidades - n;
    if (b.resultado === "proveedor") {
      // El pedido se puede despachar, pero esas unidades no aparecieron en el depósito
      cambios = { ...(await sumarPerdidas(n)), al_proveedor: caso.al_proveedor + n, ...cerrar(quedan, caso.encontradas) };
    } else {
      cambios = {
        encontradas: caso.encontradas + n, donde: b.donde ? String(b.donde).slice(0, 200) : caso.donde,
        ...cerrar(quedan, caso.encontradas + n),
      };
    }
  } else if (b.resultado === "perdido") {
    cambios = { ...(await sumarPerdidas(caso.unidades)), ...cerrar(0, caso.encontradas) };
  } else {
    return json({ error: "Resultado inválido" }, 400);
  }
  const { error: e } = await supabase.from("busqueda_profunda").update(cambios).eq("id", caso.id).eq("estado", "pendiente");
  if (e) throw e;
  return json({ ok: true, quedan: cambios.unidades });
}

// ─── Despacho ───────────────────────────────────────────────────────────────
// Cuando pasa una mensajería a retirar, se elige quién es y se escanea el QR de cada paquete (una fila por paquete
// en armado_despachos, con la mensajería a la que se le entregó: zeta, ninja, correo o andreani; queda registrado
// para poder reclamar un paquete perdido). Zeta y Ninja se eligen por separado, pero lo que hay que entregar de
// Flex y Tiendanube es UN solo total entre los dos (el "pool" zeta_ninja).
// El contador de lo que falta sale de lo pendiente en Tiendanube (por empaquetar o por enviar, de hoy o de antes) y
// en Mercado Libre (Flex listo para despachar, hasta las 14 hs) menos lo ya entregado.
//   - Cada escaneo se guarda al instante; el pedido al que corresponde se averigua en segundo plano.
//   - Un QR de otra mensajería (otro pool) no se cuenta (estado "otra"). Entre Zeta y Ninja no hay alarma: el
//     equipo decide a quién le da cada paquete, y se registra el que se eligió.

type PendienteDesp = {
  clave: string;             // 'tn|tienda|numero' o 'ml|envio': lo mismo que guarda pedido_clave al entregar
  grupo: Pool;
  tipo: "flex_ml" | "tn";
  canal: "tn" | "ml";
  tienda: Tienda;
  pedido: string;
  etiqueta: string;          // opción de envío, para mostrar
  fecha: string;
  cliente: string | null;
  para_manana: boolean;      // Flex creado hoy después de las 14 hs: sale mañana
};

/** Pedidos de Tiendanube por empaquetar (unpacked) o empaquetados sin enviar (unfulfilled). Sin "Retira en el local". */
async function pendientesDespachoTN(tienda: Tienda): Promise<PendienteDesp[]> {
  if (!CRED.tn?.[tienda]) return [];
  const desde = new Date(Date.now() - 45 * 86400_000).toISOString();
  const ordenes = new Map<string, Json>();
  for (const estado of ["unpacked", "unfulfilled"]) {
    for (let page = 1; page < 20; page++) {
      const r: Json[] = await tnGet(tienda, "/orders", {
        status: "open", payment_status: "paid", shipping_status: estado,
        created_at_min: desde, per_page: "200", page: String(page),
      });
      for (const o of r) ordenes.set(String(o.id), o);
      if (r.length < 200) break;
    }
  }
  const out: PendienteDesp[] = [];
  for (const o of ordenes.values()) {
    const f = (o.fulfillments ?? []).find((x: Json) => ["UNPACKED", "IN_PREPARATION", "PACKED"].includes(x?.status));
    if (!f || f.shipping?.extras?.shippable === false) continue;
    const etiqueta = String(f.shipping?.option?.name || o.shipping_option || "");
    const grupo = grupoDeEnvioTN(etiqueta, f.shipping?.carrier?.name ?? o.shipping_carrier_name);
    if (!grupo) continue;
    out.push({
      clave: `tn|${tienda}|${o.number}`, grupo, tipo: "tn", canal: "tn", tienda, pedido: String(o.number), etiqueta,
      fecha: o.created_at ?? "", cliente: o.customer?.name || o.contact_name || null, para_manana: false,
    });
  }
  return out;
}

/** Envíos Flex de Mercado Libre listos para despachar (uno por envío, aunque tenga varias órdenes). */
async function pendientesFlexML(tienda: Tienda, hoy: string): Promise<PendienteDesp[]> {
  const c = CRED.ml?.[tienda];
  if (!c) return [];
  const ordenes: Json[] = [];
  for (let offset = 0; offset < 1000; offset += 50) {
    const r = await mlGet(tienda,
      `/orders/search?seller=${c.user_id}&order.status=paid&shipping.status=ready_to_ship&sort=date_desc&limit=50&offset=${offset}`);
    const res: Json[] = r.body?.results ?? [];
    ordenes.push(...res);
    if (res.length < 50) break;
  }
  const envioIds = [...new Set(ordenes.map((o) => o.shipping?.id).filter(Boolean).map(String))];
  const envios = new Map<string, Json>();
  await enParalelo(envioIds, 12, async (id) => {
    const r = await mlGet(tienda, `/shipments/${id}`);
    if (r.body) envios.set(id, r.body);
  });
  const vistos = new Set<string>();
  const out: PendienteDesp[] = [];
  for (const o of ordenes) {
    const id = String(o.shipping?.id ?? "");
    const e = envios.get(id);
    if (!id || vistos.has(id) || !e || e.status !== "ready_to_ship" || e.logistic_type !== "self_service") continue;
    vistos.add(id);
    const creado = String(o.date_created ?? "");
    out.push({
      clave: `ml|${id}`, grupo: "zeta_ninja", tipo: "flex_ml", canal: "ml", tienda, pedido: String(o.pack_id ?? o.id),
      etiqueta: "Mercado Libre Flex", fecha: creado, cliente: o.buyer?.nickname ?? null,
      para_manana: creado ? esParaManana(creado, hoy) : false,
    });
  }
  return out;
}

let cacheDespacho: { at: number; items: PendienteDesp[] } | null = null;

async function pendientesDespacho(refrescar: boolean): Promise<{ at: number; items: PendienteDesp[] }> {
  if (!cacheDespacho || refrescar || Date.now() - cacheDespacho.at > 60_000) {
    const hoy = hoyAR();
    const partes = await Promise.all([
      ...TIENDAS.map((t) => pendientesDespachoTN(t)),
      ...TIENDAS.map((t) => pendientesFlexML(t, hoy)),
    ]);
    cacheDespacho = { at: Date.now(), items: partes.flat() };
  }
  return cacheDespacho;
}

async function getDespachoEstado(url: URL) {
  const hoy = hoyAR();
  const pend = await pendientesDespacho(url.searchParams.get("refrescar") === "1");

  const { data: hoyRows, error } = await supabase.from("armado_despachos")
    .select("id, creado_at, mensajeria, tipo, por, canal, tienda, pedido, pedido_clave, envio")
    .eq("estado", "ok").eq("fecha", hoy).order("id", { ascending: false }).limit(1000);
  if (error) throw error;

  // Lo pendiente que ya se entregó (cualquier día), por la clave del pedido
  const entregadas = new Set<string>();
  const claves = pend.items.map((p) => p.clave);
  for (let i = 0; i < claves.length; i += 150) {
    const { data, error: e } = await supabase.from("armado_despachos").select("pedido_clave")
      .eq("estado", "ok").in("pedido_clave", claves.slice(i, i + 150));
    if (e) throw e;
    for (const r of data ?? []) entregadas.add(r.pedido_clave);
  }

  // Lo pendiente es un total por pool (Zeta y Ninja comparten); lo entregado se cuenta por mensajería y por pool
  const filasHoy = hoyRows ?? [];
  const cuenta = (rows: Json[], t: TipoPaquete) => rows.filter((r) => r.tipo === t).length;
  const grupos: Record<string, Json> = {};
  for (const m of MENSAJERIAS) {
    const pool = poolDe(m);
    const miembros: string[] = MIEMBROS[pool];
    const propias = filasHoy.filter((r) => r.mensajeria === m);
    const delPool = filasHoy.filter((r) => miembros.includes(r.mensajeria));
    // Un paquete entregado cuyo pedido no se pudo identificar no se puede tachar de la lista: se resta del número
    const sinId = (t: TipoPaquete) => delPool.filter((r) => r.tipo === t && !r.pedido_clave).length;
    const faltan = pend.items.filter((p) => p.grupo === pool && !entregadas.has(p.clave) && !p.para_manana);
    const faltanTipo = (t: "flex_ml" | "tn") => Math.max(0, faltan.filter((p) => p.tipo === t).length - sinId(t));
    grupos[m] = {
      nombre: NOMBRE_MENSAJERIA[m],
      pool,
      nombre_pool: NOMBRE_POOL[pool],
      entregados: { flex_ml: cuenta(propias, "flex_ml"), tn: cuenta(propias, "tn"), otro: cuenta(propias, "otro"), total: propias.length },
      pool_entregados: { flex_ml: cuenta(delPool, "flex_ml"), tn: cuenta(delPool, "tn"), otro: cuenta(delPool, "otro"), total: delPool.length },
      pool_por: Object.fromEntries(miembros.map((x) => [x, filasHoy.filter((r) => r.mensajeria === x).length])),
      faltan: { flex_ml: faltanTipo("flex_ml"), tn: faltanTipo("tn"), total: faltanTipo("flex_ml") + faltanTipo("tn") },
      sin_identificar: delPool.filter((r) => !r.pedido_clave).length,
      para_manana: pend.items.filter((p) => p.grupo === pool && !entregadas.has(p.clave) && p.para_manana).length,
      lista_faltan: faltan.sort((a, b) => a.fecha.localeCompare(b.fecha)).slice(0, 300).map((p) => ({
        tipo: p.tipo, canal: p.canal, tienda: p.tienda, pedido: p.pedido, etiqueta: p.etiqueta, fecha: p.fecha, cliente: p.cliente,
        atrasado: p.fecha ? fechaHoraAR(p.fecha).dia < hoy : false,
      })),
    };
  }
  return json({
    fecha: hoy,
    actualizado_at: new Date(pend.at).toISOString(),
    grupos,
    entregados: (hoyRows ?? []).slice(0, 300).map((r) => ({
      id: r.id, at: r.creado_at, mensajeria: r.mensajeria, tipo: r.tipo, por: r.por, canal: r.canal,
      tienda: r.tienda, pedido: r.pedido,
    })),
  });
}

type DatosPaquete = { canal?: "tn" | "ml"; tienda?: Tienda; pedido?: string; pedido_clave?: string; envio?: string };

const tiendaMLDe = (sender?: string): Tienda | undefined =>
  TIENDAS.find((t) => sender && CRED.ml?.[t] && String(CRED.ml[t].user_id) === sender);

/** Códigos escritos a mano (número de venta o de envío): no dicen la mensajería, se averigua con el pedido. */
async function resolverAMano(p: Paquete): Promise<{ paquete: Paquete; datos: DatosPaquete } | { error: string }> {
  if (p.formato === "numero_tn") {
    const ref = p.ref!;
    const hallados = (await Promise.all(TIENDAS.map(async (t) => (await tnBuscar(t, ref)).map((o) => ({ t, o }))))).flat();
    if (!hallados.length) return { error: `No encontré el pedido ${ref}.` };
    if (hallados.length > 1) return { error: `Hay más de un pedido ${ref} (Pret y Laván). Escaneá el QR de la etiqueta.` };
    const { t, o } = hallados[0];
    const f = (o.fulfillments ?? [])[0];
    const grupo = grupoDeEnvioTN(String(f?.shipping?.option?.name || o.shipping_option || ""), f?.shipping?.carrier?.name ?? o.shipping_carrier_name);
    const clave = `tn|${t}|${o.number}`;
    return {
      paquete: { ...p, grupo, tipo: "tn", clave },
      datos: { canal: "tn", tienda: t, pedido: String(o.number), pedido_clave: clave },
    };
  }
  let envio = p.envio ?? "";
  if (p.formato === "ml_venta") envio = (await mlEnvioDeVenta(p.venta!)) ?? "";
  if (!envio) return { error: "Mercado Libre no encontró esa venta." };
  for (const t of TIENDAS.filter((x) => CRED.ml?.[x])) {
    const e = await mlGet(t, `/shipments/${envio}`);
    if (e.status !== 200) continue;
    const flex = e.body?.logistic_type === "self_service";
    return {
      paquete: { ...p, formato: flex ? "ml_flex" : "ml_otro", grupo: flex ? "zeta_ninja" : null, tipo: flex ? "flex_ml" : null, clave: `ml|${envio}`, envio },
      datos: { canal: "ml", tienda: t, envio, pedido_clave: `ml|${envio}` },
    };
  }
  return { error: "Mercado Libre no encontró ese envío." };
}

/** Averigua a qué pedido corresponde un paquete ya contado y lo anota. Nunca tira: el escaneo ya quedó guardado. */
async function identificarPaquete(id: number, p: Paquete, datos: DatosPaquete) {
  try {
    const cambios: Json = {};
    if (p.formato === "ml_flex" || p.formato === "ml_envio_manual" || p.formato === "ml_venta") {
      // Número de venta (el del carrito si lo hay), igual que lo imprime la etiqueta
      const tienda = datos.tienda;
      if (tienda && p.envio) {
        const items = await mlGet(tienda, `/shipments/${p.envio}/items`);
        const ordenId = String((items.body ?? [])[0]?.order_id ?? "");
        if (ordenId) {
          const o = await mlGet(tienda, `/orders/${ordenId}`);
          cambios.pedido = String(o.body?.pack_id ?? ordenId);
        }
      }
    } else if (p.formato === "zeta" || p.formato === "ninja" || p.formato === "correo") {
      let ref = p.ref ?? "";
      if (p.formato === "zeta") ref = (await zetaNumeroVenta(p.zetaToken!)) ?? "";
      if (ref) {
        const hallados = (await Promise.all(TIENDAS.map(async (t) => (await tnBuscar(t, ref)).map((o) => ({ t, o }))))).flat();
        // Solo si es uno: con dos pedidos (Pret y Laván) con el mismo número no se adivina
        if (hallados.length === 1) {
          Object.assign(cambios, {
            canal: "tn", tienda: hallados[0].t, pedido: String(hallados[0].o.number),
            pedido_clave: `tn|${hallados[0].t}|${hallados[0].o.number}`,
          });
        }
      }
    }
    if (Object.keys(cambios).length) {
      const { error } = await supabase.from("armado_despachos").update(cambios).eq("id", id);
      if (error) throw error;
    }
  } catch (err) {
    console.error("identificar paquete", id, err);
  }
}

async function postDespachoEscanear(req: Request) {
  const b = await req.json();
  const elegida = b.mensajeria as Mensajeria;
  if (!MENSAJERIAS.includes(elegida)) return json({ error: "Falta la mensajería" }, 400);
  const raw = String(b.codigo ?? "").slice(0, 500);
  let p = detectarPaquete(raw);
  if (!p) return json({ estado: "no_reconocido", mensaje: "No reconozco ese código." });

  let datos: DatosPaquete = {};
  if (p.formato === "ml_flex") {
    datos = { canal: "ml", tienda: tiendaMLDe(p.sender), envio: p.envio, pedido_clave: `ml|${p.envio}` };
  } else if (p.formato === "ml_correo") {
    datos = { canal: "ml", envio: p.envio };
  } else if (p.resolverGrupo) {
    const r = await resolverAMano(p);
    if ("error" in r) return json({ estado: "no_reconocido", mensaje: r.error });
    p = r.paquete;
    datos = r.datos;
  }

  if (!p.grupo) {
    // Un código que ninguna mensajería conocida reconoce solo se acepta si se eligió Andreani (su QR no se conoce)
    if (p.formato === "desconocido" && elegida === "andreani") p = { ...p, grupo: "andreani", tipo: "tn" };
    else if (p.formato === "desconocido") return json({ estado: "no_reconocido", mensaje: "No reconozco ese código. Escribí el número de venta." });
    else if (p.formato === "ml_colecta") return json({ estado: "otra", grupo_detectado: null, mensaje: "Es un paquete de Mercado Libre Colecta. Lo retira Mercado Libre." });
    else return json({ estado: "otra", grupo_detectado: null, mensaje: "Este paquete no es de ninguna de estas mensajerías." });
  }
  const grupoDelPaquete = p.grupo;
  if (!grupoDelPaquete) return json({ estado: "no_reconocido", mensaje: "No reconozco ese código." });
  if (grupoDelPaquete !== poolDe(elegida)) {
    return json({ estado: "otra", grupo_detectado: grupoDelPaquete, mensaje: `Este paquete es de ${NOMBRE_POOL[grupoDelPaquete]}.` });
  }

  const repetido = async () => {
    const campos = "por, creado_at, mensajeria";
    let previo = (await supabase.from("armado_despachos").select(campos).eq("estado", "ok").eq("clave", p!.clave!).limit(1)).data?.[0];
    if (!previo && datos.pedido_clave) {
      previo = (await supabase.from("armado_despachos").select(campos).eq("estado", "ok").eq("pedido_clave", datos.pedido_clave).limit(1)).data?.[0];
    }
    return json({ estado: "repetido", previo: previo ?? null, mensaje: "Este paquete ya estaba contado." });
  };
  // Escrito a mano y ya entregado con su QR: la clave del QR es otra, se mira por el pedido
  if (datos.pedido_clave) {
    const { data } = await supabase.from("armado_despachos").select("id").eq("estado", "ok").eq("pedido_clave", datos.pedido_clave).limit(1);
    if (data?.length) return await repetido();
  }

  const { data, error } = await supabase.from("armado_despachos").insert({
    fecha: hoyAR(),
    mensajeria: elegida,
    tipo: p.tipo ?? "otro",
    por: b.por ? String(b.por).slice(0, 80) : null,
    codigo_leido: raw,
    clave: p.clave,
    canal: datos.canal ?? null,
    tienda: datos.tienda ?? null,
    pedido: datos.pedido ?? null,
    envio: datos.envio ?? null,
    pedido_clave: datos.pedido_clave ?? null,
  }).select("id").single();
  if (error) {
    if (error.code === "23505") return await repetido();
    throw error;
  }
  // El pedido se averigua después (Zeta consulta su página): contar es al instante
  const tarea = identificarPaquete(data.id, p, datos);
  // deno-lint-ignore no-explicit-any
  (globalThis as any).EdgeRuntime?.waitUntil?.(tarea);
  return json({ estado: "ok", id: data.id, formato: p.formato, grupo: p.grupo, tipo: p.tipo });
}

async function postDespachoAnular(req: Request) {
  const b = await req.json();
  const id = Number(b.id);
  if (!id) return json({ error: "Falta el paquete" }, 400);
  const { error } = await supabase.from("armado_despachos").update({
    estado: "anulado", anulado_at: new Date().toISOString(), anulado_por: b.por ? String(b.por).slice(0, 80) : null,
  }).eq("id", id).eq("estado", "ok");
  if (error) throw error;
  return json({ ok: true });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS_HEADERS });
  if (!checkPassword(req)) return json({ error: "Contraseña inválida" }, 401);

  const url = new URL(req.url);
  const path = url.pathname.replace(/^\/armado-api/, "") || "/";

  try {
    if (req.method === "GET" && path === "/ingresar") return json({ ok: true }); // solo valida la contraseña
    if (req.method === "POST" && path === "/buscar") return await postBuscar(req);
    if (req.method === "POST" && path === "/controles") return await postControl(req);
    if (req.method === "GET" && path === "/faltantes") return await getFaltantes();
    if (req.method === "GET" && path === "/busqueda/pendientes") return await getPendientes(url);
    if (req.method === "GET" && path === "/busqueda/estado") return await getEstado(url);
    if (req.method === "POST" && path === "/busqueda/marcar") return await postMarcar(req);
    if (req.method === "GET" && path === "/profunda") return await getProfunda();
    if (req.method === "POST" && path === "/profunda/tomar") return await postProfundaTomar(req);
    if (req.method === "POST" && path === "/profunda/cerrar") return await postProfundaCerrar(req);
    if (req.method === "GET" && path === "/despacho/estado") return await getDespachoEstado(url);
    if (req.method === "POST" && path === "/despacho/escanear") return await postDespachoEscanear(req);
    if (req.method === "POST" && path === "/despacho/anular") return await postDespachoAnular(req);
    return json({ error: "Ruta no encontrada" }, 404);
  } catch (err) {
    console.error(err);
    return json({ error: String(err) }, 500);
  }
});
