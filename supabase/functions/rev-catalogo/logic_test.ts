// Pruebas de la actualización del catálogo. Correr con: deno test supabase/functions/rev-catalogo/logic_test.ts
import { assertEquals } from "jsr:@std/assert@1";
import { skusParaSumar } from "./logic.ts";

const info = new Map([
  ["26770bverde", { sku: "26770BVERDE", costo: 29949 }],
  ["sin-costo", { sku: "SIN-COSTO", costo: null }],
  ["combo-x2", { sku: "COMBO-X2", costo: 100 }],
  ["ya-1", { sku: "YA-1", costo: 10 }],
]);

Deno.test("suma solo simples, con Tiendanube y costo, que no estaban (con el SKU de Tiendanube)", () => {
  const r = skusParaSumar([
    { sku: " 26770bVerde ", is_combo: false },
    { sku: "SIN-COSTO", is_combo: false },
    { sku: "COMBO-X2", is_combo: true },
    { sku: "SOLO-ECOMM", is_combo: false },
    { sku: "ya-1", is_combo: false },
    { sku: "", is_combo: false },
    { sku: null, is_combo: false },
  ], new Set(["ya-1"]), info);
  assertEquals(r, { nuevos: ["26770BVERDE"], combos: 1, sin_tiendanube: 1, sin_costo: 1, ya_estaban: 1 });
});

Deno.test("un SKU repetido en Ecomm-App cuenta una vez", () => {
  const r = skusParaSumar([{ sku: "26770BVERDE", is_combo: false }, { sku: "26770bverde", is_combo: false }], new Set(), info);
  assertEquals(r.nuevos, ["26770BVERDE"]);
});

Deno.test("un SKU ya en el catálogo (aunque esté deshabilitado) no se vuelve a sumar", () => {
  const r = skusParaSumar([{ sku: "26770BVERDE", is_combo: false }], new Set(["26770bverde"]), info);
  assertEquals([r.nuevos.length, r.ya_estaban], [0, 1]);
});
