// supabase/functions/rev-sync/index.ts
//
// Sincronización de stock hacia los revendedores (flujo 2 del plan: tus ventas propias bajan el stock en Ecomm-App
// y los revendedores tienen que enterarse, porque Ecomm-App no avisa nada).
//
// Cada corrida:
//   1. Lee de Ecomm-App el stock del depósito de los SKU de `rev_catalogo` (SOLO LECTURA: POST /productos/consulta).
//   2. Calcula lo que se ofrece = stock − colchón (logic.ts) y lo deja en `rev_stock_ofrecido`.
//   3. Compara con lo último que se le mandó a cada publicación vinculada (`rev_publicaciones`).
//   4. MODO SOMBRA (`rev_config.modo = 'sombra'`, el de arranque): solo registra en `rev_sync_estado` qué cambiaría.
//      Nada se escribe en Ecomm-App ni en Mercado Libre.
//      MODO ACTIVO: además actualiza la cantidad de esas publicaciones en la cuenta de cada revendedor.
//      (Ese tramo se probó a mano con `PUT /items/{id}` pero no desde esta función; no activar sin probarlo antes.)
//
// Esta función NUNCA escribe en Ecomm-App ni toca cuentas de Pret/Lavan: lee Ecomm-App y, en modo activo, escribe solo
// en publicaciones de cuentas de revendedores conectadas.
//
// Seguridad: la llamada lleva ?t=<token>; la función le pregunta a la base (`rev_sync_token_valido`, token en Vault) si es
// válido y rechaza todo lo demás (falla cerrada). Solo la llama `disparar_rev_sync()` de la base.

import { createClient } from "jsr:@supabase/supabase-js@2";
import { COLUMNAS_REVENDEDOR, dormir, ponerStockML, type Revendedor, tokenValido } from "../_shared/rev_ml.ts";
import { enLotes, publicacionesAActualizar, stockOfrecido } from "./logic.ts";

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const ECOMM_TOKEN = Deno.env.get("ECOMMAPP_TOKEN") ?? "";
const ECOMM_CONSULTA = "https://admin.ecomm-app.com/api/v3/public/inventario/productos/consulta";
const MAX_ACTUALIZACIONES_POR_CORRIDA = 300;
const MINIMO_ENCONTRADOS = 0.5; // si Ecomm-App devuelve menos de la mitad del catálogo, algo anda mal: no se actualiza nada

type Json = Record<string, unknown>;
const norm = (s: string) => s.trim().toLowerCase();

async function leerTodo<T>(tabla: string, columnas: string, orden: string): Promise<T[]> {
  const out: T[] = [];
  for (let desde = 0;; desde += 1000) {
    const { data, error } = await supabase.from(tabla).select(columnas).order(orden, { ascending: true }).range(desde, desde + 999);
    if (error) throw new Error(`${tabla}: ${error.message}`);
    out.push(...((data ?? []) as T[]));
    if (!data || data.length < 1000) break;
  }
  return out;
}

interface FilaEcomm {
  sku: string;
  stock: number;
  combo: boolean;
}

/** Lectura de stock en Ecomm-App, de a 100 SKU. Es solo lectura: reintentar es seguro. */
async function stockEcomm(skus: string[], warehouseId: number): Promise<Map<string, FilaEcomm>> {
  const out = new Map<string, FilaEcomm>();
  for (const lote of enLotes(skus, 100)) {
    let listo = false;
    for (let intento = 0; intento < 4 && !listo; intento++) {
      const r = await fetch(ECOMM_CONSULTA, {
        method: "POST",
        headers: { Authorization: `Bearer ${ECOMM_TOKEN}`, "Content-Type": "application/json" },
        body: JSON.stringify({ skus: lote, warehouse_id: warehouseId }),
      });
      if (r.status === 429 || r.status >= 500) {
        await dormir(1500 * (intento + 1));
        continue;
      }
      if (!r.ok) throw new Error(`Ecomm-App consulta -> ${r.status}`);
      const d = (await r.json()).data?.items ?? [];
      for (const p of d) {
        for (const v of p.variants ?? []) {
          out.set(norm(v.sku), { sku: v.sku, stock: Number(v.total_stock ?? 0), combo: Boolean(v.is_combo) });
        }
      }
      listo = true;
    }
    if (!listo) throw new Error("Ecomm-App no responde");
    await dormir(600); // 120 lecturas/min por token: sobra margen
  }
  return out;
}

async function guardarEstado(resultado: string, detalle: Json) {
  await supabase.from("rev_sync_estado").upsert({
    clave: "stock",
    ultimo_at: new Date().toISOString(),
    resultado,
    detalle,
  }, { onConflict: "clave" });
}

