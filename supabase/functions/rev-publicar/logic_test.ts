import { assert, assertEquals } from "jsr:@std/assert@1";
import { leerRecargo, precioConCuotas, precioPorTipo } from "./logic.ts";
import {
  colorDe, erroresML, esMarcaPropia, type ItemML, leerPedido, limpiarDescripcion, marcaValida, payloadCatalogo,
  payloadTradicional, reemplazarMarca, skuDe,
} from "./logic.ts";

const DESC = `Sábanas Hoteleras 1200 H King - Pret
Renová tu dormitorio con este juego de sábanas.

Medidas:
- Sábana plana: 260 x 280 cm.


---------------------------------------------------------------------------

SOMOS PRET A HOME

SOMOS UNA MARCA CONFIABLE CON LOCAL COMERCIAL EN CABA.
REALIZAMOS FACTURA A Y B.`;

Deno.test("descripción: corta el bloque de la tienda y los separadores", () => {
  const t = limpiarDescripcion(DESC, null);
  assert(!/somos pret/i.test(t) && !/factura/i.test(t) && !t.includes("----"));
  assert(t.endsWith("- Sábana plana: 260 x 280 cm."));
  assert(t.startsWith("Sábanas Hoteleras 1200 H King - Pret")); // tal cual: la marca del producto queda
});

Deno.test("descripción con marca propia: sin Pret", () => {
  const t = limpiarDescripcion(DESC, "Casa Nube");
  assertEquals(t.split("\n")[0], "Sábanas Hoteleras 1200 H King - Casa Nube");
  assert(!/pret/i.test(t));
});

Deno.test("descripción sin bloque queda igual", () => {
  assertEquals(limpiarDescripcion("Acolchado infantil soft", null), "Acolchado infantil soft");
});

Deno.test("reemplazarMarca cubre las formas de escribir Pret y no toca palabras que la contienen", () => {
  assertEquals(reemplazarMarca("Pret A Home, PRETAHOME, Pret. y pret", "X"), "X, X, X y X");
  assertEquals(reemplazarMarca("interpretación", "X"), "interpretación");
  // el punto de fin de oración se conserva; el de la marca "Pret." en medio de una frase, no
  assertEquals(reemplazarMarca("de la marca Pret. Con unas medidas", "Casa"), "de la marca Casa. Con unas medidas");
  assertEquals(reemplazarMarca("Sábanas Pret. hoteleras", "Casa"), "Sábanas Casa hoteleras");
  assertEquals(reemplazarMarca("Marca: Pret.", "Casa"), "Marca: Casa.");
});

Deno.test("marca propia: solo las marcas de la lista, sin importar mayúsculas", () => {
  assert(esMarcaPropia("Pret.", ["Pret.", "Pret A Home"]));
  assert(esMarcaPropia(" pret a home ", ["Pret.", "Pret A Home"]));
  assert(!esMarcaPropia("Sakura", ["Pret.", "Pret A Home"]));
  assert(!esMarcaPropia(null, ["Pret."]));
});

Deno.test("marca del revendedor: largo razonable y no puede ser Pret", () => {
  assertEquals(marcaValida("  Casa   Nube "), "Casa Nube");
  assertEquals(marcaValida("P"), null);
  assertEquals(marcaValida("Pret Deco"), null);
});

const ITEM: ItemML = {
  id: "MLA1", title: "Juego Cortinas Blackout Pret 2,10x1,40 M", category_id: "MLA4771", currency_id: "ARS",
  attributes: [
    { id: "BRAND", value_name: "Pret.", value_id: "123" }, { id: "MODEL", value_name: "Blackout" },
    { id: "GTIN", value_name: "999" }, { id: "SELLER_PACKAGE_WEIGHT", value_name: "1 kg" }, { id: "VACIO", value_name: null },
  ],
  variations: [{
    id: 10, price: 100, attribute_combinations: [{ id: "COLOR", value_name: "Gris claro", value_id: "52" }],
    attributes: [{ id: "SELLER_SKU", value_name: "SKU-GC" }, { id: "GTIN", value_name: "8435561327181" }], picture_ids: ["a", "b"],
  }],
};

