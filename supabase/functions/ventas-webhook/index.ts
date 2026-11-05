// supabase/functions/ventas-webhook/index.ts
//
// Recibe los webhooks de Tiendanube (order/paid, order/cancelled) y mantiene repo_ventas_items al día
// venta por venta, en vez de esperar al sync diario (reposicion/ventas_sync.py).
//
// El sync diario NO se apaga: queda como red de seguridad (si se pierde un aviso, esa noche lo repara) y
// escribe en la misma tabla con la misma clave única, así que un aviso repetido o un cruce entre ambos es
// inocuo (upsert idempotente).
//
// Seguridad: la URL lleva un token largo (?t=…) que se compara con el secret VENTAS_WEBHOOK_TOKEN. Sin ese
// secret la función rechaza todo (falla cerrada). No se puede validar la firma HMAC de Tiendanube porque
// requiere el client_secret de la app, que no tenemos guardado.
//
// Credenciales de TN: las mismas que usa armado-api (secret ARMADO_CREDENCIALES).
//
// Respuestas: 200 si el aviso se procesó o se descartó a propósito (TN no debe reintentar); 5xx solo si falló
// algo transitorio (API de TN o base), para que TN reintente.

import { createClient } from "jsr:@supabase/supabase-js@2";
import { canalesPorStoreId, decidir, iguales, type Json } from "./logic.ts";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);
const TOKEN = Deno.env.get("VENTAS_WEBHOOK_TOKEN") ?? "";
const CRED: Json = JSON.parse(Deno.env.get("ARMADO_CREDENCIALES") ?? "{}");
const CANAL_POR_STORE = canalesPorStoreId(CRED);

function respuesta(status: number, body: Json) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function tnOrden(canal: string, orderId: string): Promise<Json | null> {
  const c = CRED.tn?.[canal.replace("tn_", "")];
  const url = new URL(`https://api.tiendanube.com/v1/${c.store_id}/orders/${encodeURIComponent(orderId)}`);
  url.searchParams.set("fields", "id,created_at,status,payment_status,products");
  for (let intento = 0; intento < 3; intento++) {
    const r = await fetch(url, {
      headers: { Authentication: `bearer ${c.access_token}`, "User-Agent": "VentasWebhook (info@pretahome.com)" },
    });
    if (r.status === 404) return null;
    if (r.status === 429 || r.status >= 500) {
      await new Promise((res) => setTimeout(res, 1000 * (intento + 1)));
      continue;
    }
    if (!r.ok) throw new Error(`Tiendanube ${canal} /orders/${orderId} -> ${r.status}`);
    return await r.json();
  }
  throw new Error(`Tiendanube ${canal} no responde`);
}

/** Último evento recibido por canal (para detectar que el webhook se cayó). Nunca rompe el procesamiento. */
async function registrarEvento(canal: string, evento: string, orderId: string, detalle: string) {
  try {
    await supabase.from("repo_webhook_estado").upsert({
      canal,
      ultimo_evento: evento,
      ultimo_order_id: orderId,
      ultimo_resultado: detalle,
      ultimo_evento_at: new Date().toISOString(),
    }, { onConflict: "canal" });
  } catch (e) {
    console.error("repo_webhook_estado", e);
  }
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return respuesta(405, { error: "method not allowed" });
  if (!TOKEN) return respuesta(500, { error: "webhook sin configurar" });
  const t = new URL(req.url).searchParams.get("t") ?? "";
  if (!iguales(t, TOKEN)) return respuesta(401, { error: "unauthorized" });

  let evento: Json;
  try {
    evento = await req.json();
  } catch {
    return respuesta(400, { error: "json inválido" });
  }
  const storeId = String(evento?.store_id ?? "");
  const canal = CANAL_POR_STORE[storeId];
  const orderId = String(evento?.id ?? "");
  const nombreEvento = String(evento?.event ?? "");
  if (!canal || !orderId) {
    console.warn("evento descartado", { storeId, orderId, nombreEvento });
    return respuesta(200, { ignorado: "tienda u orden desconocida" });
  }

  try {
    const orden = await tnOrden(canal, orderId);
    const d = decidir(canal, orden);

    if (d.accion === "guardar") {
      if (d.lineas.length) {
        const { error } = await supabase.from("repo_ventas_items")
          .upsert(d.lineas, { onConflict: "canal,order_id,item_id,sku" });
        if (error) throw new Error(`upsert repo_ventas_items: ${error.message}`);
      }
      await registrarEvento(canal, nombreEvento, orderId, `guardada (${d.lineas.length} líneas)`);
      return respuesta(200, { ok: "guardada", lineas: d.lineas.length });
    }
    if (d.accion === "cancelar") {
      const { error } = await supabase.from("repo_ventas_items")
        .update({ estado: "cancelada" }).eq("canal", canal).eq("order_id", d.order_id);
      if (error) throw new Error(`cancelar repo_ventas_items: ${error.message}`);
      await registrarEvento(canal, nombreEvento, orderId, "cancelada");
      return respuesta(200, { ok: "cancelada" });
    }
    await registrarEvento(canal, nombreEvento, orderId, `ignorada: ${d.motivo}`);
    return respuesta(200, { ignorado: d.motivo });
  } catch (err) {
    console.error("ventas-webhook", canal, orderId, err);
    return respuesta(502, { error: String((err as Error)?.message ?? err) }); // TN reintenta; el sync diario cubre igual
  }
});
