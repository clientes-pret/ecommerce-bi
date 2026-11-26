// supabase/functions/rev-sync/logic.ts
//
// Lógica pura (sin red ni base) del dropshipping con revendedores: cuánto stock se ofrece, cómo se arma la clave que
// evita descontar dos veces en Ecomm-App, cómo se interpreta su respuesta y qué publicaciones hay que actualizar.
// Va aparte del index para poder probarla con `deno test` sin tocar nada real.

export const COLCHON_DEFECTO = 5;

/** Stock que se ofrece a revendedores = stock de depósito − colchón (5 unidades por defecto), nunca negativo.
 *  `tope` limita además la cantidad por SKU (opcional). Un valor no numérico cuenta como 0: ante la duda no se ofrece. */
export function stockOfrecido(stockDeposito: number, colchon: number = COLCHON_DEFECTO, tope?: number | null): number {
  if (!Number.isFinite(stockDeposito) || !Number.isFinite(colchon)) return 0;
  const oferta = Math.max(0, Math.floor(stockDeposito) - Math.max(0, Math.floor(colchon)));
  if (tope != null && Number.isFinite(tope)) return Math.min(oferta, Math.max(0, Math.floor(tope)));
  return oferta;
}

/** Colchón que se aplica a un revendedor en un producto (19/11/2026: colchón por revendedor). Gana el del producto
 *  (rev_catalogo.colchon_override), después el del revendedor y, si no tiene, el general. */
export function colchonPara(delProducto: number | null | undefined, delRevendedor: number | null | undefined, general: number): number {
  return delProducto ?? delRevendedor ?? general;
}

// ── Clave de idempotencia para Ecomm-App ────────────────────────────────────────────────────────────────────────
// Ecomm-App acepta letras, números y `. _ : # -`, hasta 100 caracteres. Los SKU reales traen espacios y barras
// (ej. "REM/001 M"), así que se sanean y se agrega un hash del SKU exacto: dos SKU que solo difieren en un carácter
// raro no pueden terminar con la misma clave (eso descontaría una sola vez por dos ventas distintas).