Deno.test("tradicional tal cual: marca y GTIN originales, color, SKU, sin atributos de la cuenta de Pret", () => {
  const p = payloadTradicional(ITEM, { variacion: ITEM.variations![0], fotos: ["u1", "u2"], precio: 1234.567, cantidad: 7, tipoVenta: "gold_special", marca: null });
  const a = new Map((p.attributes as { id: string; value_name: string }[]).map((x) => [x.id, x.value_name]));
  assertEquals(a.get("BRAND"), "Pret.");
  assertEquals(a.get("GTIN"), "8435561327181");
  assertEquals(a.get("COLOR"), "Gris claro");
  assertEquals(a.get("SELLER_SKU"), "SKU-GC");
  assert(!a.has("VACIO") && !a.has("EMPTY_GTIN_REASON"));
  assert(a.get("SELLER_PACKAGE_WEIGHT") === "1 kg"); // las medidas del paquete se copian (ML las exige en algunas categorías)
  assertEquals(p.family_name, "Juego Cortinas Blackout Pret 2,10x1,40 M");
  assertEquals(p.price, 1234.57);
  assertEquals(p.available_quantity, 7);
  assertEquals(p.seller_custom_field, "SKU-GC");
  assertEquals(p.pictures, [{ source: "u1" }, { source: "u2" }]);
});

Deno.test("tradicional con marca propia: BRAND nuevo sin código, sin GTIN, Pret fuera del título", () => {
  const p = payloadTradicional(ITEM, { variacion: ITEM.variations![0], fotos: ["u1"], precio: 100, cantidad: 1, tipoVenta: "gold_pro", marca: "Casa Nube" });
  const at = p.attributes as { id: string; value_name: string; value_id?: string }[];
  const brand = at.find((x) => x.id === "BRAND")!;
  assertEquals(brand, { id: "BRAND", value_name: "Casa Nube" });
  assert(!at.some((x) => x.id === "GTIN"));
  assertEquals(at.find((x) => x.id === "EMPTY_GTIN_REASON")?.value_name, "El producto no tiene código registrado");
  assertEquals(p.family_name, "Juego Cortinas Blackout Casa Nube 2,10x1,40 M");
  assertEquals(p.listing_type_id, "gold_pro");
});

Deno.test("catálogo: ficha de ML + SKU", () => {
  const p = payloadCatalogo({ categoryId: "MLA30059", catalogProductId: "MLA62744056", sku: "X1", precio: 80000, cantidad: 3, tipoVenta: "gold_special" });
  assertEquals(p.catalog_listing, true);
  assertEquals(p.catalog_product_id, "MLA62744056");
  assertEquals(p.attributes, [{ id: "SELLER_SKU", value_name: "X1" }]);
});

Deno.test("sku y color de una variación", () => {
  assertEquals(skuDe([{ id: "SELLER_SKU", value_name: " A1 " }]), "A1");
  assertEquals(skuDe([], "B2"), "B2");
  assertEquals(skuDe([]), null);
  assertEquals(colorDe({ id: 1, attribute_combinations: [{ id: "COLOR", value_name: "Crema" }, { id: "X", value_name: "Liso" }] }), "Crema · Liso");
});

Deno.test("errores de ML: las advertencias no cuentan", () => {
  assertEquals(erroresML(204, null), []);
  assertEquals(erroresML(400, { message: "Validation error", cause: [{ type: "warning", message: "Mandatory free shipping added" }] }), []);
  assertEquals(erroresML(400, { message: "Validation error", cause: [{ type: "error", message: "Falta la marca" }, { type: "warning", message: "x" }] }), ["Falta la marca"]);
  assertEquals(erroresML(403, { message: "forbidden" }), ["forbidden"]);
});

Deno.test("pedido: controla lo que manda el portal", () => {
  const base = { skus: ["A", "A", "B"], tradicional: true, catalogo: false, precio: "1000", tipos_venta: ["gold_special", "hackeo"], pausada: true };
  const r = leerPedido(base);
  assert(r.ok);
  if (r.ok) {
    assertEquals(r.pedido.skus, ["A", "B"]);
    assertEquals(r.pedido.tiposVenta, ["gold_special"]);
    assertEquals(r.pedido.precio, 1000);
    assertEquals(r.pedido.marca, null);
  }
  assert(!leerPedido({ ...base, skus: [] }).ok);
  assert(!leerPedido({ ...base, tradicional: false }).ok);
  assert(!leerPedido({ ...base, tipos_venta: [] }).ok);
  assert(!leerPedido({ ...base, precio: -1 }).ok);
  assert(!leerPedido({ ...base, marca: "Pret" }).ok);
  // 7 colores × 2 tipos de venta = 14 > 12
  assert(!leerPedido({ ...base, skus: ["1", "2", "3", "4", "5", "6", "7"], tipos_venta: ["gold_special", "gold_pro"] }).ok);
});

