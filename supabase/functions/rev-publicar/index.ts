// supabase/functions/rev-publicar/index.ts
//
// "Publicar en mi cuenta" del portal de revendedores. Rutas que llama el navegador con la sesión del revendedor:
//   POST /preparar  {sku}     → la publicación de Pret de ese producto con todos sus colores (stock, costo mayorista,
//                               mínimo, catálogo, si ya lo publicó).
//   POST /publicar  {...}     → publica UN producto en el momento (tradicional y/o catálogo, Clásica/Premium, marca).
//   POST /encolar   {skus, …} → publicación masiva: deja los productos elegidos en la cola (rev_publicar_cola).
//   POST /lotes               → avance de sus publicaciones masivas.
//   POST /cancelar  {lote}    → cancela lo que todavía no se publicó de un lote.
// Y el proceso por detrás: la base lo dispara cada minuto con ?t=<token> (pg_cron → disparar_rev_funcion) y publica lo
// pendiente de la cola. Todo lo que manda el navegador se vuelve a controlar acá (ver nucleo.ts y logic.ts).

import { COLUMNAS_REVENDEDOR, type Revendedor, tokenValido } from "../_shared/rev_ml.ts";
import { esMarcaPropia, GANANCIA_NETA_POR_DEFECTO, leerOpcionesMasivas, leerPedido, MAX_PRODUCTOS_POR_LOTE, TIPOS_VENTA } from "./logic.ts";
import {
  coloresDe, comisionMLEstimada, datosSkus, detalleCatalogo, ErrorUsuario, type Json, leerItemPret, marcasPropias, procesarCola,
  publicacionesPret, publicarProducto, supabase, tokenPret,
} from "./nucleo.ts";
import { marcaDe } from "./logic.ts";
import { etiquetas, listarVentas, type Quien } from "./ventas.ts";
import {
  cambiarDescuento, cambiarEstado, descontarVenta, guardarFicha, guardarLogo, linkConectar, publicaciones as publicacionesAdmin, resumen as resumenAdmin,
} from "./admin.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
const fallo = (error: string, status = 400) => json({ error }, status);

async function revendedorDe(req: Request): Promise<{ rev: Revendedor; jwt: string } | Response> {
  const jwt = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!jwt) return fallo("Tenés que entrar al portal.", 401);
  const { data: u, error } = await supabase.auth.getUser(jwt);
  if (error || !u?.user) return fallo("Tu sesión venció: volvé a entrar.", 401);
  const { data: vin } = await supabase.from("rev_usuarios").select("revendedor_id").eq("user_id", u.user.id).maybeSingle();
  if (!vin) return fallo("Tu usuario no es de un revendedor.", 403);
  const { data: rev } = await supabase.from("rev_revendedores").select(COLUMNAS_REVENDEDOR).eq("id", vin.revendedor_id).maybeSingle();
  if (!rev) return fallo("No encontré tu cuenta.", 403);
  if ((rev as Revendedor).estado === "inactivo") return fallo("Tu cuenta está desactivada: hablá con Pret a Home.", 403);
  if ((rev as Revendedor).estado !== "activo") return fallo("Tu cuenta de Mercado Libre no está conectada: pedile a Pret a Home el link para reconectarla.", 409);
  return { rev: rev as Revendedor, jwt };
}

/** Para Ventas: un revendedor (aunque su cuenta esté desconectada, para que vea lo registrado) o el admin/dueño del panel. */
async function quienLlama(req: Request): Promise<Quien | Response> {
  const jwt = (req.headers.get("Authorization") ?? "").replace(/^Bearer\s+/i, "");
  if (!jwt) return fallo("Tenés que entrar al portal.", 401);
  const { data: u, error } = await supabase.auth.getUser(jwt);
  if (error || !u?.user) return fallo("Tu sesión venció: volvé a entrar.", 401);
  const { data: vin } = await supabase.from("rev_usuarios").select("revendedor_id").eq("user_id", u.user.id).maybeSingle();
  if (vin) {
    const { data: rev } = await supabase.from("rev_revendedores").select(COLUMNAS_REVENDEDOR).eq("id", vin.revendedor_id).maybeSingle();
    if (!rev) return fallo("No encontré tu cuenta.", 403);
    return { rev: rev as Revendedor, admin: false };
  }
  const { data: perfil } = await supabase.from("cc_perfiles").select("rol").eq("id", u.user.id).maybeSingle();
  if (perfil && ["admin", "dueno"].includes(String(perfil.rol))) return { rev: null, admin: true };
  return fallo("Tu usuario no tiene acceso.", 403);
}

