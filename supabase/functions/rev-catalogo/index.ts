// supabase/functions/rev-catalogo/index.ts
//
// Actualización diaria del catálogo de revendedores con los SKU de Ecomm-App (pedido del usuario, 24/11/2026).
//
// Lee TODAS las variantes de Ecomm-App (SOLO LECTURA: GET /productos) y suma a `rev_catalogo` las que faltan, si son
// simples (no combo) y están en Tiendanube con costo (`rev_catalogo_info`, que actualiza cada 6 h el workflow
// rev_pret_publicaciones.yml con reposicion/rev_contenido_sync.py). Solo INSERTA: nunca cambia ni borra filas del
// catálogo (un SKU deshabilitado a mano sigue deshabilitado). No escribe en Ecomm-App ni en Mercado Libre.
//
// La dispara la base una vez por día (cron `rev-catalogo-dia`) a las 07:02 AR, entre dos corridas de rev-sync, que
// también lee Ecomm-App: así las dos juntas no pasan las 120 lecturas por minuto del token.
// Seguridad: igual que rev-sync, la llamada lleva ?t=<token> y se valida contra la base (Vault).

import { createClient } from "jsr:@supabase/supabase-js@2";
import { dormir } from "../_shared/rev_ml.ts";
import { type InfoSku, norm, skusParaSumar, type VarianteEcomm } from "./logic.ts";

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const ECOMM_TOKEN = Deno.env.get("ECOMMAPP_TOKEN") ?? "";
const ECOMM_PRODUCTOS = "https://admin.ecomm-app.com/api/v3/public/inventario/productos";
const MINIMO_VARIANTES = 1000; // si Ecomm-App devuelve menos, algo anda mal: no se suma nada

type Json = Record<string, unknown>;

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

/** Todas las variantes de Ecomm-App, de a 50 productos por página. Es solo lectura: reintentar es seguro. */
async function variantesEcomm(): Promise<{ variantes: VarianteEcomm[]; paginas: number }> {
  const variantes: VarianteEcomm[] = [];
  let total = 1;
  for (let pagina = 1; pagina <= total; pagina++) {
    let d: { items?: Array<{ variants?: VarianteEcomm[] }>; pagination?: { total_pages?: number } } | null = null;
    for (let intento = 0; intento < 5 && !d; intento++) {
      const r = await fetch(`${ECOMM_PRODUCTOS}?page=${pagina}&limit=50&sort=sku_asc`, { headers: { Authorization: `Bearer ${ECOMM_TOKEN}` } });
      if (r.status === 429 || r.status >= 500) { await dormir(3000 * (intento + 1)); continue; }
      if (!r.ok) throw new Error(`Ecomm-App productos página ${pagina} -> ${r.status}`);
      d = (await r.json()).data ?? {};
    }
    if (!d) throw new Error(`Ecomm-App no responde (página ${pagina})`);
    for (const p of d.items ?? []) for (const v of p.variants ?? []) variantes.push({ sku: v.sku, is_combo: Boolean(v.is_combo) });
    total = Number(d.pagination?.total_pages ?? pagina);
    await dormir(700); // ~85 lecturas/min, debajo de las 120 del token
  }
  return { variantes, paginas: total };
}

async function actualizar(): Promise<Json> {
  const t0 = Date.now();
  if (!ECOMM_TOKEN) throw new Error("Falta ECOMMAPP_TOKEN");
  const { variantes, paginas } = await variantesEcomm();
  if (variantes.length < MINIMO_VARIANTES) throw new Error(`Ecomm-App devolvió solo ${variantes.length} variantes: no se toca el catálogo`);

  const catalogo = new Set((await leerTodo<{ sku: string }>("rev_catalogo", "sku", "sku")).map((c) => norm(c.sku)));
  const info = new Map((await leerTodo<InfoSku>("rev_catalogo_info", "sku,costo", "sku")).map((i) => [norm(i.sku), i]));
  const r = skusParaSumar(variantes, catalogo, info);

  const origen = `ecomm_auto_${new Date().toISOString().slice(0, 10)}`;
  for (let i = 0; i < r.nuevos.length; i += 500) {
    const filas = r.nuevos.slice(i, i + 500).map((sku) => ({ sku, habilitado: true, origen }));
    const { error } = await supabase.from("rev_catalogo").upsert(filas, { onConflict: "sku", ignoreDuplicates: true });
    if (error) throw new Error(`rev_catalogo insert: ${error.message}`);
  }

  const detalle = {
    paginas,
    variantes_ecomm: variantes.length,
    sumados: r.nuevos.length,
    sumados_ejemplos: r.nuevos.slice(0, 30),
    ya_estaban: r.ya_estaban,
    combos: r.combos,
    sin_tiendanube: r.sin_tiendanube,
    sin_costo: r.sin_costo,
    segundos: Math.round((Date.now() - t0) / 100) / 10,
  };
  await supabase.from("rev_sync_estado").upsert({ clave: "catalogo", ultimo_at: new Date().toISOString(), resultado: "ok", detalle }, { onConflict: "clave" });
  return { ok: true, ...detalle };
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const { data: tokenOk, error: tokenError } = await supabase.rpc("rev_sync_token_valido", { p: url.searchParams.get("t") ?? "" });
  if (tokenError || tokenOk !== true) return new Response("No autorizado", { status: 401 });
  try {
    return new Response(JSON.stringify(await actualizar()), { headers: { "Content-Type": "application/json" } });
  } catch (e) {
    const mensaje = e instanceof Error ? e.message : String(e);
    console.error("rev-catalogo:", mensaje);
    await supabase.from("rev_sync_estado").upsert({ clave: "catalogo", ultimo_at: new Date().toISOString(), resultado: "error", detalle: { error: mensaje } }, { onConflict: "clave" });
    return new Response(JSON.stringify({ ok: false, error: mensaje }), { status: 500, headers: { "Content-Type": "application/json" } });
  }
});
