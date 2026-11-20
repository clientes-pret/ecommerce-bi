// supabase/functions/rev-publicar/logic.ts
//
// Reglas para armar la publicación de un revendedor copiando la de Pret a Home (sin red ni base: se prueban en logic_test.ts).
// Decisiones del usuario (18–19/11/2026):
//   - Se copia de la publicación de Pret en Mercado Libre (no de Tiendanube): título, ficha técnica, fotos de cada color,
//     SKU, descripción. Nunca las 2 fotos institucionales (eso lo resuelve _shared/fotos_marca.ts).
//   - Tradicional: modelo nuevo de ML (una publicación por color, mismo family_name → ML las agrupa como un producto).
//   - "Con mi marca": solo productos marca Pret (rev_config.marcas_propias). Se reemplaza la marca y "Pret" en los textos, y
//     va sin código de barras (el GTIN es de la marca Pret).
//   - Catálogo: sumarse a la ficha de ML de Pret (catalog_product_id); la marca no se cambia.
//   - Clásica (gold_special, sin cuotas sin interés) y/o Premium (gold_pro, con cuotas sin interés): elige el revendedor.
//   - La descripción nunca lleva el bloque de la tienda de Pret ("SOMOS PRET A HOME… local en CABA, Flex, factura A y B").

export const TIPOS_VENTA = { gold_special: "Clásica", gold_pro: "Premium" } as const;
export type TipoVenta = keyof typeof TIPOS_VENTA;

export interface Atributo { id: string; value_name?: string | null; value_id?: string | null }
export interface VariacionML {
  id: number;
  price?: number;
  attribute_combinations?: Atributo[];
  attributes?: Atributo[];
  picture_ids?: string[];
  seller_custom_field?: string | null;
}
export interface ItemML {
  id: string;
  title: string;
  category_id: string;
  currency_id?: string;
  price?: number;
  attributes?: Atributo[];
  variations?: VariacionML[];
  pictures?: { id: string; secure_url?: string; url?: string }[];
  seller_custom_field?: string | null;
}

// Atributos que NO se copian: son de la cuenta de Pret, los calcula ML, o ML los rechaza ("not modifiable").
// Las medidas del paquete (SELLER_PACKAGE_HEIGHT/WIDTH/LENGTH/WEIGHT) SÍ se copian: en varias categorías ML las exige
// para publicar ("are all required", 20/11/2026, fundas de almohada) y el producto es el mismo que despacha Pret.
export const NO_COPIAR = new Set([
  "GIFTABLE", "IMPORT_DUTY", "VALUE_ADDED_TAX", "ITEM_CONDITION", "SELLER_SKU", "GTIN", "EMPTY_GTIN_REASON", "MAIN_COLOR",
  "PRODUCT_FEATURES", "SHIPMENT_PACKING", "IS_TOM_BRAND", "FILTRABLE_COLOR",
]);
export const MAX_FOTOS = 10;

/** Medidas del paquete (alto, ancho, largo, peso) de una publicación de Pret. ML las exige en varias categorías, también
 *  para sumarse a una ficha de catálogo. Devuelve [] si no están las cuatro. */
export function medidasPaquete(attrs: Atributo[] | undefined): Atributo[] {
  const m = (attrs ?? []).filter((a) => /^SELLER_PACKAGE_(HEIGHT|WIDTH|LENGTH|WEIGHT)$/.test(a.id) && a.value_name)
    .map((a) => ({ id: a.id, value_name: a.value_name }));
  return m.length === 4 ? m : [];
}
const SIN_GTIN = "El producto no tiene código registrado";

const norm = (s: string | null | undefined) => String(s ?? "").trim().toLowerCase();

/** Marca de la publicación (atributo BRAND). */
export const marcaDe = (it: ItemML) => it.attributes?.find((a) => a.id === "BRAND")?.value_name ?? null;

/** ¿Se puede publicar con la marca del revendedor? Solo si la marca está en la lista de marcas propias. */
export const esMarcaPropia = (marca: string | null, propias: string[]) => propias.map(norm).includes(norm(marca));

/** SKU de una variación (atributo SELLER_SKU, o el campo viejo seller_custom_field). */
export function skuDe(attrs: Atributo[] | undefined, scf?: string | null): string | null {
  const v = attrs?.find((a) => a.id === "SELLER_SKU" && a.value_name)?.value_name ?? scf ?? "";
  return v.trim() || null;
}

