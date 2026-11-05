// Correr:  deno test supabase/functions/ventas-webhook/logic_test.ts
import { assertEquals } from "jsr:@std/assert@1";
import { canalesPorStoreId, decidir, fechaDelString, iguales, lineasDeOrdenTN, parsearFechaTN } from "./logic.ts";

const orden = (extra = {}) => ({
  id: 2050000001,
  created_at: "2026-11-05T23:30:00+0000",
  status: "open",
  payment_status: "paid",
  products: [
    { sku: "PRET-A", quantity: 2 },
    { sku: "PRET-A", quantity: 1 }, // misma unidad separada en dos entradas → se suman
    { sku: "", variant_id: 777, quantity: 1 }, // sin sku → cae a variant_id
    { sku: "", variant_id: null, quantity: 5 }, // sin nada → se descarta
  ],
  ...extra,
});

Deno.test("aplana igual que tn_orders_to_lines + dedupe", () => {
  const l = lineasDeOrdenTN("tn_pret", orden());
  assertEquals(l.map((x) => [x.sku, x.cantidad]), [["PRET-A", 3], ["777", 1]]);
  assertEquals(l[0].canal, "tn_pret");
  assertEquals(l[0].item_id, "");
  assertEquals(l[0].order_id, "2050000001");
  assertEquals(l[0].fecha, "2026-11-05");
  assertEquals(l[0].creado_at, "2026-11-05T23:30:00.000Z");
});

Deno.test("la fecha es el día tal cual viene escrito (como Python), no el de UTC", () => {
  assertEquals(fechaDelString("2026-11-05T23:30:00-0300"), "2026-11-05");
  assertEquals(parsearFechaTN("2026-11-05T23:30:00-0300")!.toISOString(), "2026-11-06T02:30:00.000Z");
});

Deno.test("fechas inválidas no rompen: la orden se descarta", () => {
  assertEquals(lineasDeOrdenTN("tn_pret", orden({ created_at: "basura" })), []);
  assertEquals(lineasDeOrdenTN("tn_pret", orden({ created_at: undefined })), []);
});

Deno.test("decisión: paid/authorized guardan, cancelled cancela, el resto se ignora", () => {
  assertEquals(decidir("tn_pret", orden()).accion, "guardar");
  assertEquals(decidir("tn_pret", orden({ payment_status: "authorized" })).accion, "guardar");
  assertEquals(decidir("tn_pret", orden({ status: "cancelled" })), { accion: "cancelar", order_id: "2050000001" });
  // cancelada gana aunque figure pagada (igual que el sync diario)
  assertEquals(decidir("tn_pret", orden({ status: "cancelled", payment_status: "paid" })).accion, "cancelar");
  assertEquals(decidir("tn_pret", orden({ payment_status: "pending" })).accion, "ignorar");
  assertEquals(decidir("tn_pret", null).accion, "ignorar");
});

Deno.test("token: comparación exacta", () => {
  assertEquals(iguales("abc123", "abc123"), true);
  assertEquals(iguales("abc123", "abc124"), false);
  assertEquals(iguales("abc", "abc123"), false);
  assertEquals(iguales("", "abc"), false);
});

Deno.test("store_id → canal", () => {
  const cred = { tn: { pret: { store_id: 111 }, lavan: { store_id: "222" } } };
  assertEquals(canalesPorStoreId(cred), { "111": "tn_pret", "222": "tn_lavan" });
  assertEquals(canalesPorStoreId({}), {});
});
