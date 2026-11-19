// supabase/functions/rev-publicar/ventas.ts
//
// Sección "Ventas" del portal de revendedores (19/11/2026): las ventas de ML registradas (rev_ordenes) con su publicación
// (MLA) y SKU, el estado de la orden y del envío leídos en vivo de Mercado Libre, y la etiqueta de envío en PDF.
//   - Revendedor: ve solo las suyas.
//   - Admin/dueño del panel: ve las de todos (y puede filtrar por revendedor). La etiqueta la imprime quien despacha.
// Solo LEE en Mercado Libre, con el acceso de cada revendedor. Pedir la etiqueta hace que ML la marque como impresa
// (pasa de "lista para imprimir" a "impresa"): es lo mismo que imprimirla desde Mercado Libre.

import { COLUMNAS_REVENDEDOR, ML_API, mlGet, type Revendedor, tokenValido, USER_AGENT } from "../_shared/rev_ml.ts";
import { supabase } from "./nucleo.ts";
import { etiquetaDisponible, periodo } from "./logic.ts";

export interface Quien { rev: Revendedor | null; admin: boolean }

const MAX_ORDENES_EN_VIVO = 150; // las más recientes del período se consultan en ML; las más viejas se muestran con lo guardado
const MAX_ETIQUETAS = 50;

interface FilaOrden {
  id: number; revendedor_id: number; ml_order_id: number; ml_item_id: string | null; sku: string; cantidad: number;
  precio_unitario: number | null; estado: string; despachada: boolean | null; reposicion: string | null; devolucion: unknown; creado_at: string;
  descontado_at: string | null; repuesto_at: string | null; detalle: string | null;
}
interface OrdenML { id: number; status?: string; date_created?: string; pack_id?: number | null; shipping?: { id?: number | null } | null; buyer?: { nickname?: string } | null }
interface EnvioML { id: number; status?: string; substatus?: string | null; logistic_type?: string | null; tracking_number?: string | null }


async function enLotes<T, R>(xs: T[], n: number, f: (x: T) => Promise<R>): Promise<R[]> {
  const out: R[] = [];
  for (let i = 0; i < xs.length; i += n) out.push(...await Promise.all(xs.slice(i, i + n).map(f)));
  return out;
}

async function revendedores(ids: number[]): Promise<Map<number, Revendedor>> {
  if (!ids.length) return new Map();
  const { data } = await supabase.from("rev_revendedores").select(COLUMNAS_REVENDEDOR).in("id", ids);
  return new Map(((data ?? []) as Revendedor[]).map((r) => [r.id, r]));
}

export async function listarVentas(q: Quien, b: Record<string, unknown>) {
  const per = periodo(b.desde, b.hasta);
  if (!per) throw new Error("El período no es válido (máximo un año, y la fecha de inicio antes que la de fin).");
  let consulta = supabase.from("rev_ordenes")
    .select("id,revendedor_id,ml_order_id,ml_item_id,sku,cantidad,precio_unitario,estado,despachada,reposicion,devolucion,descontado_at,repuesto_at,detalle,creado_at")
    .gte("creado_at", `${per.desde}T00:00:00-03:00`).lte("creado_at", `${per.hasta}T23:59:59.999-03:00`)
    .order("creado_at", { ascending: false }).order("id").limit(5000);
  if (!q.admin) consulta = consulta.eq("revendedor_id", q.rev!.id);
  else if (b.revendedor_id) consulta = consulta.eq("revendedor_id", Number(b.revendedor_id));
  const { data, error } = await consulta;
  if (error) throw new Error(error.message);
  const filas = (data ?? []) as FilaOrden[];

  const revs = await revendedores([...new Set(filas.map((f) => f.revendedor_id))]);
  // Para los botones de stock del admin: qué revendedores tienen descuento manual y si el sistema está en modo real.
  const { data: dm } = await supabase.from("rev_revendedores").select("id,descuento_auto").in("id", [...revs.keys()]);
  const manual = new Set(((dm ?? []) as { id: number; descuento_auto: boolean }[]).filter((r) => r.descuento_auto === false).map((r) => r.id));
  const { data: cm } = await supabase.from("rev_config").select("valor").eq("clave", "modo").maybeSingle();
  const modoReal = cm?.valor === "real" || cm?.valor === "activo";
  const skus = [...new Set(filas.map((f) => f.sku))];
  const titulos = new Map<string, string>();
  for (let i = 0; i < skus.length; i += 200) {
    const { data: ti } = await supabase.from("rev_catalogo_info").select("sku,titulo").in("sku", skus.slice(i, i + 200));
    for (const t of (ti ?? []) as { sku: string; titulo: string }[]) titulos.set(t.sku, t.titulo);
  }

  // Una venta = una orden de ML (puede tener varias líneas/SKU).
  const ordenes = new Map<number, { ml_order_id: number; revendedor_id: number; fecha: string; estado: string; despachada: boolean | null; reposicion: string | null; devolucion: unknown; descontado_at: string | null; repuesto_at: string | null; detalle: string | null; lineas: Record<string, unknown>[] }>();
  for (const f of filas) {
    if (!ordenes.has(f.ml_order_id)) ordenes.set(f.ml_order_id, { ml_order_id: f.ml_order_id, revendedor_id: f.revendedor_id, fecha: f.creado_at, estado: f.estado, despachada: f.despachada, reposicion: f.reposicion, devolucion: f.devolucion, descontado_at: f.descontado_at, repuesto_at: f.repuesto_at, detalle: f.detalle, lineas: [] });
    ordenes.get(f.ml_order_id)!.lineas.push({ sku: f.sku, ml_item_id: f.ml_item_id, cantidad: f.cantidad, precio_unitario: f.precio_unitario, titulo: titulos.get(f.sku) ?? null });
  }

  // Estado en vivo en ML (orden + envío) de las más recientes, con el acceso de cada revendedor.
  const tokens = new Map<number, string | null>();
  const tokenDe = async (id: number) => {
    if (!tokens.has(id)) { const r = revs.get(id); tokens.set(id, r && r.estado === "activo" ? await tokenValido(supabase, r) : null); }
    return tokens.get(id);
  };
  const lista = [...ordenes.values()];
  const vivo = await enLotes(lista.slice(0, MAX_ORDENES_EN_VIVO), 8, async (o) => {
    const tok = await tokenDe(o.revendedor_id);
    if (!tok) return null;
    const ord = (await mlGet<OrdenML>(tok, `/orders/${o.ml_order_id}`)).body;
    const env = ord?.shipping?.id ? (await mlGet<EnvioML>(tok, `/shipments/${ord.shipping.id}`)).body : null;
    return { ord, env };
  });

  return {
    ventas: lista.map((o, i) => {
      const v = vivo[i] ?? null;
      return {
        ...o,
        revendedor: revs.get(o.revendedor_id)?.nickname ?? null,
        descuento_manual: manual.has(o.revendedor_id),
        en_vivo: !!v,
        orden_ml: v?.ord ? { status: v.ord.status, pack_id: v.ord.pack_id ?? null, comprador: v.ord.buyer?.nickname ?? null } : null,
        envio: v?.env ? { id: v.env.id, status: v.env.status, substatus: v.env.substatus ?? null, logistic_type: v.env.logistic_type ?? null, tracking: v.env.tracking_number ?? null } : null,
        etiqueta: etiquetaDisponible(v?.env),
      };
    }),
    periodo: per,
    modo_real: modoReal,
    en_vivo_max: MAX_ORDENES_EN_VIVO,
    revendedores: q.admin ? [...revs.values()].map((r) => ({ id: r.id, nickname: r.nickname })) : null,
  };
}

