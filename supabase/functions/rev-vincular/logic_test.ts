// Pruebas de la vinculación por SKU. Correr con: deno test supabase/functions/rev-vincular/logic_test.ts
import { assertEquals } from "jsr:@std/assert@1";
import { normSku, planificarVinculos, type PubExistente, vinculosDeItem } from "./logic.ts";

const catalogo = new Map([["abc-1", "ABC-1"], ["rem/001 m", "REM/001 M"]]);

Deno.test("normSku: sin mayúsculas ni espacios en los extremos", () => {
  assertEquals(normSku("  ABC-1 "), "abc-1");
});

Deno.test("vinculosDeItem: publicación simple con SKU", () => {
  const r = vinculosDeItem({ id: "MLA1", status: "paused", price: 100, seller_custom_field: " ABC-1 " });
  assertEquals(r, [{ ml_item_id: "MLA1", ml_variation_id: null, sku_ml: "ABC-1", precio: 100, estado_ml: "paused" }]);
});

Deno.test("vinculosDeItem: SKU vacío o en blanco cuenta como sin SKU", () => {
  assertEquals(vinculosDeItem({ id: "MLA1", seller_custom_field: "   " })[0].sku_ml, null);
  assertEquals(vinculosDeItem({ id: "MLA1", seller_custom_field: null })[0].sku_ml, null);
  assertEquals(vinculosDeItem({ id: "MLA1" })[0].sku_ml, null);
});

Deno.test("vinculosDeItem: el SKU puede venir solo en el atributo SELLER_SKU (caso real de CASAPINAR)", () => {
  const r = vinculosDeItem({
    id: "MLA3786572372",
    status: "active",
    seller_custom_field: null,
    attributes: [{ id: "BRAND", value_name: "Otra" }, { id: "SELLER_SKU", value_name: " COR-TROPICAL130X205AERO " }],
  });
  assertEquals(r[0].sku_ml, "COR-TROPICAL130X205AERO");
});

Deno.test("vinculosDeItem: si hay seller_custom_field y SELLER_SKU, gana el campo clásico", () => {
  const r = vinculosDeItem({ id: "MLA1", seller_custom_field: "ABC-1", attributes: [{ id: "SELLER_SKU", value_name: "OTRO" }] });
  assertEquals(r[0].sku_ml, "ABC-1");
});

Deno.test("vinculosDeItem: SELLER_SKU vacío o ausente cuenta como sin SKU", () => {
  assertEquals(vinculosDeItem({ id: "MLA1", attributes: [{ id: "SELLER_SKU", value_name: "  " }] })[0].sku_ml, null);
  assertEquals(vinculosDeItem({ id: "MLA1", attributes: [{ id: "BRAND", value_name: "X" }] })[0].sku_ml, null);
  assertEquals(vinculosDeItem({ id: "MLA1", attributes: null })[0].sku_ml, null);
});

Deno.test("vinculosDeItem: una variación toma su SELLER_SKU; si no tiene, el del item", () => {
  const r = vinculosDeItem({
    id: "MLA4",
    attributes: [{ id: "SELLER_SKU", value_name: "BASE" }],
    variations: [{ id: 1, attributes: [{ id: "SELLER_SKU", value_name: "ABC-1" }] }, { id: 2 }],
  });
  assertEquals(r.map((v) => [v.ml_variation_id, v.sku_ml]), [[1, "ABC-1"], [2, "BASE"]]);
});

Deno.test("vinculosDeItem: una fila por variación, con el SKU propio o el del item", () => {
  const r = vinculosDeItem({
    id: "MLA2",
    status: "active",
    price: 50,
    seller_custom_field: "BASE",
    variations: [{ id: 11, seller_custom_field: "ABC-1", price: 60 }, { id: 12, seller_custom_field: "" }],
  });
  assertEquals(r.map((v) => [v.ml_variation_id, v.sku_ml, v.precio]), [[11, "ABC-1", 60], [12, "BASE", 50]]);
});