/** Nombre del color (o la combinación) de una variación: "Gris claro", "Crema · Liso". */
export const colorDe = (v: VariacionML) =>
  (v.attribute_combinations ?? []).map((a) => a.value_name).filter(Boolean).join(" · ") || null;

/** Reemplaza las menciones de la marca Pret ("Pret a Home", "Pretahome", "Pret.", "Pret") por la marca nueva. */
export function reemplazarMarca(texto: string, marca: string): string {
  return texto
    .replace(/pret\s*[aà]\s*home/gi, marca)
    .replace(/pretahome/gi, marca)
    // "Pret." puede ser la marca con punto ("Sábanas Pret. Hoteleras") o el fin de una oración ("de la marca Pret. Con
    // unas…"): el punto se conserva solo si después viene una mayúscula o termina el texto.
    .replace(/\bpret\b\.?/gi, (m, pos: number, todo: string) => {
      const resto = todo.slice(pos + m.length);
      return marca + (m.endsWith(".") && (/^\s+[A-ZÁÉÍÓÚÑ¡¿]/.test(resto) || /^\s*$/.test(resto)) ? "." : "");
    });
}

/** Descripción para el revendedor: sin el bloque de la tienda de Pret (desde "SOMOS PRET A HOME" hasta el final) ni las
 *  líneas separadoras que lo preceden; con marca propia, además, sin menciones a Pret. */
export function limpiarDescripcion(texto: string, marcaNueva: string | null): string {
  let t = String(texto ?? "").replace(/\r\n/g, "\n");
  const m = t.search(/somos\s+pret\s*[aà]\s*home/i);
  if (m >= 0) t = t.slice(0, m);
  t = t.replace(/(\n[\s\-_=*.·•]*)+$/g, "").trimEnd();
  if (marcaNueva) t = reemplazarMarca(t, marcaNueva);
  return t.slice(0, 50000);
}

/** Normaliza el nombre de marca que escribe el revendedor. Devuelve null si no sirve. */
export function marcaValida(m: unknown): string | null {
  const s = String(m ?? "").replace(/\s+/g, " ").trim();
  if (s.length < 2 || s.length > 60) return null;
  if (/pret/i.test(s)) return null; // "con mi marca" es justamente otra marca
  return s;
}

const sinAcentos = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

/** Nombre del producto para ML (family_name, máx. 60). ML le agrega el color al final, así que si el título de Pret ya
 *  termina en el color ("…350 Ml Blanco") se lo saca, y se corta en una palabra entera (nunca "…Ml Bla"). */
export function nombreFamilia(titulo: string, colores: (string | null | undefined)[]): string {
  let t = titulo.replace(/\s+/g, " ").trim();
  for (const c of colores.filter(Boolean).map((x) => String(x).trim()).sort((a, b) => b.length - a.length)) {
    const sa = sinAcentos(t), sc = sinAcentos(c);
    if (sc && sa.endsWith(" " + sc)) { t = t.slice(0, t.length - c.length).replace(/[\s\-–,]+$/, "").trim(); break; }
  }
  if (t.length <= 60) return t;
  const corte = t.slice(0, 61);
  const sp = corte.lastIndexOf(" ");
  return (sp > 30 ? corte.slice(0, sp) : t.slice(0, 60)).replace(/[\s\-–,]+$/, "");
}

const copiar = (a: Atributo): Atributo => ({ id: a.id, value_name: a.value_name, ...(a.value_id ? { value_id: a.value_id } : {}) });

export interface OpcionesTradicional {
  variacion: VariacionML;
  fotos: string[];               // urls ya filtradas (sin fotos de marca)
  precio: number;
  cantidad: number;
  tipoVenta: TipoVenta;
  marca: string | null;          // null = tal cual (marca original)
}