/** PDF con las etiquetas de envío de las ventas elegidas (todas del mismo revendedor). */
export async function etiquetas(q: Quien, b: Record<string, unknown>): Promise<Response | { error: string; status?: number }> {
  const ids = Array.isArray(b.ordenes) ? [...new Set(b.ordenes.map(Number).filter(Number.isFinite))] : [];
  if (!ids.length) return { error: "Elegí al menos una venta." };
  if (ids.length > MAX_ETIQUETAS) return { error: `Son ${ids.length}; el máximo por vez es ${MAX_ETIQUETAS}.` };
  let consulta = supabase.from("rev_ordenes").select("ml_order_id,revendedor_id").in("ml_order_id", ids);
  if (!q.admin) consulta = consulta.eq("revendedor_id", q.rev!.id);
  const { data } = await consulta;
  const delRev = new Set(((data ?? []) as { revendedor_id: number }[]).map((r) => r.revendedor_id));
  const encontradas = new Set(((data ?? []) as { ml_order_id: number }[]).map((r) => Number(r.ml_order_id)));
  if (ids.some((i) => !encontradas.has(i))) return { error: "Alguna de las ventas elegidas no es tuya o no está registrada.", status: 403 };
  if (delRev.size !== 1) return { error: "Elegí ventas de un solo revendedor por vez (cada cuenta de Mercado Libre imprime las suyas)." };
  const rev = (await revendedores([...delRev])).get([...delRev][0]) ?? null;
  if (!rev || rev.estado !== "activo") return { error: "La cuenta de Mercado Libre de ese revendedor no está conectada.", status: 409 };
  const tok = await tokenValido(supabase, rev);
  if (!tok) return { error: "La cuenta de Mercado Libre de ese revendedor se desconectó.", status: 409 };

  // El envío de cada venta se toma de la orden en ML (no de lo que manda el navegador).
  const envios = (await enLotes(ids, 8, async (id) => (await mlGet<OrdenML>(tok, `/orders/${id}`)).body?.shipping?.id ?? null))
    .filter((x): x is number => !!x);
  if (!envios.length) return { error: "Esas ventas no tienen envío de Mercado Envíos." };
  const unicos = [...new Set(envios)]; // un carrito comparte el envío
  const r = await fetch(`${ML_API}/shipment_labels?shipment_ids=${unicos.join(",")}&response_type=pdf`, {
    headers: { Authorization: `Bearer ${tok}`, "User-Agent": USER_AGENT },
  });
  if (!r.ok) {
    let motivo = "";
    try { const j = await r.json(); motivo = j.message || j.error || ""; } catch { /* sin cuerpo */ }
    console.error(`shipment_labels -> ${r.status} ${motivo}`);
    return { error: `Mercado Libre no entregó la etiqueta${motivo ? ": " + motivo : ""}. Solo se puede imprimir cuando el envío está listo para despachar.`, status: 409 };
  }
  return new Response(r.body, { status: 200, headers: { "Content-Type": "application/pdf" } });
}