Deno.test("planificarVinculos: publicación nueva con SKU del catálogo queda vinculada con el SKU canónico", () => {
  const v = vinculosDeItem({ id: "MLA1", status: "active", price: 10, seller_custom_field: "abc-1" });
  const p = planificarVinculos(v, catalogo, []);
  assertEquals(p.nuevas, [{ ml_item_id: "MLA1", ml_variation_id: null, sku: "ABC-1", estado_ml: "active", precio: 10 }]);
  assertEquals(p.sin_reconocer, 0);
});

Deno.test("planificarVinculos: SKU que no está en el catálogo queda sin reconocer (sku nulo), no se descarta", () => {
  const v = vinculosDeItem({ id: "MLA3", status: "active", seller_custom_field: "OTRO" });
  const p = planificarVinculos(v, catalogo, []);
  assertEquals(p.nuevas[0].sku, null);
  assertEquals(p.sin_reconocer, 1);
});

const existente = (o: Partial<PubExistente>): PubExistente => ({
  id: 1,
  ml_item_id: "MLA1",
  ml_variation_id: null,
  sku: "ABC-1",
  vinculo: "auto",
  estado_ml: "active",
  precio: 10,
  ...o,
});

Deno.test("planificarVinculos: sin cambios no genera trabajo", () => {
  const v = vinculosDeItem({ id: "MLA1", status: "active", price: 10, seller_custom_field: "ABC-1" });
  const p = planificarVinculos(v, catalogo, [existente({})]);
  assertEquals([p.nuevas.length, p.actualizar.length, p.sin_cambios], [0, 0, 1]);
});

Deno.test("planificarVinculos: cambio de precio o estado actualiza", () => {
  const v = vinculosDeItem({ id: "MLA1", status: "paused", price: 12, seller_custom_field: "ABC-1" });
  const p = planificarVinculos(v, catalogo, [existente({})]);
  assertEquals(p.actualizar, [{ id: 1, sku: "ABC-1", estado_ml: "paused", precio: 12 }]);
});

Deno.test("planificarVinculos: el vínculo manual NO se pisa aunque el SKU de ML sea otro", () => {
  const v = vinculosDeItem({ id: "MLA1", status: "active", price: 10, seller_custom_field: "REM/001 M" });
  const p = planificarVinculos(v, catalogo, [existente({ vinculo: "manual", sku: "ABC-1" })]);
  assertEquals(p.actualizar.length, 0);
  assertEquals(p.sin_cambios, 1);
});

Deno.test("planificarVinculos: el vínculo manual sí refresca estado y precio, conservando el SKU", () => {
  const v = vinculosDeItem({ id: "MLA1", status: "paused", price: 99, seller_custom_field: "" });
  const p = planificarVinculos(v, catalogo, [existente({ vinculo: "manual", sku: "ABC-1" })]);
  assertEquals(p.actualizar, [{ id: 1, sku: "ABC-1", estado_ml: "paused", precio: 99 }]);
});

Deno.test("planificarVinculos: un vínculo automático que dejó de reconocerse pasa a sin SKU", () => {
  const v = vinculosDeItem({ id: "MLA1", status: "active", price: 10, seller_custom_field: "YA-NO-EXISTE" });
  const p = planificarVinculos(v, catalogo, [existente({})]);
  assertEquals(p.actualizar, [{ id: 1, sku: null, estado_ml: "active", precio: 10 }]);
  assertEquals(p.sin_reconocer, 1);
});

Deno.test("planificarVinculos: publicaciones que ya no aparecen en ML no se tocan", () => {
  const p = planificarVinculos([], catalogo, [existente({})]);
  assertEquals([p.nuevas.length, p.actualizar.length, p.sin_cambios], [0, 0, 0]);
});

Deno.test("planificarVinculos: la misma publicación con variaciones distintas son filas distintas", () => {
  const v = vinculosDeItem({ id: "MLA9", status: "active", variations: [{ id: 1, seller_custom_field: "ABC-1" }, { id: 2, seller_custom_field: "REM/001 M" }] });
  const p = planificarVinculos(v, catalogo, [existente({ ml_item_id: "MLA9", ml_variation_id: 1 })]);
  assertEquals(p.nuevas.map((n) => [n.ml_variation_id, n.sku]), [[2, "REM/001 M"]]);
});