/** Publicación tradicional de UN color (modelo nuevo de ML: family_name + atributos del color). */
export function payloadTradicional(it: ItemML, o: OpcionesTradicional): Record<string, unknown> {
  const sku = skuDe(o.variacion.attributes, o.variacion.seller_custom_field);
  const va = new Map((o.variacion.attributes ?? []).filter((a) => a.value_name).map((a) => [a.id, a]));
  let attrs: Atributo[] = (it.attributes ?? []).filter((a) => a.value_name && !NO_COPIAR.has(a.id)).map(copiar);
  attrs.push(...(o.variacion.attribute_combinations ?? []).filter((a) => a.value_name).map(copiar));
  attrs = attrs.filter((a, i) => attrs.findIndex((b) => b.id === a.id) === i);
  if (sku) attrs.push({ id: "SELLER_SKU", value_name: sku });
  let familia = it.title;
  if (o.marca) {
    // Marca propia: BRAND nuevo, sin GTIN, y "Pret" fuera de los textos (sin value_id: el código era de la marca vieja).
    attrs = attrs.map((a) => a.id === "BRAND" ? { id: "BRAND", value_name: o.marca }
      : /pret/i.test(String(a.value_name)) ? { id: a.id, value_name: reemplazarMarca(String(a.value_name), o.marca!) } : a);
    if (!attrs.some((a) => a.id === "BRAND")) attrs.push({ id: "BRAND", value_name: o.marca });
    attrs.push({ id: "EMPTY_GTIN_REASON", value_name: SIN_GTIN });
    familia = reemplazarMarca(familia, o.marca);
  } else if (va.get("GTIN")?.value_name) {
    attrs.push({ id: "GTIN", value_name: va.get("GTIN")!.value_name });
  }
  const colores = [
    ...(o.variacion.attribute_combinations ?? []).filter((a) => a.id === "COLOR").map((a) => a.value_name),
    ...(it.variations?.length ? [] : (it.attributes ?? []).filter((a) => a.id === "COLOR" || a.id === "MAIN_COLOR").map((a) => a.value_name)),
  ];
  return {
    family_name: nombreFamilia(familia, colores),
    category_id: it.category_id,
    currency_id: it.currency_id ?? "ARS",
    price: Math.round(o.precio * 100) / 100,
    available_quantity: o.cantidad,
    buying_mode: "buy_it_now",
    condition: "new",
    listing_type_id: o.tipoVenta,
    pictures: o.fotos.slice(0, MAX_FOTOS).map((source) => ({ source })),
    attributes: attrs,
    ...(sku ? { seller_custom_field: sku } : {}),
    shipping: { mode: "me2", local_pick_up: false, free_shipping: false },
  };
}

/** Publicación de catálogo: se suma a la ficha de ML (catalog_product_id) con su SKU. La ficha pone título, fotos y marca. */
export function payloadCatalogo(o: { categoryId: string; catalogProductId: string; sku: string; precio: number; cantidad: number; tipoVenta: TipoVenta; medidas?: Atributo[] }) {
  return {
    category_id: o.categoryId,
    catalog_product_id: o.catalogProductId,
    catalog_listing: true,
    currency_id: "ARS",
    price: Math.round(o.precio * 100) / 100,
    available_quantity: o.cantidad,
    buying_mode: "buy_it_now",
    condition: "new",
    listing_type_id: o.tipoVenta,
    attributes: [{ id: "SELLER_SKU", value_name: o.sku }, ...(o.medidas ?? [])],
    shipping: { mode: "me2", local_pick_up: false, free_shipping: false },
  };
}

/** Errores (no advertencias) de una respuesta de /items/validate o /items de ML, como texto legible. */
export function erroresML(status: number, cuerpo: unknown): string[] {
  if (status >= 200 && status < 300) return [];
  const c = cuerpo as { message?: string; error?: string; cause?: { type?: string; code?: string; message?: string }[] } | null;
  const errs = (c?.cause ?? []).filter((x) => x.type !== "warning").map((x) => x.message || x.code || "error");
  if (errs.length) return errs;
  // 400 "Validation error" con solo advertencias = ML lo acepta.
  if ((c?.cause ?? []).length && (c?.cause ?? []).every((x) => x.type === "warning")) return [];
  return [c?.message || c?.error || `HTTP ${status}`];
}

export interface Pedido {
  skus: string[];
  tradicional: boolean;
  catalogo: boolean;
  marca: string | null;
  precio: number;
  tiposVenta: TipoVenta[];
  pausada: boolean;
  recargoCuotas: number;   // % extra para Premium (cuotas sin interés), así la ganancia queda igual que en Clásica
}

export const MAX_PUBLICACIONES_POR_PEDIDO = 12;

