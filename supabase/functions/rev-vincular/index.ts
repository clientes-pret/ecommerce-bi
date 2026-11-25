// supabase/functions/rev-vincular/index.ts
//
// Vinculación automática de las publicaciones de cada revendedor con los SKU del catálogo.
//
// Para cada revendedor activo: lee SUS publicaciones en Mercado Libre (SOLO LECTURA, con las credenciales que él autorizó),
// reconoce cada una por su `seller_custom_field` (el SKU que cargó al publicar) y deja el resultado en `rev_publicaciones`:
//   - SKU que está en el catálogo habilitado → publicación vinculada (se usa para sincronizar su stock).
//   - SKU que no está, o publicación sin SKU → queda "sin reconocer" (sku nulo): el revendedor la puede vincular a mano
//     desde el portal, y esos vínculos manuales NUNCA se pisan.
// No borra nada, no escribe en Mercado Libre, no toca Ecomm-App ni cuentas de Pret/Lavan.
//
// Seguridad: igual que rev-sync, la llamada lleva ?t=<token> y se valida contra la base (Vault). La dispara la base.

import { createClient } from "jsr:@supabase/supabase-js@2";
import { COLUMNAS_REVENDEDOR, dormir, mlGet, type Revendedor, tokenValido } from "../_shared/rev_ml.ts";
import { completarPorProductoDeUsuario, type ItemML, normSku, planificarVinculos, type PubExistente, vinculosDeItem } from "./logic.ts";

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const MAX_PUBLICACIONES_POR_REVENDEDOR = 1000; // límite de paginación de ML por offset; más allá habría que usar `scan`

type Json = Record<string, unknown>;

async function leerTodo<T>(tabla: string, columnas: string, orden: string, filtro?: (q: any) => any): Promise<T[]> {
  const out: T[] = [];
  for (let desde = 0;; desde += 1000) {
    let q = supabase.from(tabla).select(columnas);
    if (filtro) q = filtro(q);
    const { data, error } = await q.order(orden, { ascending: true }).range(desde, desde + 999);
    if (error) throw new Error(`${tabla}: ${error.message}`);
    out.push(...((data ?? []) as T[]));
    if (!data || data.length < 1000) break;
  }
  return out;
}

/** Ids de todas las publicaciones del revendedor (cualquier estado). */
async function idsDePublicaciones(token: string, mlUserId: number): Promise<{ ids: string[]; truncado: boolean } | null> {
  const ids: string[] = [];
  let total = 0;
  for (let offset = 0; offset < MAX_PUBLICACIONES_POR_REVENDEDOR; offset += 100) {
    const r = await mlGet<{ results: string[]; paging: { total: number } }>(token, `/users/${mlUserId}/items/search?limit=100&offset=${offset}`);
    if (!r.body) throw new Error(`ML lista de publicaciones -> HTTP ${r.status}`);
    ids.push(...r.body.results);
    total = r.body.paging?.total ?? ids.length;
    if (r.body.results.length < 100 || ids.length >= total) break;
    await dormir(300);
  }
  return { ids, truncado: total > MAX_PUBLICACIONES_POR_REVENDEDOR };
}

async function detalles(token: string, ids: string[]): Promise<ItemML[] | null> {
  const out: ItemML[] = [];
  for (let i = 0; i < ids.length; i += 20) {
    const lote = ids.slice(i, i + 20);
    const atributos = "id,status,price,seller_custom_field,user_product_id,variations,attributes";
    const r = await mlGet<Array<{ code: number; body: ItemML }>>(token, `/items?ids=${lote.join(",")}&attributes=${atributos}`);
    if (r.body) {
      for (const x of r.body) if (x.code === 200 && x.body) out.push(x.body);
    } else if (r.status === 403) {
      // Desde los servidores de Supabase ML puede rechazar la lectura de varias publicaciones juntas (desde otras
      // conexiones sí la acepta): se lee de a una.
      for (const id of lote) {
        const u = await mlGet<ItemML>(token, `/items/${id}?attributes=${atributos}`);
        if (!u.body) throw new Error(`ML detalle de publicación ${id} -> HTTP ${u.status}`);
        out.push(u.body);
        await dormir(150);
      }
    } else {
      throw new Error(`ML detalle de publicaciones -> HTTP ${r.status}`);
    }
    await dormir(300);
  }
  return out;
}

