// supabase/functions/rev-ordenes/logic.ts
//
// Lógica pura (sin red ni base) del registro de ventas de revendedores: cuándo una orden de ML cuenta como venta, y a qué
// SKU corresponde cada línea. Una orden = una o más líneas; en `rev_ordenes` va una fila por orden y SKU.

export interface OrdenML {
  id: number;
  shipping?: { id?: number | null } | null;
  mediations?: Array<{ id: number }> | null;
  date_created?: string | null;
  status?: string | null;
  payments?: Array<{ status?: string | null }> | null;
  order_items?: Array<{
    item: { id: string; seller_sku?: string | null; seller_custom_field?: string | null; variation_id?: number | null };
    quantity: number;
    unit_price?: number | null;
  }> | null;
}

/** Estado con el que se registra la orden:
 *  - 'cancelada': ML la canceló.
 *  - 'sombra': no cancelada y con algún pago aprobado = venta confirmada (en modo sombra solo se registra, no se descuenta).
 *  - null: todavía no está paga (pago pendiente, en mediación, rechazado...): no se registra; ML vuelve a avisar cuando cambie. */
export function estadoDeOrden(o: OrdenML): "sombra" | "cancelada" | null {
  if (o.status === "cancelled") return "cancelada";
  const paga = (o.payments ?? []).some((p) => p.status === "approved");
  return paga ? "sombra" : null;
}

/** Las ventas hechas ANTES de que el revendedor se conectara no son parte del sistema (ni se descuentan ni se cobran).
 *  Una orden vieja que ML vuelve a avisar por un cambio posterior se ignora. Si falta alguna de las dos fechas no se descarta:
 *  ante la duda, la venta se registra. */
export function esAnteriorALaConexion(o: OrdenML, conectadoAt: string | null | undefined): boolean {
  if (!o.date_created || !conectadoAt) return false;
  const orden = Date.parse(o.date_created);
  const conexion = Date.parse(conectadoAt);
  if (!Number.isFinite(orden) || !Number.isFinite(conexion)) return false;
  return orden < conexion;
}

export interface LineaOrden {
  sku: string;
  ml_item_id: string;
  cantidad: number;
  precio_unitario: number | null;
  reconocida: boolean; // false = no se pudo asociar a un SKU del catálogo
}

const norm = (s: string) => s.trim().toLowerCase();

/** Líneas de la orden agrupadas por SKU (la restricción única de `rev_ordenes` es orden + SKU).
 *  Cómo se reconoce el SKU de cada línea, en este orden:
 *   1. la publicación ya vinculada (`skuDePublicacion`: ítem + variación → SKU), que incluye los vínculos hechos a mano;
 *   2. el SKU que el revendedor cargó en ML (`skuDeCatalogo`: SKU de ML → SKU del catálogo habilitado);
 *   3. si ninguno sirve, la línea se registra igual con el SKU de ML (o "?<ítem>") y `reconocida = false`: una venta nunca
 *      se pierde por no reconocerla; queda a la vista para corregirla. */
export function lineasDeOrden(
  o: OrdenML,
  skuDePublicacion: (itemId: string, variationId: number | null) => string | null,
  skuDeCatalogo: (skuMl: string) => string | null,
): LineaOrden[] {
  const porSku = new Map<string, LineaOrden>();
  for (const l of o.order_items ?? []) {
    const cantidad = Math.floor(Number(l.quantity));
    if (!Number.isFinite(cantidad) || cantidad <= 0) continue;
    const itemId = l.item.id;
    const skuMl = (l.item.seller_sku ?? l.item.seller_custom_field ?? "").trim();

    const vinculado = skuDePublicacion(itemId, l.item.variation_id ?? null);
    const delCatalogo = !vinculado && skuMl ? skuDeCatalogo(skuMl) : null;
    const sku = vinculado ?? delCatalogo ?? (skuMl || `?${itemId}`);
    const reconocida = Boolean(vinculado ?? delCatalogo);

    const previa = porSku.get(norm(sku));
    if (previa) previa.cantidad += cantidad;
    else porSku.set(norm(sku), { sku, ml_item_id: itemId, cantidad, precio_unitario: l.unit_price ?? null, reconocida });
  }
  return [...porSku.values()];
}

