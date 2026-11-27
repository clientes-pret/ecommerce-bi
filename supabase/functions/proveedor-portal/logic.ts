// Lógica de proveedor-portal, separada de la conexión para poder probarla (deno test).
// La validación fina (producto propio y pendiente, topes, fechas) la hace la base: prov_portal_responder.

export type Rpc = (fn: string, args: Record<string, unknown>) => Promise<{ data: unknown; error: { message: string; code?: string } | null }>;

export const MAX_CUERPO = 100_000; // bytes: 600 respuestas entran holgadas

const CABECERAS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Cache-Control": "no-store",
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...CABECERAS, "Content-Type": "application/json" } });

const LINK_INVALIDO = "Este link no es válido o ya no está activo. Pedí uno nuevo.";

/** Deja pasar solo los campos esperados, con su tipo. Devuelve null si algo no tiene forma de respuesta. */
export function limpiarRespuestas(lista: unknown): Record<string, unknown>[] | null {
  if (!Array.isArray(lista) || !lista.length || lista.length > 600) return null;
  const out: Record<string, unknown>[] = [];
  for (const r of lista) {
    if (!r || typeof r !== "object") return null;
    const { sku, cantidad, fecha, no_entrega } = r as Record<string, unknown>;
    if (typeof sku !== "string" || !sku || sku.length > 120) return null;
    if (cantidad != null && !(Number.isInteger(cantidad) && (cantidad as number) >= 1 && (cantidad as number) <= 100000)) return null;
    if (fecha != null && !(typeof fecha === "string" && /^\d{4}-\d{2}-\d{2}$/.test(fecha) && !Number.isNaN(Date.parse(fecha)) &&
      new Date(fecha).toISOString().slice(0, 10) === fecha)) return null;
    out.push({ sku, cantidad: cantidad ?? null, fecha: fecha ?? null, no_entrega: no_entrega === true });
  }
  return out;
}

export async function atender(req: Request, rpc: Rpc): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { headers: CABECERAS });
  if (req.method !== "POST") return json({ error: "Método no permitido" }, 405);

  const texto = await req.text();
  if (texto.length > MAX_CUERPO) return json({ error: "El envío es demasiado grande" }, 413);
  let cuerpo: Record<string, unknown>;
  try {
    cuerpo = JSON.parse(texto);
  } catch {
    return json({ error: "Pedido inválido" }, 400);
  }
  // El código viaja en el cuerpo (nunca en la dirección, para que no quede en registros).
  const token = cuerpo?.token;
  if (typeof token !== "string" || !/^[0-9a-f]{64}$/.test(token)) return json({ error: LINK_INVALIDO }, 404);

  if (cuerpo.accion === "ver") {
    const { data, error } = await rpc("prov_portal_datos", { p_token: token });
    if (error) return fallo(error);
    return data == null ? json({ error: LINK_INVALIDO }, 404) : json(data);
  }
  if (cuerpo.accion === "responder") {
    const items = limpiarRespuestas(cuerpo.respuestas);
    if (!items) return json({ error: "Revisá las cantidades y fechas: hay algún dato inválido" }, 400);
    const { data, error } = await rpc("prov_portal_responder", { p_token: token, p_items: items });
    if (error) return fallo(error);
    return data == null ? json({ error: LINK_INVALIDO }, 404) : json({ ok: true, guardadas: data });
  }
  return json({ error: "Pedido inválido" }, 400);
}

// Los avisos que escribe la base (raise exception, código P0001) son para el proveedor; el resto no se muestra.
function fallo(error: { message: string; code?: string }): Response {
  if (error.code === "P0001") return json({ error: error.message }, 400);
  console.error(error);
  return json({ error: "No se pudo completar. Probá de nuevo en unos minutos." }, 500);
}