async function vincularRevendedor(rev: Revendedor, catalogo: Map<string, string>): Promise<Json> {
  const token = await tokenValido(supabase, rev);
  if (!token) return { revendedor: rev.nickname, ok: false, motivo: "sin credencial vigente" };

  const lista = await idsDePublicaciones(token, rev.ml_user_id);
  if (!lista) return { revendedor: rev.nickname, ok: false, motivo: "no se pudo leer la lista de publicaciones" };
  const items = await detalles(token, lista.ids);
  if (!items) return { revendedor: rev.nickname, ok: false, motivo: "no se pudo leer el detalle de publicaciones" };

  // Las cerradas no se pueden reactivar ni vender: no se vinculan.
  // Las filas sin SKU toman el de su publicación "sincronizada" (mismo producto de usuario), aunque esa esté cerrada.
  const todas = items.flatMap(vinculosDeItem);
  const vinculos = completarPorProductoDeUsuario(items.filter((it) => it.status !== "closed").flatMap(vinculosDeItem), todas);
  const existentes = await leerTodo<PubExistente>(
    "rev_publicaciones",
    "id,ml_item_id,ml_variation_id,sku,vinculo,estado_ml,precio",
    "id",
    (q) => q.eq("revendedor_id", rev.id),
  );
  const plan = planificarVinculos(vinculos, catalogo, existentes);

  const ahora = new Date().toISOString();
  for (let i = 0; i < plan.nuevas.length; i += 200) {
    const filas = plan.nuevas.slice(i, i + 200).map((n) => ({ ...n, revendedor_id: rev.id, vinculo: "auto" }));
    const { error } = await supabase.from("rev_publicaciones").insert(filas);
    if (error && error.code !== "23505") throw new Error(`rev_publicaciones insert: ${error.message}`);
  }
  // `id` es "generated always": no admite upsert. La actualización va en lote por una función de la base.
  for (let i = 0; i < plan.actualizar.length; i += 200) {
    const { error } = await supabase.rpc("rev_publicaciones_actualizar", { p: plan.actualizar.slice(i, i + 200) });
    if (error) throw new Error(`rev_publicaciones update: ${error.message}`);
  }

  return {
    revendedor: rev.nickname,
    ok: true,
    publicaciones_en_ml: lista.ids.length,
    truncado: lista.truncado,
    vinculos_leidos: vinculos.length,
    nuevas: plan.nuevas.length,
    actualizadas: plan.actualizar.length,
    sin_cambios: plan.sin_cambios,
    sin_reconocer: plan.sin_reconocer,
  };
}

async function vincular(): Promise<Json> {
  const t0 = Date.now();
  const catalogo = new Map<string, string>();
  for (const c of await leerTodo<{ sku: string }>("rev_catalogo", "sku", "sku", (q) => q.eq("habilitado", true))) {
    catalogo.set(normSku(c.sku), c.sku);
  }
  const revs = await leerTodo<Revendedor>("rev_revendedores", COLUMNAS_REVENDEDOR, "id", (q) => q.eq("estado", "activo"));

  const resultados: Json[] = [];
  for (const rev of revs) {
    try {
      resultados.push(await vincularRevendedor(rev, catalogo));
    } catch (e) {
      const mensaje = e instanceof Error ? e.message : String(e);
      console.error(`rev-vincular ${rev.ml_user_id}: ${mensaje}`);
      resultados.push({ revendedor: rev.nickname, ok: false, motivo: mensaje });
    }
  }
  const fallas = resultados.filter((r) => !r.ok).length;
  const detalle = { catalogo: catalogo.size, revendedores: revs.length, fallas, segundos: Math.round((Date.now() - t0) / 100) / 10, resultados };
  await supabase.from("rev_sync_estado").upsert({
    clave: "vinculacion",
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
    return new Response(JSON.stringify(await vincular()), { headers: { "Content-Type": "application/json" } });
  } catch (e) {
    const mensaje = e instanceof Error ? e.message : String(e);
    console.error("rev-vincular:", mensaje);
    return new Response(JSON.stringify({ ok: false, error: mensaje }), { status: 500, headers: { "Content-Type": "application/json" } });
  }
});
