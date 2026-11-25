// supabase/functions/rev-vincular/logic.ts
//
// Lógica pura (sin red ni base) de la vinculación automática de publicaciones de revendedores con SKU del catálogo.
// Una publicación de ML se reconoce por su `seller_custom_field` (el SKU que el revendedor cargó al publicar); si tiene
// variaciones, cada variación puede traer el suyo.

export interface AtributoML {
  id?: string | null;
  value_name?: string | null;
}

export interface ItemML {
  id: string;
  status?: string | null;
  price?: number | null;
  seller_custom_field?: string | null;
  user_product_id?: string | null;
  attributes?: AtributoML[] | null;
  variations?: Array<{
    id: number;
    price?: number | null;
    seller_custom_field?: string | null;
    user_product_id?: string | null;
    attributes?: AtributoML[] | null;
  }> | null;
}

export interface VinculoML {
  ml_item_id: string;
  ml_variation_id: number | null;
  sku_ml: string | null;
  precio: number | null;
  estado_ml: string | null;
  /** "Producto de usuario" de ML de esta fila (variación o publicación). Dos publicaciones de la misma cuenta con el mismo
   *  producto de usuario son el mismo producto y comparten stock ("Sincronizada con #…" en el panel de ML). */
  producto_usuario?: string;
}

/** Los SKU se comparan sin distinguir mayúsculas ni espacios en los extremos (así los compara también Ecomm-App). */
export const normSku = (s: string) => s.trim().toLowerCase();

const limpio = (s: string | null | undefined): string | null => {
  const t = (s ?? "").trim();
  return t === "" ? null : t;
};

/** El SKU del vendedor puede estar en dos lugares: el campo clásico `seller_custom_field` o el atributo `SELLER_SKU`
 *  (en el modelo nuevo de ML muchas publicaciones lo traen solo ahí, con `seller_custom_field` vacío). Se prueba primero
 *  el campo clásico y después el atributo. */
function skuDeAtributos(attrs: AtributoML[] | null | undefined): string | null {
  for (const a of attrs ?? []) if (a?.id === "SELLER_SKU") return limpio(a.value_name);
  return null;
}

/** Una fila por publicación, o una por variación si tiene variaciones (cada una con el SKU propio o, si no, el del item). */
export function vinculosDeItem(it: ItemML): VinculoML[] {
  const estado = it.status ?? null;
  const skuItem = limpio(it.seller_custom_field) ?? skuDeAtributos(it.attributes);
  const variaciones = it.variations ?? [];
  if (variaciones.length > 0) {
    return variaciones.map((v) => {
      const up = limpio(v.user_product_id);
      return {
        ml_item_id: it.id,
        ml_variation_id: v.id,
        sku_ml: limpio(v.seller_custom_field) ?? skuDeAtributos(v.attributes) ?? skuItem,
        precio: v.price ?? it.price ?? null,
        estado_ml: estado,
        ...(up ? { producto_usuario: up } : {}),
      };
    });
  }
  const up = limpio(it.user_product_id);
  return [{ ml_item_id: it.id, ml_variation_id: null, sku_ml: skuItem, precio: it.price ?? null, estado_ml: estado, ...(up ? { producto_usuario: up } : {}) }];
}

/** Mercado Libre pasó las publicaciones con variaciones al modelo nuevo: cada variación quedó "sincronizada" con una
 *  publicación aparte (mismo producto de usuario, mismo stock), y muchas veces el SKU está cargado SOLO en esa
 *  publicación nueva (caso real de PINARHOME, 25/11/2026: MLA1852134043 var. 204818550051 sin SKU, sincronizada con
 *  MLA3585121682 que tiene PRETA-3VISON). A las filas sin SKU les completa el SKU de otra publicación de la misma cuenta
 *  con el mismo producto de usuario, si todas las que lo tienen dicen el mismo SKU. `todas` puede incluir publicaciones
 *  cerradas: siguen diciendo qué producto es. */
export function completarPorProductoDeUsuario(filas: VinculoML[], todas: VinculoML[] = filas): VinculoML[] {
  const skus = new Map<string, Set<string>>();
  for (const v of todas) {
    if (!v.producto_usuario || !v.sku_ml) continue;
    if (!skus.has(v.producto_usuario)) skus.set(v.producto_usuario, new Set());
    skus.get(v.producto_usuario)!.add(normSku(v.sku_ml));
  }
  const original = new Map<string, string>();
  for (const v of todas) if (v.sku_ml) original.set(normSku(v.sku_ml), v.sku_ml);
  return filas.map((v) => {
    if (v.sku_ml || !v.producto_usuario) return v;
    const s = skus.get(v.producto_usuario);
    return s && s.size === 1 ? { ...v, sku_ml: original.get([...s][0]) ?? null } : v;
  });
}

export interface PubExistente {
  id: number;
  ml_item_id: string;
  ml_variation_id: number | null;
  sku: string | null;
  vinculo: string; // 'auto' | 'manual'
  estado_ml: string | null;
  precio: number | null;
}

export interface Plan {
  nuevas: Array<{ ml_item_id: string; ml_variation_id: number | null; sku: string | null; estado_ml: string | null; precio: number | null }>;
  actualizar: Array<{ id: number; sku: string | null; estado_ml: string | null; precio: number | null }>;
  sin_cambios: number;
  sin_reconocer: number; // publicaciones que quedan sin SKU del catálogo (el revendedor puede vincularlas a mano en el portal)
}

const clave = (item: string, variacion: number | null) => `${item}|${variacion ?? 0}`;

/** Decide qué altas y cambios hay que hacer en `rev_publicaciones`.
 *  - `catalogo`: SKU normalizado → SKU tal como está en el catálogo (solo los habilitados).
 *  - Una publicación vinculada A MANO nunca se pisa en el SKU (solo se refrescan estado y precio).
 *  - Una publicación que ya no aparece en ML no se borra: la vinculación nunca elimina filas. */
export function planificarVinculos(vinculos: VinculoML[], catalogo: Map<string, string>, existentes: PubExistente[]): Plan {
  const porClave = new Map(existentes.map((e) => [clave(e.ml_item_id, e.ml_variation_id), e]));
  const plan: Plan = { nuevas: [], actualizar: [], sin_cambios: 0, sin_reconocer: 0 };

  for (const v of vinculos) {
    const reconocido = v.sku_ml ? catalogo.get(normSku(v.sku_ml)) ?? null : null;
    const ex = porClave.get(clave(v.ml_item_id, v.ml_variation_id));

    if (!ex) {
      plan.nuevas.push({ ml_item_id: v.ml_item_id, ml_variation_id: v.ml_variation_id, sku: reconocido, estado_ml: v.estado_ml, precio: v.precio });
      if (!reconocido) plan.sin_reconocer++;
      continue;
    }

    const skuFinal = ex.vinculo === "manual" ? ex.sku : reconocido;
    if (!skuFinal) plan.sin_reconocer++;
    const cambio = skuFinal !== ex.sku || v.estado_ml !== ex.estado_ml || (v.precio ?? null) !== (ex.precio ?? null);
    if (cambio) plan.actualizar.push({ id: ex.id, sku: skuFinal, estado_ml: v.estado_ml, precio: v.precio });
    else plan.sin_cambios++;
  }
  return plan;
}
