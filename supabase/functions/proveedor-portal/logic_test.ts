// Correr: deno test supabase/functions/proveedor-portal/logic_test.ts
import { atender, limpiarRespuestas, MAX_CUERPO, type Rpc } from "./logic.ts";

function igual(a: unknown, b: unknown, msg = "") {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg} esperado ${JSON.stringify(b)}, obtenido ${JSON.stringify(a)}`);
}
const TOKEN = "a".repeat(64);
const pedir = (body: unknown, method = "POST") =>
  new Request("http://x/proveedor-portal", { method, body: method === "POST" ? (typeof body === "string" ? body : JSON.stringify(body)) : undefined });

function doble(respuesta: Awaited<ReturnType<Rpc>>) {
  const llamadas: [string, Record<string, unknown>][] = [];
  const rpc: Rpc = (fn, args) => { llamadas.push([fn, args]); return Promise.resolve(respuesta); };
  return { rpc, llamadas };
}

Deno.test("código mal formado: 404 sin tocar la base", async () => {
  const d = doble({ data: {}, error: null });
  for (const token of [undefined, "", "abc", "A".repeat(64), "a".repeat(65), 5, "' or 1=1 --"]) {
    igual((await atender(pedir({ token, accion: "ver" }), d.rpc)).status, 404, String(token));
  }
  igual(d.llamadas.length, 0);
});

Deno.test("ver: devuelve los datos; dado de baja o inexistente = 404", async () => {
  const ok = doble({ data: { proveedor: "X", productos: [] }, error: null });
  const r = await atender(pedir({ token: TOKEN, accion: "ver" }), ok.rpc);
  igual(r.status, 200); igual(await r.json(), { proveedor: "X", productos: [] });
  igual(r.headers.get("Cache-Control"), "no-store");
  igual(ok.llamadas[0], ["prov_portal_datos", { p_token: TOKEN }]);
  igual((await atender(pedir({ token: TOKEN, accion: "ver" }), doble({ data: null, error: null }).rpc)).status, 404);
});

Deno.test("responder: solo pasan los campos esperados", async () => {
  const d = doble({ data: 1, error: null });
  const r = await atender(pedir({ token: TOKEN, accion: "responder", respuestas: [{ sku: "S", cantidad: 3, fecha: "2026-12-05", proveedor: "otro", pend_u: 9 }] }), d.rpc);
  igual(r.status, 200);
  igual(d.llamadas[0][1].p_items, [{ sku: "S", cantidad: 3, fecha: "2026-12-05", no_entrega: false }]);
});

Deno.test("respuestas inválidas no llegan a la base", async () => {
  const malas: unknown[] = [null, [], "x", [null], [{}], [{ sku: "" }], [{ sku: "S", cantidad: 0 }], [{ sku: "S", cantidad: 1.5 }],
    [{ sku: "S", cantidad: "3" }], [{ sku: "S", cantidad: 100001 }], [{ sku: "S", fecha: "05/12/2026" }], [{ sku: "S", fecha: "2026-02-31" }],
    [{ sku: "S".repeat(121), cantidad: 1 }], Array.from({ length: 601 }, () => ({ sku: "S", cantidad: 1 }))];
  for (const m of malas) igual(limpiarRespuestas(m), null, JSON.stringify(m)?.slice(0, 60));
  const d = doble({ data: 1, error: null });
  igual((await atender(pedir({ token: TOKEN, accion: "responder", respuestas: [{ sku: "S", cantidad: -1 }] }), d.rpc)).status, 400);
  igual(d.llamadas.length, 0);
});

Deno.test("los avisos de la base se muestran; los errores internos no", async () => {
  const aviso = await atender(pedir({ token: TOKEN, accion: "ver" }), doble({ data: null, error: { code: "P0001", message: "Ya no está pendiente" } }).rpc);
  igual(aviso.status, 400); igual((await aviso.json()).error, "Ya no está pendiente");
  const original = console.error; console.error = () => {};
  const interno = await atender(pedir({ token: TOKEN, accion: "ver" }), doble({ data: null, error: { code: "42P01", message: "relation secreta does not exist" } }).rpc);
  console.error = original;
  igual(interno.status, 500); igual((await interno.json()).error.includes("secreta"), false);
});

Deno.test("método, tamaño, JSON y acción", async () => {
  const d = doble({ data: {}, error: null });
  igual((await atender(pedir(null, "GET"), d.rpc)).status, 405);
  igual((await atender(pedir(null, "OPTIONS"), d.rpc)).status, 200);
  igual((await atender(pedir("x".repeat(MAX_CUERPO + 1)), d.rpc)).status, 413);
  igual((await atender(pedir("{no es json"), d.rpc)).status, 400);
  igual((await atender(pedir({ token: TOKEN, accion: "borrar" }), d.rpc)).status, 400);
  igual(d.llamadas.length, 0);
});
