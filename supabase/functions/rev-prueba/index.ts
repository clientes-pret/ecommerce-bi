// supabase/functions/rev-prueba/index.ts
//
// Prueba de Fase 0 del dropshipping con revendedores en Mercado Libre. Hace solo tres cosas:
//   GET  /conectar        → manda al revendedor a la pantalla de autorización de ML.
//   GET  /callback        → ML devuelve acá al revendedor con un código; lo canjeamos por las credenciales de su
//                           cuenta y las guardamos en rev_revendedores. La página que ve el revendedor no muestra nada sensible.
//   POST /notificaciones  → ML avisa acá de cada venta (topic orders_v2) de TODAS las cuentas que autorizaron la
//                           aplicación. Solo se registra el aviso en rev_ml_avisos; no se descuenta stock ni se toca nada.
//
// Credenciales de la aplicación de ML (una app NUEVA, aparte de las de Pret y Lavan): secrets
// ML_REV_CLIENT_ID y ML_REV_CLIENT_SECRET. Sin ellas la función no hace nada.
//
// Seguridad: el parámetro `state` va firmado (HMAC con el client_secret) y vence a los 30 minutos, así nadie puede
// conectar una cuenta sin pasar por /conectar. Los avisos de ML no llevan firma: por eso este código no confía en su
// contenido para nada importante (en la versión real cada aviso se vuelve a leer desde ML con el token del
// revendedor antes de actuar).

import { createClient } from "jsr:@supabase/supabase-js@2";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);
const CLIENT_ID = Deno.env.get("ML_REV_CLIENT_ID") ?? "";
const CLIENT_SECRET = Deno.env.get("ML_REV_CLIENT_SECRET") ?? "";
const REDIRECT_URI = `${Deno.env.get("SUPABASE_URL")}/functions/v1/rev-prueba/callback`;
const ML_AUTH = "https://auth.mercadolibre.com.ar/authorization";
const ML_API = "https://api.mercadolibre.com";
const STATE_VIDA_MS = 30 * 60 * 1000;
// Temas de mucho volumen (cambios de precios y ofertas de la propia cuenta, ~90 % de los avisos medidos el
// 17/11/2026) que no sirven para esta prueba: se responden 200 sin guardar, para no llenar la base.
const TOPICS_IGNORADOS = new Set(["items_prices", "public_offers"]);

const enc = new TextEncoder();

async function firmar(texto: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(CLIENT_SECRET), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(texto));
  return Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function crearState(): Promise<string> {
  const base = `${crypto.randomUUID()}.${Date.now()}`;
  return `${base}.${await firmar(base)}`;
}

async function stateValido(state: string): Promise<boolean> {
  const partes = state.split(".");
  if (partes.length !== 3) return false;
  const [nonce, ts, firma] = partes;
  if (firma !== await firmar(`${nonce}.${ts}`)) return false;
  const edad = Date.now() - Number(ts);
  return Number.isFinite(edad) && edad >= 0 && edad < STATE_VIDA_MS;
}

function escapar(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
}

function pagina(titulo: string, mensaje: string, status = 200): Response {
  const html = `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapar(titulo)}</title><style>body{font-family:system-ui,sans-serif;max-width:480px;margin:15vh auto;padding:0 20px;text-align:center;color:#222}
h1{font-size:22px}p{color:#555;line-height:1.5}</style></head><body><h1>${escapar(titulo)}</h1><p>${escapar(mensaje)}</p></body></html>`;
  return new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
}

async function conectar(): Promise<Response> {
  if (!CLIENT_ID || !CLIENT_SECRET) return pagina("Todavía no está listo", "Falta configurar la aplicación. Avisale a quien te invitó.", 503);
  const url = new URL(ML_AUTH);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", CLIENT_ID);
  url.searchParams.set("redirect_uri", REDIRECT_URI);
  url.searchParams.set("state", await crearState());
  return Response.redirect(url.toString(), 302);
}