/** Revisa el pedido que manda el portal (no confía en nada). Devuelve el pedido limpio o el motivo del rechazo. */
export function leerPedido(b: Record<string, unknown>): { ok: true; pedido: Pedido } | { ok: false; error: string } {
  const skus = Array.isArray(b.skus) ? [...new Set(b.skus.map((s) => String(s).trim()).filter(Boolean))] : [];
  const tiposVenta = Array.isArray(b.tipos_venta) ? [...new Set(b.tipos_venta.map(String))].filter((t): t is TipoVenta => t in TIPOS_VENTA) : [];
  const precio = Number(b.precio);
  const tradicional = b.tradicional === true, catalogo = b.catalogo === true;
  if (!skus.length) return { ok: false, error: "Elegí al menos un color." };
  if (!tradicional && !catalogo) return { ok: false, error: "Elegí publicación tradicional, catálogo o las dos." };
  if (!tiposVenta.length) return { ok: false, error: "Elegí Clásica, Premium o las dos." };
  if (!Number.isFinite(precio) || precio <= 0) return { ok: false, error: "Poné un precio válido." };
  let marca: string | null = null;
  if (b.marca != null && String(b.marca).trim() !== "") {
    marca = marcaValida(b.marca);
    if (!marca) return { ok: false, error: "La marca tiene que tener entre 2 y 60 letras y no puede ser Pret." };
  }
  const total = skus.length * tiposVenta.length * ((tradicional ? 1 : 0) + (catalogo ? 1 : 0));
  if (total > MAX_PUBLICACIONES_POR_PEDIDO) {
    return { ok: false, error: `Son ${total} publicaciones de una vez; el máximo es ${MAX_PUBLICACIONES_POR_PEDIDO}. Elegí menos colores o un solo tipo.` };
  }
  const recargoCuotas = leerRecargo(b.recargo_cuotas);
  if (recargoCuotas == null) return { ok: false, error: `El recargo por cuotas tiene que estar entre 0 y ${MAX_RECARGO_CUOTAS} %.` };
  return { ok: true, pedido: { skus, tradicional, catalogo, marca, precio, tiposVenta, pausada: b.pausada === true, recargoCuotas } };
}

// ───────────────────────── Publicación masiva ─────────────────────────
// Regla de precio (no se puede poner un precio a mano para cientos de productos):
//   pret       = el precio al que lo publica Pret a Home
//   pret_pct   = el de Pret ± un porcentaje (ej. -5 = 5 % más barato)
//   costo_pct  = costo mayorista + un porcentaje
export type ReglaPrecio =
  | { tipo: "margen"; valor: number }   // ganancia neta estimada (decisión del usuario: la opción por defecto, 10 %)
  | { tipo: "pret" } | { tipo: "pret_pct"; valor: number } | { tipo: "costo_pct"; valor: number };

// Lo que se estima que Mercado Libre + impuestos se quedan del precio de venta (comisión, envío, cargos). El usuario lo
// estimó en 30 % (19/11/2026); se puede cambiar en rev_config 'comision_ml_estimada' sin tocar el código.
export const COMISION_ML_ESTIMADA = 30;
export const GANANCIA_NETA_POR_DEFECTO = 10;
// Lo que Mercado Libre cobra DE MÁS en Premium (cuotas sin interés) sobre Clásica, en % del precio. Medido el 19/11/2026
// con /sites/MLA/listing_prices en las categorías de cortinas, cubiertos y manteles: Clásica 14,35 %, Premium 27,25 %
// (≈ 13 puntos). Se puede cambiar en rev_config 'recargo_cuotas' y el revendedor lo puede ajustar al publicar.
export const RECARGO_CUOTAS_ESTIMADO = 13;
export const MAX_RECARGO_CUOTAS = 40;

/** Precio Premium para que la ganancia EN PESOS sea la misma que en Clásica (pedido del usuario, 19/11/2026).
 *  En Clásica queda precio × (1 − ML) − costo; en Premium ML cobra además `recargoPct`, así que
 *  premium × (1 − ML − recargo) = precio × (1 − ML) → premium = precio × (1 − ML) / (1 − ML − recargo).
 *  Ej.: $100, ML 30 %, recargo 13 % → $123 (ML + recargo ≈ $53, quedan $70, igual que en Clásica). Redondea hacia arriba. */
export function precioConCuotas(precio: number, recargoPct: number, comisionPct = COMISION_ML_ESTIMADA): number | null {
  if (!(precio > 0)) return null;
  if (!(recargoPct > 0)) return precio;
  const resto = 1 - (comisionPct + recargoPct) / 100;
  if (resto < 0.05) return null;
  return Math.ceil((precio * (1 - comisionPct / 100)) / resto);
}

