// Pruebas de la parte pura de Despacho, con códigos reales de etiquetas:
//   deno test supabase/functions/armado-api/despacho_test.ts
import { assertEquals } from "jsr:@std/assert@1";
import { detectarPaquete, esParaManana, grupoDeEnvioTN } from "./despacho.ts";

Deno.test("ML Flex: id + sender_id → Zeta/Ninja, Flex", () => {
  const p = detectarPaquete('{"id":"48360235661","sender_id":1255615205,"hash_code":null,"security_digit":"0"}')!;
  assertEquals([p.formato, p.grupo, p.tipo, p.clave, p.sender], ["ml_flex", "zeta_ninja", "flex_ml", "ml|48360235661", "1255615205"]);
});

Deno.test("ML Colecta no es de ninguna mensajería de Despacho", () => {
  const p = detectarPaquete('{"id":"48356856113","t":"lm"}')!;
  assertEquals([p.formato, p.grupo, p.clave], ["ml_colecta", null, null]);
});

Deno.test("ML vía Correo (carrier_data) → Correo", () => {
  const p = detectarPaquete('{"carrier_data":"HC561528815|Sucursal|5250|","id":"48428481173","t":"l"}')!;
  assertEquals([p.formato, p.grupo, p.tipo, p.clave], ["ml_correo", "correo", "otro", "ml|48428481173"]);
});

Deno.test("Correo Argentino: seguimiento|DOM|…", () => {
  const p = detectarPaquete("197816A39ME75AC601|DOM|SANTA FE|2617|HC562285037")!;
  assertEquals([p.formato, p.grupo, p.clave, p.ref], ["correo", "correo", "correo|197816A39ME75AC601", "197816A39ME75AC601"]);
});

Deno.test("Zeta y Ninja son el mismo grupo", () => {
  const z = detectarPaquete('{"did": 1571347}')!;
  const n = detectarPaquete('{"internalID": "ABC123"}')!;
  assertEquals([z.formato, z.grupo, z.clave, z.zetaToken], ["zeta", "zeta_ninja", "zeta|1571347", "1571347d54df4s8a513"]);
  assertEquals([n.formato, n.grupo, n.clave], ["ninja", "zeta_ninja", "ninja|ABC123"]);
});

Deno.test("Zeta por la dirección de seguimiento da la misma clave que por el did", () => {
  const p = detectarPaquete("https://zetaenvios.lightdata.app/tracking.php?token=1571347d54df4s8a513")!;
  assertEquals(p.clave, "zeta|1571347");
});

Deno.test("Números escritos a mano piden averiguar la mensajería", () => {
  assertEquals(detectarPaquete("52084")!.resolverGrupo, true);
  assertEquals(detectarPaquete("2000012345678901")!.formato, "ml_venta");
  assertEquals(detectarPaquete("48360235661")!.formato, "ml_envio_manual");
});

Deno.test("Texto desconocido queda como candidato a Andreani", () => {
  const p = detectarPaquete("AND-0012345678")!;
  assertEquals([p.formato, p.grupo, p.clave], ["desconocido", null, "andreani|and-0012345678"]);
});

Deno.test("Mensajería de cada opción de envío de Tiendanube (nombres reales)", () => {
  const casos: [string, "zeta_ninja" | "correo" | "andreani" | null][] = [
    ["Envío Nube - Correo Argentino Clásico a domicilio", "correo"],
    ["Envío Nube - Correo Argentino Expreso a sucursal", "correo"],
    ["Envío Nube - Andreani a sucursal", "andreani"],
    ["Envío Nube - Andreani a domicilio", "andreani"],
    ["Envío en el día - CABA", "zeta_ninja"],
    ["Envío en el día - GBA 2", "zeta_ninja"],
    ["Ninja Flex - Flex", "zeta_ninja"],
    ["Envío Express CABA 24/48 Hs.", "zeta_ninja"],
    ["Punto de retiro", null],
    ["PRET A HOME", null],
    ["Costo y plazo de envío estándar", null],
  ];
  for (const [nombre, esperado] of casos) assertEquals(grupoDeEnvioTN(nombre), esperado, nombre);
  assertEquals(grupoDeEnvioTN("Punto de retiro", "Envío Nube"), "correo");
});

Deno.test("Corte de las 14 hs (hora Argentina)", () => {
  assertEquals(esParaManana("2026-11-05T16:59:00Z", "2026-11-05"), false); // 13:59 AR
  assertEquals(esParaManana("2026-11-05T17:00:00Z", "2026-11-05"), true);  // 14:00 AR
  assertEquals(esParaManana("2026-11-04T20:00:00Z", "2026-11-05"), false); // ayer 17:00 AR: se debe hoy
});
