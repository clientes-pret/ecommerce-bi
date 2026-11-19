// supabase/functions/rev-ordenes/index.ts
//
// Registro de las ventas de los revendedores (MODO SOMBRA).
//
// Mercado Libre avisa cada venta de cada cuenta conectada a la URL de `rev-prueba`, que deja el aviso en `rev_ml_avisos`.
// Esta función, disparada cada minuto por la base, toma los avisos nuevos de ventas (`orders_v2`) de cuentas de
// revendedores, vuelve a leer cada orden en ML con las credenciales de ese revendedor (nunca se confía en el contenido del
// aviso, que no viene firmado) y la registra en `rev_ordenes`:
//   - venta confirmada (paga y no cancelada) → fila 'sombra' (venta simulada);
//   - cancelada → 'cancelada', y se guarda si el paquete ya había salido del depósito: si no salió, el stock vuelve solo
//     (reposicion 'inmediata'); si salió, vuelve cuando el depósito lo recibe (reposicion 'al_recibir', Operaciones → Devoluciones);
//   - con reclamo/devolución en ML → se guarda el estado del reclamo y del envío de vuelta en `devolucion`;
//   - todavía sin pago aprobado → no se registra (ML vuelve a avisar cuando cambie).
//
// NO descuenta stock en Ecomm-App ni escribe nada en Mercado Libre. El descuento real (adjust en Ecomm-App) no está
// implementado a propósito: lo decide el dueño, y hasta entonces todo es registro. Reprocesar es inocuo: la clave única
// (orden, SKU) y las reglas de `nuevoEstadoParaExistente` evitan duplicados y no tocan lo ya descontado.
//
// Los avisos `items`/`stock-locations` (el eco de nuestros propios cambios de stock) se ignoran: solo importa `orders_v2`.
//
// Seguridad: igual que rev-sync, la llamada lleva ?t=<token> y se valida contra la base (Vault). La dispara la base.

import { createClient } from "jsr:@supabase/supabase-js@2";
import { COLUMNAS_REVENDEDOR, mlGet, type Revendedor, tokenValido } from "../_shared/rev_ml.ts";
import {
  type DevolucionML, type EnvioML, esAnteriorALaConexion, estadoDeOrden, fueDespachada, lineasDeOrden, nuevoEstadoParaExistente,
  type OrdenML, type ReclamoML, reposicionPara, resumenReclamo,
} from "./logic.ts";

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const MAX_AVISOS_POR_CORRIDA = 200;
const MAX_INTENTOS_POR_ORDEN = 5; // una orden que falla 5 veces seguidas se descarta del cursor (la reconciliación la recupera)

type Json = Record<string, unknown>;
const norm = (s: string) => s.trim().toLowerCase();

interface Estado {
  id: number; // último aviso ya procesado
  fallos: Record<string, number>; // "usuarioML|/orders/123" → intentos fallidos
}

async function leerEstado(): Promise<Estado> {
  const { data } = await supabase.from("rev_sync_estado").select("detalle").eq("clave", "ordenes_cursor").maybeSingle();
  const d = (data?.detalle ?? {}) as Partial<Estado>;
  return { id: Number(d.id ?? 0), fallos: d.fallos ?? {} };
}

async function guardarEstado(e: Estado) {
  await supabase.from("rev_sync_estado").upsert({ clave: "ordenes_cursor", ultimo_at: new Date().toISOString(), resultado: "ok", detalle: e }, { onConflict: "clave" });
}