/** Recargo por cuotas que manda el portal: 0 si no viene; null si es inválido. */
export function leerRecargo(v: unknown): number | null {
  if (v == null || v === "") return 0;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 && n <= MAX_RECARGO_CUOTAS ? n : null;
}

/** Precio de una publicación según su tipo: Premium lleva el recargo por cuotas, Clásica el precio tal cual. */
export const precioPorTipo = (precio: number, tipo: TipoVenta, recargoPct: number, comisionPct = COMISION_ML_ESTIMADA): number | null =>
  tipo === "gold_pro" ? precioConCuotas(precio, recargoPct, comisionPct) : precio;

export function leerRegla(r: unknown): ReglaPrecio | null {
  const o = (r ?? {}) as { tipo?: string; valor?: unknown };
  const v = Number(o.valor);
  if (o.tipo === "margen" && Number.isFinite(v) && v >= 0 && v <= 50) return { tipo: "margen", valor: v };
  if (o.tipo === "pret") return { tipo: "pret" };
  if (o.tipo === "pret_pct" && Number.isFinite(v) && v > -90 && v <= 500) return { tipo: "pret_pct", valor: v };
  if (o.tipo === "costo_pct" && Number.isFinite(v) && v >= 0 && v <= 1000) return { tipo: "costo_pct", valor: v };
  return null;
}

/** Precio para ganar un % neto: precio − ML/impuestos (comisión % del precio) − costo = ganancia % del precio.
 *  precio = costo / (1 − comisión − ganancia). Ej.: costo 60, ML 30 %, ganancia 10 % → 100 (ML 30, costo 60, quedan 10).
 *  Se redondea hacia arriba para no quedar por debajo de la ganancia pedida. */
export function precioPorMargen(costo: number, gananciaPct: number, comisionPct = COMISION_ML_ESTIMADA): number | null {
  const resto = 1 - comisionPct / 100 - gananciaPct / 100;
  if (!(costo > 0) || resto < 0.05) return null;
  return Math.ceil(costo / resto);
}

/** Ganancia neta estimada (en % del precio) de vender a `precio`. */
export function margenDePrecio(precio: number, costo: number, comisionPct = COMISION_ML_ESTIMADA): number | null {
  if (!(precio > 0) || !(costo > 0)) return null;
  return ((precio * (1 - comisionPct / 100) - costo) / precio) * 100;
}

/** Precio de venta de un SKU según la regla, en pesos. null = no se puede calcular (falta el dato). */
export function precioPorRegla(regla: ReglaPrecio, datos: { pret: number | null; costo: number | null }, comisionPct = COMISION_ML_ESTIMADA): number | null {
  if (regla.tipo === "margen") return datos.costo != null ? precioPorMargen(datos.costo, regla.valor, comisionPct) : null;
  const base = regla.tipo === "costo_pct" ? datos.costo : datos.pret;
  if (base == null || !(base > 0)) return null;
  const pct = regla.tipo === "pret" ? 0 : regla.valor;
  return Math.round(base * (1 + pct / 100));
}

export interface OpcionesMasivas {
  tradicional: boolean;
  catalogo: boolean;
  marca: string | null;
  tiposVenta: TipoVenta[];
  pausada: boolean;
  todosColores: boolean;
  regla: ReglaPrecio;
  recargoCuotas: number;     // % extra para Premium (cuotas); 0 = mismo precio que Clásica
}
export const MAX_PRODUCTOS_POR_LOTE = 500;

export function leerOpcionesMasivas(b: Record<string, unknown>): { ok: true; op: OpcionesMasivas } | { ok: false; error: string } {
  const tiposVenta = Array.isArray(b.tipos_venta) ? [...new Set(b.tipos_venta.map(String))].filter((t): t is TipoVenta => t in TIPOS_VENTA) : [];
  const tradicional = b.tradicional === true, catalogo = b.catalogo === true;
  if (!tradicional && !catalogo) return { ok: false, error: "Elegí publicación tradicional, catálogo o las dos." };
  if (!tiposVenta.length) return { ok: false, error: "Elegí Clásica, Premium o las dos." };
  const regla = leerRegla(b.regla);
  if (!regla) return { ok: false, error: "Elegí cómo calcular el precio." };
  let marca: string | null = null;
  if (b.marca != null && String(b.marca).trim() !== "") {
    marca = marcaValida(b.marca);
    if (!marca) return { ok: false, error: "La marca tiene que tener entre 2 y 60 letras y no puede ser Pret." };
  }
  const recargoCuotas = leerRecargo(b.recargo_cuotas);
  if (recargoCuotas == null) return { ok: false, error: `El recargo por cuotas tiene que estar entre 0 y ${MAX_RECARGO_CUOTAS} %.` };
  return { ok: true, op: { tradicional, catalogo, marca, tiposVenta, pausada: b.pausada === true, todosColores: b.todos_colores === true, regla, recargoCuotas } };
}

