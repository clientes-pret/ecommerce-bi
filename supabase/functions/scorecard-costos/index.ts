// supabase/functions/scorecard-costos/index.ts
//
// Actualiza `scorecard_costos_ecomapp` (los costos que usa el Scorecard de Mercado Libre) con los de Ecomm-App.
//
// La llama reposicion/scorecard_ml_sync.py antes de calcular. Vive acá porque el token de Ecomm-App ya está cargado
// como secreto de las funciones (el mismo de revendedores). SOLO LECTURA en Ecomm-App (GET /productos). En la base
// solo agrega o pisa costos: un SKU que deja de venir conserva el último. `?simular=1` no escribe nada.
// Seguridad: verify_jwt = true y además exige la clave de servicio (la clave pública del proyecto no alcanza).

import { createClient } from "jsr:@supabase/supabase-js@2";
import { comparar, costosPorSku, rolDelToken, type VarianteCosto } from "./logic.ts";

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const ECOMM_TOKEN = Deno.env.get("ECOMMAPP_TOKEN") ?? "";
const ECOMM_PRODUCTOS = "https://admin.ecomm-app.com/api/v3/public/inventario/productos";
const TABLA = "scorecard_costos_ecomapp";
const MINIMO_SKUS = 1000; // si vienen menos con costo, algo anda mal (o el token dejó de ver costos): no se toca nada

const dormir = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Todas las variantes de Ecomm-App, de a 50 productos por página. Es solo lectura: reintentar es seguro. */
async function variantesEcomm(): Promise<VarianteCosto[]> {
  const variantes: VarianteCosto[] = [];
  let total = 1;
  for (let pagina = 1; pagina <= total; pagina++) {
    let d: { items?: Array<{ variants?: VarianteCosto[] }>; pagination?: { total_pages?: number } } | null = null;
    for (let intento = 0; intento < 5 && !d; intento++) {
      const r = await fetch(`${ECOMM_PRODUCTOS}?page=${pagina}&limit=50&sort=sku_asc`, { headers: { Authorization: `Bearer ${ECOMM_TOKEN}` } });
      if (r.status === 429 || r.status >= 500) { await dormir(3000 * (intento + 1)); continue; }
      if (!r.ok) throw new Error(`Ecomm-App productos página ${pagina} -> ${r.status}`);
      d = (await r.json()).data ?? {};
    }
    if (!d) throw new Error(`Ecomm-App no responde (página ${pagina})`);
    for (const p of d.items ?? []) variantes.push(...(p.variants ?? []));
    total = Number(d.pagination?.total_pages ?? pagina);
    await dormir(900); // el token (120 lecturas/min) lo comparte con la sincronización de revendedores
  }
  return variantes;
}

async function guardados(): Promise<Map<string, number>> {
  const out = new Map<string, number>();
  for (let desde = 0;; desde += 1000) {
    const { data, error } = await supabase.from(TABLA).select("sku,costo").order("sku").range(desde, desde + 999);
    if (error) throw new Error(`${TABLA}: ${error.message}`);
    for (const f of data ?? []) out.set(f.sku, Number(f.costo));
    if (!data || data.length < 1000) break;
  }
  return out;
}

async function actualizar(simular: boolean) {
  if (!ECOMM_TOKEN) throw new Error("Falta ECOMMAPP_TOKEN");
  const variantes = await variantesEcomm();
  const { costos, repetidos } = costosPorSku(variantes);
  if (costos.size < MINIMO_SKUS) throw new Error(`Ecomm-App devolvió solo ${costos.size} SKUs con costo: no se actualiza la lista`);
  const cambios = comparar(costos, await guardados());
  if (!simular) {
    const ahora = new Date().toISOString();
    const filas = [...costos].map(([sku, costo]) => ({ sku, costo, actualizado_en: ahora }));
    for (let i = 0; i < filas.length; i += 500) {
      const { error } = await supabase.from(TABLA).upsert(filas.slice(i, i + 500), { onConflict: "sku" });
      if (error) throw new Error(`${TABLA} upsert: ${error.message}`);
    }
  }
  return {
    ok: true,
    simulacion: simular,
    variantes: variantes.length,
    con_costo: costos.size,
    nuevos: cambios.agregados,
    cambiados: cambios.cambiados.length,
    cambiados_ejemplos: cambios.cambiados.slice(0, 30),
    solo_en_la_lista: cambios.noVienen,
    repetidos: repetidos.length,
  };
}

const json = (cuerpo: unknown, status = 200) => new Response(JSON.stringify(cuerpo), { status, headers: { "Content-Type": "application/json" } });

Deno.serve(async (req) => {
  if (rolDelToken(req.headers.get("Authorization")) !== "service_role") return json({ ok: false, error: "No autorizado" }, 401);
  try {
    return json(await actualizar(new URL(req.url).searchParams.get("simular") === "1"));
  } catch (e) {
    const mensaje = e instanceof Error ? e.message : String(e);
    console.error("scorecard-costos:", mensaje);
    return json({ ok: false, error: mensaje }, 500);
  }
});