import { estadoFinal, leerOpcionesMasivas, leerRegla, precioPorRegla } from "./logic.ts";

Deno.test("regla de precio", () => {
  assertEquals(precioPorRegla({ tipo: "pret" }, { pret: 42764.3, costo: 22350 }), 42764);
  assertEquals(precioPorRegla({ tipo: "pret_pct", valor: -5 }, { pret: 10000, costo: 1 }), 9500);
  assertEquals(precioPorRegla({ tipo: "pret_pct", valor: 10 }, { pret: 10000, costo: 1 }), 11000);
  assertEquals(precioPorRegla({ tipo: "costo_pct", valor: 60 }, { pret: null, costo: 22350 }), 35760);
  assertEquals(precioPorRegla({ tipo: "costo_pct", valor: 60 }, { pret: 1, costo: null }), null); // sin costo, no se inventa
  assertEquals(precioPorRegla({ tipo: "pret" }, { pret: null, costo: 5 }), null);
});

Deno.test("regla: valores fuera de rango se rechazan", () => {
  assertEquals(leerRegla({ tipo: "pret_pct", valor: -95 }), null);
  assertEquals(leerRegla({ tipo: "costo_pct", valor: -1 }), null);
  assertEquals(leerRegla({ tipo: "otra" }), null);
  assertEquals(leerRegla({ tipo: "costo_pct", valor: "40" }), { tipo: "costo_pct", valor: 40 });
});

Deno.test("opciones masivas", () => {
  const r = leerOpcionesMasivas({ tradicional: true, tipos_venta: ["gold_pro"], regla: { tipo: "pret" }, todos_colores: true, marca: "Casa Nube" });
  assert(r.ok);
  if (r.ok) { assertEquals(r.op.marca, "Casa Nube"); assertEquals(r.op.todosColores, true); assertEquals(r.op.pausada, false); }
  assert(!leerOpcionesMasivas({ tradicional: true, tipos_venta: ["gold_pro"] }).ok); // sin regla
  assert(!leerOpcionesMasivas({ tipos_venta: ["gold_pro"], regla: { tipo: "pret" } }).ok); // sin tipo
});

Deno.test("estado final de un producto", () => {
  assertEquals(estadoFinal([{ ok: true }, { ok: true }]), "listo");
  assertEquals(estadoFinal([{ ok: true }, { ok: false }]), "parcial");
  assertEquals(estadoFinal([{ ok: false }]), "error");
  assertEquals(estadoFinal([]), "error");
});

import { nombreFamilia } from "./logic.ts";
Deno.test("nombre del producto: sin el color del final y cortado en palabra entera", () => {
  assertEquals(nombreFamilia("Cafetera Prensa Francesa 3 Pocillos Sakura Infusa 350 Ml Blanco", ["Blanco"]),
    "Cafetera Prensa Francesa 3 Pocillos Sakura Infusa 350 Ml");
  assertEquals(nombreFamilia("Sábanas Lisas - Gris Oscuro", ["Gris oscuro"]), "Sábanas Lisas");
  assertEquals(nombreFamilia("Juego Cortinas Blackout Textil Vinilico 2,10x1,40 M 2 Paños", ["Gris claro"]),
    "Juego Cortinas Blackout Textil Vinilico 2,10x1,40 M 2 Paños");
  const largo = nombreFamilia("Juego Sabanas Lisas Hoteleras Piel De Durazno 1200 Hilos King Size Premium Extra", []);
  // "…1200 Hilos King" pasa de 60 letras: se corta antes de "King", en palabra entera
  assertEquals(largo, "Juego Sabanas Lisas Hoteleras Piel De Durazno 1200 Hilos");
});

