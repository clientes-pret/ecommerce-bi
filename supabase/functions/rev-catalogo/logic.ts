// supabase/functions/rev-catalogo/logic.ts
//
// Lógica pura (sin red ni base) de la actualización del catálogo de revendedores desde Ecomm-App.
// Pedido del usuario (24/11/2026): el catálogo son los SKU de Ecomm-App, no solo los que se vendieron en ML.

export interface VarianteEcomm {
  sku: string | null;
  is_combo: boolean;
}

export interface InfoSku {
  sku: string; // tal como está en Tiendanube (rev_catalogo_info)
  costo: number | null;
}

export interface Resultado {
  nuevos: string[]; // SKU a sumar, con la escritura de Tiendanube
  combos: number;
  sin_tiendanube: number;
  sin_costo: number;
  ya_estaban: number;
}

export const norm = (s: string) => s.trim().toLowerCase();

/** SKU de Ecomm-App que faltan en el catálogo y se pueden ofrecer:
 *  - no combos (vender un combo exige descontar cada componente; quedan para otra etapa, decisión del 11/11/2026),
 *  - que estén en Tiendanube (sin título ni fotos no se puede publicar) y con costo (sin costo no hay precio mayorista).
 *  Los que ya están en el catálogo, habilitados o no, no se tocan. */
export function skusParaSumar(variantes: VarianteEcomm[], catalogo: Set<string>, info: Map<string, InfoSku>): Resultado {
  const r: Resultado = { nuevos: [], combos: 0, sin_tiendanube: 0, sin_costo: 0, ya_estaban: 0 };
  const vistos = new Set<string>();
  for (const v of variantes) {
    const sku = (v.sku ?? "").trim();
    if (!sku) continue;
    const k = norm(sku);
    if (vistos.has(k)) continue;
    vistos.add(k);
    if (catalogo.has(k)) { r.ya_estaban++; continue; }
    if (v.is_combo) { r.combos++; continue; }
    const i = info.get(k);
    if (!i) { r.sin_tiendanube++; continue; }
    if (!(Number(i.costo) > 0)) { r.sin_costo++; continue; }
    r.nuevos.push(i.sku);
  }
  return r;
}
