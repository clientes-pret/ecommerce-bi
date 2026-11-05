// supabase/functions/ventas-webhook/logic.ts
//
// Lógica pura (sin red ni base) del webhook de ventas, para poder testearla con `deno test`.
// Replica EXACTAMENTE lo que hace el sync diario en Python para Tiendanube:
//   - reposicion/ventas_sync.py::_tn_orders_updated_since  → qué órdenes cuentan / se cancelan
//   - reposicion/core.py::tn_orders_to_lines               → cómo se aplana la orden en líneas
//   - reposicion/ventas_sync.py::_dedupe_lineas            → líneas repetidas se suman
// Si cambia una de esas, cambiar esta también (y al revés).

// deno-lint-ignore no-explicit-any
export type Json = any;

export type Linea = {
  canal: string;
  order_id: string;
  item_id: string; // '' = no aplica (TN)
  sku: string;
  cantidad: number;
  fecha: string; // YYYY-MM-DD
  creado_at: string; // ISO
};

export type Decision =
  | { accion: "guardar"; lineas: Linea[] }
  | { accion: "cancelar"; order_id: string }
  | { accion: "ignorar"; motivo: string };

/** Compara dos strings sin cortar en el primer carácter distinto (para el token del webhook). */
export function iguales(a: string, b: string): boolean {
  const ea = new TextEncoder().encode(a);
  const eb = new TextEncoder().encode(b);
  let diff = ea.length ^ eb.length;
  const n = Math.max(ea.length, eb.length);
  for (let i = 0; i < n; i++) diff |= (ea[i] ?? 0) ^ (eb[i] ?? 0);
  return diff === 0;
}

/** Tiendanube manda fechas como "2026-11-05T17:00:00+0000"; V8 no siempre las parsea sin los dos puntos. */
export function parsearFechaTN(s: string): Date | null {
  if (!s) return null;
  const norm = s.replace(/Z$/, "+00:00").replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
  const d = new Date(norm);
  return isNaN(d.getTime()) ? null : d;
}

/**
 * Misma fecha que Python: `datetime.fromisoformat(...).date()` toma el día en el offset que trae
 * el string (no convierte a UTC), así que acá se toma el día tal cual viene escrito.
 */
export function fechaDelString(s: string): string {
  return s.slice(0, 10);
}

/** Aplana una orden de TN → líneas de repo_ventas_items (tn_orders_to_lines + _dedupe_lineas). */
export function lineasDeOrdenTN(canal: string, o: Json): Linea[] {
  const orderId = String(o?.id ?? "");
  if (!orderId) return [];
  const creado = parsearFechaTN(String(o?.created_at ?? ""));
  if (!creado) return [];
  const porClave = new Map<string, Linea>();
  for (const item of o?.products ?? []) {
    const sku = String(item?.sku || item?.variant_id || "");
    if (!sku) continue;
    const cantidad = Number(item?.quantity ?? 1);
    const clave = `${orderId}|${sku}`;
    const previa = porClave.get(clave);
    if (previa) {
      previa.cantidad += cantidad; // TN a veces separa la misma unidad en dos entradas
    } else {
      porClave.set(clave, {
        canal,
        order_id: orderId,
        item_id: "",
        sku,
        cantidad,
        fecha: fechaDelString(String(o.created_at)),
        creado_at: creado.toISOString(),
      });
    }
  }
  return [...porClave.values()];
}

/** Qué hacer con la orden: igual que el sync diario (cancelada → baja lógica; paid/authorized → venta). */
export function decidir(canal: string, o: Json): Decision {
  if (!o || o.id == null) return { accion: "ignorar", motivo: "orden vacía" };
  if (o.status === "cancelled") return { accion: "cancelar", order_id: String(o.id) };
  if (o.payment_status === "paid" || o.payment_status === "authorized") {
    return { accion: "guardar", lineas: lineasDeOrdenTN(canal, o) };
  }
  return { accion: "ignorar", motivo: `pago ${o.payment_status ?? "?"}, estado ${o.status ?? "?"}` };
}

/** {store_id → canal} a partir del secret ARMADO_CREDENCIALES ({tn: {pret: {store_id}, lavan: {...}}}). */
export function canalesPorStoreId(cred: Json): Record<string, string> {
  const out: Record<string, string> = {};
  for (const tienda of ["pret", "lavan"]) {
    const sid = cred?.tn?.[tienda]?.store_id;
    if (sid != null) out[String(sid)] = `tn_${tienda}`;
  }
  return out;
}