// ───────────────────────── /preparar ─────────────────────────
async function preparar(rev: Revendedor, jwt: string, b: Json): Promise<Response> {
  const sku = String(b.sku ?? "").trim();
  if (!sku) return fallo("Falta el producto.");
  const filas = await publicacionesPret([sku]);
  const tra = filas.find((f) => f.tipo === "tradicional");
  const tok = await tokenPret();
  let item = null;
  let colores: { sku: string; color: string | null; variacion: { price?: number; picture_ids?: string[] } | null }[] = [{ sku, color: null, variacion: null }];
  if (tra) {
    item = await leerItemPret(tok, tra.ml_item_id);
    colores = coloresDe(item);
    if (!colores.some((c) => c.sku === sku)) colores.unshift({ sku, color: null, variacion: null });
  }
  const skus = colores.map((c) => c.sku);
  const [todas, det, propias, ya, comision] = await Promise.all([
    publicacionesPret(skus), detalleCatalogo(jwt, skus), marcasPropias(),
    supabase.from("rev_publicaciones").select("sku").eq("revendedor_id", rev.id).in("sku", skus),
    comisionMLEstimada(),
  ]);
  const yaPor = new Map<string, number>();
  for (const r of (ya.data ?? []) as { sku: string }[]) yaPor.set(r.sku, (yaPor.get(r.sku) ?? 0) + 1);
  const pics = new Map((item?.pictures ?? []).map((p) => [p.id, p.secure_url || p.url || ""]));
  const marca = item ? marcaDe(item) : null;
  return json({
    item: item ? { id: item.id, titulo: item.title, permalink: tra?.permalink, marca, marca_propia: esMarcaPropia(marca, propias) } : null,
    seleccionado: sku,
    tipos_venta: TIPOS_VENTA,
    comision_ml: comision,
    ganancia_defecto: GANANCIA_NETA_POR_DEFECTO,
    colores: colores.map((c) => {
      const d = det.get(c.sku);
      const cat = todas.find((f) => f.sku === c.sku && f.tipo === "catalogo" && f.catalog_product_id);
      return {
        sku: c.sku, color: c.color,
        foto: c.variacion?.picture_ids?.map((i) => pics.get(i)).find(Boolean) ?? null,
        precio_pret: c.variacion?.price ?? item?.price ?? cat?.precio ?? null,
        en_catalogo: !!d,
        ofrecido: d ? Number(d.stock_ofrecido ?? 0) : 0,
        costo_mayorista: d?.precio_mayorista ?? null,
        precio_minimo: d?.precio_minimo ?? null,
        catalogo: cat ? { catalog_product_id: cat.catalog_product_id, ml_item_id: cat.ml_item_id } : null,
        ya_publicadas: yaPor.get(c.sku) ?? 0,
      };
    }),
  });
}