async function procesar(): Promise<Json> {
  const t0 = Date.now();
  const { data: revsData, error: revsError } = await supabase.from("rev_revendedores").select(COLUMNAS_REVENDEDOR).eq("estado", "activo");
  if (revsError) throw new Error(`rev_revendedores: ${revsError.message}`);
  const revs = new Map(((revsData ?? []) as Revendedor[]).map((r) => [r.ml_user_id, r]));
  if (!revs.size) return { ok: true, aviso: "sin revendedores activos" };

  const estado = await leerEstado();
  const { data: avisos, error: avisosError } = await supabase
    .from("rev_ml_avisos")
    .select("id,ml_user_id,resource")
    .eq("topic", "orders_v2")
    .gt("id", estado.id)
    .in("ml_user_id", [...revs.keys()])
    .order("id", { ascending: true })
    .limit(MAX_AVISOS_POR_CORRIDA);
  if (avisosError) throw new Error(`rev_ml_avisos: ${avisosError.message}`);
  if (!avisos?.length) return { ok: true, avisos_nuevos: 0 };

  // Un aviso por orden (ML manda varios por cada cambio): se procesa una vez, con el id más alto.
  const ultimoPorOrden = new Map<string, { id: number; ml_user_id: number; resource: string }>();
  for (const a of avisos) ultimoPorOrden.set(`${a.ml_user_id}|${a.resource}`, a as { id: number; ml_user_id: number; resource: string });

  // Mapas para reconocer SKU: publicaciones vinculadas por revendedor y catálogo habilitado.
  const { data: pubsData } = await supabase.from("rev_publicaciones").select("revendedor_id,ml_item_id,ml_variation_id,sku").not("sku", "is", null).limit(20000);
  const pubs = new Map<string, string>();
  for (const p of (pubsData ?? []) as Array<{ revendedor_id: number; ml_item_id: string; ml_variation_id: number | null; sku: string }>) {
    pubs.set(`${p.revendedor_id}|${p.ml_item_id}|${p.ml_variation_id ?? 0}`, p.sku);
  }
  const catalogo = new Map<string, string>();
  for (let desde = 0;; desde += 1000) {
    const { data } = await supabase.from("rev_catalogo").select("sku").eq("habilitado", true).order("sku").range(desde, desde + 999);
    for (const c of (data ?? []) as Array<{ sku: string }>) catalogo.set(norm(c.sku), c.sku);
    if (!data || data.length < 1000) break;
  }

  const tokens = new Map<number, string | null>();
  const cuenta = { ordenes_leidas: 0, ventas_registradas: 0, canceladas: 0, cancelaciones_clasificadas: 0, con_reclamo: 0, sin_pago: 0, no_reconocidas: 0, ignoradas: 0, anteriores_a_la_conexion: 0, fallidas: 0 };
  let primerFallo: number | null = null;

  for (const a of ultimoPorOrden.values()) {
    const m = /^\/orders\/(\d+)$/.exec(a.resource ?? "");
    if (!m) {
      cuenta.ignoradas++;
      continue;
    }
    const rev = revs.get(a.ml_user_id)!;
    const claveFallo = `${a.ml_user_id}|${a.resource}`;
    try {
      if (!tokens.has(rev.id)) tokens.set(rev.id, await tokenValido(supabase, rev));
      const token = tokens.get(rev.id);
      if (!token) throw new Error("sin credencial vigente");

      const r = await mlGet<OrdenML>(token, `/orders/${m[1]}`);
      if (!r.body) {
        if (r.status === 404) {
          cuenta.ignoradas++;
          continue;
        }
        throw new Error(`ML /orders -> HTTP ${r.status}`);
      }
      cuenta.ordenes_leidas++;
      const orden = r.body;
      if (esAnteriorALaConexion(orden, rev.conectado_at)) {
        cuenta.anteriores_a_la_conexion++;
        delete estado.fallos[claveFallo];
        continue;
      }
      const nuevo = estadoDeOrden(orden);
      if (!nuevo) {
        cuenta.sin_pago++;
        delete estado.fallos[claveFallo];
        continue;
      }

      const lineas = lineasDeOrden(
        orden,
        (item, variacion) => pubs.get(`${rev.id}|${item}|${variacion ?? 0}`) ?? null,
        (skuMl) => catalogo.get(norm(skuMl)) ?? null,
      );
      const { data: existentesData, error: exError } = await supabase.from("rev_ordenes").select("id,sku,estado").eq("ml_order_id", orden.id);
      if (exError) throw new Error(`rev_ordenes: ${exError.message}`);
      const existentes = new Map(((existentesData ?? []) as Array<{ id: number; sku: string; estado: string }>).map((e) => [norm(e.sku), e]));

      for (const l of lineas) {
        const ex = existentes.get(norm(l.sku));
        if (!ex) {
          if (nuevo === "cancelada") continue; // nunca se vio como venta: no hay nada que cancelar
          const { error } = await supabase.from("rev_ordenes").insert({
            revendedor_id: rev.id,
            ml_order_id: orden.id,
            ml_item_id: l.ml_item_id,
            sku: l.sku,
            cantidad: l.cantidad,
            precio_unitario: l.precio_unitario,
            estado: "sombra",
            detalle: l.reconocida ? null : "SKU no reconocido: no está en el catálogo ni en una publicación vinculada",
          });
          if (error && error.code !== "23505") throw new Error(`rev_ordenes insert: ${error.message}`);
          cuenta.ventas_registradas++;
          if (!l.reconocida) cuenta.no_reconocidas++;
        } else {
          const cambio = nuevoEstadoParaExistente(ex.estado, nuevo);
          if (cambio) {
            const { error } = await supabase.from("rev_ordenes").update({ estado: cambio, actualizado_at: new Date().toISOString() }).eq("id", ex.id);
            if (error) throw new Error(`rev_ordenes update: ${error.message}`);
            if (cambio === "cancelada") cuenta.canceladas++;
          }
        }
      }
      // Cancelada: ¿el paquete había salido del depósito? Define cuándo vuelve el stock (una sola vez por fila).
      if (nuevo === "cancelada") {
        const { data: canc } = await supabase.from("rev_ordenes").select("id").eq("ml_order_id", orden.id).eq("estado", "cancelada").is("reposicion", null);
        if (canc?.length) {
          const envio = orden.shipping?.id ? (await mlGet<EnvioML>(token, `/shipments/${orden.shipping.id}`)).body : null;
          const despachada = fueDespachada(envio);
          const { error } = await supabase.from("rev_ordenes")
            .update({ despachada, reposicion: reposicionPara(despachada), actualizado_at: new Date().toISOString() })
            .in("id", canc.map((c) => c.id));
          if (error) throw new Error(`rev_ordenes reposicion: ${error.message}`);
          cuenta.cancelaciones_clasificadas += canc.length;
        }
      }
      // Reclamos / devoluciones de esta venta (la orden de ML trae sus ids en `mediations`).
      if (orden.mediations?.length) {
        const reclamos = [];
        for (const md of orden.mediations) {
          const c = await mlGet<ReclamoML>(token, `/post-purchase/v1/claims/${md.id}`);
          if (!c.body) continue;
          const d = await mlGet<DevolucionML>(token, `/post-purchase/v2/claims/${md.id}/returns`); // 404 = reclamo sin envío de vuelta
          reclamos.push(resumenReclamo(c.body, d.body));
        }
        if (reclamos.length) {
          const { error } = await supabase.from("rev_ordenes").update({ devolucion: { reclamos }, actualizado_at: new Date().toISOString() }).eq("ml_order_id", orden.id);
          if (error) throw new Error(`rev_ordenes devolucion: ${error.message}`);
          cuenta.con_reclamo++;
        }
      }
      delete estado.fallos[claveFallo];
    } catch (e) {
      const mensaje = e instanceof Error ? e.message : String(e);
      estado.fallos[claveFallo] = (estado.fallos[claveFallo] ?? 0) + 1;
      console.error(`rev-ordenes ${claveFallo}: ${mensaje} (intento ${estado.fallos[claveFallo]})`);
      if (estado.fallos[claveFallo] >= MAX_INTENTOS_POR_ORDEN) {
        delete estado.fallos[claveFallo]; // se descarta del cursor; la reconciliación la recupera
        cuenta.fallidas++;
      } else if (primerFallo === null || a.id < primerFallo) {
        primerFallo = a.id; // el cursor no avanza más allá de un aviso que hay que reintentar
      }
    }
  }

  const maximo = avisos[avisos.length - 1].id as number;
  estado.id = primerFallo !== null ? Math.max(estado.id, primerFallo - 1) : maximo;
  await guardarEstado(estado);
  return { ok: true, avisos_nuevos: avisos.length, ordenes_distintas: ultimoPorOrden.size, ...cuenta, reintentos_pendientes: primerFallo !== null, segundos: Math.round((Date.now() - t0) / 100) / 10 };
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const { data: tokenOk, error: tokenError } = await supabase.rpc("rev_sync_token_valido", { p: url.searchParams.get("t") ?? "" });
  if (tokenError || tokenOk !== true) return new Response("No autorizado", { status: 401 });
  try {
    const resumen = await procesar();
    if ((resumen.avisos_nuevos as number | undefined) || resumen.fallidas) {
      await supabase.from("rev_sync_estado").upsert({ clave: "ordenes", ultimo_at: new Date().toISOString(), resultado: "ok", detalle: resumen }, { onConflict: "clave" });
    }
    return new Response(JSON.stringify(resumen), { headers: { "Content-Type": "application/json" } });
  } catch (e) {
    const mensaje = e instanceof Error ? e.message : String(e);
    console.error("rev-ordenes:", mensaje);
    return new Response(JSON.stringify({ ok: false, error: mensaje }), { status: 500, headers: { "Content-Type": "application/json" } });
  }
});