async function callback(url: URL): Promise<Response> {
  if (!CLIENT_ID || !CLIENT_SECRET) return pagina("Todavía no está listo", "Falta configurar la aplicación.", 503);
  if (url.searchParams.get("error")) return pagina("No se conectó la cuenta", "Cancelaste la autorización o Mercado Libre la rechazó. Podés volver a intentarlo desde el link que te pasaron.", 400);
  const code = url.searchParams.get("code") ?? "";
  const state = url.searchParams.get("state") ?? "";
  if (!code || !state || !(await stateValido(state))) {
    return pagina("El link venció", "Volvé a abrir el link que te pasaron para conectar tu cuenta.", 400);
  }

  const r = await fetch(`${ML_API}/oauth/token`, {
    method: "POST",
    headers: { "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      code,
      redirect_uri: REDIRECT_URI,
    }),
  });
  if (!r.ok) {
    // Estado y motivo del rechazo (campos `error`/`message` de ML, que en un error no traen credenciales ni datos de la cuenta).
    let motivo = "";
    try {
      const e = await r.json();
      motivo = `${e.error ?? ""} ${e.message ?? ""}`.trim().slice(0, 300);
    } catch { /* sin cuerpo JSON */ }
    console.error(`ML /oauth/token -> ${r.status} ${motivo}`);
    return pagina("No se pudo conectar", "Mercado Libre no aceptó la conexión. Probá de nuevo en unos minutos.", 502);
  }
  const t = await r.json();

  let nickname: string | null = null;
  try {
    const me = await fetch(`${ML_API}/users/me`, { headers: { Authorization: `Bearer ${t.access_token}` } });
    if (me.ok) nickname = (await me.json()).nickname ?? null;
  } catch { /* el nombre es opcional */ }

  // Las cuentas PROPIAS del dueño (Pret, Lavan: lista en rev_config 'cuentas_propias') se conectan solo para poder leer sus
  // publicaciones: quedan 'pausadas' para que nunca se las trate como un revendedor (no se les sincroniza stock ni se
  // registran sus ventas como ventas de revendedor).
  const { data: cfgPropias } = await supabase.from("rev_config").select("valor").eq("clave", "cuentas_propias").maybeSingle();
  const propias = Array.isArray(cfgPropias?.valor) ? (cfgPropias!.valor as number[]).map(Number) : [];
  const estado = propias.includes(Number(t.user_id)) ? "pausado" : "activo";

  const { error } = await supabase.from("rev_revendedores").upsert({
    ml_user_id: t.user_id,
    nickname,
    access_token: t.access_token,
    refresh_token: t.refresh_token,
    token_expires_at: new Date(Date.now() + (t.expires_in ?? 21600) * 1000).toISOString(),
    scopes: t.scope ?? null,
    estado,
    actualizado_at: new Date().toISOString(),
  }, { onConflict: "ml_user_id" });
  if (error) {
    console.error("rev_revendedores upsert:", error.message);
    return pagina("No se pudo guardar", "La conexión con Mercado Libre funcionó pero no pudimos guardarla. Avisá a quien te invitó.", 500);
  }
  return pagina("¡Listo! Tu cuenta quedó conectada", "Ya podés cerrar esta ventana.");
}

async function notificacion(req: Request): Promise<Response> {
  let body: Record<string, unknown> = {};
  try {
    body = await req.json();
  } catch { /* aviso sin cuerpo válido: se registra vacío */ }
  if (TOPICS_IGNORADOS.has(String(body.topic))) return new Response("ok", { status: 200 });
  const { error } = await supabase.from("rev_ml_avisos").insert({
    topic: (body.topic as string) ?? null,
    ml_user_id: (body.user_id as number) ?? null,
    resource: (body.resource as string) ?? null,
    application_id: (body.application_id as number) ?? null,
    intentos: (body.attempts as number) ?? null,
    crudo: body,
  });
  // 5xx solo si falló la base, para que ML reintente; todo lo demás es 200.
  if (error) {
    console.error("rev_ml_avisos insert:", error.message);
    return new Response("error", { status: 500 });
  }
  return new Response("ok", { status: 200 });
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const ruta = url.pathname.replace(/\/+$/, "");
  if (req.method === "GET" && ruta.endsWith("/conectar")) return await conectar();
  if (req.method === "GET" && ruta.endsWith("/callback")) return await callback(url);
  if (ruta.endsWith("/notificaciones")) {
    if (req.method === "POST") return await notificacion(req);
    return new Response("ok", { status: 200 }); // ML puede verificar la URL con un GET
  }
  return new Response("No encontrado", { status: 404 });
});
