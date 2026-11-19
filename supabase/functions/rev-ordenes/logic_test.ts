// Pruebas del registro de ventas de revendedores. Correr con: deno test supabase/functions/rev-ordenes/logic_test.ts
import { assertEquals } from "jsr:@std/assert@1";
import { debeReavisar, esAnteriorALaConexion, estadoDeOrden, lineasDeOrden, nuevoEstadoParaExistente, type OrdenML } from "./logic.ts";

Deno.test("debeReavisar: venta paga en ML que no está en la base → sí", () => {
  assertEquals(debeReavisar("sombra", undefined), true);
  assertEquals(debeReavisar("sombra", []), true);
});

Deno.test("debeReavisar: venta paga que ya está en la base, en cualquier estado → no", () => {
  assertEquals(debeReavisar("sombra", ["sombra"]), false);
  assertEquals(debeReavisar("sombra", ["descontada"]), false);
  assertEquals(debeReavisar("sombra", ["cancelada"]), false);
});

Deno.test("debeReavisar: cancelada en ML y todavía como venta en la base → sí", () => {
  assertEquals(debeReavisar("cancelada", ["sombra"]), true);
  assertEquals(debeReavisar("cancelada", ["nueva", "cancelada"]), true);
});

Deno.test("debeReavisar: cancelada en ML y ya cancelada o ausente → no", () => {
  assertEquals(debeReavisar("cancelada", ["cancelada"]), false);
  assertEquals(debeReavisar("cancelada", undefined), false);
});

Deno.test("debeReavisar: orden sin pago aprobado → no", () => {
  assertEquals(debeReavisar(null, undefined), false);
  assertEquals(debeReavisar(null, ["sombra"]), false);
});

Deno.test("esAnteriorALaConexion: una orden de antes de la conexión se ignora, una posterior no", () => {
  const conexion = "2026-11-18T11:33:05.584Z";
  assertEquals(esAnteriorALaConexion({ id: 1, date_created: "2026-11-17T20:00:00.000-03:00" }, conexion), true);
  assertEquals(esAnteriorALaConexion({ id: 1, date_created: "2026-11-18T09:00:00.000-03:00" }, conexion), false);
});

Deno.test("esAnteriorALaConexion: respeta la zona horaria de ML (-03:00 / -04:00)", () => {
  // 08:40 en -03:00 = 11:40 UTC: posterior a la conexión de las 11:33 UTC
  assertEquals(esAnteriorALaConexion({ id: 1, date_created: "2026-11-18T08:40:00.000-03:00" }, "2026-11-18T11:33:05Z"), false);
  // 08:20 en -03:00 = 11:20 UTC: anterior
  assertEquals(esAnteriorALaConexion({ id: 1, date_created: "2026-11-18T08:20:00.000-03:00" }, "2026-11-18T11:33:05Z"), true);
});

Deno.test("esAnteriorALaConexion: ante datos faltantes o inválidos NO descarta (se registra)", () => {
  assertEquals(esAnteriorALaConexion({ id: 1 }, "2026-11-18T11:33:05Z"), false);
  assertEquals(esAnteriorALaConexion({ id: 1, date_created: "2026-11-18T08:20:00.000-03:00" }, null), false);
  assertEquals(esAnteriorALaConexion({ id: 1, date_created: "basura" }, "2026-11-18T11:33:05Z"), false);
});

const pubs = new Map([["MLA1|0", "ABC-1"], ["MLA2|7", "XYZ-9"]]);
const skuPub = (item: string, v: number | null) => pubs.get(`${item}|${v ?? 0}`) ?? null;
const catalogo = new Map([["rem/001 m", "REM/001 M"]]);
const skuCat = (s: string) => catalogo.get(s.trim().toLowerCase()) ?? null;

const orden = (extra: Partial<OrdenML> = {}): OrdenML => ({
  id: 2000019556227970,
  status: "paid",
  payments: [{ status: "approved" }],
  order_items: [{ item: { id: "MLA1", seller_sku: "abc-1" }, quantity: 2, unit_price: 100 }],
  ...extra,
});