// ───────────────────────── /publicar (un producto, en el momento) ─────────────────────────
async function publicarUno(rev: Revendedor, b: Json): Promise<Response> {
  const lp = leerPedido(b);
  if (!lp.ok) return fallo(lp.error);
  const p = lp.pedido;
  const d = await datosSkus(p.skus, false);
  for (const s of p.skus) {
    if (!d.habilitado(s)) return fallo(`${s} no está en el catálogo para revendedores.`);
    if (d.stock(s) < 1) return fallo(`${s} no tiene stock para ofrecer ahora.`);
    const min = d.minimo(s);
    if (min != null && p.precio < min) return fallo(`El precio mínimo de ${s} es $ ${min.toLocaleString("es-AR")}: no se puede publicar más barato.`);
  }
  const itemId = p.tradicional ? String(b.item_id ?? "") : null;
  const tokPret = await tokenPret();
  if (p.tradicional) {
    const filas = await publicacionesPret(p.skus);
    const deItem = new Set(filas.filter((f) => f.tipo === "tradicional" && f.ml_item_id === itemId).map((f) => f.sku));
    if (!itemId || p.skus.some((s) => !deItem.has(s))) return fallo("Los colores elegidos no son de esa publicación de Pret a Home. Volvé a abrir el producto.");
    if (p.marca) {
      const item = await leerItemPret(tokPret, itemId);
      if (!esMarcaPropia(marcaDe(item), await marcasPropias())) return fallo(`Este producto es de la marca ${marcaDe(item) ?? "de un proveedor"}: solo se puede publicar con su marca original.`);
    }
  }
  const tokRev = await tokenValido(supabase, rev);
  if (!tokRev) return fallo("Tu cuenta de Mercado Libre se desconectó: pedile a Pret a Home el link para reconectarla.", 409);
  const resultados = await publicarProducto(rev, tokRev, tokPret, {
    itemId, skus: p.skus, precios: new Map(p.skus.map((s) => [s, p.precio])), cantidades: new Map(p.skus.map((s) => [s, d.stock(s)])),
    tradicional: p.tradicional, catalogo: p.catalogo, marca: p.marca, tiposVenta: p.tiposVenta, pausada: p.pausada,
  });
  console.log(`rev-publicar ${rev.nickname ?? rev.id}: ${resultados.filter((r) => r.ok).length} creadas, ${resultados.filter((r) => !r.ok).length} con error`);
  return json({ resultados });
}

// ───────────────────────── publicación masiva ─────────────────────────
async function encolar(rev: Revendedor, b: Json): Promise<Response> {
  const lo = leerOpcionesMasivas(b);
  if (!lo.ok) return fallo(lo.error);
  const op = lo.op;
  const elegidos = Array.isArray(b.skus) ? [...new Set(b.skus.map((s) => String(s).trim()).filter(Boolean))] : [];
  if (!elegidos.length) return fallo("Elegí al menos un producto.");
  if (elegidos.length > MAX_PRODUCTOS_POR_LOTE) return fallo(`Son ${elegidos.length} productos; el máximo por vez es ${MAX_PRODUCTOS_POR_LOTE}.`);

  // Se agrupan los colores por publicación de Pret (la más vendida de cada SKU), así cada producto se publica junto.
  const filas = await publicacionesPret(elegidos);
  const grupos = new Map<string, { item_id: string | null; skus: string[] }>();
  const sinPublicacion: string[] = [];
  for (const s of elegidos) {
    const tra = filas.find((f) => f.sku === s && f.tipo === "tradicional");
    const cat = filas.find((f) => f.sku === s && f.tipo === "catalogo" && f.catalog_product_id);
    if (op.tradicional && tra) {
      const k = "t:" + tra.ml_item_id;
      if (!grupos.has(k)) grupos.set(k, { item_id: tra.ml_item_id, skus: [] });
      grupos.get(k)!.skus.push(s);
    } else if (op.catalogo && cat) {
      grupos.set("c:" + s, { item_id: null, skus: [s] });
    } else sinPublicacion.push(s);
  }
  if (!grupos.size) return fallo("Ninguno de los productos elegidos tiene una publicación de Pret a Home del tipo que elegiste.");
  const lote = crypto.randomUUID();
  const opciones = { tradicional: op.tradicional, catalogo: op.catalogo, marca: op.marca, tipos_venta: op.tiposVenta, pausada: op.pausada, regla: op.regla };
  const { error } = await supabase.from("rev_publicar_cola").insert([...grupos.values()].map((g) => ({
    revendedor_id: rev.id, lote, item_id: g.item_id, skus: g.skus, todos_colores: op.todosColores && !!g.item_id, opciones,
  })));
  if (error) throw new Error(error.message);
  return json({ lote, productos: grupos.size, sin_publicacion: sinPublicacion });
}

