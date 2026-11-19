// supabase/functions/rev-publicar/nucleo.ts
//
// Núcleo de "Publicar en mi cuenta" (lo usan las rutas de index.ts y el proceso de la cola de publicación masiva).
//   - LEE la publicación de Pret con la conexión propia de solo lectura (fila 'pausado' de rev_revendedores; no es la de
//     producción de armado-api). En la cuenta de Pret no escribe nada.
//   - ESCRIBE en Mercado Libre SOLO en la cuenta del revendedor, y en la base solo en rev_publicaciones / rev_publicar_cola.
//   - No toca Ecomm-App.

import { createClient, type SupabaseClient } from "jsr:@supabase/supabase-js@2";
import { COLUMNAS_REVENDEDOR, ML_API, mlGet, type Revendedor, tokenValido, USER_AGENT } from "../_shared/rev_ml.ts";
import { sinFotosDeMarca } from "../_shared/fotos_marca.ts";
import {
  claveCondicion, claveResultado, COMISION_ML_ESTIMADA, colorDe, condicionDeItem, minutosDePausa, RITMO, erroresML, esMarcaPropia, estadoFinal, type ItemML, limpiarDescripcion, marcaDe,
  MAX_RECARGO_CUOTAS, type OpcionesMasivas, payloadCatalogo, payloadTradicional, precioPorRegla, precioPorTipo, RECARGO_CUOTAS_ESTIMADO, skuDe, type TipoVenta,
  type VariacionML,
} from "./logic.ts";

/** Stock que se ofrece: stock de Ecomm-App − colchón, nunca negativo (misma cuenta que rev-sync y que rev_ofrecido_de). */
const stockOfrecido = (stock: number, colchon: number) =>
  Number.isFinite(stock) && Number.isFinite(colchon) ? Math.max(0, Math.floor(stock) - Math.max(0, Math.floor(colchon))) : 0;

