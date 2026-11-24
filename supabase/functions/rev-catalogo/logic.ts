// supabase/functions/rev-catalogo/logic.ts
//
// Lógica pura (sin red ni base) de la actualización del catálogo de revendedores desde Ecomm-App.
// Pedido del usuario (24/11/2026): el catálogo son los SKU de Ecomm-App (la fuente de verdad del stock), no los de
// Tiendanube ni los que se vendieron en ML. Tiendanube solo aporta título, fotos y costo cuando los tiene.

export interface VarianteEcomm {
  sku: string | null;
  is_combo: boolean;
  description?: string | null;
  variant_description?: string | null;
  image_url?: string | null;
  price?: number | null;
}

export interface InfoSku {
  sku: string; // tal como está en Tiendanube (rev_catalogo_info)
  costo: number | null;
}

/** Fila de `rev_catalogo_info` armada con lo que tiene Ecomm-App, para un SKU que no está en Tiendanube. Sin costo:
 *  la API no lo da con el permiso de solo lectura; sus ventas quedan "sin precio mayorista" hasta que se cargue. */
export interface InfoEcomm {
  sku: string;
  titulo: string;
  descripcion: string;
  fotos: string[];
  costo: null;
  precio_sugerido: number | null;
  fuente: "ecomm";
}

export interface Resultado {
  nuevos: string[]; // SKU a sumar al catálogo (con la escritura de Tiendanube si está ahí; si no, la de Ecomm-App)
  info_nueva: InfoEcomm[]; // título/foto de Ecomm-App para los que no están en Tiendanube
  combos: number;
  mr: number;
  ya_estaban: number;
  sin_tiendanube: number; // de los nuevos: sin título/fotos de Tiendanube
  sin_costo: number; // de los nuevos: sin costo (sin precio mayorista)
}

export const norm = (s: string) => s.trim().toLowerCase();

/** SKU "MR-…" de Ecomm-App: no se ofrecen a revendedores (pedido del usuario, 24/11/2026). */
export const esMR = (sku: string) => /^mr-/i.test(sku.trim());

const limpio = (s: string | null | undefined) => (s ?? "").trim();

/** SKU de Ecomm-App que faltan en el catálogo:
 *  - no combos (vender un combo exige descontar cada componente; quedan para otra etapa, decisión del 11/11/2026),
 *  - no "MR-".
 *  Los que ya están en el catálogo, habilitados o no, no se tocan. */
export function skusParaSumar(variantes: VarianteEcomm[], catalogo: Set<string>, info: Map<string, InfoSku>): Resultado {
  const r: Resultado = { nuevos: [], info_nueva: [], combos: 0, mr: 0, ya_estaban: 0, sin_tiendanube: 0, sin_costo: 0 };
  const vistos = new Set<string>();
  for (const v of variantes) {
    const sku = limpio(v.sku);
    if (!sku) continue;
    const k = norm(sku);
    if (vistos.has(k)) continue;
    vistos.add(k);
    if (catalogo.has(k)) { r.ya_estaban++; continue; }
    if (v.is_combo) { r.combos++; continue; }
    if (esMR(sku)) { r.mr++; continue; }
    const i = info.get(k);
    if (i) {
      r.nuevos.push(i.sku);
      if (!(Number(i.costo) > 0)) r.sin_costo++;
      continue;
    }
    r.nuevos.push(sku);
    r.sin_tiendanube++;
    r.sin_costo++;
    const nombre = limpio(v.description), variante = limpio(v.variant_description);
    r.info_nueva.push({
      sku,
      titulo: [nombre || sku, variante].filter(Boolean).join(" - "),
      descripcion: "",
      fotos: limpio(v.image_url) ? [limpio(v.image_url)] : [],
      costo: null,
      precio_sugerido: Number(v.price) > 0 ? Number(v.price) : null,
      fuente: "ecomm",
    });
  }
  return r;
}
