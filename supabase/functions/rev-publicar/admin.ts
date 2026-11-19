// supabase/functions/rev-publicar/admin.ts
//
// Datos del portal de ADMINISTRADOR de revendedores (19/11/2026, pedido del usuario). Solo admin/dueño del panel.
//   POST /admin/resumen        → revendedores, líneas de venta (12 meses), pagos y estado de los procesos: con eso el
//                                portal arma el Dashboard y la Administración (por revendedor, por semana y por mes).
//   POST /admin/publicaciones  → todas las publicaciones de revendedores vinculadas a un SKU del catálogo.
// Solo lee la base (con la clave de servicio): no toca Mercado Libre ni Ecomm-App.

import { supabase } from "./nucleo.ts";

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

const CUENTAS_PROPIAS = async (): Promise<number[]> => {
  const { data } = await supabase.from("rev_config").select("valor").eq("clave", "cuentas_propias").maybeSingle();
  return Array.isArray(data?.valor) ? (data!.valor as unknown[]).map(Number) : [];
};

/** Revendedores (sin las cuentas propias de Pret/Lavan, que están conectadas solo para leer). */
async function revendedores() {
  const propias = await CUENTAS_PROPIAS();
  const { data, error } = await supabase.from("rev_revendedores").select("id,ml_user_id,nickname,estado,conectado_at").order("nickname");
  if (error) throw new Error(error.message);
  return ((data ?? []) as { id: number; ml_user_id: number; nickname: string | null; estado: string; conectado_at: string | null }[])
    .filter((r) => !propias.includes(Number(r.ml_user_id)));
}

export async function resumen() {
  const revs = await revendedores();
  const ids = revs.map((r) => r.id);
  const desde = new Date(Date.now() - 366 * 86400_000).toISOString();
  const [ventas, pagos, pubs, sync, config] = await Promise.all([
    ids.length ? leerTodo<Record<string, unknown>>("rev_ordenes", "id,revendedor_id,ml_order_id,sku,cantidad,precio_unitario,precio_mayorista,estado,despachada,devolucion,creado_at", "id",
      (q) => q.in("revendedor_id", ids).gte("creado_at", desde)) : [],
    ids.length ? leerTodo<Record<string, unknown>>("rev_pagos", "id,revendedor_id,fecha,monto,nota", "id", (q) => q.in("revendedor_id", ids)) : [],
    ids.length ? leerTodo<{ revendedor_id: number; sku: string | null; estado_ml: string | null }>("rev_publicaciones", "id,revendedor_id,sku,estado_ml", "id", (q) => q.in("revendedor_id", ids)) : [],
    supabase.from("rev_sync_estado").select("clave,ultimo_at,resultado"),
    supabase.from("rev_config").select("clave,valor").in("clave", ["modo", "margen_mayorista", "colchon", "comision_ml_estimada"]),
  ]);
  const skus = [...new Set((ventas as { sku: string }[]).map((v) => v.sku))];
  const titulos: Record<string, string> = {};
  for (let i = 0; i < skus.length; i += 200) {
    const { data } = await supabase.from("rev_catalogo_info").select("sku,titulo").in("sku", skus.slice(i, i + 200));
    for (const t of (data ?? []) as { sku: string; titulo: string }[]) titulos[t.sku] = t.titulo;
  }
  // Publicaciones por revendedor: total, vinculadas, activas vinculadas, SKUs distintos.
  const porRev: Record<number, { total: number; vinculadas: number; activas: number; skus: Set<string> }> = {};
  for (const p of pubs) {
    const r = (porRev[p.revendedor_id] ??= { total: 0, vinculadas: 0, activas: 0, skus: new Set() });
    r.total++;
    if (p.sku) { r.vinculadas++; r.skus.add(p.sku); if (p.estado_ml === "active") r.activas++; }
  }
  const skusPublicados = new Set(pubs.filter((p) => p.sku).map((p) => p.sku!));
  return {
    revendedores: revs.map((r) => ({ ...r, publicaciones: { total: porRev[r.id]?.total ?? 0, vinculadas: porRev[r.id]?.vinculadas ?? 0, activas: porRev[r.id]?.activas ?? 0, skus: porRev[r.id]?.skus.size ?? 0 } })),
    ventas, pagos, titulos,
    skus_publicados: skusPublicados.size,
    procesos: sync.data ?? [],
    config: Object.fromEntries(((config.data ?? []) as { clave: string; valor: unknown }[]).map((c) => [c.clave, c.valor])),
  };
}

export async function publicaciones() {
  const revs = await revendedores();
  const nick = new Map(revs.map((r) => [r.id, r.nickname]));
  const ids = revs.map((r) => r.id);
  if (!ids.length) return { publicaciones: [] };
  const pubs = await leerTodo<Record<string, unknown> & { sku: string; revendedor_id: number }>("rev_publicaciones",
    "id,revendedor_id,ml_item_id,ml_variation_id,sku,estado_ml,precio,vinculo,ultima_cantidad_enviada,ultimo_envio_at,creado_at,actualizado_at", "id",
    (q) => q.in("revendedor_id", ids).not("sku", "is", null));
  const skus = [...new Set(pubs.map((p) => p.sku))];
  const info = new Map<string, { titulo?: string; foto?: string }>(), cat = new Map<string, { precio_minimo: number | null; habilitado: boolean }>(), stock = new Map<string, number>();
  for (let i = 0; i < skus.length; i += 200) {
    const lote = skus.slice(i, i + 200);
    const [a, b, c] = await Promise.all([
      supabase.from("rev_catalogo_info").select("sku,titulo,fotos").in("sku", lote),
      supabase.from("rev_catalogo").select("sku,precio_minimo,habilitado").in("sku", lote),
      supabase.from("rev_stock_ofrecido").select("sku,ofrecido").in("sku", lote),
    ]);
    for (const x of (a.data ?? []) as { sku: string; titulo: string; fotos: string[] }[]) info.set(x.sku, { titulo: x.titulo, foto: Array.isArray(x.fotos) ? x.fotos[0] : undefined });
    for (const x of (b.data ?? []) as { sku: string; precio_minimo: number | null; habilitado: boolean }[]) cat.set(x.sku, x);
    for (const x of (c.data ?? []) as { sku: string; ofrecido: number }[]) stock.set(x.sku, Number(x.ofrecido));
  }
  return {
    publicaciones: pubs.map((p) => {
      const c = cat.get(p.sku), min = c?.precio_minimo != null ? Number(c.precio_minimo) : null;
      return {
        ...p, revendedor: nick.get(p.revendedor_id) ?? null, titulo: info.get(p.sku)?.titulo ?? null, foto: info.get(p.sku)?.foto ?? null,
        en_catalogo: !!c?.habilitado, ofrecido: stock.get(p.sku) ?? null, precio_minimo: min,
        bajo_minimo: min != null && p.precio != null && Number(p.precio) < min,
      };
    }),
  };
}