Deno.test("estadoDeOrden: pagada y no cancelada = venta (sombra)", () => {
  assertEquals(estadoDeOrden(orden()), "sombra");
});

Deno.test("estadoDeOrden: cancelada manda sobre el pago", () => {
  assertEquals(estadoDeOrden(orden({ status: "cancelled" })), "cancelada");
});

Deno.test("estadoDeOrden: sin pago aprobado todavía no cuenta", () => {
  assertEquals(estadoDeOrden(orden({ payments: [{ status: "pending" }] })), null);
  assertEquals(estadoDeOrden(orden({ payments: [{ status: "rejected" }] })), null);
  assertEquals(estadoDeOrden(orden({ payments: [] })), null);
  assertEquals(estadoDeOrden(orden({ payments: null })), null);
});

Deno.test("estadoDeOrden: con un pago aprobado entre varios, cuenta", () => {
  assertEquals(estadoDeOrden(orden({ payments: [{ status: "rejected" }, { status: "approved" }] })), "sombra");
});

Deno.test("lineasDeOrden: usa primero la publicación vinculada", () => {
  const l = lineasDeOrden(orden(), skuPub, skuCat);
  assertEquals(l, [{ sku: "ABC-1", ml_item_id: "MLA1", cantidad: 2, precio_unitario: 100, reconocida: true }]);
});

Deno.test("lineasDeOrden: si la publicación no está vinculada, reconoce por el SKU cargado en ML", () => {
  const o = orden({ order_items: [{ item: { id: "MLA55", seller_custom_field: "REM/001 M" }, quantity: 1, unit_price: 50 }] });
  assertEquals(lineasDeOrden(o, skuPub, skuCat)[0], { sku: "REM/001 M", ml_item_id: "MLA55", cantidad: 1, precio_unitario: 50, reconocida: true });
});

Deno.test("lineasDeOrden: una venta no reconocida se registra igual, marcada como no reconocida", () => {
  const o = orden({ order_items: [{ item: { id: "MLA77", seller_sku: "COSA-RARA" }, quantity: 3 }] });
  assertEquals(lineasDeOrden(o, skuPub, skuCat)[0], { sku: "COSA-RARA", ml_item_id: "MLA77", cantidad: 3, precio_unitario: null, reconocida: false });
});

Deno.test("lineasDeOrden: sin SKU de ningún lado queda como ?<ítem>", () => {
  const o = orden({ order_items: [{ item: { id: "MLA88" }, quantity: 1 }] });
  const l = lineasDeOrden(o, skuPub, skuCat)[0];
  assertEquals([l.sku, l.reconocida], ["?MLA88", false]);
});

Deno.test("lineasDeOrden: la variación se usa para encontrar la publicación vinculada", () => {
  const o = orden({ order_items: [{ item: { id: "MLA2", variation_id: 7 }, quantity: 1 }] });
  assertEquals(lineasDeOrden(o, skuPub, skuCat)[0].sku, "XYZ-9");
});

Deno.test("lineasDeOrden: dos líneas del mismo SKU se suman en una (clave única orden + SKU)", () => {
  const o = orden({
    order_items: [
      { item: { id: "MLA1" }, quantity: 2, unit_price: 100 },
      { item: { id: "MLA1" }, quantity: 3, unit_price: 100 },
    ],
  });
  const l = lineasDeOrden(o, skuPub, skuCat);
  assertEquals(l.length, 1);
  assertEquals(l[0].cantidad, 5);
});

Deno.test("lineasDeOrden: cantidades inválidas se ignoran", () => {
  const o = orden({ order_items: [{ item: { id: "MLA1" }, quantity: 0 }, { item: { id: "MLA1" }, quantity: -2 }, { item: { id: "MLA1" }, quantity: NaN }] });
  assertEquals(lineasDeOrden(o, skuPub, skuCat), []);
});

