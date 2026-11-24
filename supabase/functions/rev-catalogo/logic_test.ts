// Pruebas de la actualización del catálogo. Correr con: deno test supabase/functions/rev-catalogo/logic_test.ts
import { assertEquals } from "jsr:@std/assert@1";
import { esMR, skusParaSumar } from "./logic.ts";

const info = new Map([
  ["26770bverde", { sku: "26770BVERDE", costo: 29949 }],
  ["sin-costo", { sku: "SIN-COSTO", costo: null }],
  ["ya-1", { sku: "YA-1", costo: 10 }],
]);

Deno.test("suma todo lo simple de Ecomm-App que falta, menos combos y MR-", () => {
  const r = skusParaSumar([
    { sku: " 26770bVerde ", is_combo: false },
    { sku: "SIN-COSTO", is_combo: false },
    { sku: "COMBO-X2", is_combo: true },
    { sku: "MR-COR-TROPICAL130X205NATURAL", is_combo: false },
    { sku: "mr-pah-tt420azul", is_combo: false },
    { sku: "SOLO-ECOMM", is_combo: false, description: " Cubre colchón ", variant_description: "80x190", image_url: "https://x/y.jpg", price: 1000 },
    { sku: "ya-1", is_combo: false },
    { sku: "", is_combo: false },
    { sku: null, is_combo: false },
  ], new Set(["ya-1"]), info);
  assertEquals(r.nuevos, ["26770BVERDE", "SIN-COSTO", "SOLO-ECOMM"]);
  assertEquals([r.combos, r.mr, r.ya_estaban, r.sin_tiendanube, r.sin_costo], [1, 2, 1, 1, 2]);
  assertEquals(r.info_nueva, [{
    sku: "SOLO-ECOMM", titulo: "Cubre colchón - 80x190", descripcion: "", fotos: ["https://x/y.jpg"], costo: null, precio_sugerido: 1000, fuente: "ecomm",
  }]);
});

Deno.test("sin nombre en Ecomm-App el título es el SKU; sin foto, lista vacía", () => {
  const r = skusParaSumar([{ sku: "X-1", is_combo: false }], new Set(), new Map());
  assertEquals([r.info_nueva[0].titulo, r.info_nueva[0].fotos], ["X-1", []]);
});

Deno.test("un SKU repetido en Ecomm-App cuenta una vez", () => {
  const r = skusParaSumar([{ sku: "26770BVERDE", is_combo: false }, { sku: "26770bverde", is_combo: false }], new Set(), info);
  assertEquals(r.nuevos, ["26770BVERDE"]);
});

Deno.test("un SKU ya en el catálogo (aunque esté deshabilitado) no se vuelve a sumar", () => {
  const r = skusParaSumar([{ sku: "26770BVERDE", is_combo: false }], new Set(["26770bverde"]), info);
  assertEquals([r.nuevos.length, r.ya_estaban], [0, 1]);
});

Deno.test("esMR: solo el prefijo MR-", () => {
  assertEquals(["MR-PAH-TT420AZUL", " mr-x", "PAH-MR-1", "MRX-1"].map(esMR), [true, true, false, false]);
});