async function sincronizar(): Promise<Json> {
  const t0 = Date.now();
  const cfgRows = await leerTodo<{ clave: string; valor: unknown }>("rev_config", "clave,valor", "clave");
  const cfg = Object.fromEntries(cfgRows.map((c) => [c.clave, c.valor]));
  const colchon = Number(cfg.colchon ?? 5);
  const warehouseId = Number(cfg.warehouse_id ?? 1083);
  const modo = cfg.modo === "activo" ? "activo" : "sombra";

  const catalogo = (await leerTodo<{ sku: string; habilitado: boolean; colchon_override: number | null }>(
    "rev_catalogo",
    "sku,habilitado,colchon_override",
    "sku",
  )).filter((c) => c.habilitado);
  if (!catalogo.length) {
    await guardarEstado("sin_catalogo", { modo });
    return { ok: true, modo, aviso: "catálogo vacío" };
  }

  const eco = await stockEcomm(catalogo.map((c) => c.sku), warehouseId);
  const ahora = new Date().toISOString();
  const ofrecidos = new Map<string, number>();
  const filas: Json[] = [];
  let sinDato = 0;
  let combos = 0;
  for (const c of catalogo) {
    const e = eco.get(norm(c.sku));
    if (!e) {
      sinDato++;
      continue;
    }
    if (e.combo) {
      combos++;
      continue;
    }
    const oferta = stockOfrecido(e.stock, c.colchon_override ?? colchon);
    ofrecidos.set(norm(c.sku), oferta);
    filas.push({ sku: c.sku, stock_ecomm: e.stock, ofrecido: oferta, consultado_at: ahora });
  }

  // Protección: si Ecomm-App devolvió muy poco, no se confía en la lectura y no se actualiza nada.
  if (filas.length < catalogo.length * MINIMO_ENCONTRADOS) {
    const detalle = { modo, catalogo: catalogo.length, encontrados: filas.length, sinDato, combos };
    await guardarEstado("lectura_incompleta", detalle);
    return { ok: false, motivo: "lectura incompleta de Ecomm-App; no se actualizó nada", ...detalle };
  }
  for (const lote of enLotes(filas, 500)) {
    const { error } = await supabase.from("rev_stock_ofrecido").upsert(lote, { onConflict: "sku" });
    if (error) throw new Error(`rev_stock_ofrecido: ${error.message}`);
  }

  const revs = await leerTodo<Revendedor>("rev_revendedores", COLUMNAS_REVENDEDOR, "id");
  const activos = new Map(revs.filter((r) => r.estado === "activo").map((r) => [r.id, r]));
  // `sku` puede ser nulo (publicación todavía sin reconocer, ver migración del portal): esas no se sincronizan.
  const pubs = (await leerTodo<{
    id: number;
    revendedor_id: number;
    ml_item_id: string;
    ml_variation_id: number | null;
    sku: string | null;
    ultima_cantidad_enviada: number | null;
  }>("rev_publicaciones", "id,revendedor_id,ml_item_id,ml_variation_id,sku,ultima_cantidad_enviada", "id"))
    .filter((p) => activos.has(p.revendedor_id) && p.sku)
    .map((p) => ({ ...p, sku: p.sku as string }));

  const cambios = publicacionesAActualizar(ofrecidos, pubs.map((p) => ({ ...p, sku: norm(p.sku) })));
  const resumen: Json = {
    modo,
    catalogo: catalogo.length,
    con_stock_leido: filas.length,
    sin_dato_en_ecomm: sinDato,
    combos_omitidos: combos,
    con_oferta_hoy: filas.filter((f) => (f.ofrecido as number) > 0).length,
    unidades_ofrecidas: filas.reduce((s, f) => s + (f.ofrecido as number), 0),
    publicaciones_vinculadas: pubs.length,
    publicaciones_a_actualizar: cambios.length,
  };

  if (modo === "sombra") {
    resumen.haria = cambios.slice(0, 10).map((c) => ({ item: c.ml_item_id, sku: c.sku, de: c.ultima_cantidad_enviada, a: c.nueva }));
    resumen.segundos = Math.round((Date.now() - t0) / 100) / 10;
    await guardarEstado("sombra_ok", resumen);
    return { ok: true, ...resumen };
  }

  // MODO ACTIVO: actualizar publicaciones de revendedores (tope por corrida; el resto sigue en la próxima).
  let enviadas = 0;
  let fallidas = 0;
  const porRevendedor = new Map<number, typeof cambios>();
  for (const c of cambios.slice(0, MAX_ACTUALIZACIONES_POR_CORRIDA)) {
    porRevendedor.set(c.revendedor_id, [...(porRevendedor.get(c.revendedor_id) ?? []), c]);
  }
  for (const [revId, lista] of porRevendedor) {
    const token = await tokenValido(supabase, activos.get(revId)!);
    if (!token) {
      fallidas += lista.length;
      continue;
    }
    for (const c of lista) {
      const ok = await ponerStockML(token, c.ml_item_id, c.ml_variation_id, c.nueva);
      if (ok) {
        enviadas++;
        await supabase.from("rev_publicaciones").update({
          ultima_cantidad_enviada: c.nueva,
          ultimo_envio_at: new Date().toISOString(),
          actualizado_at: new Date().toISOString(),
        }).eq("id", c.id);
      } else {
        fallidas++;
      }
      await dormir(250);
    }
  }
  resumen.enviadas = enviadas;
  resumen.fallidas = fallidas;
  resumen.segundos = Math.round((Date.now() - t0) / 100) / 10;
  await guardarEstado(fallidas ? "activo_con_fallas" : "activo_ok", resumen);
  return { ok: true, ...resumen };
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  // El token de disparo vive en Vault (base); acá solo se pregunta si el recibido es válido. Falla cerrada ante cualquier error.
  const { data: tokenOk, error: tokenError } = await supabase.rpc("rev_sync_token_valido", { p: url.searchParams.get("t") ?? "" });
  if (tokenError || tokenOk !== true) {
    return new Response("No autorizado", { status: 401 });
  }
  if (!ECOMM_TOKEN) return new Response("Falta ECOMMAPP_TOKEN", { status: 503 });
  try {
    return new Response(JSON.stringify(await sincronizar()), { headers: { "Content-Type": "application/json" } });
  } catch (e) {
    const mensaje = e instanceof Error ? e.message : String(e);
    console.error("rev-sync:", mensaje);
    await guardarEstado("error", { mensaje }).catch(() => {});
    return new Response(JSON.stringify({ ok: false, error: mensaje }), { status: 500, headers: { "Content-Type": "application/json" } });
  }
});