async function lotes(rev: Revendedor): Promise<Response> {
  const desde = new Date(Date.now() - 7 * 86400_000).toISOString();
  const { data, error } = await supabase.from("rev_publicar_cola")
    .select("id,lote,item_id,skus,estado,resultados,error,creado_at,terminado_at,opciones")
    .eq("revendedor_id", rev.id).gte("creado_at", desde).order("id", { ascending: false }).limit(1000);
  if (error) throw new Error(error.message);
  return json({ filas: data ?? [], comision_ml: await comisionMLEstimada(), ganancia_defecto: GANANCIA_NETA_POR_DEFECTO });
}

async function cancelar(rev: Revendedor, b: Json): Promise<Response> {
  const lote = String(b.lote ?? "");
  if (!/^[0-9a-f-]{36}$/i.test(lote)) return fallo("Falta el lote.");
  const { data, error } = await supabase.from("rev_publicar_cola").update({ estado: "cancelado", terminado_at: new Date().toISOString() })
    .eq("revendedor_id", rev.id).eq("lote", lote).eq("estado", "pendiente").select("id");
  if (error) throw new Error(error.message);
  return json({ cancelados: data?.length ?? 0 });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return fallo("Método no permitido.", 405);
  const url = new URL(req.url);
  const ruta = url.pathname.replace(/\/+$/, "");
  try {
    // Proceso por detrás (cola): lo dispara la base con el token de Vault.
    if (url.searchParams.has("t")) {
      const { data: ok, error } = await supabase.rpc("rev_sync_token_valido", { p: url.searchParams.get("t") ?? "" });
      if (error || ok !== true) return new Response("No autorizado", { status: 401 });
      return json(await procesarCola());
    }
    let body: Json = {};
    // Portal de administrador: solo admin/dueño del panel.
    if (ruta.includes("/admin/")) {
      const q = await quienLlama(req);
      if (q instanceof Response) return q;
      if (!q.admin) return fallo("Solo para el administrador.", 403);
      if (ruta.endsWith("/admin/resumen")) return json(await resumenAdmin());
      if (ruta.endsWith("/admin/publicaciones")) return json(await publicacionesAdmin());
      let b: Json = {};
      try { b = await req.json(); } catch { /* vacío */ }
      try {
        if (ruta.endsWith("/admin/revendedor/estado")) return json(await cambiarEstado(b));
        if (ruta.endsWith("/admin/revendedor/descuento")) return json(await cambiarDescuento(b));
        if (ruta.endsWith("/admin/revendedor/ficha")) return json(await guardarFicha(b));
        if (ruta.endsWith("/admin/revendedor/logo")) return json(await guardarLogo(b));
        if (ruta.endsWith("/admin/descontar")) return json(await descontarVenta(b));
        if (ruta.endsWith("/admin/conectar")) return json(linkConectar());
      } catch (e) { return fallo((e as Error).message, 400); }
      return fallo("No encontrado.", 404);
    }
    // Ventas y etiquetas: las ve el revendedor (las suyas) y el admin/dueño del panel (las de todos).
    if (ruta.endsWith("/ventas") || ruta.endsWith("/etiquetas")) {
      const q = await quienLlama(req);
      if (q instanceof Response) return q;
      try { body = await req.json(); } catch { /* vacío */ }
      if (ruta.endsWith("/ventas")) return json(await listarVentas(q, body));
      const r = await etiquetas(q, body);
      if (r instanceof Response) return new Response(r.body, { status: 200, headers: { ...CORS, "Content-Type": "application/pdf" } });
      return fallo(r.error, r.status ?? 400);
    }
    const quien = await revendedorDe(req);
    if (quien instanceof Response) return quien;
    try { body = await req.json(); } catch { /* vacío */ }
    if (ruta.endsWith("/preparar")) return await preparar(quien.rev, quien.jwt, body);
    if (ruta.endsWith("/publicar")) return await publicarUno(quien.rev, body);
    if (ruta.endsWith("/encolar")) return await encolar(quien.rev, body);
    if (ruta.endsWith("/lotes")) return await lotes(quien.rev);
    if (ruta.endsWith("/cancelar")) return await cancelar(quien.rev, body);
    return fallo("No encontrado.", 404);
  } catch (e) {
    console.error("rev-publicar:", (e as Error).message);
    return fallo(e instanceof ErrorUsuario ? e.message : (e as Error).message || "Error inesperado.", 500);
  }
});
