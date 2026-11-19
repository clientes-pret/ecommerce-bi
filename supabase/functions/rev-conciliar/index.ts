// supabase/functions/rev-conciliar/index.ts
//
// Conciliación de ventas de revendedores: la red de seguridad de `rev-ordenes`.
//
// Los avisos de Mercado Libre pueden perderse o llegar tarde. Cada tanto esta función repasa, directamente en ML, las
// órdenes de los últimos días de cada revendedor activo (las que tuvieron algún cambio) y las compara con `rev_ordenes`:
//   - orden paga en ML y ausente en `rev_ordenes`            → se vuelve a avisar;
//   - orden cancelada en ML y todavía como venta en la base  → se vuelve a avisar;
//   - reclamo/devolución de ML nuevo o que cambió (la búsqueda de órdenes no los trae: se leen los reclamos del revendedor)
//     sobre una venta registrada → se vuelve a avisar, y rev-ordenes guarda el estado del reclamo y del envío de vuelta.
// "Volver a avisar" = dejar un aviso `orders_v2` en `rev_ml_avisos` (marcado `origen: conciliacion`): lo toma `rev-ordenes`
// en el minuto siguiente y lo procesa con las mismas reglas. Así no hay una segunda lógica para mantener.
//
// Solo LEE en Mercado Libre y escribe en tablas `rev_*`. No toca Ecomm-App. Las órdenes anteriores a la conexión del
// revendedor se ignoran (igual que en `rev-ordenes`).
//
// Seguridad: igual que las demás, la llamada lleva ?t=<token> y se valida contra la base (Vault). La dispara la base.

import { createClient } from "jsr:@supabase/supabase-js@2";
import { COLUMNAS_REVENDEDOR, dormir, mlGet, type Revendedor, tokenValido } from "../_shared/rev_ml.ts";
import { debeReavisar, esAnteriorALaConexion, estadoDeOrden, type OrdenML, reclamoCambio, type ReclamoGuardado, type ReclamoML } from "../rev-ordenes/logic.ts";

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const DIAS_ATRAS = 4;
const POR_PAGINA = 50;
const MAX_ORDENES_POR_REVENDEDOR = 1000; // límite de paginación de ML por offset

type Json = Record<string, unknown>;

/** Órdenes del revendedor con algún cambio en los últimos días (incluye cancelaciones de órdenes más viejas). */
async function ordenesRecientes(token: string, mlUserId: number): Promise<{ ordenes: OrdenML[]; truncado: boolean } | { error: string }> {
  const desde = new Date(Date.now() - DIAS_ATRAS * 86400000).toISOString().replace("Z", "-00:00");
  const ordenes: OrdenML[] = [];
  let total = 0;
  for (let offset = 0; offset < MAX_ORDENES_POR_REVENDEDOR; offset += POR_PAGINA) {
    const r = await mlGet<{ results: OrdenML[]; paging: { total: number } }>(
      token,
      `/orders/search?seller=${mlUserId}&order.date_last_updated.from=${encodeURIComponent(desde)}&sort=date_desc&limit=${POR_PAGINA}&offset=${offset}`,
    );
    if (!r.body) return { error: `ML /orders/search -> HTTP ${r.status}` };
    ordenes.push(...r.body.results);
    total = r.body.paging?.total ?? ordenes.length;
    if (r.body.results.length < POR_PAGINA || ordenes.length >= total) break;
    await dormir(300);
  }
  return { ordenes, truncado: total > MAX_ORDENES_POR_REVENDEDOR };
}