/** Clave de una publicación dentro de un producto, para no repetirla si el proceso se corta y se retoma. */
export const claveResultado = (sku: string, tipo: string, tipoVenta: string) => `${sku}|${tipo}|${tipoVenta}`;

/** Estado final de un producto de la cola según lo que salió. */
export function estadoFinal(resultados: { ok: boolean }[]): "listo" | "parcial" | "error" {
  const ok = resultados.filter((r) => r.ok).length;
  return ok && ok === resultados.length ? "listo" : ok ? "parcial" : "error";
}

/** Ventas: la etiqueta de envío se puede imprimir cuando el envío está "listo para enviar" y el correo todavía no lo
 *  retiró (lista para imprimir o ya impresa, para reimprimirla). */
export const etiquetaDisponible = (e: { status?: string; substatus?: string | null } | null | undefined): boolean =>
  e?.status === "ready_to_ship" && (e.substatus == null || e.substatus === "ready_to_print" || e.substatus === "printed");

// Ventas: período por defecto y máximo (días de Argentina).
const DIAS_VENTAS = 30;
const MAX_DIAS_VENTAS = 366;
/** Período pedido (días de Argentina, YYYY-MM-DD, ambos incluidos). Sin fechas: los últimos 30 días. */
export function periodo(desde: unknown, hasta: unknown, ahora = new Date()): { desde: string; hasta: string } | null {
  const ok = (x: unknown) => typeof x === "string" && /^\d{4}-\d{2}-\d{2}$/.test(x);
  const hoyAR = new Date(ahora.getTime() - 3 * 3600_000).toISOString().slice(0, 10);
  const h = ok(hasta) ? String(hasta) : hoyAR;
  const d = ok(desde) ? String(desde) : new Date(Date.parse(h + "T12:00:00Z") - (DIAS_VENTAS - 1) * 86400_000).toISOString().slice(0, 10);
  if (d > h) return null;
  if ((Date.parse(h) - Date.parse(d)) / 86400_000 > MAX_DIAS_VENTAS) return null;
  return { desde: d, hasta: h };
}


// ───────────────────────── Cuidar la cuenta: ritmo, límites de ML y duplicados (19/11/2026) ─────────────────────────
// Documentación de ML (developers.mercadolibre.com.ar, "Rate limit / Error 429" y "Buenas prácticas"): el límite se
// cuenta por aplicación, no publican números; ante 429 hay que esperar (backoff) y espaciar; las acciones masivas mal
// hechas pueden sancionar la cuenta del vendedor. Ayuda de ML 2517 "Publicaciones duplicadas": no se permite el mismo
// producto con las mismas condiciones de venta; sí con distinta forma de envío o de pago (Clásica vs Premium).

/** Cuántas publicaciones crea la publicación masiva por minuto, y la pausa entre una y otra. */
export const RITMO = { creacionesPorCorrida: 10, pausaEntreCreacionesMs: 2000 };

/** Minutos de pausa después de que ML pidió frenar (429) N veces seguidas: 1, 2, 4, 8, 16 y como máximo 30. */
export const minutosDePausa = (vecesSeguidas: number) => Math.min(30, 2 ** Math.max(0, vecesSeguidas - 1));

/** "Condición de venta" de una publicación: tradicional o catálogo + tipo (Clásica/Premium). Dos publicaciones del mismo
 *  SKU con la misma condición en la misma cuenta son duplicadas para ML. Las finalizadas no cuentan. */
export const claveCondicion = (tipo: "tradicional" | "catalogo", tipoVenta: string) => `${tipo}|${tipoVenta}`;
export function condicionDeItem(it: { status?: string | null; listing_type_id?: string | null; catalog_listing?: boolean | null } | null): string | null {
  if (!it || !it.listing_type_id || it.status === "closed") return null;
  return claveCondicion(it.catalog_listing ? "catalogo" : "tradicional", it.listing_type_id);
}