Deno.test("nuevoEstadoParaExistente: una venta simulada que se cancela pasa a cancelada", () => {
  assertEquals(nuevoEstadoParaExistente("sombra", "cancelada"), "cancelada");
  assertEquals(nuevoEstadoParaExistente("nueva", "sombra"), "sombra");
});

Deno.test("nuevoEstadoParaExistente: una venta ya descontada que ML cancela pasa a cancelada (el stock lo devuelve el descuento)", () => {
  assertEquals(nuevoEstadoParaExistente("descontada", "cancelada"), "cancelada");
  assertEquals(nuevoEstadoParaExistente("sin_stock", "cancelada"), "cancelada");
});

Deno.test("nuevoEstadoParaExistente: lo ya descontado o con problema no se toca desde acá", () => {
  assertEquals(nuevoEstadoParaExistente("descontada", "sombra"), null);
  assertEquals(nuevoEstadoParaExistente("sin_stock", "sombra"), null);
  assertEquals(nuevoEstadoParaExistente("error", "sombra"), null);
});

Deno.test("nuevoEstadoParaExistente: sin cambio no hace nada", () => {
  assertEquals(nuevoEstadoParaExistente("sombra", "sombra"), null);
  assertEquals(nuevoEstadoParaExistente("cancelada", "cancelada"), null);
});

Deno.test("nuevoEstadoParaExistente: una cancelada no vuelve a venta sola", () => {
  assertEquals(nuevoEstadoParaExistente("cancelada", "sombra"), null);
});

import { fueDespachada, reclamoCambio, reposicionPara, resumenReclamo } from "./logic.ts";

Deno.test("¿salió del depósito?", () => {
  assertEquals(fueDespachada({ status: "delivered" }), true);
  assertEquals(fueDespachada({ status: "shipped" }), true);
  assertEquals(fueDespachada({ status: "not_delivered" }), true);
  assertEquals(fueDespachada({ status: "ready_to_ship", substatus: "picked_up" }), true);
  assertEquals(fueDespachada({ status: "ready_to_ship", substatus: "ready_to_print" }), false);
  assertEquals(fueDespachada({ status: "handling" }), false);
  assertEquals(fueDespachada({ status: "cancelled" }), false);
  assertEquals(fueDespachada(null), null);
  assertEquals(fueDespachada({ status: "algo_nuevo" }), null);
});

Deno.test("cuándo vuelve el stock de una cancelación", () => {
  assertEquals(reposicionPara(false), "inmediata");
  assertEquals(reposicionPara(true), "al_recibir");
  assertEquals(reposicionPara(null), "al_recibir"); // sin datos: se espera a recibirlo
});

Deno.test("reclamo: resumen y si cambió", () => {
  const c = { id: 5, type: "returns", stage: "claim", status: "opened", last_updated: "2026-11-18T10:00:00" };
  const r = resumenReclamo(c, { status: "label_generated", status_money: "retained", shipments: [{ status: "ready_to_ship", type: "return" }] });
  assertEquals(r.devolucion_estado, "label_generated");
  assertEquals(r.envio_vuelta, "ready_to_ship");
  assertEquals(reclamoCambio(c, { reclamos: [r] }), false);
  assertEquals(reclamoCambio({ ...c, last_updated: "2026-11-19T10:00:00" }, { reclamos: [r] }), true);
  assertEquals(reclamoCambio(c, null), true);
});

Deno.test("debeReavisar: una venta descontada que ML canceló → sí (hay que devolver el stock)", () => {
  assertEquals(debeReavisar("cancelada", ["descontada"]), true);
  assertEquals(debeReavisar("cancelada", ["sin_stock"]), true);
  assertEquals(debeReavisar("cancelada", ["cancelada"]), false);
  assertEquals(debeReavisar("cancelada", ["error"]), false); // un error se revisa a mano
});