function hash32(texto: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < texto.length; i++) {
    h ^= texto.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

export type TipoMovimiento = "venta" | "cancelacion" | "devolucion";

/** Misma orden + mismo SKU + mismo tipo ⇒ siempre la misma clave, así reintentar o reprocesar nunca descuenta de más. */
export function claveIdempotente(mlOrderId: string | number, sku: string, tipo: TipoMovimiento = "venta"): string {
  const saneado = sku.trim().replace(/[^A-Za-z0-9._:#-]/g, "_");
  const base = `rev-${tipo}-${mlOrderId}-`;
  const sufijo = `-${hash32(sku.trim())}`;
  const maxSku = 100 - base.length - sufijo.length;
  return `${base}${saneado.slice(0, Math.max(0, maxSku))}${sufijo}`;
}

// ── Interpretación de la respuesta de POST /variantes/stock ─────────────────────────────────────────────────────
export type ResultadoEcomm =
  | { resultado: "aplicada"; stockDespues?: number }
  | { resultado: "aplicada_parcial"; faltante: number }
  | { resultado: "reintentar_misma_clave"; esperarSegundos: number }
  | { resultado: "rechazada"; motivo: string; stockActual?: number }
  | { resultado: "desconocido" };

/** Traduce la respuesta de Ecomm-App en una acción. Regla de oro: ante 5xx, timeout o `processing` se reintenta SIEMPRE
 *  con la misma clave; ante `outcome_unknown` NO se reintenta con otra clave (primero hay que mirar el stock real). */
export function interpretarRespuestaEcomm(
  http: number,
  cuerpo: { data?: Record<string, unknown>; error?: { code?: string } } | null,
  retryAfter?: number,
): ResultadoEcomm {
  const data = (cuerpo?.data ?? {}) as Record<string, unknown>;
  const estado = data.status as string | undefined;
  const linea = ((data.lines as Record<string, unknown>[] | undefined) ?? [])[0] ?? {};

  if (http === 429 || (http >= 500 && estado !== "outcome_unknown")) {
    return { resultado: "reintentar_misma_clave", esperarSegundos: retryAfter ?? 2 };
  }
  if (estado === "applied") return { resultado: "aplicada", stockDespues: linea.stock_after as number | undefined };
  if (estado === "applied_with_shortfall") {
    return { resultado: "aplicada_parcial", faltante: Number(linea.shortfall_quantity ?? 0) };
  }
  if (estado === "processing") return { resultado: "reintentar_misma_clave", esperarSegundos: retryAfter ?? 2 };
  if (estado === "rejected") {
    const rechazo = (data.rejection ?? {}) as Record<string, unknown>;
    const lineaRechazo = ((rechazo.lines as Record<string, unknown>[] | undefined) ?? [])[0] ?? {};
    return {
      resultado: "rechazada",
      motivo: String(rechazo.reason ?? "desconocido"),
      stockActual: lineaRechazo.current_stock as number | undefined,
    };
  }
  return { resultado: "desconocido" };
}

// ── Qué publicaciones hay que actualizar en ML ──────────────────────────────────────────────────────────────────
export interface Publicacion {
  id: number;
  sku: string;
  ultima_cantidad_enviada: number | null;
}

/** Publicaciones cuyo stock en ML difiere de lo que corresponde ofrecer hoy. Un SKU sin dato de stock se saltea
 *  (no se pisa con 0 por un faltante de datos). */
export function publicacionesAActualizar<T extends Publicacion>(ofrecidos: Map<string, number>, pubs: T[]): Array<T & { nueva: number }> {
  const out: Array<T & { nueva: number }> = [];
  for (const p of pubs) {
    const nueva = ofrecidos.get(p.sku);
    if (nueva === undefined) continue;
    if (p.ultima_cantidad_enviada !== nueva) out.push({ ...p, nueva });
  }
  return out;
}

/** ML no deja cambiar la cantidad de una publicación en revisión (`under_review`) ni cerrada: responde 400 y la función
 *  la reintentaba cada 5 min (medido 24/11/2026: 46 de 47 envíos). Se saltea hasta que la vinculación (cada hora) vea
 *  otro estado; como `ultima_cantidad_enviada` no cambia, en cuanto se active se le manda. */
export const ESTADOS_SIN_STOCK = new Set(["under_review", "closed", "inactive"]);
export const aceptaStock = (estado: string | null | undefined) => !ESTADOS_SIN_STOCK.has(String(estado ?? ""));

export function enLotes<T>(lista: T[], tamano: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < lista.length; i += tamano) out.push(lista.slice(i, i + tamano));
  return out;
}

// ── Motivo del movimiento en Ecomm-App (campo `reason`, 1–200 letras) ───────────────────────────────────────────
// Pedido del usuario (19/11/2026): que en Ecomm-App quede asentado de qué revendedor es cada movimiento, con el formato
// "venta-casalavan", "venta-casapinar", etc., más el número de venta de ML para poder rastrearla.
//   venta          → "venta-casalavan #2000012345678901 SKU PRET210-CBOGRIS"
//   cancelacion    → "cancelacion-casalavan #…" (vuelve el stock de una venta cancelada antes de despachar)
//   devolucion     → "devolucion-casalavan #…" (vuelve el stock cuando el depósito recibe la devolución)
export function motivoMovimiento(tipo: "venta" | "cancelacion" | "devolucion", revendedor: string | null | undefined, mlOrderId: string | number, sku: string): string {
  const quien = String(revendedor ?? "revendedor").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 40) || "revendedor";
  return `${tipo}-${quien} #${mlOrderId} SKU ${sku.trim()}`.slice(0, 200);
}

// ── Descuento real de ventas de revendedores (19/11/2026) ───────────────────────────────────────────────────────
// Solo corre en modo 'real' (rev_config.modo). Cada línea de venta (rev_ordenes) se descuenta UNA vez en Ecomm-App con
// la clave única de la venta; si después se cancela antes de salir del depósito, o vuelve como devolución en buen
// estado, el stock se devuelve UNA vez con su propia clave (cancelacion-/devolucion-).

export interface TransicionDescuento {
  estado: "descontada" | "sin_stock" | "error" | null; // null = queda como está y se reintenta con la MISMA clave
  descontado: boolean;                                  // true = Ecomm-App movió el stock
  detalle: string | null;
}

/** Qué hacer con una línea según la respuesta de Ecomm-App a su descuento. `codigoError` = error.code de un 4xx. */
export function transicionDescuento(r: ResultadoEcomm, http: number, codigoError?: string | null): TransicionDescuento {
  if (r.resultado === "aplicada") return { estado: "descontada", descontado: true, detalle: null };
  if (r.resultado === "aplicada_parcial") {
    return { estado: "descontada", descontado: true, detalle: `Ecomm-App descontó con faltante: ${r.faltante} unidad(es) no había en el depósito.` };
  }
  if (r.resultado === "reintentar_misma_clave") return { estado: null, descontado: false, detalle: "Ecomm-App pidió reintentar: se reintenta con la misma clave." };
  if (r.resultado === "rechazada") {
    return r.motivo === "insufficient_stock"
      ? { estado: "sin_stock", descontado: false, detalle: `Sin stock en el depósito para descontar${r.stockActual != null ? ` (hay ${r.stockActual})` : ""}: revisar la venta.` }
      : { estado: "error", descontado: false, detalle: `Ecomm-App rechazó el descuento (${r.motivo}).` };
  }
  // 4xx con código (SKU inexistente, campos inválidos...): no se movió nada; no se reintenta solo.
  if (http >= 400 && http < 500 && codigoError) return { estado: "error", descontado: false, detalle: `Ecomm-App: ${codigoError}.` };
  // outcome_unknown, 502 o respuesta rara: NO se reintenta con otra clave; hay que mirar el stock real.
  return { estado: "error", descontado: false, detalle: "Resultado desconocido en Ecomm-App: revisar el stock del SKU antes de reintentar." };
}

/** ¿Hay que devolver stock de esta línea? Solo si se descontó y todavía no se devolvió:
 *  cancelada antes de salir del depósito → 'cancelacion'; devolución recibida en buen estado → 'devolucion'. */
export function reposicionPendiente(f: { estado: string; descontado_at: string | null; repuesto_at: string | null; reposicion: string | null; recibido_estado: string | null }): "cancelacion" | "devolucion" | null {
  if (!f.descontado_at || f.repuesto_at || f.estado !== "cancelada") return null;
  if (f.reposicion === "inmediata") return "cancelacion";
  if (f.reposicion === "al_recibir" && f.recibido_estado === "bien") return "devolucion";
  return null;
}

/** Cuentas propias de Mercado Libre (Pret a Home, Casa Laván): su stock lo maneja Ecomm-App, así que este sistema NUNCA
 *  les manda stock ni descuenta sus ventas, sin importar en qué estado queden (pedido del usuario, 26/11/2026). Son las de
 *  "solo publicar" y las que figuran en `rev_config.cuentas_propias`. */
export function esCuentaPropia(r: { ml_user_id: number | string; solo_publicaciones?: boolean | null }, propias: unknown): boolean {
  if (r.solo_publicaciones === true) return true;
  return Array.isArray(propias) && propias.map(Number).includes(Number(r.ml_user_id));
}
