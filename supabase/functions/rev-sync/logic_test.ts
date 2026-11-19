// Pruebas de la lógica pura de revendedores. Correr con: deno test supabase/functions/rev-sync/logic_test.ts
import { assertEquals, assertNotEquals } from "jsr:@std/assert@1";
import {
  claveIdempotente,
  enLotes,
  interpretarRespuestaEcomm,
  publicacionesAActualizar,
  stockOfrecido,
} from "./logic.ts";

// ── stock ofrecido ───────────────────────────────────────────────────────────────────────────────────────────────
Deno.test("stockOfrecido: depósito menos 5 unidades de colchón", () => {
  assertEquals(stockOfrecido(12), 7);
  assertEquals(stockOfrecido(983), 978);
});

Deno.test("stockOfrecido: con 5 o menos no se ofrece nada, nunca negativo", () => {
  assertEquals(stockOfrecido(5), 0);
  assertEquals(stockOfrecido(3), 0);
  assertEquals(stockOfrecido(0), 0);
  assertEquals(stockOfrecido(-4), 0);
});

Deno.test("stockOfrecido: datos raros no ofrecen (ante la duda, 0)", () => {
  assertEquals(stockOfrecido(NaN), 0);
  assertEquals(stockOfrecido(Infinity), 0);
  assertEquals(stockOfrecido(10, NaN), 0);
});

Deno.test("stockOfrecido: colchón propio y tope por SKU", () => {
  assertEquals(stockOfrecido(20, 10), 10);
  assertEquals(stockOfrecido(20, 5, 8), 8);
  assertEquals(stockOfrecido(20, 5, 100), 15);
  assertEquals(stockOfrecido(20, 5, null), 15);
});

// ── clave de idempotencia ────────────────────────────────────────────────────────────────────────────────────────
Deno.test("claveIdempotente: es determinística (reprocesar no cambia la clave)", () => {
  assertEquals(claveIdempotente(2000019556227970, "21107-470MADERA"), claveIdempotente("2000019556227970", "21107-470MADERA"));
});

Deno.test("claveIdempotente: solo caracteres válidos para Ecomm-App y máximo 100", () => {
  const skus = ["REM/001 M", "A+B#2", "ñandú ü", "X".repeat(300), "  con espacios  "];
  for (const sku of skus) {
    for (const tipo of ["venta", "cancelacion"] as const) {
      const c = claveIdempotente(2000019556227970, sku, tipo);
      assertEquals(/^[A-Za-z0-9._:#-]+$/.test(c), true, `caracteres inválidos en ${c}`);
      assertEquals(c.length <= 100, true, `muy larga: ${c.length}`);
    }
  }
});

Deno.test("claveIdempotente: SKU distintos nunca comparten clave aunque se saneen igual", () => {
  const a = claveIdempotente(1, "A B");
  const b = claveIdempotente(1, "A_B");
  const c = claveIdempotente(1, "A/B");
  assertNotEquals(a, b);
  assertNotEquals(a, c);
  assertNotEquals(b, c);
});

Deno.test("claveIdempotente: SKU largos que difieren al final siguen distintos", () => {
  const base = "Y".repeat(200);
  assertNotEquals(claveIdempotente(1, base + "1"), claveIdempotente(1, base + "2"));
});

Deno.test("claveIdempotente: venta y cancelación de la misma orden tienen claves distintas", () => {
  assertNotEquals(claveIdempotente(1, "ABC", "venta"), claveIdempotente(1, "ABC", "cancelacion"));
});

Deno.test("claveIdempotente: órdenes distintas, claves distintas", () => {
  assertNotEquals(claveIdempotente(1, "ABC"), claveIdempotente(2, "ABC"));
});

// ── respuesta de Ecomm-App ───────────────────────────────────────────────────────────────────────────────────────
Deno.test("interpretarRespuestaEcomm: applied", () => {
  const r = interpretarRespuestaEcomm(200, { data: { status: "applied", lines: [{ stock_after: 911 }] } });
  assertEquals(r, { resultado: "aplicada", stockDespues: 911 });
});

Deno.test("interpretarRespuestaEcomm: applied_with_shortfall informa el faltante", () => {
  const r = interpretarRespuestaEcomm(200, { data: { status: "applied_with_shortfall", lines: [{ shortfall_quantity: 2 }] } });
  assertEquals(r, { resultado: "aplicada_parcial", faltante: 2 });
});

Deno.test("interpretarRespuestaEcomm: processing se reintenta con la MISMA clave", () => {
  const r = interpretarRespuestaEcomm(202, { data: { status: "processing" } }, 3);
  assertEquals(r, { resultado: "reintentar_misma_clave", esperarSegundos: 3 });
});

Deno.test("interpretarRespuestaEcomm: rejected por stock insuficiente trae el stock actual", () => {
  const r = interpretarRespuestaEcomm(409, {
    data: { status: "rejected", rejection: { reason: "insufficient_stock", lines: [{ current_stock: 1 }] } },
  });
  assertEquals(r, { resultado: "rechazada", motivo: "insufficient_stock", stockActual: 1 });
});

Deno.test("interpretarRespuestaEcomm: 5xx y 429 se reintentan con la misma clave", () => {
  assertEquals(interpretarRespuestaEcomm(503, null).resultado, "reintentar_misma_clave");
  assertEquals(interpretarRespuestaEcomm(500, null).resultado, "reintentar_misma_clave");
  assertEquals(interpretarRespuestaEcomm(429, null, 5), { resultado: "reintentar_misma_clave", esperarSegundos: 5 });
});

Deno.test("interpretarRespuestaEcomm: outcome_unknown (502) NO se reintenta: queda 'desconocido'", () => {
  const r = interpretarRespuestaEcomm(502, { data: { status: "outcome_unknown" } });
  assertEquals(r.resultado, "desconocido");
});

Deno.test("interpretarRespuestaEcomm: respuesta que no se entiende queda 'desconocido', nunca 'aplicada'", () => {
  assertEquals(interpretarRespuestaEcomm(200, {}).resultado, "desconocido");
  assertEquals(interpretarRespuestaEcomm(200, null).resultado, "desconocido");
});

// ── publicaciones a actualizar ───────────────────────────────────────────────────────────────────────────────────
Deno.test("publicacionesAActualizar: solo las que difieren de lo ofrecido", () => {
  const ofrecidos = new Map([["A", 7], ["B", 0], ["C", 3]]);
  const pubs = [
    { id: 1, sku: "A", ultima_cantidad_enviada: 7 }, // igual: no se toca
    { id: 2, sku: "B", ultima_cantidad_enviada: 4 }, // baja a 0
    { id: 3, sku: "C", ultima_cantidad_enviada: null }, // nunca se envió
  ];
  const r = publicacionesAActualizar(ofrecidos, pubs);
  assertEquals(r.map((p) => [p.id, p.nueva]), [[2, 0], [3, 3]]);
});

Deno.test("publicacionesAActualizar: un SKU sin dato de stock no se pisa con 0", () => {
  const r = publicacionesAActualizar(new Map(), [{ id: 1, sku: "X", ultima_cantidad_enviada: 5 }]);
  assertEquals(r.length, 0);
});

Deno.test("enLotes: parte en grupos del tamaño pedido", () => {
  assertEquals(enLotes([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assertEquals(enLotes([], 100), []);
});
