import { colchonPara } from "./logic.ts";
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

import { motivoMovimiento } from "./logic.ts";
Deno.test("motivo en Ecomm-App: venta-<revendedor> con número de venta y SKU", () => {
  assertEquals(motivoMovimiento("venta", "CASALAVAN", 2000012345678901, "PRET210-CBOGRIS"), "venta-casalavan #2000012345678901 SKU PRET210-CBOGRIS");
  assertEquals(motivoMovimiento("devolucion", "Casa Pinar", 1, "X"), "devolucion-casapinar #1 SKU X");
  assertEquals(motivoMovimiento("venta", null, 1, "X"), "venta-revendedor #1 SKU X");
  assertEquals(motivoMovimiento("venta", "A", 1, "S".repeat(300)).length, 200);
});

import { reposicionPendiente, transicionDescuento } from "./logic.ts";
Deno.test("descuento: qué estado queda según Ecomm-App", () => {
  assertEquals(transicionDescuento({ resultado: "aplicada" }, 200).estado, "descontada");
  assertEquals(transicionDescuento({ resultado: "aplicada_parcial", faltante: 1 }, 200).descontado, true);
  assertEquals(transicionDescuento({ resultado: "reintentar_misma_clave", esperarSegundos: 2 }, 202).estado, null);
  assertEquals(transicionDescuento({ resultado: "rechazada", motivo: "insufficient_stock", stockActual: 0 }, 409).estado, "sin_stock");
  assertEquals(transicionDescuento({ resultado: "rechazada", motivo: "expected_stock_conflict" }, 409).estado, "error");
  assertEquals(transicionDescuento({ resultado: "desconocido" }, 422, "VARIANT_NOT_FOUND").detalle, "Ecomm-App: VARIANT_NOT_FOUND.");
  const u = transicionDescuento({ resultado: "desconocido" }, 502);
  assertEquals([u.estado, u.descontado], ["error", false]); // outcome_unknown: nunca se reintenta con otra clave
});

Deno.test("reposición: solo lo descontado y una sola vez", () => {
  const base = { estado: "cancelada", descontado_at: "2026-11-19", repuesto_at: null, reposicion: "inmediata", recibido_estado: null };
  assertEquals(reposicionPendiente(base), "cancelacion");
  assertEquals(reposicionPendiente({ ...base, repuesto_at: "2026-11-20" }), null);       // ya devuelto
  assertEquals(reposicionPendiente({ ...base, descontado_at: null }), null);             // nunca se descontó
  assertEquals(reposicionPendiente({ ...base, reposicion: "al_recibir" }), null);        // espera al depósito
  assertEquals(reposicionPendiente({ ...base, reposicion: "al_recibir", recibido_estado: "bien" }), "devolucion");
  assertEquals(reposicionPendiente({ ...base, reposicion: "al_recibir", recibido_estado: "danado" }), null);
  assertEquals(reposicionPendiente({ ...base, estado: "descontada" }), null);             // venta vigente
});

Deno.test("colchón por revendedor: gana el del producto, después el del revendedor, después el general", () => {
  assertEquals(colchonPara(null, null, 3), 3);
  assertEquals(colchonPara(null, 10, 3), 10);
  assertEquals(colchonPara(0, 10, 3), 0);
  assertEquals(colchonPara(null, 0, 3), 0);
  assertEquals(stockOfrecido(50, colchonPara(null, 10, 3)), 40);
});

import { aceptaStock } from "./logic.ts";
Deno.test("aceptaStock: en revisión, cerrada o inactiva no; activa, pausada o sin dato sí", () => {
  assertEquals(["under_review", "closed", "inactive"].map(aceptaStock), [false, false, false]);
  assertEquals(["active", "paused", null, undefined].map(aceptaStock), [true, true, true, true]);
});

Deno.test("cuentas propias: nunca se les sincroniza stock", async () => {
  const { esCuentaPropia } = await import("./logic.ts");
  const propias = [1255615205, 189036603];
  assertEquals(esCuentaPropia({ ml_user_id: 189036603, solo_publicaciones: true }, propias), true);
  assertEquals(esCuentaPropia({ ml_user_id: 1255615205, solo_publicaciones: false }, propias), true); // Pret, aunque no sea "solo publicar"
  assertEquals(esCuentaPropia({ ml_user_id: 999, solo_publicaciones: true }, []), true);
  assertEquals(esCuentaPropia({ ml_user_id: "260792647", solo_publicaciones: false }, propias), false);
  assertEquals(esCuentaPropia({ ml_user_id: 260792647 }, null), false);
});