export const URL_SB = Deno.env.get("SUPABASE_URL")!;
export const ANON = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
export const supabase = createClient(URL_SB, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const PRET_ML_USER = 1255615205;

export type Json = Record<string, unknown>;
export class ErrorUsuario extends Error {} // mensaje para mostrarle tal cual al revendedor
/** ML pidió frenar (429) aunque se reintentó con espera: hay que cortar y seguir más tarde. */
export class LimiteML extends Error {}
/** Se llegó al cupo de publicaciones de esta corrida: se sigue en la próxima (cada minuto). */
export class PausaRitmo extends Error {}
const dormirMs = (ms: number) => new Promise((ok) => setTimeout(ok, ms));
const conAzar = (ms: number) => ms + Math.floor(Math.random() * ms * 0.5); // jitter, como pide ML

export async function tokenPret(): Promise<string> {
  const { data } = await supabase.from("rev_revendedores").select(COLUMNAS_REVENDEDOR).eq("ml_user_id", PRET_ML_USER).maybeSingle();
  if (!data || (data as Revendedor).estado !== "pausado") throw new Error("La conexión de solo lectura con la cuenta de Pret a Home no está disponible.");
  const t = await tokenValido(supabase, data as Revendedor);
  if (!t) throw new Error("No pude renovar la conexión de solo lectura con la cuenta de Pret a Home.");
  return t;
}

export async function mlEnviar(token: string, method: "POST" | "PUT", path: string, body: unknown): Promise<{ status: number; body: unknown }> {
  for (let intento = 0; intento < 3; intento++) {
    const r = await fetch(`${ML_API}${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json", "User-Agent": USER_AGENT },
      body: JSON.stringify(body),
    });
    const texto = await r.text();
    let cuerpo: unknown = null;
    try { cuerpo = texto ? JSON.parse(texto) : null; } catch { cuerpo = { message: texto.slice(0, 300) }; }
    if ((r.status === 429 || r.status >= 500) && intento < 2) { await dormirMs(conAzar(2000 * 2 ** intento)); continue; }
    if (r.status === 429) throw new LimiteML("Mercado Libre pidió ir más despacio.");
    return { status: r.status, body: cuerpo };
  }
  return { status: 0, body: null };
}

// ───────────────────────── datos ─────────────────────────
export interface FilaPret { ml_item_id: string; ml_variation_id: number; sku: string; tipo: string; catalog_product_id: string | null; vendidas: number | null; permalink: string | null; precio: number | null }

export async function publicacionesPret(skus: string[]): Promise<FilaPret[]> {
  const out: FilaPret[] = [];
  for (let i = 0; i < skus.length; i += 200) {
    const { data, error } = await supabase.from("rev_pret_publicaciones")
      .select("ml_item_id,ml_variation_id,sku,tipo,catalog_product_id,vendidas,permalink,precio").in("sku", skus.slice(i, i + 200))
      .order("vendidas", { ascending: false, nullsFirst: false }).order("ml_item_id").order("ml_variation_id");
    if (error) throw new Error(error.message);
    out.push(...((data ?? []) as FilaPret[]));
  }
  return out;
}

/** % estimado que se quedan ML + impuestos (rev_config 'comision_ml_estimada'; si no está, 30). */
export async function comisionMLEstimada(): Promise<number> {
  const { data } = await supabase.from("rev_config").select("valor").eq("clave", "comision_ml_estimada").maybeSingle();
  const v = Number(data?.valor);
  return Number.isFinite(v) && v >= 0 && v < 90 ? v : COMISION_ML_ESTIMADA;
}

/** % que ML cobra de más en Premium (rev_config 'recargo_cuotas'; si no está, el medido: 13). Es el valor inicial del
 *  campo "Recargo por cuotas"; el revendedor lo puede cambiar al publicar. */
export async function recargoCuotasEstimado(): Promise<number> {
  const { data } = await supabase.from("rev_config").select("valor").eq("clave", "recargo_cuotas").maybeSingle();
  const v = Number(data?.valor);
  return data && Number.isFinite(v) && v >= 0 && v <= MAX_RECARGO_CUOTAS ? v : RECARGO_CUOTAS_ESTIMADO;
}

export async function marcasPropias(): Promise<string[]> {
  const { data } = await supabase.from("rev_config").select("valor").eq("clave", "marcas_propias").maybeSingle();
  return Array.isArray(data?.valor) ? (data!.valor as unknown[]).map(String) : [];
}

/** Lo que ve el revendedor de cada SKU (costo mayorista, mínimo, stock), con SU sesión: misma regla que el catálogo. */
export async function detalleCatalogo(jwt: string, skus: string[]): Promise<Map<string, Json>> {
  const cli: SupabaseClient = createClient(URL_SB, ANON || Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { global: { headers: { Authorization: `Bearer ${jwt}` } } });
  const { data, error } = await cli.rpc("rev_catalogo_detalle", { p_skus: skus });
  if (error) throw new Error(error.message);
  return new Map((data as Json[]).map((d) => [String(d.sku), d]));
}

/** Datos de negocio de cada SKU para UN revendedor, leídos por el servicio: catálogo habilitado, mínimo, stock que se le
 *  ofrece (stock de Ecomm-App − su colchón) y su costo mayorista (con su margen). Colchón y margen son por revendedor
 *  desde el 19/11/2026; si no tiene propios, los generales. */
export async function datosSkus(skus: string[], conCosto: boolean, revId: number) {
  const [{ data: cat }, { data: stock }, { data: info }, { data: colchonRev }] = await Promise.all([
    supabase.from("rev_catalogo").select("sku,habilitado,precio_minimo,colchon_override").in("sku", skus),
    supabase.from("rev_stock_ofrecido").select("sku,stock_ecomm").in("sku", skus),
    conCosto ? supabase.from("rev_catalogo_info").select("sku,costo").in("sku", skus) : Promise.resolve({ data: [] as { sku: string; costo: number }[] }),
    supabase.rpc("rev_colchon_de", { p_rev: revId }),
  ]);
  const costo = new Map<string, number | null>();
  for (const r of (info ?? []) as { sku: string; costo: number | null }[]) {
    const { data } = await supabase.rpc("rev_precio_mayorista_de", { p_costo: r.costo, p_rev: revId });
    costo.set(r.sku, data == null ? null : Number(data));
  }
  const catPor = new Map((cat ?? []).map((c) => [c.sku as string, c as { habilitado: boolean; precio_minimo: number | null; colchon_override: number | null }]));
  const colchon = Number(colchonRev ?? 5);
  const stockPor = new Map((stock ?? []).map((s) => {
    const o = catPor.get(s.sku as string)?.colchon_override;
    return [s.sku as string, stockOfrecido(Number(s.stock_ecomm), o ?? colchon)];
  }));
  return {
    habilitado: (s: string) => !!catPor.get(s)?.habilitado,
    minimo: (s: string) => (catPor.get(s)?.precio_minimo != null ? Number(catPor.get(s)!.precio_minimo) : null),
    stock: (s: string) => stockPor.get(s) ?? 0,
    costo: (s: string) => costo.get(s) ?? null,
  };
}

export async function leerItemPret(token: string, id: string): Promise<ItemML> {
  const r = await mlGet<ItemML>(token, `/items/${id}?include_attributes=all`);
  if (!r.body) throw new Error(`No pude leer la publicación ${id} de Pret a Home (HTTP ${r.status}).`);
  return r.body;
}

/** Colores de una publicación: sus variaciones con SKU, o la publicación misma si no tiene variaciones. */
export function coloresDe(it: ItemML): { sku: string; color: string | null; variacion: VariacionML }[] {
  const vars = it.variations?.length ? it.variations
    : [{ id: 0, price: it.price, attributes: it.attributes, picture_ids: (it.pictures ?? []).map((p) => p.id), seller_custom_field: it.seller_custom_field }];
  return vars.map((v) => ({ sku: skuDe(v.attributes, v.seller_custom_field)!, color: colorDe(v), variacion: v })).filter((c) => c.sku);
}

// ───────────────────────── publicar un producto ─────────────────────────
export interface Resultado { sku: string; tipo: "tradicional" | "catalogo"; tipo_venta: TipoVenta; ok: boolean; ml_item_id?: string; permalink?: string; estado?: string; error?: string; aviso?: string; precio?: number }

export interface PedidoProducto {
  itemId: string | null;           // publicación tradicional de Pret (null = solo catálogo)
  skus: string[];                  // colores ya controlados (en catálogo, con stock, precio ≥ mínimo)
  precios: Map<string, number>;
  cantidades: Map<string, number>;
  tradicional: boolean;
  catalogo: boolean;
  marca: string | null;            // ya controlado: solo si el producto es marca Pret
  tiposVenta: TipoVenta[];
  pausada: boolean;
  recargoCuotas: number;           // % extra en Premium para que la ganancia quede igual que en Clásica
  comision: number;                // % estimado que se quedan ML + impuestos (para el recargo)
}

/** Crea las publicaciones de UN producto en la cuenta del revendedor. `hechos` = claves ya publicadas (se saltean);
 *  `alGuardar` se llama después de cada intento (la cola guarda el avance, así un corte no repite publicaciones). */
export async function publicarProducto(rev: Revendedor, tokRev: string, tokPret: string, p: PedidoProducto,
  hechos = new Set<string>(), alGuardar?: (r: Resultado) => Promise<void>, cupo?: { restantes: number }): Promise<Resultado[]> {
  const resultados: Resultado[] = [];
  // Publicaciones que el revendedor ya tiene de cada SKU, con su condición de venta (para no crear duplicadas).
  const condiciones = new Map<string, Set<string>>();
  async function condicionesDe(sku: string): Promise<Set<string>> {
    if (condiciones.has(sku)) return condiciones.get(sku)!;
    const { data } = await supabase.from("rev_publicaciones").select("ml_item_id").eq("revendedor_id", rev.id).eq("sku", sku);
    const ids = [...new Set(((data ?? []) as { ml_item_id: string }[]).map((x) => x.ml_item_id))];
    // Además, lo que la cuenta ya tenga publicado con ese SKU aunque no esté vinculado (publicado a mano, o una cuenta
    // propia como LAVANCASA con su catálogo de siempre).
    const bus = await mlGet<{ results?: string[] }>(tokRev, `/users/${rev.ml_user_id}/items/search?seller_sku=${encodeURIComponent(sku)}&limit=50`);
    if (bus.status === 429) throw new LimiteML("Mercado Libre pidió ir más despacio.");
    for (const id of bus.body?.results ?? []) if (!ids.includes(id)) ids.push(id);
    const set = new Set<string>();
    for (const id of ids) {
      const r = await mlGet<{ status?: string; listing_type_id?: string; catalog_listing?: boolean }>(tokRev, `/items/${id}?attributes=id,status,listing_type_id,catalog_listing`);
      if (r.status === 429) throw new LimiteML("Mercado Libre pidió ir más despacio.");
      const c = condicionDeItem(r.body);
      if (c) set.add(c);
    }
    condiciones.set(sku, set);
    return set;
  }
  const anotar = async (r: Resultado) => { resultados.push(r); if (alGuardar) await alGuardar(r); };
  const filas = await publicacionesPret(p.skus);

  // Premium lleva el recargo por cuotas; Clásica, el precio elegido.
  const precioDe = (sku: string, tv: TipoVenta) => precioPorTipo(p.precios.get(sku)!, tv, p.recargoCuotas, p.comision);
  async function crear(sku: string, tipo: Resultado["tipo"], tv: TipoVenta, payload: Json | null, descripcion: string | null) {
    const base: Resultado = { sku, tipo, tipo_venta: tv, ok: false, precio: precioDe(sku, tv) ?? undefined };
    if (!payload) return anotar({ ...base, error: "Con ese recargo por cuotas el precio no se puede calcular: probá con un porcentaje menor." });
    if ((await condicionesDe(sku)).has(claveCondicion(tipo, tv))) {
      return anotar({ ...base, error: "Ya tenés una publicación igual de este producto (mismo tipo y mismas cuotas). Mercado Libre no permite publicaciones duplicadas: no se creó otra." });
    }
    if (cupo) {
      if (cupo.restantes <= 0) throw new PausaRitmo("cupo de la corrida");
      cupo.restantes--;
    }
    await dormirMs(conAzar(RITMO.pausaEntreCreacionesMs)); // espaciar las creaciones, como recomienda ML
    const v = await mlEnviar(tokRev, "POST", "/items/validate", payload);
    const errV = erroresML(v.status, v.body);
    if (errV.length) return anotar({ ...base, error: "Mercado Libre no lo acepta: " + errV.join(" · ") });
    const c = await mlEnviar(tokRev, "POST", "/items", payload);
    const errC = erroresML(c.status, c.body);
    const it = c.body as { id?: string; permalink?: string; status?: string } | null;
    if (errC.length || !it?.id) return anotar({ ...base, error: "No se pudo crear: " + (errC.join(" · ") || `HTTP ${c.status}`) });
    const r: Resultado = { ...base, ok: true, ml_item_id: it.id, permalink: it.permalink, estado: it.status };
    if (descripcion) {
      const d = await mlEnviar(tokRev, "POST", `/items/${it.id}/description`, { plain_text: descripcion });
      if (d.status >= 300) r.aviso = "Se creó sin descripción (Mercado Libre no la aceptó); podés cargarla desde tu cuenta.";
    }
    if (p.pausada) {
      const z = await mlEnviar(tokRev, "PUT", `/items/${it.id}`, { status: "paused" });
      if (z.status < 300) r.estado = "paused";
      else r.aviso = (r.aviso ? r.aviso + " " : "") + "No se pudo dejar pausada: quedó activa.";
    }
    // Queda vinculada al SKU enseguida (así el stock se le mantiene). Si la vinculación automática ya la tomó, no se repite.
    const { error } = await supabase.from("rev_publicaciones").insert({
      revendedor_id: rev.id, ml_item_id: it.id, ml_variation_id: null, sku, estado_ml: r.estado ?? null, precio: base.precio ?? null,
    });
    if (error && !/duplicate|unique/i.test(error.message)) console.error(`rev_publicaciones ${it.id}: ${error.message}`);
    condiciones.get(sku)?.add(claveCondicion(tipo, tv));
    await anotar(r);
  }

  if (p.tradicional && p.itemId) {
    const item = await leerItemPret(tokPret, p.itemId);
    const desc = await mlGet<{ plain_text?: string }>(tokPret, `/items/${p.itemId}/description`);
    const descripcion = desc.body?.plain_text ? limpiarDescripcion(desc.body.plain_text, p.marca) : null;
    const pics = new Map((item.pictures ?? []).map((x) => [x.id, x.secure_url || x.url || ""]));
    for (const col of coloresDe(item).filter((c) => p.skus.includes(c.sku))) {
      const pendientes = p.tiposVenta.filter((tv) => !hechos.has(claveResultado(col.sku, "tradicional", tv)));
      if (!pendientes.length) continue;
      const { quedan, fuera } = await sinFotosDeMarca((col.variacion.picture_ids ?? []).filter((i) => pics.get(i)));
      if (!quedan.length) {
        for (const tv of pendientes) await anotar({ sku: col.sku, tipo: "tradicional", tipo_venta: tv, ok: false, error: "No le quedan fotos propias para publicar." });
        continue;
      }
      if (fuera.length) console.log(`${col.sku}: ${fuera.length} foto(s) de marca o sin revisar no se copiaron`);
      for (const tv of pendientes) {
        const precio = precioDe(col.sku, tv);
        await crear(col.sku, "tradicional", tv, precio == null ? null : payloadTradicional(item, {
          variacion: col.variacion, fotos: quedan.map((i) => pics.get(i)!), precio,
          cantidad: p.cantidades.get(col.sku)!, tipoVenta: tv, marca: p.marca,
        }), descripcion);
      }
    }
  }

  if (p.catalogo) {
    for (const s of p.skus) {
      const pendientes = p.tiposVenta.filter((tv) => !hechos.has(claveResultado(s, "catalogo", tv)));
      if (!pendientes.length) continue;
      const f = filas.find((x) => x.sku === s && x.tipo === "catalogo" && x.catalog_product_id);
      if (!f) {
        // En la publicación masiva no es un error: ese color simplemente no tiene ficha de catálogo.
        if (!p.tradicional) for (const tv of pendientes) await anotar({ sku: s, tipo: "catalogo", tipo_venta: tv, ok: false, error: "Este color no tiene ficha de catálogo para sumarse." });
        continue;
      }
      const ci = await mlGet<{ category_id: string }>(tokPret, `/items/${f.ml_item_id}?attributes=category_id`);
      if (!ci.body?.category_id) {
        for (const tv of pendientes) await anotar({ sku: s, tipo: "catalogo", tipo_venta: tv, ok: false, error: "No pude leer la ficha de catálogo; probá de nuevo." });
        continue;
      }
      for (const tv of pendientes) {
        const precio = precioDe(s, tv);
        await crear(s, "catalogo", tv, precio == null ? null : payloadCatalogo({
          categoryId: ci.body.category_id, catalogProductId: f.catalog_product_id!, sku: s, precio,
          cantidad: p.cantidades.get(s)!, tipoVenta: tv,
        }), null);
      }
    }
  }
  return resultados;
}

// ───────────────────────── cola de publicación masiva ─────────────────────────
interface FilaCola { id: number; revendedor_id: number; item_id: string | null; skus: string[]; todos_colores: boolean; opciones: OpcionesMasivasJson; resultados: Resultado[]; intentos: number }
type OpcionesMasivasJson = Omit<OpcionesMasivas, "tiposVenta" | "todosColores" | "recargoCuotas"> & { tipos_venta: TipoVenta[]; recargo_cuotas?: number };

/** Toma un producto pendiente (atómico: si otro proceso lo tomó primero, no lo repite). */
async function tomarSiguiente(): Promise<FilaCola | null> {
  for (let i = 0; i < 5; i++) {
    const { data: c } = await supabase.from("rev_publicar_cola").select("id").eq("estado", "pendiente").order("id").limit(1);
    if (!c?.length) return null;
    const { data } = await supabase.from("rev_publicar_cola")
      .update({ estado: "procesando", tomado_at: new Date().toISOString() })
      .eq("id", c[0].id).eq("estado", "pendiente").select("id,revendedor_id,item_id,skus,todos_colores,opciones,resultados,intentos");
    if (data?.length) {
      const f = data[0] as FilaCola;
      await supabase.from("rev_publicar_cola").update({ intentos: f.intentos + 1 }).eq("id", f.id);
      return f;
    }
  }
  return null;
}

/** Productos que quedaron "procesando" por un corte (más de 10 min): vuelven a la cola (hasta 3 intentos). */
async function liberarTrabados() {
  const limite = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  const { data } = await supabase.from("rev_publicar_cola").select("id,intentos,resultados").eq("estado", "procesando").lt("tomado_at", limite);
  for (const f of (data ?? []) as { id: number; intentos: number; resultados: Resultado[] }[]) {
    if (f.intentos >= 3) {
      await supabase.from("rev_publicar_cola").update({
        estado: f.resultados.some((r) => r.ok) ? "parcial" : "error", error: "Se cortó varias veces; quedó a medias.", terminado_at: new Date().toISOString(),
      }).eq("id", f.id);
    } else {
      await supabase.from("rev_publicar_cola").update({ estado: "pendiente" }).eq("id", f.id).eq("estado", "procesando");
    }
  }
}

async function procesarFila(f: FilaCola, tokPret: string, cupo: { restantes: number }) {
  const op = f.opciones;
  const { data: revRow } = await supabase.from("rev_revendedores").select(COLUMNAS_REVENDEDOR).eq("id", f.revendedor_id).maybeSingle();
  const rev = revRow as Revendedor | null;
  const terminar = (estado: string, error: string | null, resultados: Resultado[]) =>
    supabase.from("rev_publicar_cola").update({ estado, error, resultados, terminado_at: new Date().toISOString() }).eq("id", f.id);
  if (!rev || rev.estado !== "activo") return terminar("error", "La cuenta de Mercado Libre no está conectada.", f.resultados);
  const tokRev = await tokenValido(supabase, rev);
  if (!tokRev) return terminar("error", "La cuenta de Mercado Libre se desconectó.", f.resultados);

  // Colores: los elegidos y, si se pidió, todos los de esa publicación de Pret.
  let skus = [...f.skus];
  let item: ItemML | null = null;
  if (f.item_id) {
    item = await leerItemPret(tokPret, f.item_id);
    const delItem = coloresDe(item).map((c) => c.sku);
    skus = f.todos_colores ? [...new Set([...skus.filter((s) => delItem.includes(s)), ...delItem])] : skus.filter((s) => delItem.includes(s));
  }
  const filas = await publicacionesPret(skus);
  const d = await datosSkus(skus, op.regla.tipo === "costo_pct" || op.regla.tipo === "margen", rev.id);
  const comision = await comisionMLEstimada();
  const marcaOk = !!op.marca && !!item && esMarcaPropia(marcaDe(item), await marcasPropias());

  const previos = [...f.resultados];
  const hechos = new Set(previos.filter((r) => r.ok).map((r) => claveResultado(r.sku, r.tipo, r.tipo_venta)));
  const nuevos: Resultado[] = [];
  const precios = new Map<string, number>(), cantidades = new Map<string, number>(), validos: string[] = [];
  const saltear = (sku: string, error: string) => {
    if (previos.some((r) => r.sku === sku && !r.ok && r.error === error)) return;
    nuevos.push({ sku, tipo: op.tradicional ? "tradicional" : "catalogo", tipo_venta: op.tipos_venta[0], ok: false, error });
  };
  for (const s of skus) {
    const precioPret = filas.find((x) => x.sku === s && x.tipo === "tradicional" && x.ml_item_id === f.item_id)?.precio
      ?? filas.find((x) => x.sku === s)?.precio ?? null;
    const precio = precioPorRegla(op.regla, { pret: precioPret != null ? Number(precioPret) : null, costo: d.costo(s) }, comision);
    const minimo = d.minimo(s);
    if (!d.habilitado(s)) { if (f.skus.includes(s)) saltear(s, "No está en tu catálogo."); continue; }
    if (d.stock(s) < 1) { if (f.skus.includes(s)) saltear(s, "Sin stock para ofrecer."); continue; }
    if (precio == null) { saltear(s, op.regla.tipo === "costo_pct" || op.regla.tipo === "margen" ? "No tiene costo mayorista cargado." : "No encontré el precio de Pret a Home."); continue; }
    if (minimo != null && precio < minimo) { saltear(s, `Con esa regla quedaría a $ ${precio.toLocaleString("es-AR")}, debajo del mínimo ($ ${minimo.toLocaleString("es-AR")}).`); continue; }
    precios.set(s, precio); cantidades.set(s, d.stock(s)); validos.push(s);
  }
  // Aviso (sin publicación ni error): pidió su marca pero el producto no es Pret → va con la marca original.
  if (op.marca && !marcaOk && op.tradicional && item && !previos.some((r) => r.aviso && !r.ml_item_id && !r.error)) {
    nuevos.push({ sku: validos[0] ?? skus[0] ?? "", tipo: "tradicional", tipo_venta: op.tipos_venta[0], ok: false, aviso: `Va con su marca original (${marcaDe(item) ?? "del proveedor"}): solo los productos Pret se pueden publicar con tu marca.` });
  }

  const guardar = async () => { await supabase.from("rev_publicar_cola").update({ resultados: [...previos, ...nuevos] }).eq("id", f.id); };
  await guardar();
  if (validos.length) {
    await publicarProducto(rev, tokRev, tokPret, {
      itemId: f.item_id, skus: validos, precios, cantidades, tradicional: op.tradicional && !!f.item_id, catalogo: op.catalogo,
      marca: marcaOk ? op.marca : null, tiposVenta: op.tipos_venta, pausada: op.pausada,
      recargoCuotas: Number(op.recargo_cuotas ?? 0) || 0, comision,
    }, hechos, async (r) => { nuevos.push(r); await guardar(); }, cupo);
  }
  const todos = [...previos, ...nuevos].filter((r) => r.ml_item_id || r.error); // los avisos no cuentan para el estado
  const sinNada = !todos.length;
  await terminar(sinNada ? "error" : estadoFinal(todos), sinNada ? "No había nada para publicar en este producto." : null, [...previos, ...nuevos]);
}

/** Procesa la cola mientras quede tiempo (la función tiene un límite de duración). Devuelve cuántos productos procesó. */
export async function procesarCola(presupuestoMs = 100_000): Promise<{ procesados: number; pausado?: string }> {
  const inicio = Date.now();
  // Si ML pidió frenar hace poco, esta corrida no hace nada (la pausa crece si vuelve a pasar).
  const { data: est } = await supabase.from("rev_sync_estado").select("detalle").eq("clave", "publicar_pausa").maybeSingle();
  const pausa = (est?.detalle ?? {}) as { hasta?: string; veces?: number };
  if (pausa.hasta && Date.parse(pausa.hasta) > Date.now()) return { procesados: 0, pausado: `hasta ${pausa.hasta}` };
  await liberarTrabados();
  let procesados = 0, frenado = false;
  let tokPret: string | null = null;
  const cupo = { restantes: RITMO.creacionesPorCorrida };
  while (Date.now() - inicio < presupuestoMs && cupo.restantes > 0) {
    const f = await tomarSiguiente();
    if (!f) break;
    try {
      tokPret ??= await tokenPret();
      await procesarFila(f, tokPret, cupo);
    } catch (e) {
      if (e instanceof PausaRitmo || e instanceof LimiteML) {
        // No es un error del producto: vuelve a la cola tal como estaba (sin gastar un intento) y se sigue después.
        await supabase.from("rev_publicar_cola").update({ estado: "pendiente", intentos: f.intentos }).eq("id", f.id);
        if (e instanceof LimiteML) {
          frenado = true;
          const veces = (pausa.veces ?? 0) + 1;
          const hasta = new Date(Date.now() + minutosDePausa(veces) * 60_000).toISOString();
          await supabase.from("rev_sync_estado").upsert({ clave: "publicar_pausa", ultimo_at: new Date().toISOString(), resultado: "pausa_por_limite_ml", detalle: { hasta, veces } }, { onConflict: "clave" });
        }
        break;
      }
      console.error(`cola ${f.id}:`, (e as Error).message);
      // Error pasajero (ML, red): vuelve a la cola; el reintento saltea lo ya publicado.
      await supabase.from("rev_publicar_cola").update({ estado: f.intentos + 1 >= 3 ? "error" : "pendiente", error: (e as Error).message }).eq("id", f.id);
    }
    procesados++;
  }
  // Una corrida sin que ML pida frenar reinicia la cuenta de pausas.
  if (!frenado && pausa.veces) await supabase.from("rev_sync_estado").upsert({ clave: "publicar_pausa", ultimo_at: new Date().toISOString(), resultado: "ok", detalle: { veces: 0 } }, { onConflict: "clave" });
  return { procesados };
}