import { margenDePrecio, precioPorMargen } from "./logic.ts";
Deno.test("precio por ganancia neta: el ejemplo del usuario (costo 60, ML 30 %, ganancia 10 % → 100)", () => {
  assertEquals(precioPorMargen(60, 10, 30), 100);
  assertEquals(precioPorRegla({ tipo: "margen", valor: 10 }, { pret: 1, costo: 22350 }, 30), 37250);
  assertEquals(Math.round(margenDePrecio(100, 60, 30)!), 10);
  assertEquals(precioPorMargen(60, 70, 30), null); // ML + ganancia ≥ 100 %: imposible
  assertEquals(precioPorRegla({ tipo: "margen", valor: 10 }, { pret: 100, costo: null }), null); // sin costo no se inventa
  assertEquals(leerRegla({ tipo: "margen", valor: 10 }), { tipo: "margen", valor: 10 });
  assertEquals(leerRegla({ tipo: "margen", valor: 80 }), null);
  // se redondea hacia arriba: nunca por debajo de la ganancia pedida
  assert(margenDePrecio(precioPorMargen(34500, 10)!, 34500)! >= 10);
});

import { etiquetaDisponible } from "./logic.ts";
Deno.test("etiqueta: solo cuando el envío está listo para despachar", () => {
  assertEquals(etiquetaDisponible({ status: "ready_to_ship", substatus: "ready_to_print" }), true);
  assertEquals(etiquetaDisponible({ status: "ready_to_ship", substatus: "printed" }), true);
  assertEquals(etiquetaDisponible({ status: "ready_to_ship", substatus: "picked_up" }), false);
  assertEquals(etiquetaDisponible({ status: "handling" }), false);
  assertEquals(etiquetaDisponible({ status: "delivered" }), false);
  assertEquals(etiquetaDisponible(null), false);
});

import { periodo } from "./logic.ts";
Deno.test("período de ventas", () => {
  const ahora = new Date("2026-11-19T12:00:00Z"); // 09:00 en Argentina
  assertEquals(periodo(null, null, ahora), { desde: "2026-10-21", hasta: "2026-11-19" }); // últimos 30 días
  assertEquals(periodo("2026-11-01", "2026-11-15", ahora), { desde: "2026-11-01", hasta: "2026-11-15" });
  assertEquals(periodo("2026-11-15", "2026-11-01", ahora), null);          // al revés
  assertEquals(periodo("2024-01-01", "2026-11-01", ahora), null);          // más de un año
  assertEquals(periodo(null, null, new Date("2026-11-20T02:00:00Z"))?.hasta, "2026-11-19"); // 23 h en Argentina: sigue siendo 19
});

import { claveCondicion, condicionDeItem, minutosDePausa } from "./logic.ts";
Deno.test("pausa ante el límite de ML: crece y tiene tope", () => {
  assertEquals([1, 2, 3, 4, 5, 6, 9].map(minutosDePausa), [1, 2, 4, 8, 16, 30, 30]);
});
Deno.test("duplicados: misma condición = mismo tipo y mismas cuotas; las finalizadas no cuentan", () => {
  assertEquals(condicionDeItem({ status: "active", listing_type_id: "gold_special", catalog_listing: false }), claveCondicion("tradicional", "gold_special"));
  assertEquals(condicionDeItem({ status: "paused", listing_type_id: "gold_pro", catalog_listing: true }), "catalogo|gold_pro");
  assertEquals(condicionDeItem({ status: "closed", listing_type_id: "gold_pro" }), null);
  assertEquals(condicionDeItem(null), null);
});

Deno.test("recargo por cuotas: Premium deja la misma ganancia en pesos que Clásica", () => {
  // $100, ML 30 %, costo 60 → en Clásica quedan $10. Premium con 13 % más de comisión: $123 → 123 × (1 − 0,43) − 60 ≈ 10.
  assertEquals(precioConCuotas(100, 13, 30), 123);
  const p = precioConCuotas(39950, 13, 30)!;
  const gananciaClasica = 39950 * 0.70 - 25000, gananciaPremium = p * 0.57 - 25000;
  assertEquals(Math.abs(gananciaPremium - gananciaClasica) < 1, true);
  assertEquals(precioConCuotas(100, 0, 30), 100);
  assertEquals(precioConCuotas(100, 66, 30), null); // no queda margen posible
  assertEquals(precioPorTipo(100, "gold_special", 13, 30), 100);
  assertEquals(precioPorTipo(100, "gold_pro", 13, 30), 123);
  assertEquals(leerRecargo(undefined), 0);
  assertEquals(leerRecargo("13"), 13);
  assertEquals(leerRecargo(-1), null);
  assertEquals(leerRecargo(99), null);
});
