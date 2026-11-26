import { assertEquals } from "jsr:@std/assert@1";
import { comparar, costosPorSku, rolDelToken } from "./logic.ts";

Deno.test("solo entran SKU con costo; repetidos quedan con el mayor", () => {
  const r = costosPorSku([
    { sku: " A ", cost: 100 }, { sku: "A", cost: 90 }, { sku: "B", cost: "20.505" }, { sku: "C", cost: null },
    { sku: "D", cost: 0 }, { sku: "", cost: 5 }, { sku: "E" },
  ]);
  assertEquals([...r.costos], [["A", 100], ["B", 20.51]]);
  assertEquals(r.repetidos, ["A"]);
});

Deno.test("comparar informa nuevos, cambiados y los que ya no vienen", () => {
  const r = comparar(new Map([["A", 10], ["B", 25], ["C", 1]]), new Map([["A", 10], ["B", 20], ["Z", 3]]));
  assertEquals(r, { agregados: 1, cambiados: [{ sku: "B", antes: 20, ahora: 25 }], noVienen: 1 });
});

Deno.test("solo la clave de servicio tiene rol service_role", () => {
  const clave = (role: string) => `Bearer x.${btoa(JSON.stringify({ role })).replaceAll("=", "")}.y`;
  assertEquals(rolDelToken(clave("service_role")), "service_role");
  assertEquals(rolDelToken(clave("anon")), "anon");
  assertEquals(rolDelToken(null), null);
  assertEquals(rolDelToken("Bearer cualquiercosa"), null);
});
