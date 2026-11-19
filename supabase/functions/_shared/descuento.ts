// supabase/functions/_shared/descuento.ts
//
// Descuento REAL en Ecomm-App de las ventas de revendedores, y devolución del stock (19/11/2026). Lo llama rev-ordenes
// cada minuto SOLO si rev_config.modo = 'real'; en modo 'sombra' no se usa. También se puede correr a mano para UNA
// venta (`soloOrden`), que es como se prueba sin activar el modo real para todos.
//   1. Venta registrada (estado 'sombra', sin descontar)  → adjust −cantidad, motivo "venta-<revendedor> #<orden> SKU <sku>".
//   2. Cancelada antes de despachar / devolución recibida bien → adjust +cantidad, motivo "cancelacion-…" / "devolucion-…".
// Cada movimiento lleva su clave única (Idempotency-Key): reintentar o reprocesar nunca mueve dos veces. Ante un
// resultado desconocido NO se reintenta con otra clave: la línea queda en 'error' para revisarla a mano.

import type { SupabaseClient } from "jsr:@supabase/supabase-js@2";
import {
  claveIdempotente, interpretarRespuestaEcomm, motivoMovimiento, reposicionPendiente, transicionDescuento, type TipoMovimiento,
} from "../rev-sync/logic.ts";

const ECOMM_STOCK = "https://admin.ecomm-app.com/api/v3/public/inventario/variantes/stock";

interface Fila {
  id: number; revendedor_id: number; ml_order_id: number; sku: string; cantidad: number; estado: string;
  descontado_at: string | null; repuesto_at: string | null; reposicion: string | null; recibido_estado: string | null;
}

/** Un movimiento de stock en Ecomm-App. `cantidad` negativa descuenta, positiva suma. */
export async function moverStock(token: string, o: { sku: string; cantidad: number; warehouseId: number; clave: string; motivo: string }) {
  for (let intento = 0; intento < 3; intento++) {
    let http = 0, cuerpo: Record<string, unknown> | null = null, retry: number | undefined;
    try {
      const r = await fetch(ECOMM_STOCK, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Idempotency-Key": o.clave },
        body: JSON.stringify({ type: "adjust", sku: o.sku, warehouse_id: o.warehouseId, quantity: o.cantidad, reason: o.motivo }),
      });
      http = r.status;
      retry = Number(r.headers.get("Retry-After")) || undefined;
      try { cuerpo = await r.json(); } catch { cuerpo = null; }
    } catch {
      http = 599; // sin respuesta (red/timeout): se reintenta con la MISMA clave
    }
    const res = interpretarRespuestaEcomm(http, cuerpo as never, retry);
    if (res.resultado === "reintentar_misma_clave" && intento < 2) { await new Promise((ok) => setTimeout(ok, (res.esperarSegundos ?? 2) * 1000)); continue; }
    return { http, cuerpo, res, codigoError: ((cuerpo?.error as { code?: string } | undefined)?.code) ?? null };
  }
  throw new Error("inalcanzable");
}

export async function procesarDescuentos(supabase: SupabaseClient, ecommToken: string, opciones: { soloOrden?: number } = {}) {
  const { data: cfg } = await supabase.from("rev_config").select("valor").eq("clave", "warehouse_id").maybeSingle();
  const warehouseId = Number(cfg?.valor ?? 1083);
  const { data: revs } = await supabase.from("rev_revendedores").select("id,nickname,descuento_auto");
  const nick = new Map(((revs ?? []) as { id: number; nickname: string | null }[]).map((r) => [r.id, r.nickname]));
  // Revendedores con descuento MANUAL: sus ventas solo se descuentan (o se les devuelve el stock) cuando el administrador
  // lo pide para esa venta (`soloOrden`), nunca en la corrida automática.
  const manual = new Set(((revs ?? []) as { id: number; descuento_auto: boolean }[]).filter((r) => r.descuento_auto === false).map((r) => r.id));
  const tocar = (f: Fila) => opciones.soloOrden != null || !manual.has(f.revendedor_id);
  const cols = "id,revendedor_id,ml_order_id,sku,cantidad,estado,descontado_at,repuesto_at,reposicion,recibido_estado";
  const cuenta = { manuales_salteadas: 0, descontadas: 0, sin_stock: 0, errores: 0, a_reintentar: 0, repuestas: 0, errores_reposicion: 0 };
  const ahora = () => new Date().toISOString();

  // 1. Descontar ventas registradas que todavía no se descontaron.
  let q = supabase.from("rev_ordenes").select(cols).eq("estado", "sombra").is("descontado_at", null).order("id").limit(200);
  if (opciones.soloOrden) q = q.eq("ml_order_id", opciones.soloOrden);
  const { data: aDescontar, error: e1 } = await q;
  if (e1) throw new Error(`rev_ordenes: ${e1.message}`);
  for (const f of ((aDescontar ?? []) as Fila[]).filter(tocar)) {
    const clave = claveIdempotente(f.ml_order_id, f.sku, "venta");
    const m = await moverStock(ecommToken, { sku: f.sku, cantidad: -f.cantidad, warehouseId, clave, motivo: motivoMovimiento("venta", nick.get(f.revendedor_id), f.ml_order_id, f.sku) });
    const t = transicionDescuento(m.res, m.http, m.codigoError);
    const cambios: Record<string, unknown> = { ecomm_clave: clave, ecomm_respuesta: m.cuerpo, detalle: t.detalle, actualizado_at: ahora() };
    if (t.estado) cambios.estado = t.estado;
    if (t.descontado) cambios.descontado_at = ahora();
    await supabase.from("rev_ordenes").update(cambios).eq("id", f.id).eq("estado", "sombra");
    if (t.estado === "descontada") cuenta.descontadas++;
    else if (t.estado === "sin_stock") cuenta.sin_stock++;
    else if (t.estado === "error") cuenta.errores++;
    else cuenta.a_reintentar++;
  }

  // 2. Devolver el stock de lo descontado que se canceló antes de despachar o volvió en buen estado.
  let q2 = supabase.from("rev_ordenes").select(cols).eq("estado", "cancelada").not("descontado_at", "is", null).is("repuesto_at", null).order("id").limit(200);
  if (opciones.soloOrden) q2 = q2.eq("ml_order_id", opciones.soloOrden);
  const { data: aReponer, error: e2 } = await q2;
  if (e2) throw new Error(`rev_ordenes: ${e2.message}`);
  for (const f of ((aReponer ?? []) as Fila[]).filter(tocar)) {
    const tipo = reposicionPendiente(f) as TipoMovimiento | null;
    if (!tipo) continue;
    const clave = claveIdempotente(f.ml_order_id, f.sku, tipo);
    const m = await moverStock(ecommToken, { sku: f.sku, cantidad: f.cantidad, warehouseId, clave, motivo: motivoMovimiento(tipo as "cancelacion" | "devolucion", nick.get(f.revendedor_id), f.ml_order_id, f.sku) });
    const ok = m.res.resultado === "aplicada" || m.res.resultado === "aplicada_parcial";
    await supabase.from("rev_ordenes").update({
      ecomm_reposicion: { clave, http: m.http, respuesta: m.cuerpo }, actualizado_at: ahora(),
      ...(ok ? { repuesto_at: ahora() } : { detalle: `No se pudo devolver el stock en Ecomm-App (${m.codigoError ?? m.res.resultado}): revisar a mano.` }),
    }).eq("id", f.id).is("repuesto_at", null);
    if (ok) cuenta.repuestas++; else cuenta.errores_reposicion++;
  }
  return cuenta;
}