async function conciliarRevendedor(rev: Revendedor): Promise<Json> {
  const token = await tokenValido(supabase, rev);
  if (!token) return { revendedor: rev.nickname, ok: false, motivo: "sin credencial vigente" };

  const r = await ordenesRecientes(token, rev.ml_user_id);
  if ("error" in r) return { revendedor: rev.nickname, ok: false, motivo: r.error };

  const relevantes = r.ordenes.filter((o) => !esAnteriorALaConexion(o, rev.conectado_at));
  const ids = relevantes.map((o) => o.id);

  const enBase = new Map<number, string[]>(); // orden → estados de sus filas
  for (let i = 0; i < ids.length; i += 200) {
    const { data, error } = await supabase.from("rev_ordenes").select("ml_order_id,estado").eq("revendedor_id", rev.id).in("ml_order_id", ids.slice(i, i + 200));
    if (error) throw new Error(`rev_ordenes: ${error.message}`);
    for (const f of (data ?? []) as Array<{ ml_order_id: number; estado: string }>) {
      enBase.set(f.ml_order_id, [...(enBase.get(f.ml_order_id) ?? []), f.estado]);
    }
  }

  let faltantes = 0;
  let canceladasSinReflejar = 0;
  const avisos: Json[] = [];
  for (const o of relevantes) {
    const esperado = estadoDeOrden(o);
    if (debeReavisar(esperado, enBase.get(o.id))) {
      if (esperado === "sombra") faltantes++;
      else canceladasSinReflejar++;
      avisos.push({ topic: "orders_v2", ml_user_id: rev.ml_user_id, resource: `/orders/${o.id}`, intentos: 0, crudo: { origen: "conciliacion" } });
    }
  }
  // Reclamos del revendedor (abiertos + los más recientes) sobre ventas registradas que no están guardados o cambiaron.
  const reclamos = new Map<number, ReclamoML & { resource?: string; resource_id?: number }>();
  for (const q of ["status=opened&limit=100", "sort=date_created:desc&limit=50"]) {
    const rc = await mlGet<{ data?: Array<ReclamoML & { resource?: string; resource_id?: number }> }>(token,
      `/post-purchase/v1/claims/search?player_role=respondent&player_user_id=${rev.ml_user_id}&${q}`);
    for (const c of rc.body?.data ?? []) if (c.resource === "order" && c.resource_id) reclamos.set(c.id, c);
  }
  let reclamosNuevos = 0;
  const ordenesReclamo = [...new Set([...reclamos.values()].map((c) => Number(c.resource_id)))];
  const yaAvisadas = new Set(avisos.map((a) => String(a.resource)));
  for (let i = 0; i < ordenesReclamo.length; i += 200) {
    const { data, error } = await supabase.from("rev_ordenes").select("ml_order_id,devolucion").eq("revendedor_id", rev.id).in("ml_order_id", ordenesReclamo.slice(i, i + 200));
    if (error) throw new Error(`rev_ordenes: ${error.message}`);
    const guardado = new Map(((data ?? []) as Array<{ ml_order_id: number; devolucion: { reclamos?: ReclamoGuardado[] } | null }>).map((f) => [Number(f.ml_order_id), f.devolucion]));
    for (const c of reclamos.values()) {
      const oid = Number(c.resource_id);
      if (!guardado.has(oid) || !reclamoCambio(c, guardado.get(oid))) continue; // no es una venta registrada, o ya está al día
      if (yaAvisadas.has(`/orders/${oid}`)) continue;
      yaAvisadas.add(`/orders/${oid}`);
      avisos.push({ topic: "orders_v2", ml_user_id: rev.ml_user_id, resource: `/orders/${oid}`, intentos: 0, crudo: { origen: "conciliacion_reclamo" } });
      reclamosNuevos++;
    }
  }

  for (let i = 0; i < avisos.length; i += 200) {
    const { error } = await supabase.from("rev_ml_avisos").insert(avisos.slice(i, i + 200));
    if (error) throw new Error(`rev_ml_avisos insert: ${error.message}`);
  }

  return {
    revendedor: rev.nickname,
    ok: true,
    ordenes_con_cambios: r.ordenes.length,
    anteriores_a_la_conexion: r.ordenes.length - relevantes.length,
    truncado: r.truncado,
    ventas_faltantes: faltantes,
    cancelaciones_sin_reflejar: canceladasSinReflejar,
    reclamos_nuevos_o_cambiados: reclamosNuevos,
    avisos_generados: avisos.length,
  };
}

async function conciliar(): Promise<Json> {
  const t0 = Date.now();
  const { data, error } = await supabase.from("rev_revendedores").select(COLUMNAS_REVENDEDOR).eq("estado", "activo").order("id");
  if (error) throw new Error(`rev_revendedores: ${error.message}`);
  const resultados: Json[] = [];
  for (const rev of (data ?? []) as Revendedor[]) {
    try {
      resultados.push(await conciliarRevendedor(rev));
    } catch (e) {
      const mensaje = e instanceof Error ? e.message : String(e);
      console.error(`rev-conciliar ${rev.ml_user_id}: ${mensaje}`);
      resultados.push({ revendedor: rev.nickname, ok: false, motivo: mensaje });
    }
  }
  const fallas = resultados.filter((x) => !x.ok).length;
  const detalle = { dias_atras: DIAS_ATRAS, revendedores: resultados.length, fallas, segundos: Math.round((Date.now() - t0) / 100) / 10, resultados };
  await supabase.from("rev_sync_estado").upsert({
    clave: "conciliacion",
    ultimo_at: new Date().toISOString(),
    resultado: fallas ? "con_fallas" : "ok",
    detalle,
  }, { onConflict: "clave" });
  return { ok: fallas === 0, ...detalle };
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const { data: tokenOk, error: tokenError } = await supabase.rpc("rev_sync_token_valido", { p: url.searchParams.get("t") ?? "" });
  if (tokenError || tokenOk !== true) return new Response("No autorizado", { status: 401 });
  try {
    return new Response(JSON.stringify(await conciliar()), { headers: { "Content-Type": "application/json" } });
  } catch (e) {
    const mensaje = e instanceof Error ? e.message : String(e);
    console.error("rev-conciliar:", mensaje);
    return new Response(JSON.stringify({ ok: false, error: mensaje }), { status: 500, headers: { "Content-Type": "application/json" } });
  }
});