/** Conciliación: ¿hay que volver a avisar esta orden a `rev-ordenes`?
 *  - Paga en ML y sin ninguna fila en la base → sí (se perdió el aviso).
 *  - Cancelada en ML y alguna fila todavía como venta ('sombra'/'nueva') → sí (se perdió el aviso de la cancelación).
 *  - Todo lo demás → no: ya está al día, no está paga, o está en un estado que esta lógica nunca toca ('descontada', etc.). */
export function debeReavisar(esperado: "sombra" | "cancelada" | null, filasEstados: string[] | undefined): boolean {
  if (esperado === "sombra") return !filasEstados || filasEstados.length === 0;
  if (esperado === "cancelada") return Boolean(filasEstados?.some((e) => e === "sombra" || e === "nueva"));
  return false;
}

/** Qué hacer con una orden que ya estaba registrada, según el estado que figura ahora en ML.
 *  Solo se cambia lo que está en 'nueva'/'sombra' (todavía no descontó nada). Una orden 'descontada', 'sin_stock' o 'error'
 *  jamás se toca desde acá: eso lo resuelve un proceso con más cuidado. */
export function nuevoEstadoParaExistente(actual: string, ahora: "sombra" | "cancelada"): string | null {
  if (actual === ahora) return null;
  if (actual === "nueva" || actual === "sombra") return ahora;
  return null;
}

// ───────────────────────── Cancelaciones y devoluciones (19/11/2026) ─────────────────────────
// Regla acordada con el usuario: si la venta se cancela ANTES de que el paquete salga del depósito, el stock vuelve solo;
// si se cancela o se devuelve DESPUÉS, vuelve recién cuando el depósito recibe el producto (Operaciones → Devoluciones).

export interface EnvioML { status?: string | null; substatus?: string | null }

// Subestados de "listo para enviar" en los que el paquete ya lo tiene el correo / la mensajería.
const YA_SALIO = new Set(["picked_up", "dropped_off", "in_hub", "authorized_by_carrier", "in_transit", "out_for_delivery", "soon_deliver"]);

/** ¿El paquete ya había salido del depósito? null = no se sabe (sin envío o estado desconocido). */
export function fueDespachada(e: EnvioML | null | undefined): boolean | null {
  const s = e?.status ?? null;
  if (!s) return null;
  if (s === "shipped" || s === "delivered" || s === "not_delivered") return true;
  if (s === "ready_to_ship" && YA_SALIO.has(String(e?.substatus ?? ""))) return true;
  if (s === "pending" || s === "handling" || s === "ready_to_ship" || s === "cancelled") return false;
  return null;
}

/** Cuándo vuelve el stock de una venta cancelada. Si no se sabe si salió, se espera a recibirlo (mejor no inventar stock). */
export const reposicionPara = (despachada: boolean | null): "inmediata" | "al_recibir" => (despachada === false ? "inmediata" : "al_recibir");

export interface ReclamoML { id: number; type?: string | null; stage?: string | null; status?: string | null; last_updated?: string | null; resolution?: { reason?: string | null } | null }
export interface DevolucionML { status?: string | null; status_money?: string | null; subtype?: string | null; shipments?: Array<{ status?: string | null; type?: string | null }> | null }

export interface ReclamoGuardado {
  reclamo_id: number; tipo: string | null; etapa: string | null; estado: string | null; resolucion: string | null; actualizado: string | null;
  devolucion_estado: string | null; dinero: string | null; envio_vuelta: string | null;
}

/** Lo que se guarda de cada reclamo de una venta (rev_ordenes.devolucion = { reclamos: [...] }). */
export function resumenReclamo(c: ReclamoML, d: DevolucionML | null): ReclamoGuardado {
  const vuelta = (d?.shipments ?? []).find((s) => s.type === "return") ?? (d?.shipments ?? [])[0];
  return {
    reclamo_id: c.id, tipo: c.type ?? null, etapa: c.stage ?? null, estado: c.status ?? null,
    resolucion: c.resolution?.reason ?? null, actualizado: c.last_updated ?? null,
    devolucion_estado: d?.status ?? null, dinero: d?.status_money ?? null, envio_vuelta: vuelta?.status ?? null,
  };
}

/** Conciliación de reclamos: ¿hay que volver a leer la venta? Sí si el reclamo no está guardado o cambió desde entonces. */
export function reclamoCambio(c: ReclamoML, guardado: { reclamos?: ReclamoGuardado[] } | null | undefined): boolean {
  const g = guardado?.reclamos?.find((r) => r.reclamo_id === c.id);
  return !g || (g.actualizado ?? "") !== (c.last_updated ?? "") || g.estado !== (c.status ?? null);
}
